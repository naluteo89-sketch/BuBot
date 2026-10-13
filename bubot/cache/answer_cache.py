"""같은 질문에 같은 답을 돌려주는 답변 캐시 (서버 프로세스 메모리, 재시작 시 초기화).

temperature=0이어도 GPT의 질문 분석·답변 문장은 호출마다 조금씩 달라질 수 있습니다.
이전 대화를 반영한 단독 질문·옵션·데이터·색인·모델이 같으면 처음 검증을 통과한 결과를 그대로 다시 씁니다.
수치 확인 경고(warning)나 대체 답변(fallback)은 저장하지 않아 다음 질문에서 다시 생성됩니다.
"""
import copy
from collections import OrderedDict
from threading import Lock

from ..config import ANSWER_CACHE_SIZE, KNOWLEDGE_MANIFEST_PATH

CACHEABLE_ROUTES = {'numeric', 'explain'}
CACHEABLE_CHECKS = {'ok', 'corrected'}


def _manifest_stamp():
    try:
        return KNOWLEDGE_MANIFEST_PATH.stat().st_mtime_ns
    except OSError:
        return None


def cache_key(question, options, source_hash):
    """질문은 공백만 정규화합니다. 답변을 만드는 함수·모델이 바뀌면(설정 변경, 테스트 모의 객체) 다른 키가 됩니다."""
    import gpt_api
    return (' '.join(question.split()), tuple(sorted(options.items())), source_hash, _manifest_stamp(),
            gpt_api.CHAT_MODEL, gpt_api.analyze_query, gpt_api.generate_answer, gpt_api.embed_texts)


def cacheable(result):
    analysis = result.get('analysis') or {}
    return (result.get('route') in CACHEABLE_ROUTES and analysis.get('answer_check') in CACHEABLE_CHECKS
            and not analysis.get('analysis_fallback'))


class AnswerCache:
    def __init__(self, size=ANSWER_CACHE_SIZE):
        self.size = size
        self._items = OrderedDict()
        self._lock = Lock()

    def get(self, key):
        with self._lock:
            if key not in self._items:
                return None
            self._items.move_to_end(key)
            return copy.deepcopy(self._items[key])

    def put(self, key, result):
        if self.size <= 0 or not cacheable(result):
            return
        with self._lock:
            self._items[key] = copy.deepcopy(result)
            self._items.move_to_end(key)
            while len(self._items) > self.size:
                self._items.popitem(last=False)

    def clear(self):
        with self._lock:
            self._items.clear()


ANSWER_CACHE = AnswerCache()
