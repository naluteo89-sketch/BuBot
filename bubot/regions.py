"""지역 목록과 지역 이름 해석: 계층·별칭 검증, 질문 속 지역 언급 찾기, 질문에 넣을 짧은 이름.

모호한 지역을 유사도만으로 선택하지 않습니다. 같은 이름 지역(중구·광주 등)은 고르지 않고 모호로 돌려줍니다.
"""
import re
from dataclasses import dataclass
from functools import lru_cache

PROVINCES = {'서울','부산','대구','인천','광주','대전','울산','세종','경기','강원','충북','충남','전북','전남','경북','경남','제주'}
ALIASES = {'서울특별시':'서울','부산광역시':'부산','대구광역시':'대구','인천광역시':'인천',
    '광주광역시':'광주','대전광역시':'대전','울산광역시':'울산','세종특별자치시':'세종',
    '경기도':'경기','강원도':'강원','강원특별자치도':'강원','충청북도':'충북','충청남도':'충남',
    '전라북도':'전북','전북특별자치도':'전북','전라남도':'전남','경상북도':'경북','경상남도':'경남','제주특별자치도':'제주',
    '제주도':'제주'}


def region_catalog(corpus):
    result = {}
    for d in corpus.documents:
        m = d.metadata
        if m['region_id'] in result:
            continue
        path = list(dict.fromkeys(m[f'region_{i}'] for i in range(1,5)))
        name = m['region_name']
        level = '전국' if name=='전국' else '시도' if name in PROVINCES else '시군구' if name.endswith(('시','군','구')) and not name.endswith('광역시') else '권역'
        result[m['region_id']] = {'id':m['region_id'], 'name':name, 'path':path, 'label':m['region_path'], 'level':level}
    return result


def resolve_region(name, catalog):
    if not isinstance(name,str) or not name.strip():
        raise ValueError('지역명을 입력해주세요.')
    if name.strip() == "광주광역시":
        matches = [r for r in catalog.values() if r["name"] == "광주" and r["level"] == "시도"]
        if len(matches) == 1:
            return matches[0]["id"]
    for alias, canonical in ALIASES.items():
        name = name.replace(alias,canonical)
    tokens = [t for t in re.split(r'[\s/]+',name.strip()) if t]
    def aliases(value):
        return {value, value[:-1]} if len(value)>2 and value.endswith(('시','군','구')) else {value}
    candidates = [r for r in catalog.values() if tokens[-1] in aliases(r['name'])
                  and all(any(t in aliases(p) for p in r['path']) for t in tokens[:-1])]
    exact = [r for r in candidates if r['name'] == tokens[-1]]
    if len(exact) == 1 and all(exact[0]['name'] in r['path'] for r in candidates):
        # '제주'는 하위 지역 '제주시'의 약칭보다 이름이 같은 시도 '제주'가 우선.
        # '광주'처럼 약칭 후보(경기 광주시)가 다른 시도에 있으면 여전히 모호합니다.
        candidates = exact
    if len(candidates)!=1:
        detail = '; '.join(r['label'] for r in candidates)
        raise ValueError(f"지역 '{name}'을 확정할 수 없습니다. 시도와 시군구를 함께 입력해주세요." + detail)
    return candidates[0]['id']


def ranking_regions(catalog, scope=None, level='시군구'):
    if level not in {'시도','시군구','권역'}:
        raise ValueError('순위 계층은 시도, 시군구, 권역 중 하나입니다.')
    if scope and scope not in catalog:
        raise ValueError('알 수 없는 지역 ID입니다.')
    parent = catalog.get(scope)
    return {r['id'] for r in catalog.values() if r['level']==level and not r['name'].startswith('(구)')
            and (not parent or parent['name']=='전국' or all(p in r['path'] for p in parent['path']))}


def canonical_names(question):
    """질문 속 시도 정식 이름(서울특별시·경기도 등)을 데이터의 짧은 이름으로 바꿉니다."""
    for alias, canonical in ALIASES.items():
        question = question.replace(alias, canonical)
    return question


# 질문 속 지역 언급 찾기 ────────────────────────────────────────

# 질문 속 시도 표기: ALIASES와 '서울시'처럼 '시'를 붙인 표기 ('광주시'는 경기 광주시와 겹쳐 넣지 않습니다)
PROVINCE_FORMS = {**ALIASES, '서울시': '서울', '부산시': '부산', '대구시': '대구', '인천시': '인천', '대전시': '대전',
                  '울산시': '울산', '세종시': '세종'}
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


def named_region(question, catalog):
    """질문에 적힌 지역명 중 가장 구체적인 지역 하나. (지역, 모호 여부)를 돌려줍니다.

    '서울 중구'처럼 상위 지역을 함께 적으면 그 경로를 모두 포함하는 지역을 고릅니다.
    '강남'(강남구), '포항 남구'처럼 줄인 이름·상위 지역 수식도 인식하고, '중구'·'광주'처럼 여러 곳에 있는
    이름은 임의로 고르지 않고 모호(True)로 돌려줍니다. (2026-10-12: query_parser.find_regions 사용)
    """
    if not catalog:
        return None, False
    mentions = [m for m in find_regions(question, catalog) if not m.qualifier]
    if not mentions:
        return None, False
    if any(len(m.candidates) != 1 for m in mentions):
        return None, True
    found = list(dict.fromkeys(m.candidates[0] for m in mentions))
    return (catalog[found[0]], False) if len(found) == 1 else (None, True)


def child_regions(scope, catalog):
    """바로 아래 하위 지역 ('제주' → 제주시·서귀포시, '서울' → 강북지역·강남지역)."""
    depth = scope['label'].count(' / ') + 1
    return [r for r in catalog.values() if r['label'].startswith(scope['label'] + ' / ') and r['label'].count(' / ') == depth]


def short_name(path, scope):
    """'서울 / 강북지역 / 동북권 / 도봉구' → 범위가 서울이면 '도봉구', 전국이면 '서울 도봉구'."""
    parts = path.split(' / ')
    if len(parts) == 1 or (scope and path.startswith(scope['label'] + ' / ')):
        return parts[-1]
    return f'{parts[0]} {parts[-1]}'


def region_text(region, catalog):
    """지역 label을 질문에 넣을 짧은 이름으로 ('전남광주 / 광주 / 북구' → '광주 북구'). 그 지역 하나로만 해석되는 이름을 고릅니다."""
    parts = region['label'].split(' / ')
    # 권역 이름('서남권', '강남지역')은 사람이 쓰는 주소가 아니므로 뺀 이름을 먼저 시도합니다 ('서울 강서구', '광주 북구').
    named = [x for x in parts if not x.endswith(('권', '지역')) and x != '전남광주']
    full = [alias for alias, short in ALIASES.items() if short == region['name'] and region['level'] == '시도']
    forms = [' '.join(named[-2:]), named[-1] if named else '', *full, f'{parts[0]} {parts[-1]}', ' '.join(parts[-2:]), ' '.join(parts)]
    for text in dict.fromkeys(f for f in forms if f):
        found, ambiguous = named_region(text, catalog)
        if found and not ambiguous and found['id'] == region['id']:
            return text
    return None


def address_region(address, catalog):
    """'광주광역시 북구' 같은 주소 → 지역 목록의 지역 하나. 해석이 모호하거나 없으면 None."""
    found, ambiguous = named_region(canonical_names(address or ''), catalog)
    return None if ambiguous else found
