"""GPT 질문 분석 보정 (LLM 없음): 모델이 준 지역명을 질문에 적힌 대로 지역 ID로 확정하고, 상대 기간을 코드로 확정합니다.

질문 분석 API가 되묻거나 실패하면 코드 해석(query_parser)으로 분석 결과를 만듭니다.
"""
import re

from ..config import MAX_REGIONS
from ..dates import months_between, shift
from ..llm.schemas import Analysis
from ..regions import canonical_names, find_regions, resolve_region
from .period_parser import explicit_years, parse_period
from .query_parser import find_cues, parse_question

NUMERIC_INTENTS = {'lookup', 'trend', 'compare', 'argmax', 'argmin'}
MARKET_QUESTION = re.compile(r'(?:집값|시세|아파트\s*값|아파트\s*가격|부동산|시장).*'
                             r'(?:바닥|꼭지|고점|어때|어떤가|올랐|내렸|떨어|폭락|폭등|급등|급락|반등|상승|하락|흐름|분위기)')
NOW_WORDS = re.compile(r'요즘|요새|현재|지금|최근|최신|근래')


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
                # 기간이 있는 비교도 추이로 계산합니다. compare는 한 달 값만 써서 '2020~2023년 비교'에 최신 월 값이 나왔습니다.
                if analysis['intent'] in ('lookup', 'compare'):
                    analysis['intent'] = 'trend'
            if analysis['intent'] == 'explain' and 'explain' not in cues and analysis['region_names']:
                # '2016년 8월 서울 집값'은 서술이 아니라 원본 가격 조회입니다
                analysis['intent'] = 'trend' if written.kind == 'range' else 'lookup'
            if written.notes:
                analysis['period_note'] = ' '.join(written.notes)
            return analysis
        if written.kind == 'none' and analysis['intent'] == 'trend' and analysis['end_month'] != latest:
            # 모델이 기간을 지어냈거나(학습 시점 무렵 2023년 등) 비워 둔 경우
            start = max(shift(latest, -12), months[0])
            analysis.update(start_month=start, end_month=latest, month=None,
                            period_note=f'기간이 지정되지 않아 최근 1년({start} ~ {latest})을 비교했습니다.')
            return analysis
        if NOW_WORDS.search(question) and not explicit_years(question) and not re.search(r'\d{1,2}\s*월', question):
            start, end = analysis['start_month'], analysis['end_month']
            if start and end:
                if end != latest:  # 모델이 고른 기간 길이는 유지하고 끝을 최신 월로 옮깁니다
                    shifted = shift(latest, -max(months_between(start, end), 0))
                    analysis.update(start_month=max(shifted, months[0]), end_month=latest, month=None)
            else:
                analysis.update(month=latest, start_month=None, end_month=None)
        return analysis
    if span < 0:
        return analysis
    start = shift(latest, -span)
    analysis.update(start_month=start if start >= months[0] else months[0], end_month=latest, month=None)
    if analysis['intent'] == 'lookup':
        analysis['intent'] = 'trend'
    return analysis


def year_only(question, analysis):
    """월 없이 연도만 지정한 질문의 연도: 분석 결과가 한 해 범위이거나, 질문에 명시 연도가 하나뿐일 때."""
    start, end = analysis['start_month'], analysis['end_month']
    if start and end and start[:4] == end[:4]:
        return start[:4]
    years = explicit_years(question)
    return str(next(iter(years))) if len(years) == 1 else None


def clarify_fallback(question, catalog, months):
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


def analysis_from_code(question, catalog, months):
    """질문 분석 API가 실패했을 때 코드 해석만으로 만든 분석 결과. 지역을 확정하지 못하면 None."""
    frame = clarify_fallback(question, catalog, months)
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


# 모델이 준 지역명 → 지역 ID ────────────────────────────────────────

LEVEL_WORDS = {'시도', '시·도', '시군구', '시·군·구', '권역', '시', '군', '구', '지역', '전체', '모든 지역', '전국 시도'}


DATE_IN_NAME = re.compile(r'\d{4}\s*년|\d{1,2}\s*월|\d{4}-\d{1,2}')
PARTICLE = re.compile(r'(?<=[시군구도])(은|는|이|가|의|와|과|을|를|에서|에|랑|하고|도)$')


def clean_names(names):
    """모델이 지역명 자리에 넣은 계층 이름('시도')·날짜를 빼고, 지역명에 붙은 조사('제주시는')를 뗍니다."""
    cleaned = []
    for name in names:
        name = '/'.join(PARTICLE.sub('', part.strip()) for part in name.split('/')).replace('/', ' / ')
        if name.strip() and name.strip() not in LEVEL_WORDS and not DATE_IN_NAME.search(name):
            cleaned.append(name)
    return cleaned


def written_scope(name, text):
    """지역 경로 중 질문에 적힌 가장 구체적인 부분. '서울 / 강북지역 / 도심권'이고 질문에 '서울'만 있으면 '서울'.

    '강남구'의 '강남'처럼 시·군·구를 뺀 이름도 적힌 것으로 봅니다. 아무것도 적혀 있지 않으면 None.
    """
    tokens = [t for t in re.split(r'[\s/]+', canonical_names(name)) if t]
    for end in range(len(tokens), 0, -1):
        if _token_written(tokens[end - 1], text):
            return ' / '.join(tokens[:end])
    return None


def _token_written(token, text):
    if len(token) < 2:  # '시' 같은 한 글자는 어느 질문에나 들어 있어 근거가 되지 않습니다
        return False
    return token in text or (len(token) > 2 and token.endswith(('시', '군', '구')) and token[:-1] in text)


def resolve_ids(names, question, catalog, region_id=None):
    names = clean_names(names)
    if '광주광역시' in question:
        names = ['광주광역시' if name.strip() in {'광주','광주시','전남광주 / 광주'} else name for name in names]
    text, ids, skipped = canonical_names(question), [], None
    for name in names:
        try:
            ids.append(resolve_region(name, catalog))
        except ValueError as error:
            written = _written_region(name, question, catalog)
            if written:
                ids.append(written)  # 모델이 상위 경로를 틀리게 붙인 경우('경부2권 / 성남시 / 분당구'): 질문에 적힌 지역
                continue
            if written_scope(name, text) is not None:
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
    last = [t for t in re.split(r'[\s/]+', clean_names([name])[0] if clean_names([name]) else '') if t]
    if not last:
        return None
    stems = {last[-1], last[-1][:-1] if len(last[-1]) > 2 and last[-1].endswith(('시', '군', '구')) else last[-1]}
    found = {m.candidates[0] for m in find_regions(canonical_names(question), catalog)
             if len(m.candidates) == 1 and not m.qualifier
             and (catalog[m.candidates[0]]['name'] in stems or m.text in stems)}
    return found.pop() if len(found) == 1 else None


def _reject_guessed_parent(ids, question, catalog):
    """'중구'처럼 여러 시도에 있는 이름인데 질문에 시도가 없으면, 모델이 붙인 상위 지역은 추측입니다.

    resolve_region의 모호성 오류와 같은 형식으로 알려 ask()가 후보를 보여 주는 확인 질문으로 바꿉니다.
    """
    text = canonical_names(question)
    for rid in ids:
        region = catalog[rid]
        same = [r for r in catalog.values() if r['name'] == region['name']]
        if len(same) > 1 and not any(_token_written(p, text) for p in region['path'][:-1]):
            labels = '; '.join(r['label'] for r in same)
            raise ValueError(f"지역 '{region['name']}'을 확정할 수 없습니다. 시도와 시군구를 함께 입력해주세요.{labels}")


def limit_regions(ids, analysis=None):
    """지역이 MAX_REGIONS곳을 넘으면 오류 대신 앞의 MAX_REGIONS곳만 계산하고 그 사실을 analysis에 남깁니다."""
    if len(ids) <= MAX_REGIONS:
        return ids
    if analysis is not None:
        analysis['region_note'] = f'지역이 {len(ids)}곳이라 질문에 적힌 순서대로 앞의 {MAX_REGIONS}곳만 계산했습니다.'
    return ids[:MAX_REGIONS]


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


def narrow_ids(ids, intent, question, catalog, level=None):
    """모델이 지역을 계층별로 나눠 넣은 경우를 질문에 적힌 대로 정리합니다.

    - 조회·비교·서술: 다른 지역의 상위 지역인데 질문에 따로 적히지 않은 것(추가된 '서울', '서울 중구'의 수식어 '서울')은 뺍니다.
      '강남구와 서울 비교'처럼 상위 지역을 따로 적으면 그대로 비교합니다.
    - 순위: 공통 상위 지역이 질문에 있으면 그 지역이 검색 범위(서울의 구), 같은 계층을 나열만 했으면 전국 범위입니다.
    """
    text = canonical_names(question)
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
