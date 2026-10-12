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
KNOWLEDGE_TOP_K = 8            # 서술형 의미 검색 후보 수 (로컬 벡터 검색이라 API 비용 없음)
KNOWLEDGE_SEARCH_LIMIT = 12    # 한 질문에 쓰는 지식 문서 최대 수
KNOWLEDGE_FALLBACK_K = 3       # 임계값 안 문서가 하나도 없을 때 대신 쓰는 가장 가까운 문서 수

# ─────────────────────────────────────────────────────────────
# 조회 범위·토큰 제어 (토큰은 GPT에 넘기는 근거 본문에서만 제한)
# ─────────────────────────────────────────────────────────────
MAX_REGIONS = 20               # 한 질문에서 계산하는 지역 수. 넘으면 앞의 20곳만 계산하고 안내
DEFAULT_TOP_N = 10             # 순위 질문에 개수가 없을 때 보여줄 지역 수
CONTEXT_CHAR_BUDGET = int(os.getenv("BUBOT_CONTEXT_CHARS", "16000"))  # 답변 생성에 넘기는 근거 본문 총 글자 수

# ─────────────────────────────────────────────────────────────
# 답변 일관성 (API 호출 간격·재시도는 gpt_api.py의 API_MIN_INTERVAL·API_MAX_RETRIES)
# ─────────────────────────────────────────────────────────────
ANSWER_CACHE_SIZE = int(os.getenv("BUBOT_ANSWER_CACHE_SIZE", "256"))  # 0이면 끔

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
