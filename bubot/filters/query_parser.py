"""질문 문장에서 지역·기간·의도 단서를 코드로 추출합니다 (LLM 없음, 같은 문장은 항상 같은 결과).

rag_service는 여기서 확정된 값을 LLM 해석보다 우선합니다. 확정하지 못한 부분만 LLM 해석을 씁니다.
기간은 period_parser, 지역 언급은 regions.find_regions가 찾고, 이 파일은 의도 단서와 질문 프레임을 맡습니다.
- 기간: '2023년 3월', '23년', '작년', '4년 전', '지난달', '최근 3개월', '상반기', 'A부터 B까지', '요즘' 등
- 지역: 정식 이름·약칭('강남'→강남구)·상위 지역 수식('서울 중구', '경기 광주')·'전라도 광주'·같은 이름 지역의 후보
- 의도 단서: 비교·추이·순위·시점(언제 가장 쌌나)·보유 기간·가격 조건(3억 이상)·기간 평균·지원하지 않는 질문
"""
import re
import unicodedata
from dataclasses import dataclass, field

from ..regions import find_regions
from .period_parser import Period, parse_period


def normalize(text):
    """전각 숫자·공백을 정리합니다. '2023년도'는 '2023년'으로 봅니다."""
    text = unicodedata.normalize('NFKC', text or '')
    text = text.replace('년도', '년')
    return re.sub(r'\s+', ' ', text).strip()


# ─────────────────────────────────────────────────────────────
# 의도 단서·가격 조건·계층
# ─────────────────────────────────────────────────────────────

CUES = [
    ('unsupported', r'전세|월세|임대료|거래량|거래\s*건수|매물\s*수|몇\s*건|예측|전망|오를까|내릴까|떨어질까|오를지|내릴지|살까|사도\s*될까|투자|추천'),
    ('coverage', r'언제부터|언제까지|보유\s*기간|데이터\s*(?:기간|범위)|자료\s*(?:기간|범위)|몇\s*년\s*치|기간이\s*(?:어떻게|어디)|어디까지\s*(?:있|나와)'),
    ('extreme_time', r'(?:언제|시점|시기|몇\s*월|몇\s*년|기간|때)[^?.!]{0,12}(?:가장|제일|최고|최저|최대|최소)'
                     r'|(?:가장|제일|최고|최저|최대|최소)[^?.!]{0,14}(?:언제|시점|시기|기간|때|년도|달)'),
    ('filter', r'\d+(?:\.\d+)?\s*(?:억|천만|천\s*만|백만|만)\s*(?:\d+\s*천\s*만?\s*)?원?\s*(?:이상|초과|넘|이하|미만|아래|밑|이내|안\s*되)'),
    ('average', r'연\s*평균|연간\s*평균|기간\s*평균|평균\s*(?:을|은)?\s*(?:내|구해|계산)|(?:년|개월|기간|동안)\s*의?\s*평균(?!\s*매매)'),
    ('argmax', r'가장\s*(?:비싼|비쌌|비싸|높은|높았|높)|제일\s*(?:비싼|비쌌|비싸|높은|높)|최고가?|1\s*위|상위|top|TOP|Top|높은\s*순|비싼\s*순'
               r'|순위|랭킹'),
    ('argmin', r'가장\s*(?:싼|쌌|싸|저렴|낮은|낮았|낮)|제일\s*(?:싼|쌌|싸|저렴|낮은|낮)|최저가?|꼴찌|하위|낮은\s*순|싼\s*순|저렴한\s*순'),
    ('compare', r'비교|차이|vs|VS|보다\s*(?:비싸|싸|높|낮)|어디가\s*더|어느\s*(?:곳|지역|쪽)이\s*더|각각|견줘'),
    ('trend', r'추이|흐름|변화|변동|오른|올랐|오름|내린|내렸|떨어|상승|하락|증감|등락|얼마나\s*(?:올|내|변|떨)|몇\s*(?:%|퍼)'
              r'|폭락|폭등|급등|급락|떡상|떡락|반등'),
    ('explain', r'뭐야|무엇|무슨\s*뜻|의미|정의|어떻게\s*(?:계산|구해|산출)|설명|어때|어떤가|동향|분위기|상황|전반|요약'),
]
LEVEL_HINTS = [('시도', r'시도|시·도|광역\s*시도|광역자치단체'), ('권역', r'권역'),
               ('시군구', r'시군구|시·군·구|자치구|(?<![가-힣])구\s*(?:중|별|가운데|들)|(?<![가-힣])(?:시|군)\s*(?:중|별|가운데|들)')]
ENUMERATION = re.compile(r'(?:모든|전체|각|전|(\d+)\s*개)\s*(?:의\s*)?(자치구|구|군|시군구|시·군·구|시도|시·도)|(자치구|구|시군구|시도|시·도)\s*별')
UNIT_TO_THOUSAND = {'억': 100000, '천만': 10000, '천 만': 10000, '백만': 1000, '만': 10}
THRESHOLD = re.compile(r'(\d+(?:\.\d+)?)\s*(억|천\s*만|백만|만)\s*(?:(\d+)\s*천\s*만?\s*)?원?\s*'
                       r'(이상|초과|넘는|넘은|넘게|넘|이하|미만|아래|밑|이내|안\s*되는|안되는)')
TOP_N = re.compile(r'(?:top|TOP|Top|상위|하위)\s*(\d+)|(\d+)\s*(?:곳|개\s*지역|위\s*까지|개\s*시군구|개\s*구)')


def find_cues(text):
    """의도 단서를 질문에 나온 순서대로 돌려줍니다."""
    found = []
    for name, pattern in CUES:
        m = re.search(pattern, text)
        if m:
            found.append((m.start(), name))
    return [name for _, name in sorted(found)]


def find_threshold(text):
    m = THRESHOLD.search(text)
    if not m:
        return None
    value = float(m[1]) * UNIT_TO_THOUSAND[m[2]]
    if m[3]:
        value += int(m[3]) * 10000  # '3억 5천' = 3억 5천만
    word = m[4].replace(' ', '')
    op = '>=' if word == '이상' else '>' if word in ('초과', '넘는', '넘은', '넘게', '넘') else \
        '<=' if word in ('이하', '이내') else '<'
    return op, value


def find_level(text):
    for level, pattern in LEVEL_HINTS:
        if re.search(pattern, text):
            return level
    return None


def find_top_n(text):
    m = TOP_N.search(text)
    if not m:
        return None
    n = int(m[1] or m[2])
    return n if 1 <= n <= 50 else None


# ─────────────────────────────────────────────────────────────
# 질문 프레임
# ─────────────────────────────────────────────────────────────

@dataclass
class QueryFrame:
    text: str
    targets: list = field(default_factory=list)        # 확정된 지역 id (질문 순서)
    ambiguous: list = field(default_factory=list)      # [(적힌 이름, 후보 id 목록)]
    scope: str | None = None                           # 순위·목록·조건 검색 범위
    level: str | None = None
    enumerate: bool = False                            # '서울 25개 구'처럼 하위 지역 전체
    period: Period = field(default_factory=Period)
    cues: list = field(default_factory=list)
    intent: str | None = None                          # 코드로 확정한 의도 (확정 못 하면 None)
    threshold: tuple | None = None
    top_n: int | None = None


RANK_INTENTS = {'argmax', 'argmin', 'filter'}


def parse_question(question, catalog, months, today=None):
    """질문을 지역·기간·의도 단서로 나눕니다. 결과는 문장과 데이터에만 의존합니다."""
    text = normalize(question)
    mentions = find_regions(text, catalog)
    frame = QueryFrame(text=text, period=parse_period(text, months, today), cues=find_cues(text),
                       threshold=find_threshold(text), top_n=find_top_n(text), level=find_level(text))
    for m in mentions:
        if m.qualifier:
            continue
        if len(m.candidates) == 1:
            if m.candidates[0] not in frame.targets:
                frame.targets.append(m.candidates[0])
        elif m.candidates:
            frame.ambiguous.append((m.text, m.candidates))
    enum = ENUMERATION.search(text)
    if enum:
        word = enum[2] or enum[3] or ''
        frame.enumerate = True
        frame.level = '시도' if '시도' in word or '시·도' in word else '시군구'
    if frame.threshold and 'filter' not in frame.cues:
        frame.cues.append('filter')
    frame.intent = decide_intent(frame, catalog)
    if frame.intent in RANK_INTENTS or frame.enumerate:
        # 순위·조건 검색·하위 지역 전체: 질문에 적힌 가장 넓은 지역이 범위, 나머지는 대상이 아닙니다.
        if frame.targets:
            frame.scope = min(frame.targets, key=lambda i: len(catalog[i]['path']))
            frame.targets = [] if frame.intent in RANK_INTENTS or frame.enumerate else frame.targets
    return frame


def decide_intent(frame, catalog):
    """의도 단서로 의도를 확정합니다. 단서가 부족하거나 서로 부딪히면 None (LLM 해석 사용)."""
    cues, has_regions = frame.cues, bool(frame.targets)
    period = frame.period
    if 'unsupported' in cues:
        return 'unsupported'
    if 'coverage' in cues:
        return 'coverage'
    if 'extreme_time' in cues and (has_regions or not frame.enumerate) and period.kind not in ('point',):
        return 'extreme_time'
    if 'filter' in cues and frame.threshold:
        return 'filter'
    if 'average' in cues and period.kind in ('year', 'range'):
        return 'period_average'
    ranks = [c for c in cues if c in ('argmax', 'argmin')]
    if ranks:
        return ranks[0]
    if frame.enumerate:
        return 'trend' if 'trend' in cues and period.kind == 'range' else 'compare'
    if 'compare' in cues and len(frame.targets) >= 2:
        return 'trend' if period.kind == 'range' and 'trend' in cues else 'compare'
    if 'trend' in cues:
        if period.kind == 'range' and has_regions:
            return 'trend'
        if has_regions or 'explain' in cues:
            return 'explain'
        return None
    if 'explain' in cues:
        return 'explain'
    if len(frame.targets) >= 2:
        return 'trend' if period.kind == 'range' else 'compare'
    if has_regions:
        return 'trend' if period.kind == 'range' else 'lookup'
    return None
