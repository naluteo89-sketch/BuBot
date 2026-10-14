"""원본 CSV·지식 문서를 파일이 바뀔 때만 다시 읽도록 캐시합니다 (웹 서버 장기 실행 대비)."""
from pathlib import Path
from threading import Lock

from ..config import CSV_PATH, KNOWLEDGE_DIR
from ..indexing.documents import read_corpus
from ..indexing.knowledge import build_knowledge

_lock = Lock()
_cache = {}


def _stamp(path: Path):
    stat = Path(path).stat()
    return str(path), stat.st_mtime_ns, stat.st_size


def load_corpus(path: Path = CSV_PATH):
    key = ('corpus', _stamp(path))
    with _lock:
        if key not in _cache:
            _cache.clear()
            _cache[key] = read_corpus(path)
        return _cache[key]


def documents_by_id(corpus):
    """문서 ID → 문서. 요청마다 4만여 개 문서를 다시 훑지 않도록 같은 corpus 객체에 대해 한 번만 만듭니다."""
    with _lock:
        cached = _cache.get(('by_id',))
        if cached is None or cached[0] is not corpus:
            cached = _cache[('by_id',)] = (corpus, {d.id: d for d in corpus.documents})
        return cached[1]


def monthly_doc(corpus, region_id, month):
    """지역·월의 원본 월 문서 (없으면 None)."""
    return documents_by_id(corpus).get(f"region-{region_id}-month-{month}")


def monthly_docs(corpus, region_ids, months):
    """원본 월 문서 중 region_ids 지역·months 월에 해당하는 것 (원본 순서 유지)."""
    return [d for d in corpus.documents if d.metadata['kind'] == 'monthly'
            and d.metadata['region_id'] in region_ids and d.metadata['month'] in months]


def load_knowledge(corpus=None, guide_dir: Path = KNOWLEDGE_DIR):
    corpus = corpus or load_corpus()
    guides = tuple(_stamp(p) for p in sorted(Path(guide_dir).glob('*.md')))
    key = ('knowledge', corpus.source_hash, guides)
    with _lock:
        if key not in _cache:
            for old in [k for k in _cache if k[0] == 'knowledge']:
                del _cache[old]
            _cache[key] = build_knowledge(corpus, guide_dir)
        return _cache[key]
