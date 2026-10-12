"""가격 순위 집계: 전체 원본 문서의 유효 가격을 비교합니다. 유사도 검색으로 순위를 계산하지 않습니다."""
from .data_cache import load_corpus
from .regions import region_catalog, resolve_region, ranking_regions


def _aggregate_price(filters: dict, *, mode: str) -> dict:
    if mode not in {"max", "min"}:
        raise ValueError("mode는 'max' 또는 'min'이어야 합니다.")
    corpus = load_corpus()
    month = filters.get("month") or corpus.months[-1]
    if month not in corpus.months:
        return {"error": "요청한 월의 데이터가 없습니다."}
    catalog = region_catalog(corpus)
    scope = filters.get('region_id')
    if not scope and filters.get('region_name'):
        scope = resolve_region(filters['region_name'], catalog)
    level = filters.get('level') or '시군구'
    allowed = ranking_regions(catalog, scope, level)
    rows = [{"id": d.id, "text": d.text, "metadata": d.metadata} for d in corpus.documents
            if d.metadata['kind'] == 'monthly' and d.metadata['month'] == month
            and d.metadata['region_id'] in allowed and not d.metadata['missing']]
    if not rows:
        return {"error": f"{month} 조건에 맞는 문서가 없습니다."}
    # 상위 몇 개도 함께 전달합니다 (LLM이 "2위와의 차이"를 언급할 수 있도록).
    rows.sort(key=lambda r: r["metadata"]["price_thousand_krw"], reverse=(mode == "max"))
    return {
        "winner": rows[0],
        "candidates_count": len(rows),
        "filters": {"month": month, "level": level, "region_id": filters.get("region_id"), "kind": "monthly"},
        "top": rows[:filters.get("top_n") or 5],
    }


def argmax_price(filters: dict) -> dict:
    """조건 내에서 price_thousand_krw 최댓값 문서 반환."""
    return _aggregate_price(filters, mode="max")


def argmin_price(filters: dict) -> dict:
    """조건 내에서 price_thousand_krw 최솟값 문서 반환."""
    return _aggregate_price(filters, mode="min")
