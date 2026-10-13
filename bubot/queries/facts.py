"""조회·추이·비교 계산값: 지역·월 문서로 가격, 변화량·변동률, 지역 간 차이·배수를 원본에서 계산합니다 (LLM 없음)."""
from ..cache.data_cache import documents_by_id
from ..pricing import format_price


def calculate_facts(corpus, ids, intent, month, start, end, unit):
    by_id = documents_by_id(corpus)
    facts=[]
    values=[]
    for rid in ids:
        doc=by_id[f"region-{rid}-month-{end if intent=='trend' else month}"]
        name=doc.metadata['region_path']
        price=doc.metadata.get('price_thousand_krw')
        values.append((name,price))
        if intent=='trend':
            first=by_id[f"region-{rid}-month-{start}"].metadata.get('price_thousand_krw')
            facts.append(f"{name}: {start} {format_price(first,unit)} → {end} {format_price(price,unit)}")
            if first is None or price is None or first==0:
                facts.append(f"{name}: 시작·종료값 결측 또는 시작값 0으로 변동률 계산 불가")
            else:
                facts.append(f"{name}: 변화량 {format_price(price-first,unit)}, 변동률 {(price/first-1)*100:+.2f}%")
        else:
            facts.append(f"{month} {name}: {format_price(price,unit)}")
    if intent=='compare' and values:
        reference,base=values[0]
        for name,value in values[1:]:
            if base is None or value is None:
                facts.append(f"{name}: 결측으로 {reference} 대비 비교 불가")
            else:
                facts.append(f"{reference} 대비 {name} 차이: {format_price(value-base,unit)}")
                if base:
                    # 방향이 헷갈리지 않도록 주어를 밝힙니다 (이전: '서울 대비 부산: 0.2705배'를 모델이 거꾸로 읽음)
                    facts.append(f"{name} 가격은 {reference} 가격의 {value/base:.4f}배, {reference}보다 {(value/base-1)*100:+.2f}%")
    return facts
