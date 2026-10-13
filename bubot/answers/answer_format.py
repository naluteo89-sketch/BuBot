"""웹 답변 표기 정리: 금액 단위 통일과 한 줄 출처 표기.

모델이 근거 문서의 천원 표기('286,680천원')를 그대로 옮기거나 직접 환산해 단위가 섞이는 것을 막습니다.
값은 바꾸지 않고 표기만 사용자가 고른 단위로 맞춥니다.
"""
import re

from bubot.pricing import format_price
from bubot.answers.source_display import SOURCE_LABEL

# '2억 8,668만원 (286,680천원)'의 괄호 속 천원 병기
_PAREN_THOUSAND = re.compile(r'\s*\(\s*[+-]?\d[\d,]*(?:\.\d+)?\s*천원\s*\)')
# 단독 천원 표기 '288,122천원'
_THOUSAND = re.compile(r'(?<![\d.,])([+-]?\d[\d,]*(?:\.\d+)?)\s*천원')


def unify_units(text, unit):
    """unit이 천원이 아니면 답변 속 천원 표기를 그 단위로 바꿉니다. 천원을 고른 경우 그대로 둡니다."""
    if not isinstance(text, str) or not unit or unit == '천원':
        return text
    text = _PAREN_THOUSAND.sub('', text)
    return _THOUSAND.sub(lambda m: format_price(m.group(1).replace(',', ''), unit), text)


_MONTH = re.compile(r' · (\d{4}-\d{2}) · ')


def source_note(sources):
    """답변마다 같은 형식의 한 줄 출처: '출처: 데이터명 (기준 2026-08)'. 출처가 없으면 빈 문자열."""
    if not sources:
        return ''
    months = sorted({m for s in sources for m in _MONTH.findall(s)})
    uses_dataset = any(SOURCE_LABEL in s for s in sources)
    if not uses_dataset:
        return '출처: BuBot 안내 문서'
    period = ''
    if months:
        period = f' (기준 {months[0]})' if len(months) == 1 else f' (기준 {months[0]} ~ {months[-1]})'
    return f'출처: {SOURCE_LABEL}{period}'
