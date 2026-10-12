"""천원 원본 수치를 손실 없이 표시합니다. 소수점은 필요할 때만 표시합니다."""
from decimal import Decimal


def format_price(value, unit="korean") -> str:
    if value is None:
        return "데이터 없음"
    price = Decimal(str(value))
    if not price.is_finite():
        return "데이터 없음"
    def number(n):
        return format(n, ',f').rstrip('0').rstrip('.') if '.' in format(n, ',f') else format(n, ',f')
    if unit in {"천원", "만원", "억원"}:
        divisor = {"천원": 1, "만원": 10, "억원": 100000}[unit]
        return number(price / divisor) + unit
    if unit != "korean":
        raise ValueError("지원하지 않는 가격 표시 단위입니다.")
    won = abs(price * 1000)
    eok = int(won // 100000000)
    remainder = won - eok * 100000000
    man = int(remainder // 10000)
    remainder -= man * 10000
    parts = []
    if eok:
        parts.append(f"{eok:,}억")
    if man:
        parts.append(f"{man:,}만")
    if remainder:
        parts.append(number(remainder))
    return ('-' if price < 0 else '') + (' '.join(parts) if parts else '0') + '원'
