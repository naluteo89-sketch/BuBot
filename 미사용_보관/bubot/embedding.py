import hashlib
import json
from pathlib import Path

import gpt_api
from .config import BATCH_SIZE, VECTOR_PATH, CSV_PATH, DOCUMENT_VERSION, MANIFEST_PATH
from .documents import read_corpus
from .vector_store import open_client, open_collection


def index_identity(corpus, spec: dict) -> dict:
    # 원본뿐 아니라 본문·분할·출처 메타데이터 변경도 색인 버전에 반영합니다.
    content = [{"id": d.id, "text": d.text, "metadata": d.metadata} for d in corpus.documents]
    document_hash = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    identity = {"backend": "sqlite-cosine-v1", "source_sha256": corpus.source_hash, "document_version": DOCUMENT_VERSION,
                "documents_sha256": document_hash, "embedding": spec, "document_count": len(corpus.documents)}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return {**identity, "collection": f"apartments-{digest[:32]}"}


def build_index(csv_path: Path = CSV_PATH, db_path: Path = VECTOR_PATH,
                manifest_path: Path = MANIFEST_PATH, batch_size: int = BATCH_SIZE,
                embedder=None, spec: dict | None = None, progress=print) -> dict:

    if not 1 <= batch_size <= 128:
        raise ValueError("배치 크기는 1~128이어야 합니다.")
    corpus = read_corpus(csv_path)
    if embedder is None:
        with gpt_api.create_client():
            pass
    spec = gpt_api.embedding_spec() if spec is None else spec
    embedder = gpt_api.embed_texts if embedder is None else embedder
    identity = index_identity(corpus, spec)
    client = open_client(db_path)
    collection = open_collection(client, identity["collection"], create=True)
    embedded, reused = 0, 0
    for offset in range(0, len(corpus.documents), batch_size):
        batch = corpus.documents[offset:offset+batch_size]
        present = set(collection.get(ids=[doc.id for doc in batch], include=[])["ids"])
        pending = [doc for doc in batch if doc.id not in present]
        reused += len(present)
        if pending:
            vectors = embedder([doc.text for doc in pending])
            if len(vectors) != len(pending) or any(len(v) != spec["dimensions"] for v in vectors):
                raise ValueError("임베딩 결과 개수/차원이 설정과 다릅니다.")
            collection.upsert(ids=[doc.id for doc in pending], documents=[doc.text for doc in pending],
                              metadatas=[doc.metadata for doc in pending], embeddings=vectors)
            embedded += len(pending)
        progress(f"문서 {min(offset+batch_size, len(corpus.documents))}/{len(corpus.documents)} 처리")
    if collection.count() != len(corpus.documents):
        raise ValueError("벡터 저장 개수가 문서 개수와 일치하지 않습니다.")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(identity, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(manifest_path)
    return {**identity, "embedded": embedded, "reused": reused}


if __name__ == "__main__":
    from main import main
    raise SystemExit(main(["embed"]))
