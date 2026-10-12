from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_NAME = "부동산 평균 매매가 조회 AI 개발"  # 문서·웹 화면 공통 프로젝트 표시명 (챗봇 이름은 BuBot)
CSV_PATH = BASE_DIR / "data" / "new_prices.csv"
OUTPUT_DIR = BASE_DIR / "storage"
VECTOR_PATH = OUTPUT_DIR / "vectors"
DOCUMENTS_PATH = OUTPUT_DIR / "documents.jsonl"
BATCH_SIZE = 64
DOCUMENT_VERSION = "apartment-v2"

# ─────────────────────────────────────────────────────────────
# RAG 지식 색인 (서술형 질문 검색용)
# ─────────────────────────────────────────────────────────────
KNOWLEDGE_DIR = BASE_DIR / "knowledge"
KNOWLEDGE_VERSION = "knowledge-v1"
KNOWLEDGE_MANIFEST_PATH = OUTPUT_DIR / "knowledge_index.json"
KNOWLEDGE_DOCUMENTS_PATH = OUTPUT_DIR / "knowledge_documents.jsonl"
# 코사인 거리(0~2) 기준. 이보다 먼 문서는 근거로 쓰지 않습니다. 실제 질문으로 보정하세요.
KNOWLEDGE_MAX_DISTANCE = float(os.getenv("BUBOT_KNOWLEDGE_MAX_DISTANCE", "0.75"))
KNOWLEDGE_TOP_K = 4

# ─────────────────────────────────────────────────────────────
# 대화 메모리 (서버 프로세스 메모리에만 보관, 재시작 시 초기화)
# ─────────────────────────────────────────────────────────────
MEMORY_TURNS = max(3, int(os.getenv("BUBOT_MEMORY_TURNS", "3")))   # 기억할 이전 질문·답변 수 (최소 3)
MEMORY_TTL_SECONDS = int(os.getenv("BUBOT_MEMORY_TTL_SECONDS", "7200"))  # 마지막 질문 후 유지 시간
MEMORY_MAX_SESSIONS = 200

# ─────────────────────────────────────────────────────────────
# 가격 표시 단위
# ─────────────────────────────────────────────────────────────
DISPLAY_PRICE_UNIT = os.getenv("BUBOT_PRICE_UNIT", "korean")   # ← "만원" → "korean"
DISPLAY_PRICE_UNITS = ("천원", "만원", "억원", "korean")
