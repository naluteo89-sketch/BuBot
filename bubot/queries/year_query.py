"""작년 질문은 보유한 작년 월에 한정해 조회/집계합니다."""
import re
from ..regions import child_regions, named_region, region_catalog, region_text, scope_regions, short_name
from ..answers.answer_format import contexts_of, rule_answer
from ..answers.choices import subregion_choices
from ..cache.data_cache import monthly_doc, monthly_docs
from ..pricing import format_price
from ..config import CSV_PATH
from ..dates import full_year, month_label, months_in_year, this_year
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
        allowed = scope_regions(catalog, scope, level)
    elif scope:
        allowed = {scope['id']}
    else:
        return reply(f'{year}년 보유 월은 {", ".join(months)}입니다. 조회할 지역을 지정해주세요.', [], [], year, months)
    docs = [d for d in monthly_docs(corpus, allowed, months) if d.metadata.get('price_thousand_krw') is not None]
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
    result['contexts'] = contexts_of(selected)
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


# 개별 거래·아파트의 최고가/최저가: 자료는 지역 월평균이라 답할 수 없습니다. 이전에는 GPT 분석이 지역 순위(argmax)로 보고
# '광주 남구의 최고가 아파트는?'·'최저가 아파트는?'에 남구 월평균 하나를 '1위'로 똑같이 답했습니다 (2026-10-16 testlog).
# 대신 그 지역 월평균이 가장 높은(낮은) 달을 알려줍니다. '최고가 지역'·'가장 비싼 구'처럼 지역 순위를 묻는 질문은 제외합니다.
_HIGH = r'최고\s*가|최고\s*가격|최고\s*금액|가장\s*비싸(?:게)?|가장\s*비싼|제일\s*비싸(?:게)?|제일\s*비싼|가장\s*높은\s*가격|비싸게'
_LOW = r'최저\s*가|최저\s*가격|최저\s*금액|가장\s*싸(?:게)?|가장\s*싼|제일\s*싸(?:게)?|제일\s*싼|가장\s*저렴(?:한|하게)|가장\s*낮은\s*가격|(?<!비)싸게'
_DEAL = r'아파트|거래|매물|단지|집|매매\s*건|팔린|팔렸|거래된|계약|낙찰'
DEAL_EXTREME = re.compile(rf'(?:{_HIGH}|{_LOW})\s*(?:로|으로|에)?\s*(?:{_DEAL})|(?:{_DEAL})\S{{0,2}}\s*(?:{_HIGH}|{_LOW})')
REGION_RANKING = re.compile(r'지역|시도|시군구|곳|동네|순위|랭킹|(?:어느|어떤|무슨)\s*(?:구|시|군)|(?<![가-힣])(?:구|시|군)\s*(?:는|은|가|이|를|을)?\s*(?:어디|뭐)')


def deal_extreme(question, corpus, unit):
    """'2025년 강남구 최고가 거래'처럼 개별 거래·아파트의 최고가/최저가를 물으면, 답할 수 없음을 밝히고
    그 지역의 월평균 매매가격이 가장 높은(낮은) 달과 금액으로 대신 답합니다. 연도가 있으면 그 해의 달, 없으면 전체 보유 기간.
    해당 없으면 None."""
    found = DEAL_EXTREME.search(question)
    if not found or REGION_RANKING.search(question):
        return None
    high = bool(re.search(_HIGH, question))
    low = bool(re.search(_LOW, question))
    catalog = region_catalog(corpus)
    scope, ambiguous = named_region(question, catalog)
    if ambiguous:
        return None  # 같은 이름 지역('중구')은 기존 확인 질문 경로로
    scope = scope or catalog.get('1')  # 지역이 없으면 전국
    years = sorted(explicit_years(resolve_relative_years(question)))
    if len(years) > 1:
        return None
    year = years[0] if years else None
    months = months_in_year(corpus.months, year) if year else list(corpus.months)
    requested = [f'{year}-{int(m):02}' for m in re.findall(r'(?<!\d)(\d{1,2})\s*월', question) if year and 1 <= int(m) <= 12]
    if requested:
        months = [m for m in months if m in requested]
    name = region_text(scope, catalog) or short_name(scope['label'], None)
    word = '최고가' if high and not low else '최저가' if low and not high else '최고가·최저가'
    refuse = (f'개별 아파트·거래 단위의 {word}는 자료에 없어 답해드릴 수 없습니다. '
              '이 서비스의 자료는 지역별 아파트 월평균 매매가격이며, 단지나 개별 거래 금액은 없습니다.')
    if not months:
        span = f'{year}년' if year else '요청 기간'
        return rule_answer(refuse + f'\n\n{span} 자료는 원본에 없습니다. 보유 기간: {corpus.months[0]} ~ {corpus.months[-1]}',
                           intent='clarify', reason='deal_extreme')
    docs = [d for d in monthly_docs(corpus, {scope['id']}, months) if d.metadata.get('price_thousand_krw') is not None]
    if not docs:
        return rule_answer(refuse + f'\n\n{name}의 해당 기간 월평균 매매가격은 결측입니다.', intent='clarify', reason='deal_extreme')
    period = (month_label(requested[0]) if len(requested) == 1 else f'{year}년') if year else f'전체 보유 기간({months[0]} ~ {months[-1]})'
    price = lambda d: format_price(d.metadata['price_thousand_krw'], unit)
    if len(docs) == 1:
        doc = docs[0]
        selected, highlight = [doc], [month_label(doc.metadata['month'])]
        lead = (f'개별 아파트·거래 단위의 {word}는 답해드릴 수 없지만, {name}의 {month_label(doc.metadata["month"])} '
                f'아파트 월평균 매매가격은 {price(doc)}입니다.')
    else:
        picks = ([('높았던', max(docs, key=lambda d: d.metadata['price_thousand_krw']))] if high or not low else []) + \
                ([('낮았던', min(docs, key=lambda d: d.metadata['price_thousand_krw']))] if low else [])
        selected = [d for _, d in picks]
        highlight = [month_label(d.metadata['month']) for d in selected]
        parts = [f'월평균 매매가격이 가장 {w} 달은 {month_label(d.metadata["month"])}({price(d)})' for w, d in picks]
        lead = (f'개별 아파트·거래 단위의 {word}는 답해드릴 수 없지만, {period} {name}의 '
                + ', '.join(parts) + '입니다.')
    facts = [f"{d.metadata['month']} {d.metadata['region_path']}: {price(d)}" for d in selected]
    note = (f'비교 범위: {name}의 {period} 보유 월 중 값이 있는 {len(docs)}개월. ' if len(docs) > 1 else '') + (
            '값은 개별 거래 가격이 아니라 그 달 지역 아파트의 평균 매매가격이며, 그 달에 이보다 비싸거나 싼 거래가 있었을 수 있습니다.')
    if year and len(months_in_year(corpus.months, year)) < 12 and not requested:
        note += f' {year}년은 원본에 {len(months_in_year(corpus.months, year))}개월만 있습니다.'
    result = rule_answer(lead + '\n\n' + '\n'.join('- ' + f for f in facts) + '\n\n' + note,
                         facts, selected, intent='deal_extreme', price_extreme=True,
                         resolved_region_ids=[scope['id']], resolved_start=months[0], resolved_end=months[-1])
    result['highlight'] = highlight
    return result


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
    facts, used = [], []
    for region in regions:
        for month in months:
            doc = monthly_doc(corpus, region['id'], month)
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
        period_text = month_label(months[0]) if len(months) == 1 and requested else f'{year}년'
        choices, ask_children = subregion_choices(scope, catalog, period_text, '기록 전부')
    elif children and len(children) > MAX_LISTED_CHILDREN:
        notes.append(f"하위 지역 {len(children)}곳은 목록이 길어 생략했습니다. '{short_name(scope['label'], None)} 시군구 "
                     f"{month_label(months[-1])} 가격 순위'처럼 물어보세요.")
    notes.append('값은 개별 실거래 가격이 아닌 지역별 아파트 월평균 매매가격입니다.')
    if ask_children:
        notes.append(ask_children)
    result = reply(headline + '\n\n' + '\n'.join('- ' + f for f in facts) + '\n\n' + ' '.join(notes), facts, used, year, in_year)
    result['analysis'].update(intent='region_records', resolved_region_ids=[r['id'] for r in regions],
                              resolved_start=months[0], resolved_end=months[-1])
    result['contexts'] = contexts_of(used)
    result['choices'] = choices
    return result


def reply(answer, facts, docs, year, months):
    sources = [f"{CSV_PATH.name} · {d.metadata['source_row']}행 · {d.metadata['month']} · SHA-256 {d.metadata['source_sha256']}" for d in docs]
    return {'answer':answer,'facts':facts,'sources':sources,'contexts':[],
            'analysis':{'intent':'year_query','year':year,'available_months':months}}
