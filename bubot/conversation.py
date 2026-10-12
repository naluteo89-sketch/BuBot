"""대화 메모리: 세션별로 최근 질문·답변을 서버 프로세스 메모리에 보관합니다.

- 세션마다 최근 MEMORY_TURNS개(최소 3개) 질문·답변을 기억합니다.
- 디스크에 저장하지 않습니다. 서버를 재시작하거나 '새 대화'를 누르면 사라집니다.
  (테스트 기록 파일 저장은 별도 기능입니다.)
- 마지막 질문 후 MEMORY_TTL_SECONDS가 지나면 세션을 지웁니다.
"""
import re
import time
from collections import OrderedDict, deque
from threading import RLock

from .config import MEMORY_MAX_SESSIONS, MEMORY_TTL_SECONDS, MEMORY_TURNS

SESSION_PATTERN = re.compile(r'^[A-Za-z0-9_-]{8,64}$')
ANSWER_LIMIT = 700


def summarize_turn(question, result=None, error=None, standalone=None):
    """다음 질문 해석에 필요한 내용만 남깁니다 (원문 질문, 재구성 질문, 답변 요약, 확정 조건)."""
    result = result or {}
    analysis = result.get('analysis') or {}
    answer = error or result.get('answer') or ''
    if len(answer) > ANSWER_LIMIT:
        answer = answer[:ANSWER_LIMIT] + '…'
    resolved = {k: analysis.get(k) for k in ('intent', 'resolved_month', 'resolved_start', 'resolved_end',
                                             'knowledge_month', 'knowledge_year', 'level')
                if analysis.get(k)}
    regions = [c['metadata'].get('region_path') for c in result.get('contexts', [])
               if c.get('metadata', {}).get('kind') in {'monthly', 'timeline', 'region_profile'}]
    if regions:
        resolved['regions'] = list(dict.fromkeys(r for r in regions if r))
    return {'question': question,
            'standalone_question': result.get('standalone_question') or standalone or question,
            'answer': answer,
            'facts': list(result.get('facts') or [])[:6],
            'resolved': resolved,
            'clarification': analysis.get('intent') == 'clarify',
            'error': bool(error),
            'price_extreme': bool(analysis.get('price_extreme')),
            'sources': list(result.get('sources') or []) if analysis.get('price_extreme') else []}


class ConversationMemory:
    def __init__(self, turns=MEMORY_TURNS, ttl=MEMORY_TTL_SECONDS, max_sessions=MEMORY_MAX_SESSIONS, clock=time.monotonic):
        if turns < 3:
            raise ValueError('대화 메모리는 최소 3개 턴을 기억해야 합니다.')
        self.turns, self.ttl, self.max_sessions, self.clock = turns, ttl, max_sessions, clock
        self._sessions = OrderedDict()
        self._lock = RLock()

    @staticmethod
    def check(session_id):
        if not isinstance(session_id, str) or not SESSION_PATTERN.match(session_id):
            raise ValueError('대화 세션 ID 형식이 올바르지 않습니다.')
        return session_id

    def _expire(self):
        now = self.clock()
        for key in [k for k, (stamp, _) in self._sessions.items() if now - stamp > self.ttl]:
            del self._sessions[key]

    def history(self, session_id):
        with self._lock:
            self._expire()
            entry = self._sessions.get(self.check(session_id))
            return list(entry[1]) if entry else []

    def add(self, session_id, turn):
        with self._lock:
            self._expire()
            session_id = self.check(session_id)
            _, turns = self._sessions.pop(session_id, (None, deque(maxlen=self.turns)))
            turns.append(turn)
            self._sessions[session_id] = (self.clock(), turns)
            while len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)

    def reset(self, session_id):
        with self._lock:
            self._sessions.pop(self.check(session_id), None)

    def size(self, session_id):
        return len(self.history(session_id))


MEMORY = ConversationMemory()


def chat(session_id, question, *, memory=None, **options):
    """이전 대화를 반영해 질문에 답하고, 결과를 메모리에 기록합니다."""
    from .rag_service import ask
    memory = memory or MEMORY
    history = memory.history(session_id)
    try:
        result = ask(question, history=history, **options)
    except ValueError as error:
        memory.add(session_id, summarize_turn(question, error=str(error),
                                              standalone=getattr(error, 'standalone_question', None)))
        raise
    memory.add(session_id, summarize_turn(question, result))
    return {**result, 'memory_turns': memory.size(session_id), 'memory_used': len(history)}
