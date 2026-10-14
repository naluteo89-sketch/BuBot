"""장소 이름 → 지역: 여러 시군구에 걸친 생활권(광교 등)은 후보를 안내하고,
행정구역 이름이 없는 장소(경기장·역·공항 등)는 소재지를 LLM 일반 지식으로 확인해 시군구로 바꿉니다.
"""
import re

from ..answers.answer_format import clarify_answer
from ..answers.choices import candidate_choices
from ..cache.data_cache import load_corpus
from ..llm import gpt_api
from ..regions import address_region, canonical_names, find_regions, region_catalog, region_text
from .expressions import AMBIGUOUS_PLACES

PLACE_PRICE = re.compile(r'집값|시세|가격|매매|아파트|얼마|올랐|내렸|변동|추이|평균|비싸|싸')
NEARBY = r'\s*(?:근처|주변|인근|부근|근방|일대|쪽|앞|옆|동네|일원)?(?:의|에|에서)?'
# 행정구역 이름이 없는 질문에서 장소를 가리키는 표현. 있으면 질문 분석 전에 소재지를 확인합니다.
PLACE_WORDS = re.compile(r'근처|주변|인근|부근|근방|일대|일원|공항|(?<![지구영전권무])역(?:\b|(?=\s|$|앞|근처|주변|인근))|야구장|축구장|경기장|구장|'
                         r'필드|파크|스타디움|돔|대학|캠퍼스|병원|공원|타워|해수욕장|터미널|청사|시청|구청|신도시|단지|랜드|월드|센터')


def ambiguous_place(question):
    """'광교 집값'처럼 여러 시군구에 걸친 생활권이면 후보 지역 선택지를 붙인 확인 답변. 아니면 None."""
    for place, candidates in AMBIGUOUS_PLACES.items():
        if place in question and ',' in candidates:
            catalog = region_catalog(load_corpus())
            labels = []
            for name in candidates.split(','):
                region = address_region(name.strip(), catalog)  # '수원시 영통구' → 지역 label
                labels.append(region['label'] if region else name.strip())
            return clarify_answer(f"'{place}'은(는) 여러 시군구에 걸친 생활권이고 동 단위 자료가 없습니다. "
                                  f"다음 중 하나를 골라 다시 물어보세요: {candidates}.", 'out_of_scope',
                                  candidate_choices(question, place, labels))
    return None


def _place_question(question, kwargs):
    """지역 ID·지역명 없이 가격을 묻는 질문인지 (장소 이름일 수 있음)."""
    return (not kwargs.get('region_id') and PLACE_PRICE.search(question)
            and not find_regions(canonical_names(question), region_catalog(load_corpus())))


def mentions_place(question, kwargs):
    return PLACE_WORDS.search(question) and _place_question(question, kwargs)


def _clarify(answer, reason, choices=()):
    return 'answer', clarify_answer(answer, reason, choices), None


def locate(question, kwargs):
    """데이터에 없는 장소 이름을 LLM 일반 지식으로 시군구에 대응시킵니다.

    질문에 지역명이 이미 있으면 호출하지 않습니다(일반 질문은 API 호출이 늘지 않음). LLM이 답한 주소는
    지역 목록의 지역 하나로 해석되는지, 질문에 넣을 짧은 이름이 그 지역으로만 해석되는지 코드로 검증합니다.
    반환: ('question', 다시 조회할 질문, 안내문) | ('answer', 확인 질문 결과, None) | None(장소 아님)
    """
    if not _place_question(question, kwargs):
        return None
    catalog = region_catalog(load_corpus())
    try:
        place = gpt_api.locate_place(question)
    except Exception:
        return None
    name = (place.get('place') or '').strip()
    if PLACE_PRICE.search(name) or len(name) > 30:
        name = ''  # 모델이 장소 대신 질문 전체를 적은 경우
    region = address_region(place.get('address'), catalog) if place.get('found') else None
    text = region and region_text(region, catalog)
    if name and text:
        rewritten = (re.sub(re.escape(name) + NEARBY, text + ' ', question, count=1) if name in question
                     else f'{text} {question}')
        note = (f"'{name}'은(는) 일반적으로 알려진 소재지({place['address']})를 기준으로 {text} 전체 값을 조회했습니다. "
                "동 단위 자료가 없어 장소 주변만의 값은 아닙니다. 위치가 다르면 지역명을 직접 적어 주세요.")
        return 'question', ' '.join(rewritten.split()), note
    candidates = list(dict.fromkeys(r['label'] for r in (address_region(a, catalog) for a in place.get('candidates') or []) if r))
    if name and len(candidates) >= 2:
        return _clarify(f"'{name}'은(는) 여러 지역에 걸쳐 있거나 같은 이름이 여러 곳이라 한 지역으로 정할 수 없습니다. "
                        f"다음 중 하나를 골라 다시 물어보세요: {', '.join(candidates)}.", 'ambiguous_place',
                        candidate_choices(question, name, candidates))
    if PLACE_WORDS.search(question):
        # 장소를 특정하지 못했으면 다른 지역(전국 등)으로 대신 답하지 않고 되묻습니다.
        what = f"'{name}'의" if name else '말씀하신 장소의'
        return _clarify(f"{what} 위치를 확인하지 못했습니다. 동 단위 자료는 없으니 시군구 이름으로 물어보세요. "
                        "예: '광주 북구 2021년 8월 가격', '서울 강서구 최근 1년 변동률'", 'unknown_place')
    return None
