"""일상 표현을 데이터가 이해하는 명시 표현으로 바꿉니다 (LLM 없음, 같은 날짜·데이터면 항상 같은 결과).

- 상대 시점: '지난달', '이번 달', '3개월 전', '반년 전', '작년 이맘때', '작년 말', '올해 초', '지난 분기',
  '지난달보다', '연초 대비', '10년 사이' → '2026년 8월', '2026년 1월부터 2026년 8월까지', '최근 10년'
  · 'N년/N개월 전'·'작년 이맘때'는 데이터 최신 월에서 셉니다 (최근 N년·지역 프로필의 'N년 전'과 같은 기준).
  · '이번 달·지난달·분기·올해 말'은 오늘 날짜 기준이며, 아직 자료가 없으면 최신 월로 바꾸고 안내합니다.
- 지역 별칭: '마용성', '노도강', '강남3구', '분당', '동탄', '송도' 등 → 데이터의 지역명
- 면적 표현: '평당', '국평', '30평' 등은 면적 자료가 없어 계산하지 않고 안내합니다.
바꾼 내용은 notes로 돌려주어 답변 끝에 밝힙니다.
"""
import re

from ..dates import now_kst, shift


def _label(month):
    return f"{month[:4]}년 {int(month[5:7])}월"


# ── 지역 별칭: 동네·생활권 이름 → 데이터 지역명 (괄호는 동 단위 자료가 없다는 안내) ──
REGION_SLANG = [
    (r'강남\s*4\s*구', '강남구, 서초구, 송파구, 강동구', None),
    (r'강남\s*3\s*구', '강남구, 서초구, 송파구', None),
    (r'마용성', '마포구, 용산구, 성동구', None),
    (r'노도강', '노원구, 도봉구, 강북구', None),
    (r'금관구', '금천구, 관악구, 구로구', None),
    (r'분당(?!구)', '성남시 분당구', None),
    (r'동탄(?!구)', '화성시 동탄구', None),
    (r'일산(?![동서]구)', '고양시 일산동구, 일산서구', None),
    (r'판교', '성남시 분당구', '판교'),
    (r'송도', '인천 연수구', '송도'),
    (r'목동', '양천구', '목동'),
    (r'잠실', '송파구', '잠실'),
    (r'반포|서초동', '서초구', None),
    (r'압구정|대치동|개포동', '강남구', None),
    (r'여의도', '영등포구', '여의도'),
    (r'상암동?', '마포구', '상암'),
    (r'평촌', '안양시 동안구', '평촌'),
    (r'산본', '군포시', '산본'),
]
# 여러 시군구에 걸친 생활권: 임의로 고르지 않고 후보를 안내합니다.
AMBIGUOUS_PLACES = {'광교': '수원시 영통구, 용인시 수지구', '위례': '서울 송파구, 성남시 수정구, 하남시',
                    '검단': '인천 서구', '미사': '하남시', '다산': '남양주시'}
# 사건 기준 시점 (근거: 국내 첫 확진 2020-01 → 그 직전 달)
EVENTS = [(r'코로나\s*(?:전|이전|직전)', '2019-12', '코로나19 이전을 2019년 12월로 보았습니다.')]


def _han_river(text, notes):
    """'강남이랑 강북'처럼 둘 다 구 없이 쓰면 한강 남북 권역(강남지역·강북지역)으로 봅니다."""
    south, north = r'강남(?![구지\d]|\s*[34]\s*구)', r'강북(?![구지])'
    if re.search(south, text) and re.search(north, text):
        notes.append("'강남·강북'을 한강 남북 권역(강남지역·강북지역)으로 보았습니다. 구 단위는 강남구·강북구로 적어 주세요.")
        text = re.sub(north, '강북지역', re.sub(south, '강남지역', text))
    return text

AREA = re.compile(r'평당|평단가|국평|국민\s*평형|\d+\s*평(?![균가])\s*(?:형|대)?|평형|평수|'
                  r'\d+\s*(?:㎡|m2|제곱미터)|전용\s*면적')

NUM_WORDS = {'한': 1, '두': 2, '세': 3, '석': 3, '네': 4, '넉': 4, '다섯': 5, '여섯': 6, '일곱': 7, '여덟': 8, '아홉': 9,
             '열': 10, '열한': 11, '열두': 12}
SINO = {'일': 1, '이': 2, '삼': 3, '사': 4, '오': 5, '육': 6, '칠': 7, '팔': 8, '구': 9, '십': 10}
AFTER = r'(?=\s*(?:전|이전|사이|새|간|동안|째|만에|대비|보다))'
COMPARE = r'\s*(?:대비|보다|에\s*비해|와\s*비교|과\s*비교|이랑\s*비교|하고\s*비교)'
NOW_AFTER = r'\s*(?:올해|금년|현재|지금|요즘)?'


def _numbers(text):
    """'두 달', '반년', '일 년' → '2개월', '6개월', '1년'. 기간·시점 표현 앞에서만 바꿉니다."""
    text = re.sub(r'반\s*년' + AFTER, '6개월', text)
    text = re.sub(r'(열한|열두|다섯|여섯|일곱|여덟|아홉|한|두|세|석|네|넉|열)\s*달' + AFTER,
                  lambda m: f"{NUM_WORDS[m[1]]}개월", text)
    text = re.sub(r'(열한|열두|다섯|여섯|일곱|여덟|아홉|한|두|세|네|열)\s*해' + AFTER,
                  lambda m: f"{NUM_WORDS[m[1]]}년", text)
    text = re.sub(r'(?<![가-힣])([일이삼사오육칠팔구십])\s*(년|개월)' + AFTER, lambda m: f"{SINO[m[1]]}{m[2]}", text)
    return re.sub(r'(?<![\d.])(\d+)\s*달' + AFTER, r'\1개월', text)


def rewrite(question, months, today=None):
    """(바꾼 질문, 안내 목록). months: 보유 월 목록(정렬)."""
    today = today or now_kst()
    first, latest = months[0], months[-1]
    this_month = f"{today.year}-{today.month:02d}"
    year = today.year
    notes = []
    text = question

    text = _han_river(text, notes)
    for pattern, target, place in REGION_SLANG:
        if re.search(pattern, text):
            text = re.sub(pattern, target, text)
            if place:
                notes.append(f"동 단위 자료가 없어 '{place}' 대신 {target} 전체 값입니다.")
    for place, target in AMBIGUOUS_PLACES.items():
        if place in text and ',' not in target:
            text = text.replace(place, target)
            notes.append(f"동 단위 자료가 없어 '{place}' 대신 {target} 전체 값입니다.")

    if AREA.search(text):
        text = AREA.sub('', text)
        notes.append('면적 자료가 없어 평당·평형(국평 등)별 가격은 계산할 수 없습니다. '
                     '아래 값은 면적 구분 없는 아파트 월평균 매매가격입니다.')

    text = _numbers(text)
    # '올해 3월', '작년 11월' → 명시 연도 ('작년·올해' 단독은 기존 규칙 year_query가 처리)
    text = re.sub(r'(재작년|작년|지난\s*해|올해|올\s*해|금년)\s*(\d{1,2})\s*월',
                  lambda m: f"{year + {'재작년': -2, '작년': -1, '지난해': -1}.get(m[1].replace(' ', ''), 0)}년 {int(m[2])}월", text)
    # '올해 들어', '연초부터' → 올해 1월 ~ 최신 월
    text = re.sub(r'(?:올해|올|금년)\s*(?:들어(?:서)?|초부터|부터)|연초\s*부터',
                  lambda m: f"{_label(min(f'{year}-01', latest))}부터 {_label(latest)}까지", text)
    # 명시 월이 하나뿐이고 바로 뒤에 '대비·보다'가 오면 그 달 ~ 최신 월
    explicit = list(re.finditer(r'(?<!\d)(\d{4})\s*년\s*(\d{1,2})\s*월(?P<cmp>' + COMPARE + NOW_AFTER + ')', text))
    if len(re.findall(r'(?<!\d)\d{4}\s*년\s*\d{1,2}\s*월', text)) == 1 and explicit:
        m = explicit[0]
        text = text[:m.start()] + f"{m[1]}년 {int(m[2])}월부터 {_label(latest)}까지 " + text[m.end():]

    def clamp(month, word):
        if month > latest:
            notes.append(f"'{word}'({_label(month)}) 자료는 아직 없어 최신 보유 월 {_label(latest)} 기준입니다.")
            return latest
        if month < first:
            notes.append(f"'{word}' → {_label(month)}. 자료는 {_label(first)}부터 있습니다.")
        return month

    def counted(word, month):
        notes.append(f"'{word}' → {_label(month)} (최신 보유 월 {_label(latest)}에서 센 시점)")
        return month

    # 기준 시점 표현 → (정규식, 월 계산). 비교형('…보다', '…대비', '…부터')은 그 시점 ~ 최신 월 범위입니다.
    anchors = [
        (r'(?<![\d.])(\d+)\s*(년|개월)\s*(?:전|이전)', lambda m: counted(m[0], shift(latest, -int(m[1]) * (12 if m[2] == '년' else 1)))),
        (r'(?:작년|지난\s*해|전년)\s*(?:이맘\s*때|이맘|이\s*맘때|같은\s*달|같은\s*때|동월|동기|이\s*시기)',
         lambda m: counted(m[0], shift(latest, -12))),
        (r'(?:재작년)\s*(?:말|연말|12월)', lambda m: clamp(f"{year - 2}-12", m[0])),
        (r'(?:작년|지난\s*해|전년)\s*(?:말|연말|년말)', lambda m: clamp(f"{year - 1}-12", m[0])),
        (r'(?:작년|지난\s*해|전년)\s*(?:초|연초)', lambda m: clamp(f"{year - 1}-01", m[0])),
        (r'(?:올해|올\s*해|금년|이번\s*해)\s*(?:말|연말)', lambda m: clamp(f"{year}-12", m[0])),
        # 연도 없는 '연초·연말' ('2023년 연말'처럼 연도가 앞에 있으면 기존 규칙이 처리)
        (r'(?:올해|올\s*해|금년|이번\s*해|올)\s*(?:초|연초)|(?<![가-힣])(?<!년 )연초', lambda m: clamp(f"{year}-01", m[0])),
        (r'(?<![가-힣])(?<!년 )연말', lambda m: clamp(f"{year - 1}-12", m[0])),
        # '지난달보다'는 최신 월과 그 전 달 비교, '지난달 가격'은 오늘 기준 지난달(없으면 최신 월)
        (r'지난\s*달|저번\s*달|지난번\s*달|전월|전달', lambda m, cmp: shift(latest, -1) if cmp else clamp(shift(this_month, -1), m[0])),
        (r'이번\s*달|이달|당월|금월', lambda m: clamp(this_month, m[0])),
        # 월별 자료라 주·일 단위는 없습니다: 오늘이 속한 달(없으면 최신 월)
        (r'지난\s*주|저번\s*주|이번\s*주|금주|어제|오늘|그저께|그제|엊그제|며칠\s*전|요\s*며칠',
         lambda m: notes.append(f"월별 자료라 '{m[0]}' 같은 주·일 단위 값은 없어 최신 보유 월 {_label(latest)} 기준입니다.")
         or min(this_month, latest)),
        *[(pattern, (lambda month, note: lambda m: notes.append(note) or month)(month, note)) for pattern, month, note in EVENTS],
    ]

    for pattern, compute in anchors:
        def replace(m, pattern=pattern, compute=compute):
            anchor = re.fullmatch(pattern, m['a'])
            cmp = bool(m['cmp'])
            month = compute(anchor, cmp) if compute.__code__.co_argcount == 2 else compute(anchor)
            return f"{_label(month)}부터 {_label(latest)}까지 " if cmp else _label(month)
        text = re.sub(f'(?P<a>{pattern})(?P<cmp>{COMPARE}{NOW_AFTER})?', replace, text)

    # 분기: 오늘 날짜 기준. 자료가 없는 월은 최신 월까지로 줄입니다.
    def quarter(m):
        q = (today.month - 1) // 3 + (0 if '이번' in m[0] else -1)
        y = year + (q // 4)
        q %= 4
        start, end = f"{y}-{q * 3 + 1:02d}", f"{y}-{q * 3 + 3:02d}"
        if start > latest:
            notes.append(f"'{m[0]}'({_label(start)}~{_label(end)}) 자료는 아직 없어 최신 보유 월 {_label(latest)} 기준입니다.")
            return _label(latest)
        if end > latest:
            notes.append(f"'{m[0]}'({_label(start)}~{_label(end)}) 중 {_label(latest)}까지만 자료가 있습니다.")
            end = latest
        return f"{_label(start)}부터 {_label(end)}까지"
    text = re.sub(r'지난\s*분기|전\s*분기|직전\s*분기|이번\s*분기', quarter, text)

    # 'N년 사이·새' → '최근 N년' ('N년간·동안'은 기존 규칙이 기간으로 처리합니다)
    text = re.sub(r'(?:지난\s*)?(?<![\d.])(\d+)\s*(년|개월)\s*(?:사이|새)(?:에)?', r'최근 \1\2', text)
    text = re.sub(r'지난\s*(\d+)\s*(년|개월)(?!\s*\d*\s*월)', r'최근 \1\2', text)
    return re.sub(r'\s+', ' ', text).strip(), list(dict.fromkeys(notes))
