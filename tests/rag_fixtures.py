"""오프라인 테스트용 지식 색인. 실제 API를 호출하지 않습니다."""
import atexit
import shutil
import tempfile
import zlib
from functools import lru_cache
from pathlib import Path

DIM = 128
SPEC = {"model": "offline-ngram", "dimensions": DIM, "endpoint": "offline"}


def fake_embed(texts):
    """문자 2-gram 해시 벡터. 배관·라우팅 검증용이며 실제 의미 검색 품질을 대표하지 않습니다."""
    vectors = []
    for text in texts:
        v = [0.0] * DIM
        compact = ''.join(text.split())
        for i in range(len(compact) - 1):
            v[zlib.crc32(compact[i:i + 2].encode()) % DIM] += 1.0
        v[0] += 1e-6
        vectors.append(v)
    return vectors


@lru_cache(maxsize=1)
def knowledge_collection():
    from bubot.knowledge_index import build_knowledge_index
    from bubot.vector_store import open_client, open_collection
    root = Path(tempfile.mkdtemp())
    atexit.register(shutil.rmtree, root, True)
    result = build_knowledge_index(db_path=root / "vectors", manifest_path=root / "knowledge.json",
                                   embedder=fake_embed, spec=SPEC, progress=lambda _: None)
    return open_collection(open_client(root / "vectors"), result["collection"])
