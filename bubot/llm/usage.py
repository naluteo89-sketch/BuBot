"""OpenAI API 사용량·추정 비용 기록과 일·월 비용 한도.

- 호출마다 응답의 토큰 수(입력·캐시 입력·출력)로 비용을 추정해 storage/api_usage.json에 날짜별로 누적합니다.
- 오늘 또는 이번 달 누적 비용이 한도에 도달하면 API를 호출하지 않습니다(BudgetExceeded).
  rag_service는 이때도 규칙 기반 답변·원본 계산값 답변으로 응답합니다.
- 금액은 아래 단가표로 계산한 추정치입니다. 실제 청구액은 OpenAI 사용량 페이지에서 확인하세요.
"""
import json
import os
import time
from threading import Lock

from ..config import OUTPUT_DIR
from ..dates import today_text

USAGE_PATH = OUTPUT_DIR / 'api_usage.json'
DAILY_BUDGET_USD = float(os.getenv('BUBOT_DAILY_BUDGET_USD', '2'))     # 0이면 한도 없음
MONTHLY_BUDGET_USD = float(os.getenv('BUBOT_MONTHLY_BUDGET_USD', '30'))  # 0이면 한도 없음
# 1M 토큰당 USD (입력, 캐시된 입력, 출력). 2026-10 기준 공개 단가를 옮긴 값이므로 가격 변경 시 함께 고치세요.
PRICES = {'gpt-6-luna': (0.10, 0.01, 0.50), 'gpt-6.1-sol': (2.00, 0.10, 10.00),
          'gpt-4o-mini': (0.15, 0.075, 0.60), 'gpt-4o': (2.50, 1.25, 10.00),
          'text-embedding-3-small': (0.02, 0.02, 0.0)}
_lock = Lock()


class BudgetExceeded(ValueError):
    """API 비용 한도 도달. ValueError라 웹에서는 오류 화면이 아니라 안내 답변으로 보입니다."""


def _load(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'days': {}}


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def tokens_of(response):
    """응답 객체의 (입력, 캐시 입력, 출력) 토큰. Responses API와 Embeddings API 모두 지원."""
    usage = getattr(response, 'usage', None)
    if usage is None:
        return 0, 0, 0
    prompt = _int(getattr(usage, 'input_tokens', None)) or _int(getattr(usage, 'prompt_tokens', None))
    cached = _int(getattr(getattr(usage, 'input_tokens_details', None), 'cached_tokens', None))
    output = _int(getattr(usage, 'output_tokens', None))
    return prompt, min(cached, prompt), output


def cost_usd(model, prompt, cached, output):
    price_in, price_cached, price_out = PRICES.get(model, PRICES['gpt-4o'])  # 모르는 모델은 비싼 쪽으로 계산
    return ((prompt - cached) * price_in + cached * price_cached + output * price_out) / 1_000_000


def record(model, response, *, path=None, day=None):
    """호출 1건의 사용량을 누적합니다. 토큰 정보가 없으면(테스트용 모의 응답 등) 기록하지 않습니다."""
    prompt, cached, output = tokens_of(response)
    if not (prompt or output):
        return 0.0
    cost = cost_usd(model, prompt, cached, output)
    path = path or USAGE_PATH
    day = day or today_text()
    with _lock:
        data = _load(path)
        entry = data['days'].setdefault(day, {'usd': 0.0, 'calls': 0, 'models': {}})
        entry['usd'] = round(entry['usd'] + cost, 6)
        entry['calls'] += 1
        tokens = entry['models'].setdefault(model, {'input': 0, 'cached': 0, 'output': 0, 'calls': 0})
        for key, value in (('input', prompt), ('cached', cached), ('output', output), ('calls', 1)):
            tokens[key] += value
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        temporary.replace(path)
    return cost


def totals(*, path=None, day=None):
    day = day or today_text()
    days = _load(path or USAGE_PATH)['days']
    month = sum(v['usd'] for d, v in days.items() if d[:7] == day[:7])
    today = days.get(day, {})
    return {'today_usd': round(today.get('usd', 0.0), 4), 'today_calls': today.get('calls', 0),
            'month_usd': round(month, 4), 'daily_budget_usd': DAILY_BUDGET_USD, 'monthly_budget_usd': MONTHLY_BUDGET_USD}


def check(*, path=None, day=None):
    """한도에 도달했으면 BudgetExceeded. API 호출 직전에 부릅니다."""
    now = totals(path=path, day=day)
    if DAILY_BUDGET_USD and now['today_usd'] >= DAILY_BUDGET_USD:
        raise BudgetExceeded(f"오늘 API 사용 한도(약 ${DAILY_BUDGET_USD:g})에 도달해 AI 분석·문장 생성을 잠시 멈췄습니다. "
                             "원본 계산이 가능한 질문은 계속 답합니다. 한도는 내일 0시(한국 시간)에 초기화됩니다.")
    if MONTHLY_BUDGET_USD and now['month_usd'] >= MONTHLY_BUDGET_USD:
        raise BudgetExceeded(f"이번 달 API 사용 한도(약 ${MONTHLY_BUDGET_USD:g})에 도달해 AI 분석·문장 생성을 멈췄습니다. "
                             "원본 계산이 가능한 질문은 계속 답합니다. 관리자에게 한도 조정을 요청하세요.")


def timed(timings, stage, fn, *args, **kwargs):
    """API 호출 단계별 소요 시간(초)을 기록합니다. 테스트 기록에서 느린 단계를 찾는 용도입니다."""
    start = time.perf_counter()
    try:
        return fn(*args, **kwargs)
    finally:
        timings[stage] = round(time.perf_counter() - start, 3)
