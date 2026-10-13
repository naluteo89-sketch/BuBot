"""가격 기준 지역 찾기 (LLM 없음): 가격 조건('5억 이하 서울 구', '10억 넘는 시도')에 맞는 지역 목록과
최고·최저 가격 순위. 전체 원본 문서의 유효 가격을 비교하며, 유사도 검색으로 순위를 계산하지 않습니다.
"""
import re

from ..answers.answer_format import rule_answer
from ..cache.data_cache import load_corpus
from ..filters.period_parser import parse_period
from ..filters.query_parser import find_level, find_threshold
from ..pricing import format_price
from ..regions import named_region, ranking_regions, region_catalog, resolve_region

LIST_LIMIT = 30
OP_WORDS = {'>=': '이상', '>': '초과', '<=': '이하', '<': '미만'}


def price_filter(question, corpus, unit):
    """'5억 이하 서울 구'처럼 가격 조건에 맞는 지역 목록. 가격 조건이 없으면 None."""
    threshold = find_threshold(question)
    if not threshold:
        return None
    op, value = threshold
    catalog = region_catalog(corpus)
    scope, ambiguous = named_region(question, catalog)
    if ambiguous:
        return rule_answer('같은 이름의 지역이 있습니다. 시도와 시군구를 함께 지정해주세요.', intent='clarify')
    period = parse_period(question, corpus.months)
    month = period.month if period.kind == 'point' and period.month in corpus.months else corpus.months[-1]
    level = find_level(question) or ('시도' if re.search(r'시도|광역', question) else '시군구')
    allowed = {scope['id']} if scope and scope['level'] == '시군구' else ranking_regions(catalog, scope['id'] if scope else None, level)
    docs = [d for d in corpus.documents if d.metadata['kind'] == 'monthly' and d.metadata['month'] == month
            and d.metadata['region_id'] in allowed and d.metadata.get('price_thousand_krw') is not None]
    test = {'>=': lambda p: p >= value, '>': lambda p: p > value, '<=': lambda p: p <= value, '<': lambda p: p < value}[op]
    hits = sorted((d for d in docs if test(d.metadata['price_thousand_krw'])), key=lambda d: d.metadata['price_thousand_krw'])
    where = scope['label'] if scope else '전국'
    head = (f"{month} 기준 {where} {level} 중 아파트 월평균 매매가격이 {format_price(value, unit)} {OP_WORDS[op]}인 곳은 "
            f"{len(hits)}곳입니다 (결측 제외 {len(docs)}곳 중).")
    facts = [f"{d.metadata['region_path']}: {format_price(d.metadata['price_thousand_krw'], unit)}" for d in hits[:LIST_LIMIT]]
    if len(hits) > LIST_LIMIT:
        facts.append(f"외 {len(hits) - LIST_LIMIT}곳 (가격 낮은 순 {LIST_LIMIT}곳만 표시)")
    if hits:
        body = '\n'.join(facts)
    elif docs:
        # 조건에 가장 가까운 곳을 함께 보여 줍니다 ('5억 이하 서울 구' → 가장 싼 도봉구)
        nearest = (min if op in ('<=', '<') else max)(docs, key=lambda d: d.metadata['price_thousand_krw'])
        body = (f"조건에 맞는 지역이 없습니다. 가장 가까운 곳은 {nearest.metadata['region_path']} "
                f"{format_price(nearest.metadata['price_thousand_krw'], unit)}입니다.")
    else:
        body = '조건에 맞는 지역이 없습니다.'
    if period.notes:
        head += ' ' + ' '.join(period.notes)
    return rule_answer(head + '\n\n' + body, facts, hits[:LIST_LIMIT], intent='filter', resolved_month=month,
                     level=level, count=len(hits), candidates=len(docs))


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
