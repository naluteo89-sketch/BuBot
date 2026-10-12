"""하이브리드 RAG 라우터.

질문 분석(GPT) 후 두 경로로 나뉩니다.
- 수치 경로 (lookup/compare/trend/argmax/argmin): 원본에서 정확한 가격 문서를 직접 조회하고
  Python으로 계산합니다. 여기에 지식 색인에서 검색한 지역 프로필·시장 요약·안내 문서를 보강 근거로 붙입니다.
- 서술 경로 (explain): 지식 색인을 벡터 검색해 찾은 문서만 근거로 GPT가 답합니다.
  관련 문서가 임계값 안에 없으면 GPT를 호출하지 않고 근거 없음으로 안내합니다.
"""
import re
import time

import gpt_api
from .config import (CONTEXT_CHAR_BUDGET, DEFAULT_TOP_N, DISPLAY_PRICE_UNIT, DISPLAY_PRICE_UNITS, KNOWLEDGE_FALLBACK_K,
                     KNOWLEDGE_SEARCH_LIMIT, KNOWLEDGE_TOP_K, MAX_REGIONS)
from .data_cache import documents_by_id, load_corpus, load_knowledge
from .knowledge import _shift
from .knowledge_index import GUIDE_KINDS, retrieve, validate_knowledge_index
from .pricing import format_price
from .regions import ALIASES, region_catalog, resolve_region, gwangju_clarification
from .retrieval import argmax_price, argmin_price
from .schemas import Analysis
from .coverage import explicit_years, year_coverage
from .year_query import last_year_answer, region_records, resolve_relative_years
from .declines import declining_regions
from .period_queries import aggregate_query, negative_concept, correction_query, annual_mean_concept
from .answer_check import number_issues
from .answer_cache import ANSWER_CACHE, cache_key
from .keyboard import keyboard_to_hangul
from .query_parser import find_cues, find_regions, find_top_n, parse_period, parse_question
from .expressions import ambiguous_place, ambiguous_place_candidates, rewrite
from .price_filter import out_of_scope, price_filter


def calculate_facts(corpus, ids, intent, month, start, end, unit):
    by_id = documents_by_id(corpus)
    facts=[]
    values=[]
    for rid in ids:
        doc=by_id[f"region-{rid}-month-{end if intent=='trend' else month}"]
        name=doc.metadata['region_path']
        price=doc.metadata.get('price_thousand_krw')
        values.append((name,price))
        if intent=='trend':
            first=by_id[f"region-{rid}-month-{start}"].metadata.get('price_thousand_krw')
            facts.append(f"{name}: {start} {format_price(first,unit)} → {end} {format_price(price,unit)}")
            if first is None or price is None or first==0:
                facts.append(f"{name}: 시작·종료값 결측 또는 시작값 0으로 변동률 계산 불가")
            else:
                facts.append(f"{name}: 변화량 {format_price(price-first,unit)}, 변동률 {(price/first-1)*100:+.2f}%")
        else:
            facts.append(f"{month} {name}: {format_price(price,unit)}")
    if intent=='compare' and values:
        reference,base=values[0]
        for name,value in values[1:]:
            if base is None or value is None:
                facts.append(f"{name}: 결측으로 {reference} 대비 비교 불가")
            else:
                facts.append(f"{reference} 대비 {name} 차이: {format_price(value-base,unit)}")
                if base:
                    # 방향이 헷갈리지 않도록 주어를 밝힙니다 (이전: '서울 대비 부산: 0.2705배'를 모델이 거꾸로 읽음)
                    facts.append(f"{name} 가격은 {reference} 가격의 {value/base:.4f}배, {reference}보다 {(value/base-1)*100:+.2f}%")
    return facts


NUMERIC_INTENTS = {'lookup', 'trend', 'compare', 'argmax', 'argmin'}
MARKET_WORDS = re.compile(r'시장|전국|요즘|최근|현재|지금|순위|top|TOP|Top|많이|오른|내린|상승|하락|흐름|동향')
MARKET_QUESTION = re.compile(r'(?:집값|시세|아파트\s*값|아파트\s*가격|부동산|시장).*'
                             r'(?:바닥|꼭지|고점|어때|어떤가|올랐|내렸|떨어|폭락|폭등|급등|급락|반등|상승|하락|흐름|분위기)')
FULL_RANKING = re.compile(r'순위|랭킹|(?:구|시도|시군구|지역)\s*별|전체|모든|줄\s*세')
PLACE_PRICE = re.compile(r'집값|시세|가격|매매|아파트|얼마|올랐|내렸|변동|추이|평균|비싸|싸')
PRICE_WORDS = re.compile(r'가격|매매|집값|시세|아파트|부동산|거래|변동|상승|하락|올랐|내렸|비싼|싼|순위|평균|지수|억|만원|천원')
NOW_WORDS = re.compile(r'요즘|요새|현재|지금|최근|최신|근래')
MONTH_WORDS = re.compile(r'\d{1,2}\s*월|\d{4}-\d{1,2}|달|전월|동월|이맘|같은 시기|연말|연초|연중|상반기|하반기|분기|그때')


def open_index():
    """원본·지식 문서와 지식 색인을 확인합니다. API 비용이 생기기 전에 실패해야 합니다."""
    corpus = load_corpus()
    knowledge = load_knowledge(corpus)
    return corpus, validate_knowledge_index(knowledge)


def _months_between(start, end):
    return (int(end[:4]) * 12 + int(end[5:7])) - (int(start[:4]) * 12 + int(start[5:7]))


def apply_relative_period(question, analysis, months):
    """상대 기간은 모델 해석 대신 코드로 확정합니다.

    - '최근 N개월': 최신 월 포함 N개월 (예: 최신 8월 → 6~8월), '최근 N년': N년 전 같은 달 ~ 최신 월
    - 질문에 적힌 월·기간('2016년 8월', '2026년 1월부터 2026년 8월까지')은 코드 해석(query_parser)이 우선입니다.
      ('N년 전·지난달·연초 대비' 같은 상대 표현은 expressions.rewrite가 먼저 명시 월로 바꿔 둡니다.)
    - 기간 없이 '올랐어?·폭락했어?'처럼 변화를 물으면 최근 1년(최신 월과 1년 전 같은 달)을 비교합니다.
    - 연도·월 없이 '요즘·현재·최근'만 있으면 데이터 최신 월 기준입니다. 모델이 학습 시점 무렵의
      과거 월(예: 2023-10)을 고르는 것을 막습니다.
    """
    latest = months[-1]
    match = re.search(r'최근\s*(\d+)\s*(개월|달)', question)
    years = re.search(r'최근\s*(\d+)\s*년', question)
    if match:
        span = int(match[1]) - 1
    elif years:
        span = int(years[1]) * 12
    else:
        written = parse_period(question, months)
        cues = find_cues(question)
        if written.kind in ('point', 'range'):
            if written.kind == 'point' and analysis['intent'] != 'trend':
                analysis.update(month=written.month, start_month=None, end_month=None)
            elif written.kind == 'range':
                analysis.update(start_month=written.start, end_month=written.end, month=None)
                if analysis['intent'] == 'lookup':
                    analysis['intent'] = 'trend'
            if analysis['intent'] == 'explain' and 'explain' not in cues and analysis['region_names']:
                # '2016년 8월 서울 집값'은 서술이 아니라 원본 가격 조회입니다
                analysis['intent'] = 'trend' if written.kind == 'range' else 'lookup'
            if written.notes:
                analysis['period_note'] = ' '.join(written.notes)
            return analysis
        if written.kind == 'none' and analysis['intent'] == 'trend' and analysis['end_month'] != latest:
            # 모델이 기간을 지어냈거나(학습 시점 무렵 2023년 등) 비워 둔 경우
            start = max(_shift(latest, -12), months[0])
            analysis.update(start_month=start, end_month=latest, month=None,
                            period_note=f'기간이 지정되지 않아 최근 1년({start} ~ {latest})을 비교했습니다.')
            return analysis
        if NOW_WORDS.search(question) and not explicit_years(question) and not re.search(r'\d{1,2}\s*월', question):
            start, end = analysis['start_month'], analysis['end_month']
            if start and end:
                if end != latest:  # 모델이 고른 기간 길이는 유지하고 끝을 최신 월로 옮깁니다
                    shifted = _shift(latest, -max(_months_between(start, end), 0))
                    analysis.update(start_month=max(shifted, months[0]), end_month=latest, month=None)
            else:
                analysis.update(month=latest, start_month=None, end_month=None)
        return analysis
    if span < 0:
        return analysis
    start = _shift(latest, -span)
    analysis.update(start_month=start if start >= months[0] else months[0], end_month=latest, month=None)
    if analysis['intent'] == 'lookup':
        analysis['intent'] = 'trend'
    return analysis


def _source_line(doc):
    m = doc['metadata']
    if m.get('kind') in {'monthly', 'timeline'}:
        period = m.get('month') or f"{m.get('start_month')} ~ {m.get('end_month')}"
        return (f"[{doc['id']}] {m['source']} · {m['region_path']} · {period} · 원본 {m['source_row']}행 · "
                f"SHA-256 {m['source_sha256']}")
    title = doc['text'].splitlines()[0]
    row = f" · 원본 {m['source_row']}행" if m.get('source_row') else ''
    return f"[{doc['id']}] 지식 문서 · {title} · {m['source']}{row} · SHA-256 {m['source_sha256']}"


LEVEL_WORDS = {'시도', '시·도', '시군구', '시·군·구', '권역', '시', '군', '구', '지역', '전체', '모든 지역', '전국 시도'}


DATE_IN_NAME = re.compile(r'\d{4}\s*년|\d{1,2}\s*월|\d{4}-\d{1,2}')
PARTICLE = re.compile(r'(?<=[시군구도])(은|는|이|가|의|와|과|을|를|에서|에|랑|하고|도)$')


def _clean_names(names):
    """모델이 지역명 자리에 넣은 계층 이름('시도')·날짜를 빼고, 지역명에 붙은 조사('제주시는')를 뗍니다."""
    cleaned = []
    for name in names:
        name = '/'.join(PARTICLE.sub('', part.strip()) for part in name.split('/')).replace('/', ' / ')
        if name.strip() and name.strip() not in LEVEL_WORDS and not DATE_IN_NAME.search(name):
            cleaned.append(name)
    return cleaned


def _written_scope(name, text):
    """지역 경로 중 질문에 적힌 가장 구체적인 부분. '서울 / 강북지역 / 도심권'이고 질문에 '서울'만 있으면 '서울'.

    '강남구'의 '강남'처럼 시·군·구를 뺀 이름도 적힌 것으로 봅니다. 아무것도 적혀 있지 않으면 None.
    """
    tokens = [t for t in re.split(r'[\s/]+', _normalized(name)) if t]
    for end in range(len(tokens), 0, -1):
        if _token_written(tokens[end - 1], text):
            return ' / '.join(tokens[:end])
    return None


def _token_written(token, text):
    if len(token) < 2:  # '시' 같은 한 글자는 어느 질문에나 들어 있어 근거가 되지 않습니다
        return False
    return token in text or (len(token) > 2 and token.endswith(('시', '군', '구')) and token[:-1] in text)


def _resolve_ids(names, question, catalog, region_id=None):
    names = _clean_names(names)
    if '광주광역시' in question:
        names = ['광주광역시' if name.strip() in {'광주','광주시','전남광주 / 광주'} else name for name in names]
    text, ids, skipped = _normalized(question), [], None
    for name in names:
        try:
            ids.append(resolve_region(name, catalog))
        except ValueError as error:
            written = _written_region(name, question, catalog)
            if written:
                ids.append(written)  # 모델이 상위 경로를 틀리게 붙인 경우('경부2권 / 성남시 / 분당구'): 질문에 적힌 지역
                continue
            if _written_scope(name, text) is not None:
                raise  # 질문에 적힌 이름이 모호하거나 없으면 사용자에게 알립니다
            skipped = skipped or error  # 모델이 덧붙인 해석 불가 이름('제주 / 제주 시')은 건너뜁니다
    if not ids and skipped and not region_id:
        raise skipped
    if region_id:
        if region_id not in catalog:
            raise ValueError('알 수 없는 지역 ID입니다.')
        ids = [region_id]
    ids = list(dict.fromkeys(ids))
    if not region_id:
        _reject_guessed_parent(ids, question, catalog)
    return ids


def _written_region(name, question, catalog):
    """모델이 준 이름의 마지막 부분과 같은 지역이 질문에 하나로 확정되어 적혀 있으면 그 ID."""
    last = [t for t in re.split(r'[\s/]+', _clean_names([name])[0] if _clean_names([name]) else '') if t]
    if not last:
        return None
    stems = {last[-1], last[-1][:-1] if len(last[-1]) > 2 and last[-1].endswith(('시', '군', '구')) else last[-1]}
    found = {m.candidates[0] for m in find_regions(_normalized(question), catalog)
             if len(m.candidates) == 1 and not m.qualifier
             and (catalog[m.candidates[0]]['name'] in stems or m.text in stems)}
    return found.pop() if len(found) == 1 else None


def _reject_guessed_parent(ids, question, catalog):
    """'중구'처럼 여러 시도에 있는 이름인데 질문에 시도가 없으면, 모델이 붙인 상위 지역은 추측입니다.

    resolve_region의 모호성 오류와 같은 형식으로 알려 ask()가 후보를 보여 주는 확인 질문으로 바꿉니다.
    """
    text = _normalized(question)
    for rid in ids:
        region = catalog[rid]
        same = [r for r in catalog.values() if r['name'] == region['name']]
        if len(same) > 1 and not any(_token_written(p, text) for p in region['path'][:-1]):
            labels = '; '.join(r['label'] for r in same)
            raise ValueError(f"지역 '{region['name']}'을 확정할 수 없습니다. 시도와 시군구를 함께 입력해주세요.{labels}")


def _limit(ids, analysis=None):
    """지역이 MAX_REGIONS곳을 넘으면 오류 대신 앞의 MAX_REGIONS곳만 계산하고 그 사실을 analysis에 남깁니다."""
    if len(ids) <= MAX_REGIONS:
        return ids
    if analysis is not None:
        analysis['region_note'] = f'지역이 {len(ids)}곳이라 질문에 적힌 순서대로 앞의 {MAX_REGIONS}곳만 계산했습니다.'
    return ids[:MAX_REGIONS]


def _normalized(question):
    for alias, canonical in ALIASES.items():
        question = question.replace(alias, canonical)
    return question


def _prefer_written_names(ids, text, catalog):
    """질문에 '강남'이라고 썼는데 모델이 권역 '강남지역'을 고른 경우, 적힌 이름 그대로(강남구) 해석합니다."""
    result = []
    for rid in ids:
        region = catalog[rid]
        if region['name'] not in text and region['level'] == '권역':
            stem = next((region['name'][:-len(s)] for s in ('지역', '권') if region['name'].endswith(s)), '')
            if len(stem) >= 2 and stem in text:
                try:
                    alternative = resolve_region(stem, catalog)
                except ValueError:
                    alternative = None
                if alternative and catalog[alternative]['path'][0] == region['path'][0]:
                    rid = alternative
        result.append(rid)
    return list(dict.fromkeys(result))


def _written_alone(region, descendants, text):
    """'서울 중구'의 '서울'처럼 하위 지역 앞의 수식어로만 쓰였으면 False, 따로 쓰인 곳이 있으면 True."""
    following = set()
    for d in descendants:
        path = d['path']
        following.update(path[path.index(region['name']) + 1:] if region['name'] in path else path)
    following |= {p[:-1] for p in following if len(p) > 2 and p.endswith(('시', '군', '구'))}
    for match in re.finditer(re.escape(region['name']), text):
        if any(len(p) > len(region['name']) and text.startswith(p, match.start()) for p in following):
            continue  # '제주시' 안의 '제주'처럼 하위 지역 이름의 일부
        after = text[match.end():].lstrip(' 의')
        if not any(after.startswith(p) for p in following):
            return True
    return False


def _narrow_ids(ids, intent, question, catalog, level=None):
    """모델이 지역을 계층별로 나눠 넣은 경우를 질문에 적힌 대로 정리합니다.

    - 조회·비교·서술: 다른 지역의 상위 지역인데 질문에 따로 적히지 않은 것(추가된 '서울', '서울 중구'의 수식어 '서울')은 뺍니다.
      '강남구와 서울 비교'처럼 상위 지역을 따로 적으면 그대로 비교합니다.
    - 순위: 공통 상위 지역이 질문에 있으면 그 지역이 검색 범위(서울의 구), 같은 계층을 나열만 했으면 전국 범위입니다.
    """
    text = _normalized(question)
    ids = _prefer_written_names(ids, text, catalog)
    if len(ids) < 2:
        return ids
    paths = {i: set(catalog[i]['path']) for i in ids}
    if intent in {'argmax', 'argmin'}:
        common = set.intersection(*paths.values())
        scope = [r['id'] for r in catalog.values() if set(r['path']) == common and r['name'] in text]
        if scope:
            return scope[:1]
        if level and all(catalog[i]['level'] == level for i in ids):
            return []  # 예: '시도 중 가장 비싼 곳'에 17개 시도를 나열 → 전국 시도 비교
        return ids
    # 질문에 적힌 지역이 있으면, 경로 어디에도 질문의 단어가 없는 지역(모델이 덧붙인 '전국' 등)은 뺍니다.
    written = [i for i in ids if any(_token_written(p, text) for p in catalog[i]['path'])]
    ids = written or ids
    keep = []
    for rid in ids:
        descendants = [catalog[d] for d in ids if d != rid and paths[rid] < paths[d]]
        if descendants and not _written_alone(catalog[rid], descendants, text):
            continue
        keep.append(rid)
    return keep


def _year_only(question, analysis):
    """월 없이 연도만 지정한 질문의 연도: 분석 결과가 한 해 범위이거나, 질문에 명시 연도가 하나뿐일 때."""
    start, end = analysis['start_month'], analysis['end_month']
    if start and end and start[:4] == end[:4]:
        return start[:4]
    years = explicit_years(question)
    return str(next(iter(years))) if len(years) == 1 else None


def _fit_budget(contexts, budget=CONTEXT_CHAR_BUDGET):
    """GPT에 넘기는 근거 본문을 budget 글자 안으로 줄입니다 (토큰 소모는 여기서만 제어).

    직접 조회한 원본 문서(pool 없음)를 먼저 두고, 지식 검색 보강 문서는 남는 만큼만 붙입니다.
    그래도 넘치면 긴 문서(예: 20개 지역의 추이 문서)를 같은 길이로 잘라 냅니다. 핵심 수치는 verified_facts에 있습니다.
    """
    if sum(len(c['text']) for c in contexts) <= budget:
        return contexts
    kept = [c for c in contexts if 'pool' not in c]
    used = sum(len(c['text']) for c in kept)
    for c in contexts:
        if 'pool' in c and used + len(c['text']) <= budget:
            kept.append(c)
            used += len(c['text'])
    if used <= budget or not kept:
        return kept
    remaining, cap = budget, None
    for i, length in enumerate(sorted(len(c['text']) for c in kept)):
        share = remaining // (len(kept) - i)
        if length > share:
            cap = max(200, share)
            break
        remaining -= length
    return [c if cap is None or len(c['text']) <= cap else
            {**c, 'text': c['text'][:cap] + '\n…(분량 제한으로 이하 생략. 수치는 verified_facts 기준)'} for c in kept]


# GPT가 답변 형식 지시문을 따라 쓰는 문단 ('결론에 없는 보조 맥락이 없습니다', '확인되지 않는 부분은 없습니다')
FORMAT_ECHO = re.compile(r'^(?:결론에\s*없는\s*)?(?:보조\s*맥락|참고\s*(?:사항|내용)|한계|확인되지\s*않는\s*부분)'
                         r'(?:은|는|이|가)?\s*(?:특별히\s*)?(?:없|해당\s*없)[^\n]*$')


def _drop_format_echo(answer):
    paragraphs = [p for p in answer.split('\n\n') if not FORMAT_ECHO.match(p.strip())]
    return '\n\n'.join(paragraphs).strip() or answer


def _fallback_answer(contexts, display, facts, error):
    """답변 생성(GPT)이 실패해도 이미 확보한 계산값·근거 원문으로 답합니다."""
    if facts:
        body = '원본 데이터로 계산한 결과입니다.\n' + '\n'.join('- ' + f for f in facts)
    else:
        text = contexts[0]['text']
        body = '질문과 가장 가까운 지식 문서 내용입니다.\n' + (text[:700] + '…' if len(text) > 700 else text)
    from .usage import BudgetExceeded
    reason = (str(error) if isinstance(error, BudgetExceeded) else
              '문장형 답변 생성이 완료되지 않아 계산값·문서 원문으로 안내했습니다. 잠시 후 다시 질문하면 문장형 답변을 받을 수 있습니다.')
    return {'answer': body + '\n\n※ ' + reason,
            'model': None, 'unit': display, 'used_context_ids': [c['id'] for c in contexts],
            'error_type': type(error).__name__}


def _generate(question, contexts, analysis, display, facts, timings):
    """답변을 만든 뒤 문장 속 변동률(%)을 근거와 대조합니다.

    부호가 뒤집혔거나 근거에 없는 값이 있으면 오류 목록을 알려 주고 1회 다시 쓰게 합니다.
    그래도 맞지 않으면 답변 끝에 확인 안내를 붙입니다. 생성 자체가 실패하면 계산값·원문으로 대체합니다.
    결과는 analysis['answer_check'](ok·corrected·warning·fallback)에 남습니다.
    """
    prompt_contexts = _fit_budget(contexts)
    if prompt_contexts is not contexts:
        analysis['context_trimmed'] = True
    try:
        result = _timed(timings, '답변생성', gpt_api.generate_answer, question, prompt_contexts, analysis, display, facts)
    except Exception as error:
        analysis['answer_check'] = 'fallback'
        analysis['generation_error'] = type(error).__name__
        return _fallback_answer(contexts, display, facts, error)
    result = {**result, 'answer': _drop_format_echo(result['answer'])}
    evidence = [question, *facts, *(c['text'] for c in contexts)]
    issues = number_issues(result['answer'], evidence)
    analysis['answer_check'] = 'ok'
    if issues:
        try:
            result = _timed(timings, '답변재생성', gpt_api.generate_answer, question, prompt_contexts, analysis, display,
                            facts, correction=issues)
            issues = number_issues(result['answer'], evidence)
        except Exception as error:
            analysis['generation_error'] = type(error).__name__  # 첫 답변을 유지하고 아래 확인 안내를 붙입니다
        analysis['answer_check'] = 'corrected'
        if issues:
            analysis['answer_check'] = 'warning'
            # 화면(web/components/message.js)은 이 표시를 안내 상자로 바꿉니다. 표시 문자열을 바꾸면 함께 고치세요.
            result = {**result, 'answer': result['answer'] + '\n\n[수치 확인 필요] 답변의 일부 수치가 계산 근거와 다를 수 있어요. '
                      + '확인할 표현: ' + ', '.join(issues) + '. 아래 원본 데이터 계산과 출처를 기준으로 확인해 주세요.'}
    return result


def _timed(timings, stage, fn, *args, **kwargs):
    """API 호출 단계별 소요 시간(초)을 기록합니다. 테스트 기록에서 느린 단계를 찾는 용도입니다."""
    start = time.perf_counter()
    try:
        return fn(*args, **kwargs)
    finally:
        timings[stage] = round(time.perf_counter() - start, 3)


def explain(question, analysis, corpus, collection, catalog, display, region_id=None, embedder=None, timings=None,
            top_k=None):
    """서술 경로: 지식 색인 벡터 검색 → 검색 근거만으로 답변."""
    timings = {} if timings is None else timings
    ids = _limit(_narrow_ids(_resolve_ids(analysis['region_names'], question, catalog, region_id), 'explain', question, catalog),
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
    # 후보는 넉넉히 검색하고(로컬 검색이라 비용 없음), GPT에 넘기는 분량은 _generate의 _fit_budget에서 제한합니다.
    hits, diagnostics = _timed(timings, '임베딩·검색', retrieve, collection, query, region_ids=ids, month=month,
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
    result = _generate(question, hits, analysis, display, [], timings)
    return {**result, 'analysis': analysis, 'contexts': hits, 'facts': [],
            'sources': [_source_line(h) for h in hits], 'route': 'explain', 'timings': timings}


def _clarify_fallback(question, catalog, months):
    """모델이 되물었지만 질문에 확정된 지역이 적혀 있으면 코드 해석(query_parser)으로 답합니다.

    예: '세종 집값'(모델: 데이터 없음), '서울 구별 순위', '강남 아파트 몇 억이야'(모델: 기간 없음).
    지역 없는 시장 전반 질문('집값 바닥 찍었어?')은 전국 기준입니다. 같은 이름 지역 등 모호하면 그대로 되묻습니다.
    """
    frame = parse_question(question, catalog, months)
    if frame.ambiguous or 'unsupported' in frame.cues:
        return {}
    regions = frame.targets or ([frame.scope] if frame.scope else [])
    if not regions:
        if not MARKET_QUESTION.search(question):
            return {}
        return {'intent': 'explain', 'region_names': ['전국'], 'message': ''}
    intent = frame.intent if frame.intent in NUMERIC_INTENTS | {'explain'} else None
    if intent is None:
        intent = ('argmax' if frame.enumerate else 'trend' if 'trend' in frame.cues
                  else 'compare' if len(regions) > 1 else 'lookup')
    return {'intent': intent, 'region_names': [catalog[r]['label'] for r in regions], 'message': '',
            'level': frame.level or '시군구'}


def _analysis_from_code(question, catalog, months):
    """질문 분석 API가 실패했을 때 코드 해석만으로 만든 분석 결과. 지역을 확정하지 못하면 None."""
    frame = _clarify_fallback(question, catalog, months)
    if not frame:
        return None
    period = parse_question(question, catalog, months).period
    raw = {'intent': 'clarify', 'region_names': [], 'month': None, 'start_month': None, 'end_month': None,
           'display_unit': None, 'level': '시군구', 'rewritten_query': question, 'message': '', **frame}
    if period.kind == 'point' and period.month in months:
        raw['month'] = period.month
    elif period.kind == 'range' and period.start in months and period.end in months:
        raw.update(start_month=period.start, end_month=period.end)
    try:
        return Analysis.model_validate(raw).model_dump()
    except Exception:
        return None


def _with_notes(result, notes):
    """expressions.rewrite가 바꾼 내용(시점·지역·면적 안내)을 답변 끝에 밝힙니다."""
    new = [n for n in notes if n not in result.get('notes', [])]
    if not new or not isinstance(result.get('answer'), str):
        return result
    analysis = result.get('analysis') or {}
    if analysis.get('intent') == 'clarify' and analysis.get('reason') != 'ambiguous_region':
        return result  # 범위 밖·확인 질문에는 '오늘 → 최신 월' 같은 시점 안내가 의미 없음
    return {**result, 'answer': result['answer'] + '\n\n' + '\n'.join('※ ' + n for n in new),
            'notes': [*result.get('notes', []), *new]}


def _rewritten(question):
    if not isinstance(question, str):
        return question, []
    return rewrite(question, load_corpus().months)


def _ask(question, **kwargs):
    question, notes = _rewritten(question)  # 재구성된 질문에 남은 상대 표현도 명시 월로 바꿉니다
    # 장소 이름('김포공항 주변', '챔피언스 필드 근처')은 질문 분석 모델이 지역을 추측하기 전에 소재지를 확인합니다.
    located = _locate(question, kwargs) if _mentions_place(question, kwargs) else None
    if located is None:
        try:
            result = _ask_core(question, **kwargs)
        except ValueError:
            located = _locate(question, kwargs)  # 지역 오류로 끝난 경우에도 장소 이름이면 한 번 더
            if located is None:
                raise
        else:
            # 지역을 확정하지 못해 되물었으면 장소 이름인지 확인합니다 ('챔피언스 필드 집값').
            located = _locate(question, kwargs) if _unresolved(result) else None
            if located is None:
                return _with_notes(result, notes)
    kind, payload, note = located
    if kind == 'answer':
        return _with_notes(payload, notes)
    return _with_notes(_ask_core(payload, **kwargs), [*notes, note])


NEARBY = r'\s*(?:근처|주변|인근|부근|근방|일대|쪽|앞|옆|동네|일원)?(?:의|에|에서)?'
# 행정구역 이름이 없는 질문에서 장소를 가리키는 표현. 있으면 질문 분석 전에 소재지를 확인합니다.
PLACE_WORDS = re.compile(r'근처|주변|인근|부근|근방|일대|일원|공항|(?<![지구영전권무])역(?:\b|(?=\s|$|앞|근처|주변|인근))|야구장|축구장|경기장|구장|'
                         r'필드|파크|스타디움|돔|대학|캠퍼스|병원|공원|타워|해수욕장|터미널|청사|시청|구청|신도시|단지|랜드|월드|센터')


def _no_region_written(question):
    corpus = load_corpus()
    return not find_regions(_normalized(question), region_catalog(corpus))


def _mentions_place(question, kwargs):
    return (not kwargs.get('region_id') and PLACE_PRICE.search(question) and PLACE_WORDS.search(question)
            and _no_region_written(question))


def _unresolved(result):
    """지역을 확정하지 못해 되물은 결과인지 (범위 밖·같은 이름 지역 안내는 제외)."""
    analysis = result.get('analysis') or {}
    return analysis.get('intent') == 'clarify' and analysis.get('reason') not in {'out_of_scope', 'ambiguous_region'}


def _region_text(region, catalog):
    """지역 label을 질문에 넣을 짧은 이름으로 ('전남광주 / 광주 / 북구' → '광주 북구'). 그 지역 하나로만 해석되는 이름을 고릅니다."""
    from .year_query import named_region
    parts = region['label'].split(' / ')
    # 권역 이름('서남권', '강남지역')은 사람이 쓰는 주소가 아니므로 뺀 이름을 먼저 시도합니다 ('서울 강서구', '광주 북구').
    named = [x for x in parts if not x.endswith(('권', '지역')) and x != '전남광주']
    full = [alias for alias, short in ALIASES.items() if short == region['name'] and region['level'] == '시도']
    forms = [' '.join(named[-2:]), named[-1] if named else '', *full, f'{parts[0]} {parts[-1]}', ' '.join(parts[-2:]), ' '.join(parts)]
    for text in dict.fromkeys(f for f in forms if f):
        found, ambiguous = named_region(text, catalog)
        if found and not ambiguous and found['id'] == region['id']:
            return text
    return None


def _label_of(text):
    """'수원시 영통구' 같은 이름 → 지역 label (없으면 원문)."""
    region = _address_region(text, region_catalog(load_corpus()))
    return region['label'] if region else text


def _address_region(address, catalog):
    """'광주광역시 북구' 같은 주소 → 지역 목록의 지역 하나. 해석이 모호하거나 없으면 None."""
    from .year_query import named_region
    found, ambiguous = named_region(_normalized(address or ''), catalog)
    return None if ambiguous else found


def _clarify(answer, reason, choices=()):
    return 'answer', {'answer': answer, 'analysis': {'intent': 'clarify', 'reason': reason},
                      'contexts': [], 'facts': [], 'sources': [], 'route': 'rule', 'choices': list(choices)}, None


def _candidate_choices(question, name, labels):
    """후보 지역 버튼: 질문 속 모호한 이름(name)을 후보 지역 이름으로 바꾼 질문을 보냅니다."""
    catalog = region_catalog(load_corpus())
    by_label = {r['label']: r for r in catalog.values()}
    choices = []
    for label in labels:
        region = by_label.get(label.strip())
        text = region and _region_text(region, catalog)
        if not text:
            continue
        asked = question.replace(name, text, 1) if name and name in question else f'{text} {question}'
        choices.append({'label': text, 'question': ' '.join(asked.split())})
    return choices


def _locate(question, kwargs):
    """데이터에 없는 장소 이름을 LLM 일반 지식으로 시군구에 대응시킵니다.

    질문에 지역명이 이미 있으면 호출하지 않습니다(일반 질문은 API 호출이 늘지 않음). LLM이 답한 주소는
    지역 목록의 지역 하나로 해석되는지, 질문에 넣을 짧은 이름이 그 지역으로만 해석되는지 코드로 검증합니다.
    반환: ('question', 다시 조회할 질문, 안내문) | ('answer', 확인 질문 결과, None) | None(장소 아님)
    """
    if kwargs.get('region_id') or not PLACE_PRICE.search(question) or not _no_region_written(question):
        return None
    catalog = region_catalog(load_corpus())
    try:
        place = gpt_api.locate_place(question)
    except Exception:
        return None
    name = (place.get('place') or '').strip()
    if PLACE_PRICE.search(name) or len(name) > 30:
        name = ''  # 모델이 장소 대신 질문 전체를 적은 경우
    region = _address_region(place.get('address'), catalog) if place.get('found') else None
    text = region and _region_text(region, catalog)
    if name and text:
        rewritten = (re.sub(re.escape(name) + NEARBY, text + ' ', question, count=1) if name in question
                     else f'{text} {question}')
        note = (f"'{name}'은(는) 일반적으로 알려진 소재지({place['address']})를 기준으로 {text} 전체 값을 조회했습니다. "
                "동 단위 자료가 없어 장소 주변만의 값은 아닙니다. 위치가 다르면 지역명을 직접 적어 주세요.")
        return 'question', ' '.join(rewritten.split()), note
    candidates = list(dict.fromkeys(r['label'] for r in (_address_region(a, catalog) for a in place.get('candidates') or []) if r))
    if name and len(candidates) >= 2:
        return _clarify(f"'{name}'은(는) 여러 지역에 걸쳐 있거나 같은 이름이 여러 곳이라 한 지역으로 정할 수 없습니다. "
                        f"다음 중 하나를 골라 다시 물어보세요: {', '.join(candidates)}.", 'ambiguous_place',
                        _candidate_choices(question, name, candidates))
    if PLACE_WORDS.search(question):
        # 장소를 특정하지 못했으면 다른 지역(전국 등)으로 대신 답하지 않고 되묻습니다.
        what = f"'{name}'의" if name else '말씀하신 장소의'
        return _clarify(f"{what} 위치를 확인하지 못했습니다. 동 단위 자료는 없으니 시군구 이름으로 물어보세요. "
                        "예: '광주 북구 2021년 8월 가격', '서울 강서구 최근 1년 변동률'", 'unknown_place')
    return None


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
    unanswerable = out_of_scope(question) or ambiguous_place(question)
    if unanswerable:
        place = ambiguous_place_candidates(question)
        choices = _candidate_choices(question, place[0], [_label_of(c) for c in place[1]]) if place else []
        return {'answer': unanswerable, 'analysis': {'intent': 'clarify', 'reason': 'out_of_scope'},
                'contexts': [], 'facts': [], 'sources': [], 'route': 'rule', 'choices': choices}
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
        return coverage
    catalog=region_catalog(corpus)
    timings={}
    fallback_reason=None
    try:
        raw=_timed(timings,'질문분석',gpt_api.analyze_query,question,catalog,corpus.months)
    except Exception as error:
        # 질문 분석 API가 실패해도 코드 해석(query_parser)으로 지역이 확정되면 계속 답합니다.
        raw=_analysis_from_code(question,catalog,corpus.months)
        if raw is None:
            raise
        fallback_reason=type(error).__name__
    analysis=Analysis.model_validate(raw).model_dump()
    if fallback_reason:
        analysis['analysis_fallback']=fallback_reason
    if analysis['intent']=='clarify':
        analysis.update(_clarify_fallback(question,catalog,corpus.months))
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
        text=_normalized(question)
        names=list(dict.fromkeys(filter(None, (_written_scope(n, text) for n in _clean_names(names)))))
    named=_resolve_ids(names, question, catalog, region_id)
    ids=_limit(_narrow_ids(named, intent, question, catalog, analysis['level']), analysis)
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
    year=_year_only(question, analysis) if month is None and not analysis['month'] else None
    if year and (intent!='trend' or not (analysis['start_month'] and analysis['end_month'])):
        # 월 없이 연도만 지정: 최신 월로 대체하지 않고 그 해의 보유 월을 씁니다.
        in_year=[m for m in corpus.months if m.startswith(year)]
        if not in_year:
            raise ValueError(f'{year}년 데이터는 없습니다. 보유 기간: {corpus.months[0]} ~ {corpus.months[-1]}')
        if intent=='trend':
            previous=f'{int(year)-1}-12'
            analysis.update(start_month=previous if previous in corpus.months else in_year[0], end_month=in_year[-1])
            period_note=f"연도만 지정되어 {analysis['start_month']} ~ {in_year[-1]} 기간을 비교했습니다 (전년 12월 기준)."
        else:
            analysis['month']=in_year[-1]
            period_note=f'월이 지정되지 않아 {year}년의 마지막 보유 월({in_year[-1]}) 값을 사용했습니다.'
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
        support, diagnostics = _timed(timings, '임베딩·검색', retrieve, collection, analysis['rewritten_query'] or question,
                                      region_ids=profile_ids, month=knowledge_month, general_k=3,
                                      general_kinds=GUIDE_KINDS, embedder=embedder, limit=KNOWLEDGE_SEARCH_LIMIT)
    except Exception as error:
        support, diagnostics = [], {'used': [], 'rejected': [], 'error': type(error).__name__}
    analysis.update(route='numeric', resolved_region_ids=ids, resolved_month=target_month,
                    resolved_start=start,resolved_end=end,display_unit=display, retrieval=diagnostics)
    contexts=contexts+support
    result=_generate(question,contexts,analysis,display,facts,timings)
    sources=[_source_line(c) for c in contexts]
    choices=[]
    if intent=='lookup' and len(ids)==1:
        # 하위 지역이 있으면 '하위 지역 값도 필요하신가요?' 선택지 버튼 (예: 광주 → 동구·서구·남구·북구·광산구)
        from .year_query import subregion_choices
        choices,_=subregion_choices(catalog[ids[0]],catalog,f"{target_month[:4]}년 {int(target_month[5:])}월",'기록 전부')
    return {**result,'analysis':analysis,'contexts':contexts,'facts':facts,'sources':sources,'route':'numeric',
            'timings':timings,'choices':choices}


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
    question, notes = _rewritten(question)
    return _with_notes(_converse(question, history, **kwargs), notes)


def _converse(question, history=None, **kwargs):
    from .source_display import display_sources
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000:
        raise ValueError('질문은 1~1000자, 검색 개수는 1~50개여야 합니다.')
    converted = keyboard_to_hangul(question)  # 한/영 전환 없이 친 한글 (예: 'tjdnf' → '서울')
    if converted:
        question = converted
    if annual_mean_concept(question):
        result={'answer':'네. 이 서비스에서 연간 평균을 요청하면 해당 연도의 월평균 매매가격을 합해 유효한 월 수로 나눈 단순 평균을 사용합니다. 12개월이 모두 있으면 12로 나누며, 결측 월은 제외하고 사용한 월 수를 밝힙니다. 12월 가격 하나는 연평균이 아닙니다. 거래 건수로 가중한 개별 실거래 평균과도 다릅니다.',
                'analysis':{'intent':'concept'},'facts':[],'sources':[],'contexts':[],'route':'rule','standalone_question':question,'followup':False,'timings':{}}
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
        return {'answer':'네. 변동률은 비교 기준보다 가격이 내려가면 음수가 됩니다. '
                         '변동률 = (현재 가격 − 기준 가격) ÷ 기준 가격 × 100입니다. '
                         '예를 들어 1억원에서 9천만원으로 내려가면 -10%입니다. '
                         '특정 지역·월의 변동률이 양수인 것과 음수가 될 수 있는지는 별개의 질문입니다. '
                         '기준 가격이 0이면 이 식으로 변동률을 계산할 수 없습니다.',
                'analysis':{'intent':'concept'},'facts':[],'sources':[],'contexts':[],
                'route':'rule','standalone_question':question,'followup':False,'timings':{}}
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
                result=_ask(original,**kwargs)
                return display_sources({**result,'standalone_question':original,'followup':True,'timings':{}})
    standalone, followup, timings = question, False, {}
    explicit_extreme = re.search(r'(?<![0-9])(?:[0-9]{4}|[0-9]{2})년',question) and re.search('최저|최고|가장 싼|가장 비싼',question) and not re.search('아까|그 지역|이전',question)
    raw_aggregate=aggregate_query(question,load_corpus(),kwargs.get('unit') or DISPLAY_PRICE_UNIT)
    if raw_aggregate is not None and raw_aggregate['analysis']['intent']!='clarify':
        return display_sources({**raw_aggregate,'standalone_question':question,'followup':False,'input_converted':bool(converted),'timings':{}})
    if history and not explicit_extreme and _self_contained(question):
        history = None  # 지역·기간을 모두 적은 새 질문은 재구성하지 않음 (직전 지역이 섞여 들어가는 것 방지, API 1회 절약)
    if history and not explicit_extreme:
        open_index()  # 색인 문제는 재구성 API 호출 전에 확인
        condensed = _timed(timings, '질문재구성', gpt_api.condense_question, question, history)
        standalone, followup = condensed['standalone_question'], condensed['is_followup']
        if followup:
            catalog=region_catalog(load_corpus())
            from .year_query import named_region
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
            result = _ask(standalone, **kwargs)
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
                      'choices': _candidate_choices(standalone, name and name[1], candidates.split('; '))}
        else:
            error.standalone_question = standalone
            raise
    result = {**result, 'standalone_question': standalone, 'followup': followup, 'input_converted': bool(converted),
              'timings': {**timings, **result.get('timings', {})}}
    return display_sources(result)
