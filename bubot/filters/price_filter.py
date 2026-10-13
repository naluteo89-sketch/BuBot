"""가격 조건 검색('5억 이하 서울 구', '10억 넘는 시도')과 자료 밖 질문 안내. 원본에서 직접 계산합니다 (LLM 없음)."""
import re

from ..config import CSV_PATH
from ..pricing import format_price
from .query_parser import find_level, find_threshold, parse_period
from ..regions import ranking_regions, region_catalog
from ..queries.year_query import named_region

LIST_LIMIT = 30
OP_WORDS = {'>=': '이상', '>': '초과', '<=': '이하', '<': '미만'}

RENT = re.compile(r'전세|월세|전월세|반전세|임대료|보증금')
VOLUME = re.compile(r'거래량|거래\s*건수|몇\s*건|매물\s*수|매물이\s*몇')
FORECAST = re.compile(r'예측|전망|오를까|내릴까|떨어질까|오를지|내릴지|떨어질지|살까|사도\s*될까|팔까|팔아야|사야\s*할까|'
                      r'(?:투자|매수|매도|구매)\s*(?:추천|해도|할까|타이밍|시기)|추천')
HOUSING = re.compile(r'집|아파트|부동산|시세|가격|매매|동네|매수|매도|청약')
OTHER_HOUSING = re.compile(r'오피스텔|빌라|연립|다세대|단독\s*주택|다가구|상가|토지|땅값|원룸|도시형\s*생활\s*주택')
# 특정 단지·개별 거래: 자료는 지역 월평균이라 '래미안 실거래가'에 강남구 평균을 단지 가격처럼 답하면 안 됩니다.
INDIVIDUAL = re.compile(r'실거래(?!\s*가격\s*지수|가\s*지수)|개별\s*거래|거래\s*내역|호가|평형|\d+\s*동\s*\d+\s*호|단지')
COMPLEX = re.compile(r'래미안|자이|힐스테이트|아이파크|푸르지오|[eE이]\s*편한\s*세상|롯데\s*캐슬|더\s*샵|센트레빌|위브|'
                     r'아크로|디에이치|트리마제|헬리오시티|타워\s*팰리스|은마|리센츠|파크리오|엘스|'
                     r'(?<![가-힣])(?![가-힣]*(?:시|구|군|도|동|읍|면|전국|서울|수도권|지방|동네|근처|주변|우리)아파트)[가-힣A-Za-z0-9]{2,}(?:아파트|APT)')


def out_of_scope(question):
    """전세·월세, 거래량, 예측·투자 추천처럼 자료로 답할 수 없는 질문의 안내문. 해당 없으면 None."""
    if RENT.search(question):
        return ('이 서비스의 자료는 지역별 아파트 월평균 매매가격뿐이라 전세·월세 가격은 답할 수 없습니다. '
                "매매가격은 '서울 아파트 가격'처럼 물어보세요.")
    if VOLUME.search(question):
        return ('거래량·거래 건수·매물 자료는 없습니다. 이 서비스는 지역별 아파트 월평균 매매가격만 제공합니다. '
                "가격 변화는 '서울 최근 1년 변동률'처럼 물어보세요.")
    if OTHER_HOUSING.search(question):
        return ('이 서비스의 자료는 아파트 월평균 매매가격뿐이라 오피스텔·빌라(연립·다세대)·단독주택·상가·토지 가격은 답할 수 없습니다. '
                "아파트 가격은 '서울 아파트 가격'처럼 물어보세요.")
    if INDIVIDUAL.search(question) or COMPLEX.search(question):
        return ('이 서비스의 자료는 시도·시군구 단위의 아파트 월평균 매매가격이라 특정 단지의 가격이나 개별 실거래가는 없습니다. '
                '지역 평균을 단지 가격으로 대신 안내하지 않습니다. '
                "지역 평균은 '강남구 2026년 8월 평균 매매가격'처럼 물어보세요.")
    if FORECAST.search(question) and HOUSING.search(question):
        return ('앞으로의 가격 예측이나 매수·매도·투자 추천은 하지 않습니다. 자료로 확인할 수 있는 것은 지난 가격과 변화입니다. '
                "예: '서울 최근 1년 변동률', '강남구 역대 최고가 대비 현재 수준'.")
    return None


def _response(answer, facts=(), docs=(), **analysis):
    return {'answer': answer, 'facts': list(facts), 'route': 'rule', 'analysis': analysis,
            'contexts': [{'id': d.id, 'text': d.text, 'metadata': d.metadata} for d in docs],
            'sources': [f"[{d.id}] {CSV_PATH.name} · {d.metadata['region_path']} · {d.metadata['month']} · "
                        f"원본 {d.metadata['source_row']}행 · SHA-256 {d.metadata['source_sha256']}" for d in docs]}


def price_filter(question, corpus, unit):
    """'5억 이하 서울 구'처럼 가격 조건에 맞는 지역 목록. 가격 조건이 없으면 None."""
    threshold = find_threshold(question)
    if not threshold:
        return None
    op, value = threshold
    catalog = region_catalog(corpus)
    scope, ambiguous = named_region(question, catalog)
    if ambiguous:
        return _response('같은 이름의 지역이 있습니다. 시도와 시군구를 함께 지정해주세요.', intent='clarify')
    period = parse_period(question, corpus.months)
    month = period.month if period.kind == 'point' and period.month in corpus.months else corpus.months[-1]
    level = find_level(question) or ('시도' if re.search(r'시도|광역', question) else '시군구')
    allowed = {scope['id']} if scope and scope['level'] == '시군구' else ranking_regions(catalog, scope['id'] if scope else None, level)
    docs = [d for d in corpus.documents if d.metadata['kind'] == 'monthly' and d.metadata['month'] == month
            and d.metadata['region_id'] in allowed and d.metadata.get('price_thousand_krw') is not None]
    test = {'>=': lambda p: p >= value, '>': lambda p: p > value, '<=': lambda p: p <= value, '<': lambda p: p < value}[op]
    hits = sorted((d for d in docs if test(d.metadata['price_thousand_krw'])), key=lambda d: d.metadata['price_thousand_krw'])
    where = scope['label'] if scope else '전국'
    head = (f"{month} 기준 {where} {level} 중 아파트 월평균 매매가격이 {format_price(value, unit)} {OP_WORDS[op]}인 곳은 "
            f"{len(hits)}곳입니다 (결측 제외 {len(docs)}곳 중).")
    facts = [f"{d.metadata['region_path']}: {format_price(d.metadata['price_thousand_krw'], unit)}" for d in hits[:LIST_LIMIT]]
    if len(hits) > LIST_LIMIT:
        facts.append(f"외 {len(hits) - LIST_LIMIT}곳 (가격 낮은 순 {LIST_LIMIT}곳만 표시)")
    if hits:
        body = '\n'.join(facts)
    elif docs:
        # 조건에 가장 가까운 곳을 함께 보여 줍니다 ('5억 이하 서울 구' → 가장 싼 도봉구)
        nearest = (min if op in ('<=', '<') else max)(docs, key=lambda d: d.metadata['price_thousand_krw'])
        body = (f"조건에 맞는 지역이 없습니다. 가장 가까운 곳은 {nearest.metadata['region_path']} "
                f"{format_price(nearest.metadata['price_thousand_krw'], unit)}입니다.")
    else:
        body = '조건에 맞는 지역이 없습니다.'
    if period.notes:
        head += ' ' + ' '.join(period.notes)
    return _response(head + '\n\n' + body, facts, hits[:LIST_LIMIT], intent='filter', resolved_month=month,
                     level=level, count=len(hits), candidates=len(docs))
