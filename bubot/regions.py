"""지역 계층/별칭 검증. 모호한 지역을 유사도만으로 선택하지 않습니다."""
import re

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
