"""답변 생성: GPT에 넘길 근거 분량 조절, 답변 작성 후 수치 검증·1회 재작성, 생성 실패 시 계산값·원문으로 답변."""
import re

from ..config import CONTEXT_CHAR_BUDGET
from ..llm import gpt_api
from ..llm.usage import BudgetExceeded, timed
from .answer_check import number_issues


def fit_budget(contexts, budget=CONTEXT_CHAR_BUDGET):
    """GPT에 넘기는 근거 본문을 budget 글자 안으로 줄입니다 (토큰 소모는 여기서만 제어).

    직접 조회한 원본 문서(pool 없음)를 먼저 두고, 지식 검색 보강 문서는 남는 만큼만 붙입니다.
    그래도 넘치면 긴 문서(예: 20개 지역의 추이 문서)를 같은 길이로 잘라 냅니다. 핵심 수치는 verified_facts에 있습니다.
    """
    if sum(len(c['text']) for c in contexts) <= budget:
        return contexts
    kept = [c for c in contexts if 'pool' not in c]
    used = sum(len(c['text']) for c in kept)
    for c in contexts:
        if 'pool' in c and used + len(c['text']) <= budget:
            kept.append(c)
            used += len(c['text'])
    if used <= budget or not kept:
        return kept
    remaining, cap = budget, None
    for i, length in enumerate(sorted(len(c['text']) for c in kept)):
        share = remaining // (len(kept) - i)
        if length > share:
            cap = max(200, share)
            break
        remaining -= length
    return [c if cap is None or len(c['text']) <= cap else
            {**c, 'text': c['text'][:cap] + '\n…(분량 제한으로 이하 생략. 수치는 verified_facts 기준)'} for c in kept]


# GPT가 답변 형식 지시문을 따라 쓰는 문단 ('결론에 없는 보조 맥락이 없습니다', '확인되지 않는 부분은 없습니다')
FORMAT_ECHO = re.compile(r'^(?:결론에\s*없는\s*)?(?:보조\s*맥락|참고\s*(?:사항|내용)|한계|확인되지\s*않는\s*부분)'
                         r'(?:은|는|이|가)?\s*(?:특별히\s*)?(?:없|해당\s*없)[^\n]*$')


def drop_format_echo(answer):
    paragraphs = [p for p in answer.split('\n\n') if not FORMAT_ECHO.match(p.strip())]
    return '\n\n'.join(paragraphs).strip() or answer


def fallback_answer(contexts, display, facts, error):
    """답변 생성(GPT)이 실패해도 이미 확보한 계산값·근거 원문으로 답합니다."""
    if facts:
        body = '원본 데이터로 계산한 결과입니다.\n' + '\n'.join('- ' + f for f in facts)
    else:
        text = contexts[0]['text']
        body = '질문과 가장 가까운 지식 문서 내용입니다.\n' + (text[:700] + '…' if len(text) > 700 else text)
    reason = (str(error) if isinstance(error, BudgetExceeded) else
              '문장형 답변 생성이 완료되지 않아 계산값·문서 원문으로 안내했습니다. 잠시 후 다시 질문하면 문장형 답변을 받을 수 있습니다.')
    return {'answer': body + '\n\n※ ' + reason,
            'model': None, 'unit': display, 'used_context_ids': [c['id'] for c in contexts],
            'error_type': type(error).__name__}


def generate(question, contexts, analysis, display, facts, timings):
    """답변을 만든 뒤 문장 속 변동률(%)을 근거와 대조합니다.

    부호가 뒤집혔거나 근거에 없는 값이 있으면 오류 목록을 알려 주고 1회 다시 쓰게 합니다.
    그래도 맞지 않으면 답변 끝에 확인 안내를 붙입니다. 생성 자체가 실패하면 계산값·원문으로 대체합니다.
    결과는 analysis['answer_check'](ok·corrected·warning·fallback)에 남습니다.
    """
    prompt_contexts = fit_budget(contexts)
    if prompt_contexts is not contexts:
        analysis['context_trimmed'] = True
    try:
        result = timed(timings, '답변생성', gpt_api.generate_answer, question, prompt_contexts, analysis, display, facts)
    except Exception as error:
        analysis['answer_check'] = 'fallback'
        analysis['generation_error'] = type(error).__name__
        return fallback_answer(contexts, display, facts, error)
    result = {**result, 'answer': drop_format_echo(result['answer'])}
    evidence = [question, *facts, *(c['text'] for c in contexts)]
    issues = number_issues(result['answer'], evidence)
    analysis['answer_check'] = 'ok'
    if issues:
        try:
            result = timed(timings, '답변재생성', gpt_api.generate_answer, question, prompt_contexts, analysis, display,
                            facts, correction=issues)
            issues = number_issues(result['answer'], evidence)
        except Exception as error:
            analysis['generation_error'] = type(error).__name__  # 첫 답변을 유지하고 아래 확인 안내를 붙입니다
        analysis['answer_check'] = 'corrected'
        if issues:
            analysis['answer_check'] = 'warning'
            # 화면(web/components/message.js)은 이 표시를 안내 상자로 바꿉니다. 표시 문자열을 바꾸면 함께 고치세요.
            result = {**result, 'answer': result['answer'] + '\n\n[수치 확인 필요] 답변의 일부 수치가 계산 근거와 다를 수 있어요. '
                      + '확인할 표현: ' + ', '.join(issues) + '. 아래 원본 데이터 계산과 출처를 기준으로 확인해 주세요.'}
    return result
