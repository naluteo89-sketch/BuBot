"""날짜·월 계산 공용 함수: 한국 시간(KST) 현재 시각, 'YYYY-MM' 월 이동·개월 수·표기, 두 자리 연도, 그 해의 보유 월."""
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def now_kst():
    """한국 시간 현재 시각."""
    return datetime.now(KST)


def today_text():
    """한국 시간 오늘 날짜 (YYYY-MM-DD)."""
    return now_kst().strftime('%Y-%m-%d')


def this_year(today=None):
    """today(없으면 한국 시간 현재)의 연도."""
    return (today or now_kst()).year


def full_year(value):
    """연도 표기 → 네 자리 연도. 두 자리는 2000년대로 봅니다 ('26' → 2026)."""
    return int(value) + (2000 if len(value) == 2 else 0)


def shift(month, delta):
    """'YYYY-MM' 월을 delta개월 이동한 월."""
    total = int(month[:4]) * 12 + int(month[5:7]) - 1 + delta
    return f"{total // 12}-{total % 12 + 1:02d}"


def months_between(start, end):
    """두 월(YYYY-MM) 사이 개월 수 (end − start)."""
    return (int(end[:4]) * 12 + int(end[5:7])) - (int(start[:4]) * 12 + int(start[5:7]))


def months_in_year(months, year):
    """보유 월 목록 중 그 해(year: 2025 또는 '2025')의 월."""
    return [m for m in months if m.startswith(f"{year}-")]


def month_label(month):
    """'YYYY-MM' → 'YYYY년 M월'."""
    return f"{month[:4]}년 {int(month[5:7])}월"


def year_point(year, months):
    """연도만 지정된 단일 시점: 그 해의 마지막 보유 월."""
    in_year = months_in_year(months, year)
    return in_year[-1] if in_year else None


def year_range(year, months):
    """연도만 지정된 추이: 전년 12월(없으면 그 해 첫 보유 월) ~ 그 해 마지막 보유 월."""
    in_year = months_in_year(months, year)
    if not in_year:
        return None, None
    previous = f"{int(year) - 1}-12"
    return (previous if previous in months else in_year[0]), in_year[-1]
