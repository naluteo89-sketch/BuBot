"""질문 속 기간·연도 해석 (LLM 없음, 같은 문장·날짜·데이터면 항상 같은 결과).

'2023년 3월', '23년', '작년', '4년 전', '지난달', '최근 3개월', '상반기', 'A부터 B까지', '요즘' 등을 보유 월로 확정합니다.
보유 기간 밖의 시점은 데이터 안으로 맞추되, 질문에 적힌 시점이 보유 기간 뒤면 beyond로 표시합니다.
"""
import re
from dataclasses import dataclass, field

from ..dates import full_year, now_kst, shift


@dataclass
class Period:
    """질문에 적힌 기간. kind: none | point | year | range | now"""
    kind: str = 'none'
    month: str | None = None
    start: str | None = None
    end: str | None = None
    year: str | None = None
    notes: list = field(default_factory=list)
    spans: list = field(default_factory=list)
    beyond: str | None = None  # 질문에 적힌 시점이 보유 기간 뒤 (예: '2027년 3월') → 최신 월로 대신 답하지 않음

    @property
    def explicit(self):
        return self.kind != 'none'


YEAR2 = r'(\d{4}|\d{2})'
DURATION_BEFORE = re.compile(r'(최근|지난|과거|약|무려|향후|앞으로|이후|후)\s*$')
DURATION_AFTER = re.compile(r'\s*(간|동안|전|째|치|만에|이상|이내|넘|여|가량|정도|새|후|차)')


def _year_ok(text, match, group=1):
    """두 자리 숫자+년이 기간('10년간', '최근 10년')이 아니라 연도일 때만 True."""
    value = match.group(group)
    if value is None or len(value) == 4:
        return True
    return not (DURATION_BEFORE.search(text[:match.start(group)]) or DURATION_AFTER.match(text, match.end()))


def _token(kind, start, end, span, **extra):
    return {'kind': kind, 'start': start, 'end': end, 'span': span, **extra}


def _period_tokens(text, today, latest):
    """기간 표현을 위치 순서대로 찾습니다. 앞선 패턴이 차지한 위치는 뒤 패턴이 다시 쓰지 않습니다."""
    this_month = f"{today.year}-{today.month:02d}"
    tokens, taken = [], []

    def free(span):
        return all(span[1] <= a or span[0] >= b for a, b in taken)

    def add(token):
        if free(token['span']):
            taken.append(token['span'])
            tokens.append(token)

    def year_months(y):
        return f"{y}-01", f"{y}-12"

    # '최근 N년'·'N개월간': 최신 월까지의 기간 ('최근' 쪽이 먼저 자리를 차지합니다)
    for pattern in (r'(?:최근|지난)\s*(\d+)\s*(개월|달|년)(?:\s*(?:간|동안))?', r'(?<![\d.])(\d+)\s*(개월|달|년)\s*(?:간|동안)'):
        for m in re.finditer(pattern, text):
            n, unit = int(m[1]), m[2]
            span = n * 12 if unit == '년' else max(n - 1, 0)
            add(_token('range', shift(latest, -span), latest, m.span(), recent=True))
    for m in re.finditer(r'(?:작년|지난\s*해|전년)\s*(?:같은\s*달\s*|동월\s*|이맘때\s*)?(?:대비|보다|와\s*비교|과\s*비교|이랑\s*비교)', text):
        add(_token('range', shift(latest, -12), latest, m.span(), recent=True))
    # 네 자리 연도('2024년 전국')와 '전'으로 시작하는 낱말(전국·전체·전세 등)은 'N년 전'이 아닙니다 (expressions.rewrite와 같은 조건).
    for m in re.finditer(r'(?<![\d.])(\d{1,2})\s*(년|개월|달)\s*(?:전|이전)(?!국|체|세|라|남|북|주|역)', text):
        n, unit = int(m[1]), m[2]
        month = shift(this_month, -(n * 12 if unit == '년' else n))
        add(_token('point', month, month, m.span(), relative=True))
    for m in re.finditer(rf'(?<!\d){YEAR2}\s*년\s*(\d{{1,2}})\s*월(?!\s*세)', text):
        if 1 <= int(m[2]) <= 12:
            month = f"{full_year(m[1])}-{int(m[2]):02d}"
            add(_token('point', month, month, m.span()))
    for m in re.finditer(r'(?<!\d)((?:19|20)\d{2})\s*[-./]\s*(\d{1,2})(?!\d)', text):
        if 1 <= int(m[2]) <= 12:
            month = f"{m[1]}-{int(m[2]):02d}"
            add(_token('point', month, month, m.span()))
    for m in re.finditer(rf'(?:(?<!\d){YEAR2}\s*년\s*)?(상|하)\s*반기', text):
        y = full_year(m[1]) if m[1] else int(latest[:4])
        add(_token('range', f"{y}-{'01' if m[2] == '상' else '07'}", f"{y}-{'06' if m[2] == '상' else '12'}", m.span()))
    for m in re.finditer(rf'(?:(?<!\d){YEAR2}\s*년\s*)?([1-4])\s*분기', text):
        y, q = (full_year(m[1]) if m[1] else int(latest[:4])), int(m[2])
        add(_token('range', f"{y}-{q * 3 - 2:02d}", f"{y}-{q * 3:02d}", m.span()))
    for m in re.finditer(rf'(?<!\d){YEAR2}\s*년\s*(연초|초|연말|말)(?![가-힣])', text):
        y = full_year(m[1])
        month = f"{y}-{'01' if '초' in m[2] else '12'}"
        add(_token('point', month, month, m.span()))
    for m in re.finditer(r'재작년|작년|지난\s*해|지난해|올해|올\s*해|금년|이번\s*해|내년|다음\s*해', text):
        word = m[0].replace(' ', '')
        offset = {'재작년': -2, '작년': -1, '지난해': -1, '올해': 0, '금년': 0, '이번해': 0, '내년': 1, '다음해': 1}[word]
        y = today.year + offset
        add(_token('year', *year_months(y), m.span(), year=str(y)))
    for m in re.finditer(r'지난\s*달|저번\s*달|전월|전달|이번\s*달|이달|당월|다음\s*달', text):
        word = m[0].replace(' ', '')
        delta = {'지난달': -1, '저번달': -1, '전월': -1, '전달': -1, '이번달': 0, '이달': 0, '당월': 0, '다음달': 1}[word]
        month = shift(this_month, delta)
        add(_token('point', month, month, m.span(), relative=True))
    for m in re.finditer(rf'(?<!\d){YEAR2}\s*년(?!\s*\d{{1,2}}\s*월)', text):
        if _year_ok(text, m):
            y = full_year(m[1])
            add(_token('year', *year_months(y), m.span(), year=str(y)))
    for m in re.finditer(r'(?<![\d년])(\d{1,2})\s*월(?!\s*세)', text):
        if 1 <= int(m[1]) <= 12:
            add(_token('month_only', None, None, m.span(), month_number=int(m[1])))
    for m in re.finditer(r'요즘|요새|현재|지금|최근|최신|근래', text):
        add(_token('now', latest, latest, m.span()))
    return sorted(tokens, key=lambda t: t['span'][0])


def _fill_month_only(tokens, latest):
    """'3월부터 8월까지'처럼 연도가 없는 월은 앞쪽 연도를 쓰고, 없으면 최신 월 이전의 가장 최근 그 달입니다."""
    year = None
    for t in tokens:
        if t['kind'] == 'month_only':
            n = t['month_number']
            y = year
            if y is None:
                y = int(latest[:4]) if n <= int(latest[5:7]) else int(latest[:4]) - 1
            month = f"{y}-{n:02d}"
            t.update(kind='point', start=month, end=month, inferred_year=year is None)
        elif t.get('start'):
            year = int(t['start'][:4]) if t['kind'] in ('point', 'year') else year
    # 뒤쪽 토큰에 연도가 있고 앞쪽이 연도 없는 월이면 뒤 연도를 씁니다 ('3월부터 2024년 8월까지').
    for i, t in enumerate(tokens):
        if t.get('inferred_year') and i + 1 < len(tokens) and tokens[i + 1]['kind'] in ('point', 'year'):
            y = tokens[i + 1]['start'][:4]
            t.update(start=f"{y}-{t['start'][5:]}", end=f"{y}-{t['start'][5:]}")
    return tokens


def parse_period(text, months, today=None):
    """질문의 기간 표현을 데이터 월로 확정합니다. months: 보유 월 목록(정렬)."""
    today = today or now_kst()
    first, latest = months[0], months[-1]
    tokens = _fill_month_only(_period_tokens(text, today, latest), latest)
    period = Period(spans=[t['span'] for t in tokens])
    until_now = re.search(r'(?:지금|현재|최근|오늘)\s*까지', text)
    if not tokens:
        return period

    def after(token):
        return text[token['span'][1]:].lstrip()

    def connector(a):
        tail = after(a)
        return tail.startswith(('부터', '에서', '~', '∼', '-', '–', '이후', '와', '과', '하고', '이랑', '랑', '대비'))

    head = tokens[0]
    if len(tokens) >= 2 and (connector(head) or after(tokens[1]).startswith('까지')):
        tail_token = tokens[-1] if after(tokens[-1]).startswith('까지') or len(tokens) == 2 else tokens[1]
        start = head['start']
        end = tail_token['end'] if tail_token['kind'] != 'now' else latest
        if tail_token['kind'] == 'year':
            end = tail_token['end']
        period.kind, period.start, period.end = 'range', start, end
    elif len(tokens) >= 2 and all(t['kind'] in ('point', 'now') for t in tokens[:2]):
        # '2025년 8월과 2026년 8월' 처럼 두 시점: 두 시점 사이 비교
        period.kind, period.start, period.end = 'range', tokens[0]['start'], tokens[1]['end']
    elif after(head).startswith(('부터', '이후', '에서부터')) or until_now:
        period.kind, period.start, period.end = 'range', head['start'], latest
        if head['kind'] == 'now':
            period.kind, period.month, period.start, period.end = 'now', latest, None, None
    elif after(head).startswith(('까지', '이전')):
        period.kind, period.start, period.end = 'range', first, head['end']
    elif head['kind'] == 'year':
        period.kind, period.year = 'year', head['year']
        period.start, period.end = head['start'], head['end']
    elif head['kind'] == 'range':
        period.kind, period.start, period.end = 'range', head['start'], head['end']
    elif head['kind'] == 'now':
        period.kind, period.month = 'now', latest
    else:
        period.kind, period.month = 'point', head['start']
        if head.get('inferred_year'):
            period.notes.append(f"연도가 없어 가장 최근의 {int(head['start'][5:])}월({head['start']})로 봤습니다.")
    return _fit_to_data(period, first, latest)


def _fit_to_data(period, first, latest):
    """보유 기간 밖의 시점을 데이터 안으로 맞추고 안내 문구를 남깁니다."""
    if period.kind == 'point' and period.month:
        if period.month > latest:
            period.beyond = period.month
            period.notes.append(f"{period.month} 자료는 아직 없어 최신 {latest} 기준으로 답합니다.")
            period.month = latest
    elif period.kind == 'range' and period.start and period.end:
        if period.start > period.end:
            period.start, period.end = period.end, period.start
        if period.start > latest:
            period.beyond = period.start
        if period.end > latest:
            period.notes.append(f"{period.end} 자료는 아직 없어 {latest}까지로 계산합니다.")
            period.end = latest
        if period.start < first <= period.end:
            period.notes.append(f"{period.start} 이전 자료는 없어 {first}부터 계산합니다.")
            period.start = first
        if period.start == period.end:
            period.kind, period.month = 'point', period.start
    return period


# 질문에 연도로 적힌 값 ────────────────────────────────────────

YEAR = re.compile(r"(?<![0-9])([0-9]{4}|[0-9]{2})\s*년")
# 두 자리 숫자+년은 기간일 수 있습니다: '최근 10년', '지난 10년간', '10년 전'은 연도가 아닙니다.
# (explicit_years 전용. 위 _year_ok의 DURATION_*와 낱말 목록이 달라 합치면 연도 판정이 바뀝니다.)
YEAR_DURATION_BEFORE = re.compile(r"(최근|지난|과거|약|무려|향후|앞으로)\s*$")
YEAR_DURATION_AFTER = re.compile(r"\s*(간|동안|전|째|치|만|이상|이내|넘|여|가량|정도|새|사이|후)")


def explicit_years(question):
    """질문에 연도로 적힌 값만 돌려줍니다 (네 자리는 항상 연도, 두 자리는 기간 표현이면 제외)."""
    years = set()
    for match in YEAR.finditer(question):
        value = match[1]
        if len(value) == 2 and (YEAR_DURATION_BEFORE.search(question[:match.start()])
                                or YEAR_DURATION_AFTER.match(question, match.end())):
            continue
        years.add(full_year(value))
    return years
