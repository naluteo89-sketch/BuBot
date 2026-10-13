"""답변 아래 선택지 버튼: 같은 이름 지역 후보, 하위 지역 함께 보기. 누르면 보낼 질문을 함께 만듭니다."""
from ..cache.data_cache import load_corpus
from ..regions import child_regions, region_catalog, region_text, short_name

MAX_CHILD_REGIONS = 5   # 하위 지역 버튼을 하나씩 보여줄 최대 개수 (넘으면 '모두 보기' 버튼만)


def candidate_choices(question, name, labels):
    """후보 지역 버튼: 질문 속 모호한 이름(name)을 후보 지역 이름으로 바꾼 질문을 보냅니다."""
    catalog = region_catalog(load_corpus())
    by_label = {r['label']: r for r in catalog.values()}
    choices = []
    for label in labels:
        region = by_label.get(label.strip())
        text = region and region_text(region, catalog)
        if not text:
            continue
        asked = question.replace(name, text, 1) if name and name in question else f'{text} {question}'
        choices.append({'label': text, 'question': ' '.join(asked.split())})
    return choices


def subregion_choices(scope, catalog, period_text, request_text):
    """하위 지역이 있으면 '함께 볼까요?' 선택지 버튼. 누르면 보낼 질문을 함께 만듭니다."""
    children = child_regions(scope, catalog)
    if not children:
        return [], ''
    name = region_text(scope, catalog) or short_name(scope['label'], None)
    choices = [{'label': f'하위 지역 {len(children)}곳 모두 함께 보기',
                'question': f'{period_text} {name} 하위 지역 포함 {request_text}'.strip()}]
    if len(children) <= MAX_CHILD_REGIONS:
        choices += [{'label': f'{short_name(c["label"], scope)}만 보기',
                     'question': f'{period_text} {region_text(c, catalog) or c["label"]} {request_text}'.strip()}
                    for c in children]
    names = '·'.join(short_name(c['label'], scope) for c in children[:MAX_CHILD_REGIONS])
    more = f' 등 {len(children)}곳' if len(children) > MAX_CHILD_REGIONS else ''
    return choices, f'{name}에는 하위 지역({names}{more})이 있습니다. 하위 지역 값도 필요하신가요?'
