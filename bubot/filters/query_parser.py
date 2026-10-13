"""질문 문장에서 지역·기간·의도 단서를 코드로 추출합니다 (LLM 없음, 같은 문장은 항상 같은 결과).

rag_service는 여기서 확정된 값을 LLM 해석보다 우선합니다. 확정하지 못한 부분만 LLM 해석을 씁니다.
- 기간: '2023년 3월', '23년', '작년', '4년 전', '지난달', '최근 3개월', '상반기', 'A부터 B까지', '요즘' 등
- 지역: 정식 이름·약칭('강남'→강남구)·상위 지역 수식('서울 중구', '경기 광주')·'전라도 광주'·같은 이름 지역의 후보
- 의도 단서: 비교·추이·순위·시점(언제 가장 쌌나)·보유 기간·가격 조건(3억 이상)·기간 평균·지원하지 않는 질문
"""
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache

KST = timezone(timedelta(hours=9))


def shift(month, delta):
    y, m = int(month[:4]), int(month[5:7])
    total = y * 12 + (m - 1) + delta
    return f"{total // 12}-{total % 12 + 1:02d}"


def months_between(start, end):
    return (int(end[:4]) * 12 + int(end[5:7])) - (int(start[:4]) * 12 + int(start[5:7]))


def normalize(text):
    """전각 숫자·공백을 정리합니다. '2023년도'는 '2023년'으로 봅니다."""
    text = unicodedata.normalize('NFKC', text or '')
    text = text.replace('년도', '년')
    return re.sub(r'\s+', ' ', text).strip()


# ─────────────────────────────────────────────────────────────
# 기간
# ─────────────────────────────────────────────────────────────

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


def _year(value):
    return int(value) + (2000 if len(value) == 2 else 0)


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

    for m in re.finditer(r'(?:최근|지난)\s*(\d+)\s*(개월|달|년)(?:\s*(?:간|동안))?', text):
        n, unit = int(m[1]), m[2]
        span = n * 12 if unit == '년' else max(n - 1, 0)
        add(_token('range', shift(latest, -span), latest, m.span(), recent=True))
    for m in re.finditer(r'(?<![\d.])(\d+)\s*(개월|달|년)\s*(?:간|동안)', text):
        n, unit = int(m[1]), m[2]
        span = n * 12 if unit == '년' else max(n - 1, 0)
        add(_token('range', shift(latest, -span), latest, m.span(), recent=True))
    for m in re.finditer(r'(?:작년|지난\s*해|전년)\s*(?:같은\s*달\s*|동월\s*|이맘때\s*)?(?:대비|보다|와\s*비교|과\s*비교|이랑\s*비교)', text):
        add(_token('range', shift(latest, -12), latest, m.span(), recent=True))
    for m in re.finditer(r'(?<![\d.])(\d+)\s*(년|개월|달)\s*(?:전|이전)', text):
        n, unit = int(m[1]), m[2]
        month = shift(this_month, -(n * 12 if unit == '년' else n))
        add(_token('point', month, month, m.span(), relative=True))
    for m in re.finditer(rf'(?<!\d){YEAR2}\s*년\s*(\d{{1,2}})\s*월(?!\s*세)', text):
        if 1 <= int(m[2]) <= 12:
            month = f"{_year(m[1])}-{int(m[2]):02d}"
            add(_token('point', month, month, m.span()))
    for m in re.finditer(r'(?<!\d)((?:19|20)\d{2})\s*[-./]\s*(\d{1,2})(?!\d)', text):
        if 1 <= int(m[2]) <= 12:
            month = f"{m[1]}-{int(m[2]):02d}"
            add(_token('point', month, month, m.span()))
    for m in re.finditer(rf'(?:(?<!\d){YEAR2}\s*년\s*)?(상|하)\s*반기', text):
        y = _year(m[1]) if m[1] else int(latest[:4])
        add(_token('range', f"{y}-{'01' if m[2] == '상' else '07'}", f"{y}-{'06' if m[2] == '상' else '12'}", m.span()))
    for m in re.finditer(rf'(?:(?<!\d){YEAR2}\s*년\s*)?([1-4])\s*분기', text):
        y, q = (_year(m[1]) if m[1] else int(latest[:4])), int(m[2])
        add(_token('range', f"{y}-{q * 3 - 2:02d}", f"{y}-{q * 3:02d}", m.span()))
    for m in re.finditer(rf'(?<!\d){YEAR2}\s*년\s*(연초|초|연말|말)(?![가-힣])', text):
        y = _year(m[1])
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
            y = _year(m[1])
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
    today = today or datetime.now(KST)
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


def year_point(year, months):
    """연도만 지정된 단일 시점: 그 해의 마지막 보유 월."""
    in_year = [m for m in months if m.startswith(f"{year}-")]
    return in_year[-1] if in_year else None


def year_range(year, months):
    """연도만 지정된 추이: 전년 12월(없으면 그 해 첫 보유 월) ~ 그 해 마지막 보유 월."""
    in_year = [m for m in months if m.startswith(f"{year}-")]
    if not in_year:
        return None, None
    previous = f"{int(year) - 1}-12"
    return (previous if previous in months else in_year[0]), in_year[-1]


# ─────────────────────────────────────────────────────────────
# 지역
# ─────────────────────────────────────────────────────────────

PROVINCE_FORMS = {
    '서울특별시': '서울', '서울시': '서울', '부산광역시': '부산', '부산시': '부산', '대구광역시': '대구', '대구시': '대구',
    '인천광역시': '인천', '인천시': '인천', '광주광역시': '광주', '대전광역시': '대전', '대전시': '대전',
    '울산광역시': '울산', '울산시': '울산', '세종특별자치시': '세종', '세종시': '세종', '경기도': '경기',
    '강원도': '강원', '강원특별자치도': '강원', '충청북도': '충북', '충청남도': '충남', '전라북도': '전북',
    '전북특별자치도': '전북', '전라남도': '전남', '경상북도': '경북', '경상남도': '경남',
    '제주특별자치도': '제주', '제주도': '제주',
}
# 흔한 낱말과 겹쳐 약칭(시·군·구를 뗀 이름)으로는 찾지 않는 지역 ('상당히', '수정해줘', '사상 최고' 등)
RISKY_STEMS = {'수정', '동안', '상당', '사상', '수영', '연수', '구리', '서북', '동남', '남동', '중원', '서원', '청원',
               '단원', '장안', '처인', '성산', '의창', '진해', '상록', '덕양', '완산', '덕진', '권선', '팔달', '만안',
               '흥덕', '효행', '병점', '만세', '서해', '동해', '광명', '남원', '정읍', '여주', '양주', '김포', '군포'}
# 시·군·구를 뗀 약칭은 유지하되 '광명시' 같이 쓸 때만 찾는 지역은 위 목록에 둡니다 (오탐보다 미탐이 안전).
PARTICLES = ('이랑', '하고', '에서', '으로', '까지', '부터', '보다', '처럼', '이나', '은', '는', '이', '가', '의', '와', '과',
             '랑', '도', '에', '로', '를', '을', '만', '나', '며', '중', '별', '쪽', '지역', '권', '일대', '내', '안',
             '전체', '쪽', '시내', '아파트', '가격', '집값', '시세', '평균', '매매', '부동산')
JEOLLA_QUALIFIERS = ('전라도', '전라남도', '전남', '전라', '호남')


@dataclass
class Mention:
    text: str
    span: tuple
    candidates: list
    exact: bool
    qualifier: bool = False


@lru_cache(maxsize=8)
def _forms(catalog_key):
    """지역 이름 표기 → 후보 id 목록. catalog_key: ((id, name, level, path), ...)"""
    forms = {}

    def add(form, rid, exact):
        entry = forms.setdefault(form, {'exact': [], 'stem': []})
        entry['exact' if exact else 'stem'].append(rid)

    for rid, name, level, path in catalog_key:
        add(name, rid, True)
        if level == '시도':
            for long_form, short in PROVINCE_FORMS.items():
                if short == name:
                    add(long_form, rid, True)
        clean = name.replace('(구)', '')
        if len(clean) >= 3 and clean.endswith(('시', '군', '구')) and not name.startswith('(구)'):
            stem = clean[:-1]
            if stem not in RISKY_STEMS:
                add(stem, rid, False)
        for suffix in ('지역', '권'):
            if level == '권역' and name.endswith(suffix) and len(name) - len(suffix) >= 2 and name not in ('수도권', '지방권'):
                pass  # 권역은 정식 이름으로만 찾습니다 ('강남'은 강남구, '강남지역'은 권역)
    pattern = re.compile('|'.join(re.escape(f) for f in sorted(forms, key=len, reverse=True)))
    return forms, pattern


def catalog_key(catalog):
    return tuple((r['id'], r['name'], r['level'], tuple(r['path'])) for r in catalog.values())


def _boundary_ok(text, start, end, forms):
    """지역 이름 앞뒤가 낱말 경계인지 ('서울대'·'상당히'·'수원지' 제외, '서울중구'처럼 붙여 쓴 지역은 허용)."""
    before = text[:start]
    if before and '가' <= before[-1] <= '힣':
        if not any(before.endswith(f) for f in forms if len(f) >= 2):
            return False
    rest = text[end:]
    if not rest or not ('가' <= rest[0] <= '힣'):
        return True
    return rest.startswith(PARTICLES) or any(rest.startswith(f) for f in forms if len(f) >= 2)


def find_regions(text, catalog):
    """질문에 적힌 지역 언급을 찾고, 상위 지역 수식('서울 중구')과 같은 이름 지역의 후보를 정리합니다."""
    key = catalog_key(catalog)
    forms, pattern = _forms(key)
    mentions = []
    for m in pattern.finditer(text):
        if not m[0] or m[0] not in forms:  # 지역 목록이 비었을 때 빈 패턴이 빈 문자열과 일치하는 경우
            continue
        if not _boundary_ok(text, m.start(), m.end(), forms):
            continue
        entry = forms[m[0]]
        exact, stem = entry['exact'], entry['stem']
        candidates = list(dict.fromkeys(exact + stem))
        if exact and stem and all(any(catalog[e]['name'] in catalog[s]['path'] for e in exact) for s in stem):
            candidates = list(exact)  # '제주' → 시도 제주 (제주시는 그 하위 지역)
        mentions.append(Mention(m[0], m.span(), candidates, bool(exact)))
    # 상위 지역 수식: 바로 앞(공백·'의'만 사이)에 적힌 지역의 하위 지역만 남깁니다.
    for i in range(1, len(mentions)):
        prev, cur = mentions[i - 1], mentions[i]
        gap = text[prev.span[1]:cur.span[0]]
        if gap.strip(' 의') != '':
            continue
        narrowed = [c for c in cur.candidates if any(catalog[p]['name'] in catalog[c]['path'] for p in prev.candidates if p != c)]
        if narrowed:
            ancestors = [p for p in prev.candidates if any(catalog[p]['name'] in catalog[c]['path'] for c in narrowed if p != c)]
            cur.candidates, prev.candidates, prev.qualifier = narrowed, ancestors or prev.candidates, True
    # '전라도 광주', '전남 광주': 광주광역시 (경기 광주시는 전라도에 없음)
    for m in mentions:
        if len(m.candidates) > 1 and any(catalog[c]['name'] == '광주' for c in m.candidates):
            before = text[:m.span[0]].rstrip(' 의')
            if before.endswith(JEOLLA_QUALIFIERS):
                m.candidates = [c for c in m.candidates if catalog[c]['name'] == '광주']
                for q in mentions:
                    if q.span[1] <= m.span[0] and before.endswith(q.text):
                        q.qualifier = True
    # 같은 이름 지역(중구 등)은 질문의 다른 곳에 적힌 시도로 좁힙니다 ('부산 남구와 동구').
    provinces = {c for m in mentions if len(m.candidates) == 1 for c in m.candidates if catalog[c]['level'] == '시도'}
    for m in mentions:
        if len(m.candidates) > 1 and provinces:
            narrowed = [c for c in m.candidates if any(catalog[p]['name'] in catalog[c]['path'] for p in provinces)]
            if len(narrowed) == 1:
                m.candidates = narrowed
    return mentions


# ─────────────────────────────────────────────────────────────
# 의도 단서·가격 조건·계층
# ─────────────────────────────────────────────────────────────

CUES = [
    ('unsupported', r'전세|월세|임대료|거래량|거래\s*건수|매물\s*수|몇\s*건|예측|전망|오를까|내릴까|떨어질까|오를지|내릴지|살까|사도\s*될까|투자|추천'),
    ('coverage', r'언제부터|언제까지|보유\s*기간|데이터\s*(?:기간|범위)|자료\s*(?:기간|범위)|몇\s*년\s*치|기간이\s*(?:어떻게|어디)|어디까지\s*(?:있|나와)'),
    ('extreme_time', r'(?:언제|시점|시기|몇\s*월|몇\s*년|기간|때)[^?.!]{0,12}(?:가장|제일|최고|최저|최대|최소)'
                     r'|(?:가장|제일|최고|최저|최대|최소)[^?.!]{0,14}(?:언제|시점|시기|기간|때|년도|달)'),
    ('filter', r'\d+(?:\.\d+)?\s*(?:억|천만|천\s*만|백만|만)\s*(?:\d+\s*천\s*만?\s*)?원?\s*(?:이상|초과|넘|이하|미만|아래|밑|이내|안\s*되)'),
    ('average', r'연\s*평균|연간\s*평균|기간\s*평균|평균\s*(?:을|은)?\s*(?:내|구해|계산)|(?:년|개월|기간|동안)\s*의?\s*평균(?!\s*매매)'),
    ('argmax', r'가장\s*(?:비싼|비쌌|비싸|높은|높았|높)|제일\s*(?:비싼|비쌌|비싸|높은|높)|최고가?|1\s*위|상위|top|TOP|Top|높은\s*순|비싼\s*순'
               r'|순위|랭킹'),
    ('argmin', r'가장\s*(?:싼|쌌|싸|저렴|낮은|낮았|낮)|제일\s*(?:싼|쌌|싸|저렴|낮은|낮)|최저가?|꼴찌|하위|낮은\s*순|싼\s*순|저렴한\s*순'),
    ('compare', r'비교|차이|vs|VS|보다\s*(?:비싸|싸|높|낮)|어디가\s*더|어느\s*(?:곳|지역|쪽)이\s*더|각각|견줘'),
    ('trend', r'추이|흐름|변화|변동|오른|올랐|오름|내린|내렸|떨어|상승|하락|증감|등락|얼마나\s*(?:올|내|변|떨)|몇\s*(?:%|퍼)'
              r'|폭락|폭등|급등|급락|떡상|떡락|반등'),
    ('explain', r'뭐야|무엇|무슨\s*뜻|의미|정의|어떻게\s*(?:계산|구해|산출)|설명|어때|어떤가|동향|분위기|상황|전반|요약'),
]
LEVEL_HINTS = [('시도', r'시도|시·도|광역\s*시도|광역자치단체'), ('권역', r'권역'),
               ('시군구', r'시군구|시·군·구|자치구|(?<![가-힣])구\s*(?:중|별|가운데|들)|(?<![가-힣])(?:시|군)\s*(?:중|별|가운데|들)')]
ENUMERATION = re.compile(r'(?:모든|전체|각|전|(\d+)\s*개)\s*(?:의\s*)?(자치구|구|군|시군구|시·군·구|시도|시·도)|(자치구|구|시군구|시도|시·도)\s*별')
UNIT_TO_THOUSAND = {'억': 100000, '천만': 10000, '천 만': 10000, '백만': 1000, '만': 10}
THRESHOLD = re.compile(r'(\d+(?:\.\d+)?)\s*(억|천\s*만|백만|만)\s*(?:(\d+)\s*천\s*만?\s*)?원?\s*'
                       r'(이상|초과|넘는|넘은|넘게|넘|이하|미만|아래|밑|이내|안\s*되는|안되는)')
TOP_N = re.compile(r'(?:top|TOP|Top|상위|하위)\s*(\d+)|(\d+)\s*(?:곳|개\s*지역|위\s*까지|개\s*시군구|개\s*구)')


def find_cues(text):
    """의도 단서를 질문에 나온 순서대로 돌려줍니다."""
    found = []
    for name, pattern in CUES:
        m = re.search(pattern, text)
        if m:
            found.append((m.start(), name))
    return [name for _, name in sorted(found)]


def find_threshold(text):
    m = THRESHOLD.search(text)
    if not m:
        return None
    value = float(m[1]) * UNIT_TO_THOUSAND[m[2].replace(' ', ' ') if m[2] != '천 만' else '천 만']
    if m[3]:
        value += int(m[3]) * 10000  # '3억 5천' = 3억 5천만
    word = m[4].replace(' ', '')
    op = '>=' if word == '이상' else '>' if word in ('초과', '넘는', '넘은', '넘게', '넘') else \
        '<=' if word in ('이하', '이내') else '<'
    return op, value


def find_level(text):
    for level, pattern in LEVEL_HINTS:
        if re.search(pattern, text):
            return level
    return None


def find_top_n(text):
    m = TOP_N.search(text)
    if not m:
        return None
    n = int(m[1] or m[2])
    return n if 1 <= n <= 50 else None


# ─────────────────────────────────────────────────────────────
# 질문 프레임
# ─────────────────────────────────────────────────────────────

@dataclass
class QueryFrame:
    text: str
    targets: list = field(default_factory=list)        # 확정된 지역 id (질문 순서)
    ambiguous: list = field(default_factory=list)      # [(적힌 이름, 후보 id 목록)]
    scope: str | None = None                           # 순위·목록·조건 검색 범위
    level: str | None = None
    enumerate: bool = False                            # '서울 25개 구'처럼 하위 지역 전체
    period: Period = field(default_factory=Period)
    cues: list = field(default_factory=list)
    intent: str | None = None                          # 코드로 확정한 의도 (확정 못 하면 None)
    threshold: tuple | None = None
    top_n: int | None = None


RANK_INTENTS = {'argmax', 'argmin', 'filter'}


def parse_question(question, catalog, months, today=None):
    """질문을 지역·기간·의도 단서로 나눕니다. 결과는 문장과 데이터에만 의존합니다."""
    text = normalize(question)
    mentions = find_regions(text, catalog)
    frame = QueryFrame(text=text, period=parse_period(text, months, today), cues=find_cues(text),
                       threshold=find_threshold(text), top_n=find_top_n(text), level=find_level(text))
    for m in mentions:
        if m.qualifier:
            continue
        if len(m.candidates) == 1:
            if m.candidates[0] not in frame.targets:
                frame.targets.append(m.candidates[0])
        elif m.candidates:
            frame.ambiguous.append((m.text, m.candidates))
    enum = ENUMERATION.search(text)
    if enum:
        word = enum[2] or enum[3] or ''
        frame.enumerate = True
        frame.level = '시도' if '시도' in word or '시·도' in word else '시군구'
    if frame.threshold and 'filter' not in frame.cues:
        frame.cues.append('filter')
    frame.intent = decide_intent(frame, catalog)
    if frame.intent in RANK_INTENTS or frame.enumerate:
        # 순위·조건 검색·하위 지역 전체: 질문에 적힌 가장 넓은 지역이 범위, 나머지는 대상이 아닙니다.
        if frame.targets:
            frame.scope = min(frame.targets, key=lambda i: len(catalog[i]['path']))
            frame.targets = [] if frame.intent in RANK_INTENTS or frame.enumerate else frame.targets
    return frame


def decide_intent(frame, catalog):
    """의도 단서로 의도를 확정합니다. 단서가 부족하거나 서로 부딪히면 None (LLM 해석 사용)."""
    cues, has_regions = frame.cues, bool(frame.targets)
    period = frame.period
    if 'unsupported' in cues:
        return 'unsupported'
    if 'coverage' in cues:
        return 'coverage'
    if 'extreme_time' in cues and (has_regions or not frame.enumerate) and period.kind not in ('point',):
        return 'extreme_time'
    if 'filter' in cues and frame.threshold:
        return 'filter'
    if 'average' in cues and period.kind in ('year', 'range'):
        return 'period_average'
    ranks = [c for c in cues if c in ('argmax', 'argmin')]
    if ranks:
        return ranks[0]
    if frame.enumerate:
        return 'trend' if 'trend' in cues and period.kind == 'range' else 'compare'
    if 'compare' in cues and len(frame.targets) >= 2:
        return 'trend' if period.kind == 'range' and 'trend' in cues else 'compare'
    if 'trend' in cues:
        if period.kind == 'range' and has_regions:
            return 'trend'
        if has_regions or 'explain' in cues:
            return 'explain'
        return None
    if 'explain' in cues:
        return 'explain'
    if len(frame.targets) >= 2:
        return 'trend' if period.kind == 'range' else 'compare'
    if has_regions:
        return 'trend' if period.kind == 'range' else 'lookup'
    return None
