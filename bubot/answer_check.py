"""답변 문장 속 변동률(%)을 근거 수치와 대조합니다.

GPT가 근거의 -0.88%를 +0.88%로 쓰거나(부호 반전) 근거에 없는 비율을 만들어 내는 경우를 찾습니다.
근거는 질문, 원본 계산값(facts), GPT에 전달한 문서 본문입니다. 금액은 표기 방식이 다양해 대조하지 않습니다.
"""
import re

PERCENT = re.compile(r'([+\-−])?\s*(\d+(?:\.(\d+))?)\s*%')
DOWN_WORDS = re.compile(r'^[^.,\n]{0,12}?(하락|감소|내렸|내린|떨어|낮아|줄어)')
UP_WORDS = re.compile(r'^[^.,\n]{0,12}?(상승|증가|올랐|오른|높아|늘어)')


def _evidence(texts):
    values = []
    for text in texts:
        for sign, number, _ in PERCENT.findall(text or ''):
            value = float(number)
            values.append((-value if sign and sign in '-−' else value, bool(sign)))
    return values


def _matches(value, evidence, tolerance):
    return any(abs(v - value) <= tolerance or (not signed and abs(abs(v) - abs(value)) <= tolerance)
               for v, signed in evidence)


def number_issues(answer, evidence_texts):
    """근거와 맞지 않는 비율 표현 목록. 비어 있으면 통과입니다."""
    evidence = _evidence(evidence_texts)
    issues = []
    for match in PERCENT.finditer(answer or ''):
        sign, number, decimals = match.groups()
        value = float(number)
        tolerance = 0.5 * 10 ** -len(decimals or '') + 0.001  # 답변이 반올림한 자릿수만큼 허용
        tail = answer[match.end():]
        if sign:
            signed = -value if sign in '-−' else value
        elif DOWN_WORDS.match(tail):
            signed = -value
        elif UP_WORDS.match(tail):
            signed = value
        else:
            if not any(abs(abs(v) - value) <= tolerance for v, _ in evidence):
                issues.append(f"{match[0].strip()} (근거에 없는 값)")
            continue
        if _matches(signed, evidence, tolerance):
            continue
        text = match[0].strip()
        if _matches(-signed, evidence, tolerance):
            issues.append(f"{text} (근거는 {'+' if -signed > 0 else '-'}{value:g}%, 부호 반대)")
        else:
            issues.append(f"{text} (근거에 없는 값)")
    return list(dict.fromkeys(issues))
