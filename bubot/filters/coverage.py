"""질문에 명시된 연도와 원본 CSV의 보유 월을 먼저 대조합니다."""
import re
from datetime import datetime, timezone, timedelta

from ..config import CSV_PATH

YEAR = re.compile(r"(?<![0-9])([0-9]{4}|[0-9]{2})\s*년")
# 두 자리 숫자+년은 기간일 수 있습니다: '최근 10년', '지난 10년간', '10년 전'은 연도가 아닙니다.
DURATION_BEFORE = re.compile(r"(최근|지난|과거|약|무려|향후|앞으로)\s*$")
DURATION_AFTER = re.compile(r"\s*(간|동안|전|째|치|만|이상|이내|넘|여|가량|정도|새|사이|후)")


def explicit_years(question):
    """질문에 연도로 적힌 값만 돌려줍니다 (네 자리는 항상 연도, 두 자리는 기간 표현이면 제외)."""
    years = set()
    for match in YEAR.finditer(question):
        value = match[1]
        if len(value) == 2 and (DURATION_BEFORE.search(question[:match.start()])
                                or DURATION_AFTER.match(question, match.end())):
            continue
        years.add(int(value) + (2000 if len(value) == 2 else 0))
    return years


def year_coverage(question, months, *, today=None):
    years = explicit_years(question)
    current = (today or datetime.now(timezone(timedelta(hours=9)))).year
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
        available = sorted({int(m[5:7]) for m in months if m.startswith(f"{year}-")})
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
