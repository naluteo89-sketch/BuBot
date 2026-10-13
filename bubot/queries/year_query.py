"""작년 질문은 보유한 작년 월에 한정해 조회/집계합니다."""
import re
from ..regions import child_regions, named_region, ranking_regions, region_catalog, region_text, short_name
from ..answers.choices import subregion_choices
from ..pricing import format_price
from ..config import CSV_PATH
from ..dates import full_year, months_in_year, this_year
from ..filters.period_parser import explicit_years

# 변화·순위를 묻는 '작년' 질문은 이 규칙이 아니라 일반 경로(GPT 분석 → 수치/서술)에서 답합니다.
CHANGE_WORDS = re.compile(r'오른|올랐|오름|내린|내렸|내림|떨어|상승|하락|변동|변화|대비|비교|추이|흐름|순위|많이|적게|보다')
YEAR_AGO = re.compile(r'(?:작년|지난해|전년)\s*(?:같은\s*달\s*|동월\s*)?(?:대비|보다|이랑\s*비교|과\s*비교|와\s*비교)')
RELATIVE_YEARS = (('재작년', -2), ('지난해', -1), ('작년', -1), ('올해', 0), ('금년', 0))


def resolve_relative_years(question, *, today=None):
    """'작년 대비'는 '최근 1년'(같은 달 1년 전과 비교)으로, 작년·올해 등은 명시 연도로 바꿉니다."""
    question = YEAR_AGO.sub('최근 1년', question)
    for word, offset in RELATIVE_YEARS:
        question = question.replace(word, f'{this_year(today) + offset}년')
    return question


def last_year_answer(question, corpus, unit, *, today=None):
    explicit = re.search(r'(?<![0-9])([0-9]{4}|[0-9]{2})년', question)
    if explicit and re.search(r'\d{1,2}\s*월', question) and not re.search(r'(?<!재)작년|지난해',question):
        return None
    extrema = re.search('최고|최저|가장 비싼|가장 싼|가장 높은|가장 낮은', question)
    relative = re.search(r'(?<!재)작년|지난해', question)
    if CHANGE_WORDS.search(question) or not (relative or (explicit and extrema)):
        return None
    year = full_year(explicit[1]) if explicit else this_year(today) - 1
    months = months_in_year(corpus.months, year)
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
    if len(months_in_year(corpus.months, year)) < 12:
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
    result = reply(headline + '\n\n' + '\n'.join('- ' + f for f in facts) + '\n\n' + note, facts, selected, year, months)
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


def _headline(selected, scope, level, high, low, year, unit):
    word = '높은' if high else '낮은'
    first = selected[0].metadata
    price = format_price(first['price_thousand_krw'], unit)
    if not (high or low):
        return f"{year}년 {short_name(first['region_path'], None)}의 월평균 매매가격은 다음과 같습니다."
    if scope and scope['level'] == '시군구':
        return (f"{year}년 {short_name(scope['label'], None)}의 월평균 매매가격이 가장 {word} 달은 "
                f"{first['month']}({price})입니다.")
    place = f"{short_name(scope['label'], None)}에서" if scope else '전국에서'
    noun = '시도는' if level == '시도' else '지역은'
    return (f"{year}년 {place} 월평균 매매가격이 가장 {word} {noun} "
            f"{short_name(first['region_path'], scope)}입니다 ({first['month']}, {price}).")


# '제주도 데이터 전부', '6월치 기록 전부'처럼 값 목록 자체를 원하는 표현 (평균·변화·순위 질문은 다른 규칙이 처리)
ALL_RECORDS = re.compile(r'전부|모두|모든|몽땅|전체\s*(?:데이터|자료|기록|값)|다\s*(?:보여|알려|가져|줘)|목록|리스트|기록|월별')
NOT_LISTING = re.compile(r'평균|합산|합계|총액|변동|상승|하락|올랐|내렸|최고|최저|가장|비교|차이|대비|추이|흐름|순위|전세|월세|거래량')
MAX_LISTED_CHILDREN = 30  # '하위 지역 포함'으로 한 번에 나열할 최대 하위 지역 수
WITH_CHILDREN = re.compile(r'하위|구별|시군구별|시별|군별|세부|각\s*(?:구|시|군)|포함|나눠|나누어')


def region_records(question, corpus, unit):
    """지역과 연도(또는 월)가 분명한 '전부 보여줘' 질문에 보유 월의 값을 그대로 나열합니다.

    이전에는 2026년처럼 일부 월만 있는 연도면 연도 보유 범위 안내(coverage)만 하고 값을 보여주지 않았습니다.
    바로 아래 하위 지역이 MAX_CHILD_REGIONS곳 이하면(제주 → 제주시·서귀포시) 함께 보여줍니다.
    """
    if not ALL_RECORDS.search(question) or NOT_LISTING.search(question):
        return None
    years = sorted(explicit_years(resolve_relative_years(question)))
    if len(years) != 1:
        return None
    catalog = region_catalog(corpus)
    scope, ambiguous = named_region(question, catalog)
    if ambiguous or not scope:
        return None
    year = years[0]
    in_year = months_in_year(corpus.months, year)
    requested = sorted({f'{year}-{int(m):02}' for m in re.findall(r'(?<!\d)(\d{1,2})\s*월', question) if 1 <= int(m) <= 12})
    months = [m for m in (requested or in_year) if m in corpus.months]
    if not months:
        wanted = ', '.join(requested) if requested else f'{year}년'
        return reply(f'{wanted} 자료는 원본에 없습니다. 보유 기간: {corpus.months[0]} ~ {corpus.months[-1]}', [], [], year, in_year)
    children = child_regions(scope, catalog)
    include = bool(WITH_CHILDREN.search(question)) and len(children) <= MAX_LISTED_CHILDREN
    regions = [scope] + (children if include else [])
    wanted = {r['id'] for r in regions}
    docs = {(d.metadata['region_id'], d.metadata['month']): d for d in corpus.documents
            if d.metadata['kind'] == 'monthly' and d.metadata['region_id'] in wanted and d.metadata['month'] in months}
    facts, used = [], []
    for region in regions:
        for month in months:
            doc = docs.get((region['id'], month))
            price = doc and doc.metadata.get('price_thousand_krw')
            facts.append(f"{month} {region['label']}: {format_price(price, unit) if price is not None else '결측'}")
            if doc is not None:
                used.append(doc)
    span = _month_span(months, corpus.months) if len(months) > 1 else months[0]
    names = '·'.join(short_name(r['label'], scope) for r in regions[1:])
    headline = (f"{region_text(scope, catalog) or short_name(scope['label'], None)}의 {span} 아파트 월평균 매매가격"
                + (f"을 하위 지역({names})까지 모두 보여드립니다." if names else "을 모두 보여드립니다."))
    notes = []
    if not requested and len(in_year) < 12:
        notes.append(f'{year}년은 원본에 {len(in_year)}개월({in_year[0]} ~ {in_year[-1]})만 있어 그 달만 표시했습니다.')
    choices, ask_children = [], ''
    if children and not include:
        period_text = f'{year}년 {int(months[0][5:])}월' if len(months) == 1 and requested else f'{year}년'
        choices, ask_children = subregion_choices(scope, catalog, period_text, '기록 전부')
    elif children and len(children) > MAX_LISTED_CHILDREN:
        notes.append(f"하위 지역 {len(children)}곳은 목록이 길어 생략했습니다. '{short_name(scope['label'], None)} 시군구 "
                     f"{months[-1][:4]}년 {int(months[-1][5:])}월 가격 순위'처럼 물어보세요.")
    notes.append('값은 개별 실거래 가격이 아닌 지역별 아파트 월평균 매매가격입니다.')
    if ask_children:
        notes.append(ask_children)
    result = reply(headline + '\n\n' + '\n'.join('- ' + f for f in facts) + '\n\n' + ' '.join(notes), facts, used, year, in_year)
    result['analysis'].update(intent='region_records', resolved_region_ids=[r['id'] for r in regions],
                              resolved_start=months[0], resolved_end=months[-1])
    result['contexts'] = [{'id': d.id, 'text': d.text, 'metadata': d.metadata} for d in used]
    result['choices'] = choices
    return result


def reply(answer, facts, docs, year, months):
    sources = [f"{CSV_PATH.name} · {d.metadata['source_row']}행 · {d.metadata['month']} · SHA-256 {d.metadata['source_sha256']}" for d in docs]
    return {'answer':answer,'facts':facts,'sources':sources,'contexts':[],
            'analysis':{'intent':'year_query','year':year,'available_months':months}}
