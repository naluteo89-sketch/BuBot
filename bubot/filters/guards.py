"""답하기 전 안내 (LLM 없음): 자료로 답할 수 없거나 확인이 필요한 질문을 GPT 호출 전에 걸러 안내합니다.

- 자료 밖 질문: 전세·월세, 거래량, 예측·투자 추천, 아파트 외 주택, 특정 단지·개별 실거래
- 보유 기간 밖 시점: '2027년 3월'처럼 질문에 적힌 시점이 최신 월보다 뒤 (최신 월 값으로 대신 답하지 않음)
- 확인 질문: '광주'(광주광역시·경기 광주시), 일부 월만 있는 연도의 보유 범위
"""
import re

from ..answers.answer_format import clarify_answer
from ..config import CSV_PATH
from ..dates import months_in_year, this_year
from .period_parser import explicit_years, parse_period

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


def unavailable(question, months):
    """자료로 답할 수 없는 질문을 GPT 호출 전에 안내합니다. 해당 없으면 None.

    - 특정 단지·개별 실거래가 (자료는 지역 월평균): '래미안 실거래가'에 강남구 평균을 단지 가격처럼 답하던 문제
    - 보유 기간 뒤의 시점: '2027년 3월'에 최신 2026-08 값으로 대신 답하던 문제
    후속 질문 재구성(GPT)이 '실거래가'를 '매매가격'으로 바꾸기 전, 사용자가 쓴 원래 질문에도 적용합니다.
    """
    text = out_of_scope(question)
    if text:
        return clarify_answer(text, 'out_of_scope')
    written = parse_period(question, months)
    if written.beyond:
        when = f'{int(written.beyond[:4])}년 {int(written.beyond[5:])}월'
        text = (f'{when}({written.beyond}) 자료는 없습니다. 보유 기간은 {months[0]} ~ {months[-1]}이며, '
                '보유 기간 이후의 가격은 예측하거나 최신 월 값으로 대신 답하지 않습니다. '
                f"최신 값은 '{int(months[-1][:4])}년 {int(months[-1][5:])}월 가격'처럼 보유한 월로 물어보세요.")
        if '지수' in question:
            text += ' 참고로 이 자료는 매매가격지수가 아니라 지역별 아파트 월평균 매매가격입니다.'
        return clarify_answer(text, 'beyond_data', requested=written.beyond)
    return None


def gwangju_clarification(question):
    if '광주' not in question:
        return None
    # 경기도 광주시에는 구가 없으므로 '광주 동구'처럼 구가 붙으면 광주광역시입니다.
    explicit = ('광주광역시' in question or bool(re.search(r'경기(?:도)?\s*광주', question))
                or bool(re.search(r'광주\s*(?:동구|서구|남구|북구|광산구)', question)))
    conflict = bool(re.search(r'전(?:라)?남(?:도)?\s*광주', question))
    if explicit and not conflict:
        return None
    return ('광주광역시와 경기도 광주시 중 어느 지역을 말씀하시나요? '
            '지역을 포함한 질문으로 다시 입력해 주세요. '
            '예: “광주광역시 2026년 8월 평균매매가격” 또는 “경기도 광주시 2026년 8월 평균매매가격”.')


def year_coverage(question, months, *, today=None):
    years = explicit_years(question)
    current = this_year(today)
    for word, offset in (("올해", 0), ("작년", -1), ("지난해", -1), ("재작년", -2), ("내년", 1)):
        if word in question and not (word == "작년" and "재작년" in question):
            years.add(current + offset)
    # 월이 명시된 조회는 기존 월별 조회로 넘깁니다.
    if not years or re.search(r"[0-9]+\s*월|[0-9]{4}-[0-9]{2}", question):
        return None
    complete = {y for y in years if len({m for m in months if m.startswith(f"{y}-")}) == 12}
    present = {y for y in years if any(m.startswith(f"{y}-") for m in months)}
    whole_year = re.search(r"평균|합산|합계|총|연간|전체|자료|데이터|보유", question)
    # 전체 연도가 있으면 RAG 라우터(연간 시장 요약 검색 등)로 넘깁니다.
    if years <= complete or (years <= present and not whole_year):
        return None
    coverage = []
    lines = []
    for year in sorted(years):
        available = sorted({int(m[5:7]) for m in months_in_year(months, year)})
        missing = [m for m in range(1, 13) if m not in available]
        coverage.append({"year": year, "available_months": available, "missing_months": missing})
        label = "·".join(f"{m}월" for m in available)
        if not available:
            lines.append(f"{year}년에 해당하는 데이터는 현재 원본 CSV에 없습니다.")
        elif missing:
            absent = "·".join(f"{m}월" for m in missing)
            lines.append(f"{year}년 데이터는 {label}만 보유하고 있습니다(12개월 중 {len(available)}개월). "
                         f"{absent}은 제공된 원본 CSV에 포함되어 있지 않습니다. "
                         f"{year}년 평균은 보유한 {len(available)}개월 값의 합 ÷ {len(available)}로 계산한 "
                         f"{year}년 {available[-1]}월까지의 평균입니다.")
        else:
            lines.append(f"{year}년은 1월부터 12월까지 데이터가 있습니다.")
    lines.append(f"전체 보유 기간은 {months[0]} ~ {months[-1]}입니다. 지역별로 값이 비어 있는 월은 추가로 있을 수 있습니다.")
    lines.append("이 자료는 지역별 아파트 월평균 매매가격이며, 개별 거래 금액이나 거래 건수 자료가 아닙니다.")
    if any(c["available_months"] for c in coverage):
        lines.append("조회할 지역을 알려주시면 보유 월의 가격이나 보유 월까지의 평균을 계산해 드립니다. "
                     "평균은 월평균 가격의 단순 평균이며, 실제 거래 전체의 평균과는 다릅니다.")
    return {"answer": "\n\n".join(lines),
            "analysis": {"intent": "clarify", "reason": "year_coverage", "coverage": coverage},
            "contexts": [], "facts": [],
            "sources": [f"{CSV_PATH.name} · 월 헤더 기준 보유 범위 확인 (가격 조회 전)"]}
