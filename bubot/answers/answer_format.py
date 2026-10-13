"""답변 표기: 규칙 답변 형식, 출처 표시명과 한 줄 출처, 금액 단위 통일.

모델이 근거 문서의 천원 표기('286,680천원')를 그대로 옮기거나 직접 환산해 단위가 섞이는 것을 막습니다.
값은 바꾸지 않고 표기만 사용자가 고른 단위로 맞춥니다.
출처는 화면에서만 사용자가 지정한 표시명으로 바꾸고, 원본 파일 및 색인 메타데이터는 유지합니다.
"""
import re

from bubot.config import CSV_PATH
from bubot.pricing import format_price

SOURCE_LABEL = "공공데이터포털, 한국부동산원_공동주택 실거래가격지수_아파트_월별 지역별 매매"

# '2억 8,668만원 (286,680천원)'의 괄호 속 천원 병기
_PAREN_THOUSAND = re.compile(r'\s*\(\s*[+-]?\d[\d,]*(?:\.\d+)?\s*천원\s*\)')
# 단독 천원 표기 '288,122천원'
_THOUSAND = re.compile(r'(?<![\d.,])([+-]?\d[\d,]*(?:\.\d+)?)\s*천원')


def rule_answer(answer, facts=(), docs=(), **analysis):
    """규칙 경로(원본 직접 계산) 답변. docs: 근거 월 문서, 출처 줄은 같은 줄을 한 번만 씁니다."""
    return {'answer': answer, 'facts': list(facts), 'route': 'rule', 'analysis': analysis,
            'contexts': [{'id': d.id, 'text': d.text, 'metadata': d.metadata} for d in docs],
            'sources': list(dict.fromkeys(
                f"[{d.id}] {CSV_PATH.name} · {d.metadata['region_path']} · {d.metadata['month']} · "
                f"원본 {d.metadata['source_row']}행 · SHA-256 {d.metadata['source_sha256']}" for d in docs))}


def clarify_answer(answer, reason, choices=(), **analysis):
    """규칙 경로의 확인·안내 답변 (계산값·근거 없음). choices: 답변 아래 선택지 버튼."""
    return {'answer': answer, 'analysis': {'intent': 'clarify', 'reason': reason, **analysis},
            'contexts': [], 'facts': [], 'sources': [], 'route': 'rule', 'choices': list(choices)}



def display_sources(result):
    """결과 사본에서 답변 속 출처 문구를 지우고, 원본 파일명을 출처 표시명으로 바꿉니다."""
    result = dict(result)
    if isinstance(result.get('answer'), str):
        result['answer'] = re.sub(r'(?:출처 문서 ID와 행 번호는 프로그램이 답변 뒤에 별도로 붙입니다\.?|(?:출처 문서 ID|출처)\s*[:：][^\n]*)', '', result['answer']).strip()
        result['answer'] = result['answer'].replace('new_prices.csv', SOURCE_LABEL).replace('data.csv', SOURCE_LABEL)
    if 'sources' in result:
        result['sources'] = [s.replace('new_prices.csv', SOURCE_LABEL).replace('data.csv', SOURCE_LABEL) for s in result['sources']]
    return result


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
