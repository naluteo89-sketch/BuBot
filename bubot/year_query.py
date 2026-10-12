"""작년 질문은 보유한 작년 월에 한정해 조회/집계합니다."""
import re
from datetime import datetime, timezone, timedelta
from .regions import ALIASES, region_catalog, ranking_regions
from .pricing import format_price
from .config import CSV_PATH

# 변화·순위를 묻는 '작년' 질문은 이 규칙이 아니라 일반 경로(GPT 분석 → 수치/서술)에서 답합니다.
CHANGE_WORDS = re.compile(r'오른|올랐|오름|내린|내렸|내림|떨어|상승|하락|변동|변화|대비|비교|추이|흐름|순위|많이|적게|보다')
YEAR_AGO = re.compile(r'(?:작년|지난해|전년)\s*(?:같은\s*달\s*|동월\s*)?(?:대비|보다|이랑\s*비교|과\s*비교|와\s*비교)')
RELATIVE_YEARS = (('재작년', -2), ('지난해', -1), ('작년', -1), ('올해', 0), ('금년', 0))


def _this_year(today=None):
    return (today or datetime.now(timezone(timedelta(hours=9)))).year


def resolve_relative_years(question, *, today=None):
    """'작년 대비'는 '최근 1년'(같은 달 1년 전과 비교)으로, 작년·올해 등은 명시 연도로 바꿉니다."""
    question = YEAR_AGO.sub('최근 1년', question)
    for word, offset in RELATIVE_YEARS:
        question = question.replace(word, f'{_this_year(today) + offset}년')
    return question


def named_region(question, catalog):
    """질문에 적힌 지역명 중 가장 구체적인 지역 하나. (지역, 모호 여부)를 돌려줍니다.

    '서울 중구'처럼 상위 지역을 함께 적으면 그 경로를 모두 포함하는 지역을 고릅니다.
    '강남'(강남구), '포항 남구'처럼 줄인 이름·상위 지역 수식도 인식하고, '중구'·'광주'처럼 여러 곳에 있는
    이름은 임의로 고르지 않고 모호(True)로 돌려줍니다. (2026-10-12: query_parser.find_regions 사용)
    """
    from .query_parser import find_regions
    if not catalog:
        return None, False
    mentions = [m for m in find_regions(question, catalog) if not m.qualifier]
    if not mentions:
        return None, False
    if any(len(m.candidates) != 1 for m in mentions):
        return None, True
    found = list(dict.fromkeys(m.candidates[0] for m in mentions))
    return (catalog[found[0]], False) if len(found) == 1 else (None, True)


def last_year_answer(question, corpus, unit, *, today=None):
    explicit = re.search(r'(?<![0-9])([0-9]{4}|[0-9]{2})년', question)
    if explicit and re.search(r'\d{1,2}\s*월', question) and not re.search(r'(?<!재)작년|지난해',question):
        return None
    extrema = re.search('최고|최저|가장 비싼|가장 싼|가장 높은|가장 낮은', question)
    relative = re.search(r'(?<!재)작년|지난해', question)
    if CHANGE_WORDS.search(question) or not (relative or (explicit and extrema)):
        return None
    year = (int(explicit[1]) + (2000 if len(explicit[1])==2 else 0)) if explicit else _this_year(today) - 1
    months = [m for m in corpus.months if m.startswith(f'{year}-')]
    requested = [int(m) for m in re.findall(r'(\d{1,2})\s*월', question)]
    if requested:
        requested_months = [f'{year}-{m:02}' for m in requested]
        if any(m not in months for m in requested_months):
            return reply(f'{year}년 요청 월은 원본에 없습니다. 보유 월: ' + ', '.join(months), [], [], year, months)
        months = [m for m in months if min(requested_months) <= m <= max(requested_months)]
    if not months:
        return reply(f'{year}년 데이터는 보유하고 있지 않습니다.', [], [], year, months)
    if re.search('합산|총액|총 거래', question):
        return reply(f'{year}년 보유 데이터는 {", ".join(months)}입니다. 이 자료는 지역별 월평균 매매가격입니다. '
                     '개별 거래 금액과 거래 건수가 없어 실제 거래 총액을 계산할 수 없습니다. '
                     '월평균 가격을 합산해도 거래 총액이 되지 않으며, 상위·하위 지역을 함께 합치면 중복될 수 있습니다.', [], [], year, months)
    catalog = region_catalog(corpus)
    # 원문에 명시된 지역만 사용합니다. 동일 이름은 임의로 선택하지 않습니다.
    scope, ambiguous = named_region(question, catalog)
    if ambiguous:
        return reply('조회할 지역을 시도와 시군구까지 명확히 입력해주세요.', [], [], year, months)
    high = bool(re.search('최고|가장 비싼|가장 높은', question))
    low = bool(re.search('최저|가장 싼|가장 낮은', question))
    if high or low:
        level = '시도' if '시도' in question else '시군구'
        allowed = ({scope['id']} if scope and scope['level']=='시군구' else
                   ranking_regions(catalog, scope['id'] if scope else None, level))
    elif scope:
        allowed = {scope['id']}
    else:
        return reply(f'{year}년 보유 월은 {", ".join(months)}입니다. 조회할 지역을 지정해주세요.', [], [], year, months)
    docs = [d for d in corpus.documents if d.metadata['kind']=='monthly'
            and d.metadata['month'] in months and d.metadata['region_id'] in allowed
            and d.metadata.get('price_thousand_krw') is not None]
    note = f'{year}년 보유 데이터({_month_span(months, corpus.months)})만 사용했습니다. '
    if len([m for m in corpus.months if m.startswith(f'{year}-')]) < 12:
        note += '나머지 월은 원본 CSV에 없어 연간 전체를 대표하지 않습니다. '
    note += '값은 개별 실거래 가격이 아닌 지역별 아파트 월평균 매매가격입니다.'
    if not docs:
        return reply(note + '\n해당 지역의 보유 월 가격이 결측입니다.', [], [], year, months)
    selected = [(max if high else min)(docs, key=lambda d:d.metadata['price_thousand_krw'])] if high or low else sorted(docs,key=lambda d:d.metadata['month'])
    facts = [f"{d.metadata['month']} {d.metadata['region_path']}: {format_price(d.metadata['price_thousand_krw'],unit)}" for d in selected]
    if high or low:
        note += f"\n비교 범위: {'전국' if not scope else scope['label']} / {'해당 지역' if scope and scope['level']=='시군구' else level}, 결측 제외 {len(docs)}개 지역·월 값 중 {'최고' if high else '최저'}."
    # 결론 한 문장을 먼저 두고(페르소나 답변 형식과 같은 순서), 계산값과 사용한 월·비교 범위 안내를 뒤에 붙입니다.
    headline = _headline(selected, scope, level if high or low else None, high, low, year, unit)
    sources = [f"{CSV_PATH.name} · {d.metadata['source_row']}행 · {d.metadata['month']} · SHA-256 {d.metadata['source_sha256']}" for d in selected]
    result = reply(headline + '\n\n' + '\n'.join('- ' + f for f in facts) + '\n\n' + note, facts, sources, year, months)
    result['contexts'] = [{'id':d.id,'text':d.text,'metadata':d.metadata} for d in selected]
    if high or low:
        result['analysis']['price_extreme'] = True
    return result


def _month_span(months, all_months):
    """연속된 월이면 '2025-01 ~ 2025-12, 12개월', 중간이 비면 목록 그대로."""
    if len(months) > 2:
        start = all_months.index(months[0])
        if list(all_months[start:start + len(months)]) == list(months):
            return f'{months[0]} ~ {months[-1]}, {len(months)}개월'
    return ', '.join(months)


def _short_name(path, scope):
    """'서울 / 강북지역 / 동북권 / 도봉구' → 범위가 서울이면 '도봉구', 전국이면 '서울 도봉구'."""
    parts = path.split(' / ')
    if len(parts) == 1 or (scope and path.startswith(scope['label'] + ' / ')):
        return parts[-1]
    return f'{parts[0]} {parts[-1]}'


def _headline(selected, scope, level, high, low, year, unit):
    word = '높은' if high else '낮은'
    first = selected[0].metadata
    price = format_price(first['price_thousand_krw'], unit)
    if not (high or low):
        return f"{year}년 {_short_name(first['region_path'], None)}의 월평균 매매가격은 다음과 같습니다."
    if scope and scope['level'] == '시군구':
        return (f"{year}년 {_short_name(scope['label'], None)}의 월평균 매매가격이 가장 {word} 달은 "
                f"{first['month']}({price})입니다.")
    place = f"{_short_name(scope['label'], None)}에서" if scope else '전국에서'
    noun = '시도는' if level == '시도' else '지역은'
    return (f"{year}년 {place} 월평균 매매가격이 가장 {word} {noun} "
            f"{_short_name(first['region_path'], scope)}입니다 ({first['month']}, {price}).")


def reply(answer, facts, sources, year, months):
    return {'answer':answer,'facts':facts,'sources':sources,'contexts':[],
            'analysis':{'intent':'year_query','year':year,'available_months':months}}
