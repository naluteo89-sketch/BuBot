"""질문 하나에 답하는 하이브리드 RAG 라우터. 이전 대화 반영(후속 질문 재구성·정정)은 conversation.py가 맡습니다.

규칙으로 답할 수 있는 질문(안내·기간 평균·가격 조건 등)은 먼저 원본으로 답하고, 나머지는 질문 분석(GPT) 후 두 경로로 나뉩니다.
- 수치 경로 (lookup/compare/trend/argmax/argmin): 원본에서 정확한 가격 문서를 직접 조회하고
  Python으로 계산합니다. 여기에 지식 색인에서 검색한 지역 프로필·시장 요약·안내 문서를 보강 근거로 붙입니다.
- 서술 경로 (explain): 지식 색인을 벡터 검색해 찾은 문서만 근거로 GPT가 답합니다.
  관련 문서가 임계값 안에 없으면 GPT를 호출하지 않고 근거 없음으로 안내합니다.
"""
import re

from .answers.answer_format import clarify_answer
from .answers.choices import candidate_choices, subregion_choices
from .answers.generation import generate
from .cache.data_cache import documents_by_id, load_corpus, load_knowledge
from .config import (DEFAULT_TOP_N, DISPLAY_PRICE_UNIT, DISPLAY_PRICE_UNITS, KNOWLEDGE_FALLBACK_K,
                     KNOWLEDGE_SEARCH_LIMIT, KNOWLEDGE_TOP_K)
from .embedding.knowledge_index import GUIDE_KINDS, retrieve, validate_knowledge_index
from .filters.analysis_correction import (NUMERIC_INTENTS, analysis_from_code, apply_relative_period, clarify_fallback,
                                         clean_names, limit_regions, narrow_ids, resolve_ids, written_scope, year_only)
from .filters.expressions import rewrite
from .filters.guards import gwangju_clarification, unavailable, year_coverage
from .filters.period_parser import explicit_years, year_point, year_range
from .filters.places import ambiguous_place, ambiguous_place_candidates, label_of, locate, mentions_place
from .filters.query_parser import find_top_n
from .llm import gpt_api
from .llm.schemas import Analysis
from .llm.usage import timed
from .pricing import format_price
from .queries.facts import calculate_facts
from .queries.period_queries import aggregate_query, declining_regions
from .queries.price_ranking import argmax_price, argmin_price, price_filter
from .queries.year_query import last_year_answer, region_records, resolve_relative_years
from .regions import canonical_names, named_region, region_catalog


MARKET_WORDS = re.compile(r'시장|전국|요즘|최근|현재|지금|순위|top|TOP|Top|많이|오른|내린|상승|하락|흐름|동향')
FULL_RANKING = re.compile(r'순위|랭킹|(?:구|시도|시군구|지역)\s*별|전체|모든|줄\s*세')
PRICE_WORDS = re.compile(r'가격|매매|집값|시세|아파트|부동산|거래|변동|상승|하락|올랐|내렸|비싼|싼|순위|평균|지수|억|만원|천원')
MONTH_WORDS = re.compile(r'\d{1,2}\s*월|\d{4}-\d{1,2}|달|전월|동월|이맘|같은 시기|연말|연초|연중|상반기|하반기|분기|그때')


def open_index():
    """원본·지식 문서와 지식 색인을 확인합니다. API 비용이 생기기 전에 실패해야 합니다."""
    corpus = load_corpus()
    knowledge = load_knowledge(corpus)
    return corpus, validate_knowledge_index(knowledge)


def _source_line(doc):
    m = doc['metadata']
    if m.get('kind') in {'monthly', 'timeline'}:
        period = m.get('month') or f"{m.get('start_month')} ~ {m.get('end_month')}"
        return (f"[{doc['id']}] {m['source']} · {m['region_path']} · {period} · 원본 {m['source_row']}행 · "
                f"SHA-256 {m['source_sha256']}")
    title = doc['text'].splitlines()[0]
    row = f" · 원본 {m['source_row']}행" if m.get('source_row') else ''
    return f"[{doc['id']}] 지식 문서 · {title} · {m['source']}{row} · SHA-256 {m['source_sha256']}"


def explain(question, analysis, corpus, collection, catalog, display, region_id=None, embedder=None, timings=None,
            top_k=None):
    """서술 경로: 지식 색인 벡터 검색 → 검색 근거만으로 답변."""
    timings = {} if timings is None else timings
    ids = limit_regions(narrow_ids(resolve_ids(analysis['region_names'], question, catalog, region_id), 'explain', question, catalog),
                 analysis)
    month = next((m for m in (analysis['month'], analysis['end_month']) if m in corpus.months), None)
    year = None
    if analysis['start_month'] and analysis['end_month'] and analysis['start_month'][:4] == analysis['end_month'][:4]:
        year = analysis['start_month'][:4]
    found = re.findall(r'(20\d{2})\s*년', question)
    if not year and found:
        year = found[-1]
    if month and analysis['start_month'] is None:
        year = year or month[:4]
    if not month and not year and not ids and MARKET_WORDS.search(question):
        month, year = corpus.months[-1], corpus.months[-1][:4]
    if year and not any(m.startswith(year) for m in corpus.months):
        year = None
    query = analysis['rewritten_query'] or question
    # 프로필·시장 요약은 지역·기간이 정해지면 조건 검색으로 가져오므로, 의미 검색은 항상 안내 문서로 한정합니다.
    # (제한하지 않으면 형식이 비슷한 월별 시장 요약 수십 개가 용어 질문의 상위 결과를 모두 차지합니다.)
    general_kinds = GUIDE_KINDS
    # 후보는 넉넉히 검색하고(로컬 검색이라 비용 없음), GPT에 넘기는 분량은 answers.generation.fit_budget에서 제한합니다.
    hits, diagnostics = timed(timings, '임베딩·검색', retrieve, collection, query, region_ids=ids, month=month,
                               year=year, general_k=top_k or KNOWLEDGE_TOP_K, general_kinds=general_kinds,
                               embedder=embedder, limit=KNOWLEDGE_SEARCH_LIMIT, fallback_k=KNOWLEDGE_FALLBACK_K)
    analysis.update(route='explain', resolved_region_ids=ids, knowledge_month=month, knowledge_year=year,
                    retrieval=diagnostics, display_unit=display, retrieval_fallback=bool(diagnostics.get('fallback')))
    if not hits:
        months = corpus.months
        return {'answer': '질문과 관련된 근거 문서를 지식 색인에서 찾지 못했습니다.\n'
                          f'BuBot은 {months[0]} ~ {months[-1]} 아파트 월평균 매매가격으로 지역별 가격·비교·추이·순위와 '
                          '데이터·용어 설명을 답합니다. 지역과 기간을 함께 적어 주세요. '
                          "예: '서울 최근 1년 변동률', '부산과 대구 가격 비교', '경기 시군구 가격 순위'",
                'analysis': analysis, 'contexts': [], 'facts': [], 'sources': [], 'route': 'explain',
                'timings': timings}
    result = generate(question, hits, analysis, display, [], timings)
    return {**result, 'analysis': analysis, 'contexts': hits, 'facts': [],
            'sources': [_source_line(h) for h in hits], 'route': 'explain', 'timings': timings}


def with_notes(result, notes):
    """expressions.rewrite가 바꾼 내용(시점·지역·면적 안내)을 답변 끝에 밝힙니다."""
    new = [n for n in notes if n not in result.get('notes', [])]
    if not new or not isinstance(result.get('answer'), str):
        return result
    analysis = result.get('analysis') or {}
    if analysis.get('intent') == 'clarify' and analysis.get('reason') != 'ambiguous_region':
        return result  # 범위 밖·확인 질문에는 '오늘 → 최신 월' 같은 시점 안내가 의미 없음
    return {**result, 'answer': result['answer'] + '\n\n' + '\n'.join('※ ' + n for n in new),
            'notes': [*result.get('notes', []), *new]}


def rewritten(question):
    if not isinstance(question, str):
        return question, []
    return rewrite(question, load_corpus().months)


def answer_question(question, **kwargs):
    """질문 하나에 답합니다 (이전 대화 반영 전 단계는 conversation.ask).

    일상 표현을 명시 월로 바꾸고, 지역명 없이 장소 이름만 있으면 소재지를 확인해 지역 질문으로 바꾼 뒤 답하고 안내문을 붙입니다.
    """
    question, notes = rewritten(question)  # 재구성된 질문에 남은 상대 표현도 명시 월로 바꿉니다
    # 장소 이름('김포공항 주변', '챔피언스 필드 근처')은 질문 분석 모델이 지역을 추측하기 전에 소재지를 확인합니다.
    located = locate(question, kwargs) if mentions_place(question, kwargs) else None
    if located is None:
        try:
            result = _ask_core(question, **kwargs)
        except ValueError:
            located = locate(question, kwargs)  # 지역 오류로 끝난 경우에도 장소 이름이면 한 번 더
            if located is None:
                raise
        else:
            # 지역을 확정하지 못해 되물었으면 장소 이름인지 확인합니다 ('챔피언스 필드 집값').
            located = locate(question, kwargs) if _unresolved(result) else None
            if located is None:
                return with_notes(result, notes)
    kind, payload, note = located
    if kind == 'answer':
        return with_notes(payload, notes)
    return with_notes(_ask_core(payload, **kwargs), [*notes, note])


def _unresolved(result):
    """지역을 확정하지 못해 되물은 결과인지 (범위 밖·같은 이름 지역 안내는 제외)."""
    analysis = result.get('analysis') or {}
    return analysis.get('intent') == 'clarify' and analysis.get('reason') not in {'out_of_scope', 'ambiguous_region'}


def _ask_core(question, *, top_k=None, region_id=None, month=None, kind=None, unit=None, embedder=None):
    if not isinstance(question,str) or not 1<=len(question.strip())<=1000 or (top_k is not None and not 1<=top_k<=50):
        raise ValueError('질문은 1~1000자, 검색 개수는 1~50개여야 합니다.')
    if unit is not None and unit not in DISPLAY_PRICE_UNITS:
        raise ValueError('지원하지 않는 가격 표시 단위입니다.')
    corpus, collection = open_index()  # 색인 문제는 API 비용 발생 전에 중단
    clarification = gwangju_clarification(question) if region_id is None else None
    if clarification:
        return {'answer': clarification, 'analysis': {'intent': 'clarify', 'reason': 'ambiguous_region'},
                'contexts': [], 'facts': [], 'sources': []}
    blocked = unavailable(question, corpus.months) if month is None else None
    if blocked:
        return blocked
    unanswerable = ambiguous_place(question)
    if unanswerable:
        place = ambiguous_place_candidates(question)
        choices = candidate_choices(question, place[0], [label_of(c) for c in place[1]]) if place else []
        return clarify_answer(unanswerable, 'out_of_scope', choices)
    # '제주도 데이터 전부'처럼 값 목록을 원하면 연도 보유 범위 안내 대신 보유 월의 값을 나열합니다 (하위 지역 포함).
    records = region_records(question, corpus, unit or DISPLAY_PRICE_UNIT) if month is None else None
    if records is not None:
        return records
    filtered = price_filter(question, corpus, unit or DISPLAY_PRICE_UNIT) if month is None else None
    if filtered is not None:
        return filtered
    aggregate = aggregate_query(question, corpus, unit or DISPLAY_PRICE_UNIT) if month is None else None
    if aggregate is not None:
        return aggregate
    declines = declining_regions(question, corpus, unit or DISPLAY_PRICE_UNIT) if month is None else None
    if declines is not None:
        return declines
    annual = last_year_answer(question, corpus, unit or DISPLAY_PRICE_UNIT) if month is None else None
    if annual is not None:
        return annual
    # 위 규칙이 답하지 않은 '작년·올해' 질문은 명시 연도로, '작년 대비'는 '최근 1년'으로 바꿔 일반 경로로 보냅니다.
    question = resolve_relative_years(question)
    coverage = year_coverage(question, corpus.months) if month is None else None
    if coverage is not None:
        # '서울 26년 데이터'처럼 지역이 있으면 안내만 하지 않고 보유 월까지의 평균을 함께 보여줍니다.
        years = [c['year'] for c in coverage['analysis']['coverage'] if c['available_months']]
        scope, ambiguous = named_region(question, region_catalog(corpus))
        if len(years) == 1 and scope and not ambiguous:
            mean = aggregate_query(f"{scope['label']} {years[0]}년 평균 매매가격", corpus, unit or DISPLAY_PRICE_UNIT)
            if mean and mean['analysis']['intent'] == 'period_mean':
                notice = [p for p in coverage['answer'].split('\n\n') if not p.startswith('조회할 지역을')]
                return {**mean, 'answer': '\n\n'.join(notice + [mean['answer']]),
                        'sources': coverage['sources'] + mean['sources']}
        return coverage
    catalog=region_catalog(corpus)
    timings={}
    fallback_reason=None
    try:
        raw=timed(timings,'질문분석',gpt_api.analyze_query,question,catalog,corpus.months)
    except Exception as error:
        # 질문 분석 API가 실패해도 코드 해석(query_parser)으로 지역이 확정되면 계속 답합니다.
        raw=analysis_from_code(question,catalog,corpus.months)
        if raw is None:
            raise
        fallback_reason=type(error).__name__
    analysis=Analysis.model_validate(raw).model_dump()
    if fallback_reason:
        analysis['analysis_fallback']=fallback_reason
    if analysis['intent']=='clarify':
        analysis.update(clarify_fallback(question,catalog,corpus.months))
    if analysis['intent']=='clarify':
        # 보유 연도 안내는 가격 질문에만 붙입니다 ('오늘 점심 메뉴'가 '2026년 자료가 있습니다'로 바뀌지 않도록).
        if PRICE_WORDS.search(question) and re.search('없|미확보|확보되지|제공되지|지원되지',analysis['message']):
            mentioned=explicit_years(question) | explicit_years(analysis['message'])
            available=[y for y in sorted(mentioned) if any(m.startswith(f'{y}-') for m in corpus.months)]
            if available:
                analysis['message']='원본에 '+', '.join(str(y)+'년' for y in available)+' 월평균 매매가격 자료가 있습니다. 개별 거래 기록은 없습니다. 조회할 지역과 필요한 계산(월별 가격·기간 평균·변동률)을 확인해주세요.'
        return {'answer':analysis['message'] or '지역과 기간을 구체적으로 입력하세요.',
                'analysis':analysis,'contexts':[],'facts':[],'sources':[],'timings':timings}
    apply_relative_period(question, analysis, corpus.months)
    display=unit or analysis['display_unit'] or DISPLAY_PRICE_UNIT
    if display not in DISPLAY_PRICE_UNITS:
        raise ValueError('BUBOT_PRICE_UNIT 설정이 올바르지 않습니다.')
    if analysis['intent'] not in NUMERIC_INTENTS:
        return explain(question, analysis, corpus, collection, catalog, display, region_id, embedder, timings, top_k)
    intent=analysis['intent']
    names=analysis['region_names']
    if intent in {'argmax','argmin'}:
        # 순위의 검색 범위는 질문에 적힌 지역입니다. 모델이 후보 지역(17개 시도 등)을 나열한 것은 무시합니다.
        text=canonical_names(question)
        names=list(dict.fromkeys(filter(None, (written_scope(n, text) for n in clean_names(names)))))
    named=resolve_ids(names, question, catalog, region_id)
    ids=limit_regions(narrow_ids(named, intent, question, catalog, analysis['level']), analysis)
    if intent=='compare' and len(ids)==1 and len(named)>1:
        intent=analysis['intent']='lookup'  # 상위 지역을 정리하고 나니 한 지역 조회
    if intent in {'lookup','trend','compare'} and not ids:
        raise ValueError('조회할 지역명을 입력해주세요.')
    if intent=='compare' and len(ids)<2:
        raise ValueError('비교할 지역을 두 개 이상 입력해주세요.')
    if intent=='lookup' and len(ids)>1:
        intent=analysis['intent']='compare'
    period_note=analysis.get('period_note')
    if (month is None and intent!='trend' and analysis['month'] and analysis['month']<corpus.months[-1]
            and not MONTH_WORDS.search(question)):
        analysis['month']=None  # 질문에 없는 월을 모델이 지어낸 경우: 연도가 있으면 그 해 기준, 없으면 최신 월
    year=year_only(question, analysis) if month is None and not analysis['month'] else None
    if year and (intent!='trend' or not (analysis['start_month'] and analysis['end_month'])):
        # 월 없이 연도만 지정: 최신 월로 대체하지 않고 그 해의 보유 월을 씁니다.
        last=year_point(year,corpus.months)
        if last is None:
            raise ValueError(f'{year}년 데이터는 없습니다. 보유 기간: {corpus.months[0]} ~ {corpus.months[-1]}')
        if intent=='trend':
            first,last=year_range(year,corpus.months)
            analysis.update(start_month=first,end_month=last)
            period_note=f"연도만 지정되어 {analysis['start_month']} ~ {last} 기간을 비교했습니다 (전년 12월 기준)."
        else:
            analysis['month']=last
            period_note=f'월이 지정되지 않아 {year}년의 마지막 보유 월({last}) 값을 사용했습니다.'
    target_month=month or analysis['month'] or corpus.months[-1]
    start=analysis['start_month'] or corpus.months[0]
    end=month or analysis['end_month'] or analysis['month'] or corpus.months[-1]
    missing=[m for m in dict.fromkeys((target_month,start,end)) if m not in corpus.months]
    if missing:
        raise ValueError(f"요청한 월({', '.join(missing)})의 데이터가 없습니다. 보유 기간: {corpus.months[0]} ~ {corpus.months[-1]}")
    if start>end:
        raise ValueError(f'기간 순서가 잘못되었습니다 ({start} → {end}).')
    if intent=='trend' and start==end and kind is None:
        # 한 달짜리 '추이'('작년 12월 강남구와 서초구 비교')는 변동률 0%라는 무의미한 값이 되므로 그 달의 조회·비교로 답합니다.
        intent=analysis['intent']='compare' if len(ids)>1 else 'lookup'
        target_month=end
    if kind is not None and ((intent=='trend' and kind!='timeline') or (intent in {'lookup','compare','argmin','argmax'} and kind!='monthly')):
        raise ValueError('조회 의도와 --kind 옵션이 일치하지 않습니다.')
    contexts=[]
    facts=[]
    knowledge_month=None
    if intent in {'argmax','argmin'}:
        if len(ids)>1:
            raise ValueError('순위 조회 범위는 한 지역씩 지정하세요.')
        # '구별 순위·랭킹'은 전체 순위, 'top 10'은 그 개수, 그 외(가장 비싼 곳)는 상위 DEFAULT_TOP_N곳
        top_n=find_top_n(question) or (30 if FULL_RANKING.search(question) else DEFAULT_TOP_N)
        aggregate=(argmax_price if intent=='argmax' else argmin_price)({
            'month':target_month,'region_id':ids[0] if ids else None,'level':analysis['level'],'top_n':top_n})
        if aggregate.get('error'):
            raise ValueError(aggregate['error'])
        contexts=aggregate['top']
        facts=[f"{target_month} {analysis['level']} 전체 유효 후보 {aggregate['candidates_count']}개를 가격으로 정렬. 결측 제외."]
        facts.extend(f"{i}위 {d['metadata']['region_path']}: {format_price(d['metadata']['price_thousand_krw'],display)}" for i,d in enumerate(contexts,1))
        analysis['aggregation']={k:v for k,v in aggregate.items() if k not in {'top','winner'}}
        knowledge_month=target_month
        profile_ids=[]
    else:
        # 정확한 지역·기간 문서는 벡터 유사도가 아니라 ID로 직접 조회합니다.
        by_id=documents_by_id(corpus)
        for rid in ids:
            doc=by_id[f"region-{rid}-timeline" if intent=='trend' else f"region-{rid}-month-{target_month}"]
            contexts.append({'id':doc.id,'text':doc.text,'metadata':dict(doc.metadata)})
        if intent=='trend':
            for context in contexts:
                # 요청 범위 밖의 월을 LLM 컨텍스트에서 제외합니다.
                context['text']='\n'.join(line for line in context['text'].splitlines()
                    if not re.match(r'^\d{4}-\d{2}:',line) or start<=line[:7]<=end)
                context['metadata']={**context['metadata'],'start_month':start,'end_month':end}
        facts=calculate_facts(corpus,ids,intent,target_month,start,end,display)
        latest=corpus.months[-1]
        profile_ids=ids if (end if intent=='trend' else target_month)==latest else []
    if not contexts:
        raise ValueError('조건에 맞는 근거 데이터가 없습니다.')
    if period_note:
        facts.insert(0, period_note)
    if analysis.get('region_note'):
        facts.insert(0, analysis['region_note'])
    # 지식 색인 검색으로 보강: 최신 월 기준 지역 프로필, 순위 월의 시장 요약, 의미상 가까운 안내 문서.
    # 보강은 부가 근거이므로 검색이 실패해도(임베딩 API 오류 등) 원본 계산값으로 답합니다.
    try:
        support, diagnostics = timed(timings, '임베딩·검색', retrieve, collection, analysis['rewritten_query'] or question,
                                      region_ids=profile_ids, month=knowledge_month, general_k=3,
                                      general_kinds=GUIDE_KINDS, embedder=embedder, limit=KNOWLEDGE_SEARCH_LIMIT)
    except Exception as error:
        support, diagnostics = [], {'used': [], 'rejected': [], 'error': type(error).__name__}
    analysis.update(route='numeric', resolved_region_ids=ids, resolved_month=target_month,
                    resolved_start=start,resolved_end=end,display_unit=display, retrieval=diagnostics)
    contexts=contexts+support
    result=generate(question,contexts,analysis,display,facts,timings)
    sources=[_source_line(c) for c in contexts]
    choices=[]
    if intent=='lookup' and len(ids)==1:
        # 하위 지역이 있으면 '하위 지역 값도 필요하신가요?' 선택지 버튼 (예: 광주 → 동구·서구·남구·북구·광산구)
        choices,_=subregion_choices(catalog[ids[0]],catalog,f"{target_month[:4]}년 {int(target_month[5:])}월",'기록 전부')
    return {**result,'analysis':analysis,'contexts':contexts,'facts':facts,'sources':sources,'route':'numeric',
            'timings':timings,'choices':choices}
