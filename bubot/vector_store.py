"""SQLite 영구 벡터 저장 + 코사인 검색. Chroma 네이티브 코드 미사용."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import Lock
import numpy as np

_LOADED = {}  # (DB 파일, 컬렉션) → (행 수·마지막 rowid, 메모리 행렬). 요청마다 SQLite 전체를 다시 읽지 않도록
_LOADED_LOCK = Lock()


def normalize(value):
    v = np.asarray(value, dtype=np.float64)
    if v.ndim != 1 or not v.size or not np.isfinite(v).all():
        raise ValueError('벡터 형식이 올바르지 않습니다.')
    norm = np.linalg.norm(v)
    if not np.isfinite(norm) or norm == 0:
        raise ValueError('벡터 크기는 유한한 양수여야 합니다.')
    return np.asarray(v / norm, dtype='<f4')


def matches(meta, where):
    if not where:
        return True
    if '$and' in where:
        return all(matches(meta, item) for item in where['$and'])
    if '$or' in where:
        return any(matches(meta, item) for item in where['$or'])
    for key, expression in where.items():
        if not isinstance(expression, dict) or len(expression) != 1 or not set(expression) <= {'$eq', '$in'}:
            raise ValueError('필터는 $eq, $in, $and, $or만 지원합니다.')
        if '$eq' in expression and meta.get(key) != expression['$eq']:
            return False
        if '$in' in expression and meta.get(key) not in expression['$in']:
            return False
    return True


class VectorClient:
    def __init__(self, path):
        Path(path).mkdir(parents=True, exist_ok=True)
        self.file = Path(path) / 'vectors.sqlite3'
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS collections (name TEXT PRIMARY KEY, dimension INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS vectors (collection TEXT, id TEXT, document TEXT, metadata TEXT, vector BLOB, PRIMARY KEY(collection,id))')
            db.execute('CREATE TABLE IF NOT EXISTS embedding_cache (key TEXT PRIMARY KEY, vector BLOB)')

    def cached_vectors(self, keys):
        """같은 모델·차원·본문의 임베딩을 재사용합니다 (문서 일부만 바뀐 경우 API 호출 절감)."""
        if not keys:
            return {}
        found = {}
        with closing(self.connect()) as db:
            for start in range(0, len(keys), 500):
                part = keys[start:start + 500]
                marks = ','.join('?' for _ in part)
                for key, blob in db.execute(f'SELECT key, vector FROM embedding_cache WHERE key IN ({marks})', part):
                    found[key] = np.frombuffer(blob, dtype='<f4').tolist()
        return found

    def cache_vectors(self, items):
        with closing(self.connect()) as db, db:
            db.executemany('INSERT OR REPLACE INTO embedding_cache VALUES (?, ?)',
                           [(key, normalize(vector).tobytes()) for key, vector in items])

    def connect(self):
        return sqlite3.connect(self.file, timeout=30)


class VectorCollection:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def count(self):
        with closing(self.client.connect()) as db:
            return db.execute('SELECT COUNT(*) FROM vectors WHERE collection=?', (self.name,)).fetchone()[0]

    def get(self, ids, include=None):
        if not ids:
            return {'ids': []}
        placeholders = ','.join('?' for _ in ids)
        with closing(self.client.connect()) as db:
            rows = db.execute(f'SELECT id FROM vectors WHERE collection=? AND id IN ({placeholders})', [self.name, *ids]).fetchall()
        return {'ids': [r[0] for r in rows]}

    def upsert(self, ids, documents, metadatas, embeddings):
        if not ids or len({len(ids), len(documents), len(metadatas), len(embeddings)}) != 1 or len(set(ids)) != len(ids):
            raise ValueError('배치 개수 또는 문서 ID가 올바르지 않습니다.')
        vectors = [normalize(v) for v in embeddings]
        dimension = len(vectors[0])
        if any(len(v) != dimension for v in vectors):
            raise ValueError('벡터 차원이 일치하지 않습니다.')
        rows = [(self.name, rid, doc, json.dumps(meta, ensure_ascii=False), v.tobytes()) for rid, doc, meta, v in zip(ids, documents, metadatas, vectors)]
        with closing(self.client.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT dimension FROM collections WHERE name=?', (self.name,)).fetchone()
            if old is None or old[0] not in (None, dimension):
                raise ValueError('저장된 벡터 차원과 다릅니다.')
            db.execute('UPDATE collections SET dimension=? WHERE name=?', (dimension, self.name))
            db.executemany('INSERT OR REPLACE INTO vectors VALUES (?, ?, ?, ?, ?)', rows)

    def _rows(self):
        """컬렉션 전체를 메모리 행렬로 읽어 둡니다. 행 수나 마지막 rowid가 바뀌면 다시 읽습니다."""
        with closing(self.client.connect()) as db:
            dimension = db.execute('SELECT dimension FROM collections WHERE name=?', (self.name,)).fetchone()[0]
            stamp = db.execute('SELECT COUNT(*), MAX(rowid) FROM vectors WHERE collection=?', (self.name,)).fetchone()
            key = (str(self.client.file.resolve()), self.name)
            cached = _LOADED.get(key)
            if cached and cached[0] == stamp:
                return dimension, cached[1]
            rows = db.execute('SELECT id, document, metadata, vector FROM vectors WHERE collection=? ORDER BY id', (self.name,)).fetchall()
        ids, docs, metas = [r[0] for r in rows], [r[1] for r in rows], [json.loads(r[2]) for r in rows]
        matrix = np.vstack([np.frombuffer(r[3], dtype='<f4') for r in rows]) if rows else np.zeros((0, dimension or 0), '<f4')
        data = (ids, docs, metas, matrix)
        with _LOADED_LOCK:
            if len(_LOADED) >= 8:
                _LOADED.clear()
            _LOADED[key] = (stamp, data)
        return dimension, data

    def query(self, query_embeddings, n_results, include=None, where=None):
        if n_results < 1:
            raise ValueError('검색 개수는 1 이상이어야 합니다.')
        queries = [normalize(v) for v in query_embeddings]
        dimension, (ids, docs, metas, matrix) = self._rows()
        if dimension is not None and any(len(q) != dimension for q in queries):
            raise ValueError('질문 벡터 차원이 색인과 다릅니다.')
        keep = [i for i, meta in enumerate(metas) if matches(meta, where)]
        result = {key: [] for key in ('ids', 'documents', 'metadatas', 'distances')}
        for query in queries:
            distances = np.clip(1 - matrix[keep] @ query, 0, 2) if keep else []
            ranked = sorted(zip(map(float, distances), keep), key=lambda r: (r[0], ids[r[1]]))[:n_results]
            result['distances'].append([d for d, _ in ranked])
            result['ids'].append([ids[i] for _, i in ranked])
            result['documents'].append([docs[i] for _, i in ranked])
            result['metadatas'].append([dict(metas[i]) for _, i in ranked])
        return result


def open_client(path):
    return VectorClient(path)


def open_collection(client, name, create=False):
    with closing(client.connect()) as db, db:
        if create:
            db.execute('INSERT OR IGNORE INTO collections(name) VALUES (?)', (name,))
        elif db.execute('SELECT 1 FROM collections WHERE name=?', (name,)).fetchone() is None:
            raise ValueError('색인이 없습니다. embed를 다시 실행하세요.')
    return VectorCollection(client, name)
