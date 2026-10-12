"""RAG 지식 색인: 지식 문서 임베딩·저장·검증·의미 검색.

SQLite 파일(storage/vectors/vectors.sqlite3)에 저장하고, manifest는 storage/knowledge_index.json입니다.
"""
import hashlib
import json
from pathlib import Path

import gpt_api
from .config import (BATCH_SIZE, KNOWLEDGE_DIR, KNOWLEDGE_MANIFEST_PATH, KNOWLEDGE_MAX_DISTANCE,
                     KNOWLEDGE_VERSION, VECTOR_PATH, CSV_PATH)
from .documents import read_corpus
from .knowledge import build_knowledge
from .vector_store import open_client, open_collection

GUIDE_KINDS = ['guide']


def knowledge_identity(knowledge, spec: dict) -> dict:
    content = [{"id": d.id, "text": d.text, "metadata": d.metadata} for d in knowledge.documents]
    document_hash = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    identity = {"backend": "sqlite-cosine-v1", "index": "knowledge", "knowledge_version": KNOWLEDGE_VERSION,
                "source_sha256": knowledge.source_hash, "documents_sha256": document_hash,
                "embedding": spec, "document_count": len(knowledge.documents)}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return {**identity, "collection": f"knowledge-{digest[:32]}"}


def build_knowledge_index(csv_path: Path = CSV_PATH, guide_dir: Path = KNOWLEDGE_DIR, db_path: Path = VECTOR_PATH,
                          manifest_path: Path = KNOWLEDGE_MANIFEST_PATH, batch_size: int = BATCH_SIZE,
                          embedder=None, spec: dict | None = None, progress=print) -> dict:
    if not 1 <= batch_size <= 128:
        raise ValueError("배치 크기는 1~128이어야 합니다.")
    knowledge = build_knowledge(read_corpus(csv_path), guide_dir)
    if embedder is None:
        gpt_api.shared_client()  # 키 확인을 임베딩 시작 전에
    spec = gpt_api.embedding_spec() if spec is None else spec
    embedder = gpt_api.embed_texts if embedder is None else embedder
    identity = knowledge_identity(knowledge, spec)
    client = open_client(db_path)
    collection = open_collection(client, identity["collection"], create=True)
    embedded = reused = 0
    docs = knowledge.documents
    spec_key = json.dumps(spec, sort_keys=True)
    def cache_key(text):
        return hashlib.sha256((spec_key + "\n" + text).encode("utf-8")).hexdigest()
    for offset in range(0, len(docs), batch_size):
        batch = docs[offset:offset + batch_size]
        present = set(collection.get(ids=[d.id for d in batch], include=[])["ids"])
        pending = [d for d in batch if d.id not in present]
        reused += len(present)
        if pending:
            cached = client.cached_vectors([cache_key(d.text) for d in pending])
            missing = [d for d in pending if cache_key(d.text) not in cached]
            if missing:
                vectors = embedder([d.text for d in missing])
                if len(vectors) != len(missing) or any(len(v) != spec["dimensions"] for v in vectors):
                    raise ValueError("임베딩 결과 개수/차원이 설정과 다릅니다.")
                client.cache_vectors([(cache_key(d.text), v) for d, v in zip(missing, vectors)])
                cached.update({cache_key(d.text): v for d, v in zip(missing, vectors)})
                embedded += len(missing)
            reused += len(pending) - len(missing)
            collection.upsert(ids=[d.id for d in pending], documents=[d.text for d in pending],
                              metadatas=[d.metadata for d in pending],
                              embeddings=[cached[cache_key(d.text)] for d in pending])
        progress(f"지식 문서 {min(offset + batch_size, len(docs))}/{len(docs)} 처리")
    if collection.count() != len(docs):
        raise ValueError("지식 벡터 저장 개수가 문서 개수와 일치하지 않습니다.")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(identity, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(manifest_path)
    return {**identity, "embedded": embedded, "reused": reused}


def validate_knowledge_index(knowledge, db_path: Path = VECTOR_PATH,
                             manifest_path: Path = KNOWLEDGE_MANIFEST_PATH, spec=None):
    if not manifest_path.exists():
        raise ValueError("RAG 지식 색인이 없습니다. 먼저 python main.py embed 를 실행하세요.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    current = knowledge_identity(knowledge, gpt_api.embedding_spec() if spec is None else spec)
    if manifest != current:
        changed = [k for k in current if manifest.get(k) != current[k] and k != 'collection']
        raise ValueError("지식 문서 또는 임베딩 설정이 변경되었습니다 (" + ', '.join(changed) +
                         "). python main.py embed 를 실행하세요. 변경된 문서만 다시 임베딩합니다.")
    collection = open_collection(open_client(db_path), manifest['collection'])
    if collection.count() != len(knowledge.documents):
        raise ValueError("저장된 지식 문서가 누락되었습니다. python main.py embed 를 실행하세요.")
    return collection


def _hits(result, pool):
    return [{"id": rid, "text": text, "metadata": meta, "distance": dist, "pool": pool}
            for rid, text, meta, dist in zip(result["ids"][0], result["documents"][0],
                                             result["metadatas"][0], result["distances"][0])]


def retrieve(collection, question: str, *, region_ids=(), month=None, year=None, general_k=4,
             general_kinds=None, max_distance=KNOWLEDGE_MAX_DISTANCE, embedder=None, limit=8):
    """질문 벡터 1개로 여러 후보군을 검색합니다.

    - region: 확정된 지역의 프로필 (조건 일치 문서이므로 거리 임계값 미적용)
    - period: 확정된 월/연도의 시장 요약 (조건 일치 문서이므로 거리 임계값 미적용)
    - general: 조건 없이 의미가 가까운 문서 (max_distance 이내만 사용)
    반환: (근거 문서 목록, 진단 정보)
    """
    embedder = gpt_api.embed_texts if embedder is None else embedder
    vectors = embedder([question])
    if len(vectors) != 1:
        raise ValueError("질문 임베딩 결과 개수가 올바르지 않습니다.")
    found, rejected = [], []
    if region_ids:
        where = {"$and": [{"kind": {"$eq": "region_profile"}}, {"region_id": {"$in": list(region_ids)}}]}
        found += _hits(collection.query(vectors, len(region_ids), include=None, where=where), "region")
    period = []
    if month:
        period.append({"$and": [{"kind": {"$eq": "market_monthly"}}, {"month": {"$eq": month}}]})
    if year:
        period.append({"$and": [{"kind": {"$eq": "market_annual"}}, {"year": {"$eq": year}}]})
    if period:
        found += _hits(collection.query(vectors, len(period), include=None, where={"$or": period}), "period")
    if general_k:
        where = {"kind": {"$in": list(general_kinds)}} if general_kinds else None
        for hit in _hits(collection.query(vectors, general_k, include=None, where=where), "general"):
            (found if hit["distance"] <= max_distance else rejected).append(hit)
    unique, seen = [], set()
    for hit in found:
        if hit["id"] not in seen:
            seen.add(hit["id"])
            unique.append(hit)
    diagnostics = {"max_distance": max_distance,
                   "used": [(h["id"], h["pool"], round(h["distance"], 4)) for h in unique[:limit]],
                   "rejected": [(h["id"], round(h["distance"], 4)) for h in rejected]}
    return unique[:limit], diagnostics
