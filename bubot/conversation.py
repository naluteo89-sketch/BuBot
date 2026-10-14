"""대화: 이전 질문을 반영해 답하고(후속 질문 재구성·정정·개념 질문), 세션별로 최근 질문·답변을 기억합니다.

질문 하나에 대한 답은 rag_service가 만듭니다. 이 파일은 그 앞뒤의 대화 처리와 메모리를 맡습니다.

대화 메모리:

- 세션마다 최근 MEMORY_TURNS개(최소 3개) 질문·답변을 기억합니다.
- 디스크에 저장하지 않습니다. 서버를 재시작하거나 '새 대화'를 누르면 사라집니다.
  (테스트 기록 파일 저장은 별도 기능입니다.)
- 마지막 질문 후 MEMORY_TTL_SECONDS가 지나면 세션을 지웁니다.
"""
import re
import time
from collections import OrderedDict, deque
from threading import RLock

from . import rag_service
from .answers.answer_format import display_sources, rule_answer
from .answers.choices import candidate_choices
from .cache.answer_cache import ANSWER_CACHE, cache_key
from .cache.data_cache import load_corpus
from .config import DISPLAY_PRICE_UNIT, MEMORY_MAX_SESSIONS, MEMORY_TTL_SECONDS, MEMORY_TURNS
from .filters.guards import unavailable
from .filters.keyboard import keyboard_to_hangul
from .filters.period_parser import explicit_years
from .filters.query_parser import parse_question
from .llm import gpt_api
from .llm.usage import timed
from .queries.period_queries import aggregate_query, annual_mean_concept, correction_query, negative_concept
from .queries.year_query import resolve_relative_years
from .regions import named_region, region_catalog

SESSION_PATTERN = re.compile(r'^[A-Za-z0-9_-]{8,64}$')
ANSWER_LIMIT = 700


def summarize_turn(question, result=None, error=None, standalone=None):
    """다음 질문 해석에 필요한 내용만 남깁니다 (원문 질문, 재구성 질문, 답변 요약, 확정 조건)."""
    result = result or {}
    analysis = result.get('analysis') or {}
    answer = error or result.get('answer') or ''
    if len(answer) > ANSWER_LIMIT:
        answer = answer[:ANSWER_LIMIT] + '…'
    resolved = {k: analysis.get(k) for k in ('intent', 'resolved_month', 'resolved_start', 'resolved_end',
                                             'knowledge_month', 'knowledge_year', 'level')
                if analysis.get(k)}
    regions = [c['metadata'].get('region_path') for c in result.get('contexts', [])
               if c.get('metadata', {}).get('kind') in {'monthly', 'timeline', 'region_profile'}]
    if regions:
        resolved['regions'] = list(dict.fromkeys(r for r in regions if r))
    return {'question': question,
            'standalone_question': result.get('standalone_question') or standalone or question,
            'answer': answer,
            'facts': list(result.get('facts') or [])[:6],
            'resolved': resolved,
            'clarification': analysis.get('intent') == 'clarify',
            'error': bool(error),
            'price_extreme': bool(analysis.get('price_extreme')),
            'sources': list(result.get('sources') or []) if analysis.get('price_extreme') else []}


class ConversationMemory:
    def __init__(self, turns=MEMORY_TURNS, ttl=MEMORY_TTL_SECONDS, max_sessions=MEMORY_MAX_SESSIONS, clock=time.monotonic):
        if turns < 3:
            raise ValueError('대화 메모리는 최소 3개 대화를 기억해야 합니다.')
        self.turns, self.ttl, self.max_sessions, self.clock = turns, ttl, max_sessions, clock
        self._sessions = OrderedDict()
        self._lock = RLock()

    @staticmethod
    def check(session_id):
        if not isinstance(session_id, str) or not SESSION_PATTERN.match(session_id):
            raise ValueError('대화 세션 ID 형식이 올바르지 않습니다.')
        return session_id

    def _expire(self):
        now = self.clock()
        for key in [k for k, (stamp, _) in self._sessions.items() if now - stamp > self.ttl]:
            del self._sessions[key]

    def history(self, session_id):
        with self._lock:
            self._expire()
            entry = self._sessions.get(self.check(session_id))
            return list(entry[1]) if entry else []

    def add(self, session_id, turn):
        with self._lock:
            self._expire()
            session_id = self.check(session_id)
            _, turns = self._sessions.pop(session_id, (None, deque(maxlen=self.turns)))
            turns.append(turn)
            self._sessions[session_id] = (self.clock(), turns)
            while len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)

    def reset(self, session_id):
        with self._lock:
            self._sessions.pop(self.check(session_id), None)

    def size(self, session_id):
        return len(self.history(session_id))


MEMORY = ConversationMemory()


def chat(session_id, question, *, memory=None, **options):
    """이전 대화를 반영해 질문에 답하고, 결과를 메모리에 기록합니다."""
    memory = memory or MEMORY
    history = memory.history(session_id)
    try:
        result = ask(question, history=history, **options)
    except ValueError as error:
        memory.add(session_id, summarize_turn(question, error=str(error),
                                              standalone=getattr(error, 'standalone_question', None)))
        raise
    memory.add(session_id, summarize_turn(question, result))
    return {**result, 'memory_turns': memory.size(session_id), 'memory_used': len(history)}


# 대화 처리 ──────────────────────────────────────────────────

AMBIGUOUS_REGION = '확정할 수 없습니다'
# 이전 대화를 가리키거나 다른 지역과 견주는 표현. 있으면 지역·기간이 적혀 있어도 재구성합니다.
REFERS_BACK = re.compile(r'비교|차이|대비|보다|그\s*(?:지역|곳|때|기간)|거기|같은|아까|이전|앞에서|위에서|방금|다시|그럼|그러면|도\s*(?:알려|보여|궁금)')


def _self_contained(question):
    """질문에 확정 지역과 기간이 모두 있고 이전 대화를 가리키지 않으면 True ('서울 2026년 8월 가격')."""
    if REFERS_BACK.search(question):
        return False
    if re.search(r'(?<!\d)\d{1,2}\s*월', question) and not explicit_years(resolve_relative_years(question)):
        return False  # '6월치 전부'처럼 연도 없는 월은 직전 대화의 연도를 이어받아야 함
    corpus = load_corpus()
    frame = parse_question(question, region_catalog(corpus), corpus.months)
    return bool(frame.targets) and not frame.ambiguous and frame.period.explicit


def ask(question, history=None, **kwargs):
    """history: 이전 대화 요약 목록(bubot.conversation). 있으면 먼저 단독 질문으로 재구성합니다.

    '지난달·3년 전·연초 대비·마용성·평당' 같은 일상 표현은 먼저 명시 월·지역명으로 바꾸고(expressions.rewrite)
    바꾼 내용을 답변 끝에 안내합니다.
    """
    question, notes = rag_service.rewritten(question)
    if '지수' in question:
        # 자료는 평균 매매가격(금액)입니다. '서울 지수'에 금액을 지수처럼 소개하지 않도록 밝힙니다.
        notes = [*notes, "질문의 '지수'는 매매가격지수 값이 아니라 지역별 아파트 월평균 매매가격(금액)으로 답했습니다. "
                         '이 서비스는 지수 값을 제공하지 않습니다.']
    return rag_service.with_notes(_converse(question, history, **kwargs), notes)


def _converse(question, history=None, **kwargs):
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000:
        raise ValueError('질문은 1~1000자, 검색 개수는 1~50개여야 합니다.')
    converted = keyboard_to_hangul(question)  # 한/영 전환 없이 친 한글 (예: 'tjdnf' → '서울')
    if converted:
        question = converted
    if annual_mean_concept(question):
        result={**rule_answer('네. 이 서비스에서 연간 평균을 요청하면 해당 연도의 월평균 매매가격을 합해 유효한 월 수로 나눈 단순 평균을 사용합니다. 12개월이 모두 있으면 12로 나누며, 결측 월은 제외하고 사용한 월 수를 밝힙니다. 12월 가격 하나는 연평균이 아닙니다. 거래 건수로 가중한 개별 실거래 평균과도 다릅니다.',
                                intent='concept'),'standalone_question':question,'followup':False,'timings':{}}
        if history:
            for turn in reversed(history):
                resolved=turn.get('resolved') or {}
                if resolved.get('resolved_start'):
                    result['analysis'].update({k:v for k,v in resolved.items() if k.startswith('resolved_')})
                    result['standalone_question']=turn.get('standalone_question',question)
                    break
        return result
    # 개념 질문은 대화의 특정 수치 질문으로 재구성하지 않습니다.
    if negative_concept(question):
        return {**rule_answer('네. 변동률은 비교 기준보다 가격이 내려가면 음수가 됩니다. '
                              '변동률 = (현재 가격 − 기준 가격) ÷ 기준 가격 × 100입니다. '
                              '예를 들어 1억원에서 9천만원으로 내려가면 -10%입니다. '
                              '특정 지역·월의 변동률이 양수인 것과 음수가 될 수 있는지는 별개의 질문입니다. '
                              '기준 가격이 0이면 이 식으로 변동률을 계산할 수 없습니다.', intent='concept'),
                'standalone_question':question,'followup':False,'timings':{}}
    # 재구성(GPT) 전에 원래 질문으로 확인합니다 ('실거래가'가 '매매가격'으로 바뀌면 단지 질문을 놓침).
    blocked=unavailable(question,load_corpus().months) if history else None
    if blocked is not None:
        return {**blocked,'standalone_question':question,'followup':False,'timings':{}}
    correction=correction_query(question,history,load_corpus(),kwargs.get('unit') or DISPLAY_PRICE_UNIT) if history else None
    if correction is not None:
        return display_sources({**correction,'standalone_question':question,'followup':True,'timings':{}})
    # 검증된 가격 극값의 후속 금액 질문은 해당 결과를 직접 이어갑니다.
    if history and re.fullmatch(r'(?:아니\s*)?(?:그\s*지역(?:의)?\s*)?(?:거래금|거래\s*금액|거래가|가격)(?:이|은|는)?\s*(?:얼마(?:인데|야|예요|인가요)?|알려줘)?[?？. ]*',question.strip()):
        for turn in reversed(history):
            if turn.get('price_extreme') and turn.get('facts'):
                return display_sources({'answer':'앞서 조회한 지역과 기준 월의 아파트 월평균 매매가격입니다.\n'+'\n'.join(turn['facts']),
                    'facts':turn['facts'],'sources':turn.get('sources',[]),'contexts':[],
                    'analysis':{'intent':'year_query','price_extreme':True},'route':'rule',
                    'standalone_question':turn.get('standalone_question',question),'followup':True,'timings':{}})
    if history and re.fullmatch(r'(?:그|그럼 그)\s*(?:지역|곳)(?:들)?(?:은|는|이|가)?[?？. ]*',question.strip()):
        for turn in reversed(history):
            if turn.get('resolved',{}).get('intent') == 'period_decline':
                original=turn['standalone_question']
                result=rag_service.answer_question(original,**kwargs)
                return display_sources({**result,'standalone_question':original,'followup':True,'timings':{}})
    standalone, followup, timings = question, False, {}
    explicit_extreme = re.search(r'(?<![0-9])(?:[0-9]{4}|[0-9]{2})년',question) and re.search('최저|최고|가장 싼|가장 비싼',question) and not re.search('아까|그 지역|이전',question)
    raw_aggregate=aggregate_query(question,load_corpus(),kwargs.get('unit') or DISPLAY_PRICE_UNIT)
    if raw_aggregate is not None and raw_aggregate['analysis']['intent']!='clarify':
        return display_sources({**raw_aggregate,'standalone_question':question,'followup':False,'input_converted':bool(converted),'timings':{}})
    if history and not explicit_extreme and _self_contained(question):
        history = None  # 지역·기간을 모두 적은 새 질문은 재구성하지 않음 (직전 지역이 섞여 들어가는 것 방지, API 1회 절약)
    if history and not explicit_extreme:
        rag_service.open_index()  # 색인 문제는 재구성 API 호출 전에 확인
        condensed = timed(timings, '질문재구성', gpt_api.condense_question, question, history)
        standalone, followup = condensed['standalone_question'], condensed['is_followup']
        if followup:
            catalog=region_catalog(load_corpus())
            scope,ambiguous=named_region(standalone,catalog)
            # '시도 중 가장 싼 곳'처럼 전국 범위 질문에는 이전 지역을 붙이지 않습니다 (붙이면 후보가 1곳으로 줄어듦).
            nationwide = re.search(r'전국|모든 지역|전체 지역|시\s*·?\s*도\s*(?:중|에서|별|들)|권역\s*(?:중|별)', question + ' ' + standalone)
            if scope is None and not ambiguous and not nationwide:
                for turn in reversed(history):
                    prior=turn.get('standalone_question') or turn.get('question','')
                    previous,ambiguous=named_region(prior,catalog)
                    if previous and not ambiguous:
                        standalone=previous['label']+' '+standalone
                        break
    key = cache_key(standalone, kwargs, load_corpus().source_hash)
    try:
        result = ANSWER_CACHE.get(key)
        if result is None:
            result = rag_service.answer_question(standalone, **kwargs)
            ANSWER_CACHE.put(key, result)
        else:
            result['cached'] = True  # 같은 단독 질문의 이전 결과 (답변 일관성)
    except ValueError as error:
        candidates = str(error).split('입력해주세요.', 1)[-1].strip() if AMBIGUOUS_REGION in str(error) else ''
        if candidates:
            # 같은 이름의 지역은 오류 대신 확인 질문으로 돌려주고, 다음 대답을 대화 메모리로 연결합니다.
            name = re.search(r"지역 '(.+?)'", str(error))
            result = {'answer': '같은 이름의 지역이 여러 곳 있습니다. 어느 지역인지 시도와 함께 알려주세요.'
                                + f'\n후보: {candidates}',
                      'analysis': {'intent': 'clarify', 'reason': 'ambiguous_region'},
                      'contexts': [], 'facts': [], 'sources': [],
                      'choices': candidate_choices(standalone, name and name[1], candidates.split('; '))}
        else:
            error.standalone_question = standalone
            raise
    result = {**result, 'standalone_question': standalone, 'followup': followup, 'input_converted': bool(converted),
              'timings': {**timings, **result.get('timings', {})}}
    return display_sources(result)
