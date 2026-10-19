"""웹 응답 변환. 자격증명과 서버 경로는 브라우저에 전달하지 않습니다."""
import os
import time
from collections import defaultdict, deque
from threading import Lock

from bubot.answers.answer_format import source_note, unify_units
from bubot.config import DISPLAY_PRICE_UNIT, DISPLAY_PRICE_UNITS, PROJECT_NAME
from bubot.conversation import MEMORY, chat
from bubot.answers.answer_format import SOURCE_LABEL

_coverage_notes = {}  # 원본 해시 → 결측 안내 문구 (페이지를 열 때마다 4만여 개 문서를 다시 훑지 않도록)

# 질문 빈도 제한 (API 비용 보호). 대화 하나와 서버 전체의 최근 1분 질문 수를 셉니다. 0이면 제한 없음.
# 대화별 기본값은 다른 챗봇 빌더(2026-10 확인: ChatLab 권장 150초 40개 ≈ 1분 16개, Chatling 예시 1분 10개,
# 평균 약 13개)보다 살짝 낮게 1분 10개입니다. 서버 전체 값은 동시 처리 4건 × 질문당 약 4초의 처리 용량 기준입니다.
SESSION_PER_MINUTE = int(os.getenv("BUBOT_SESSION_PER_MINUTE", "10"))
GLOBAL_PER_MINUTE = int(os.getenv("BUBOT_GLOBAL_PER_MINUTE", "60"))
_recent = {}            # 세션 ID → 최근 질문 시각 목록
_recent_all = deque()   # 서버 전체 최근 질문 시각
_recent_lock = Lock()


class RateLimited(Exception):
    """짧은 시간에 질문이 너무 많음 (HTTP 429)."""


def check_rate(payload, now=None):
    """최근 1분 질문 수가 한도를 넘으면 RateLimited. 통과하면 이번 질문을 셉니다."""
    now = time.monotonic() if now is None else now
    session = payload.get("session_id") if isinstance(payload, dict) else None
    with _recent_lock:
        for stamps in (_recent_all, *_recent.values()):
            while stamps and now - stamps[0] > 60:
                stamps.popleft()
        for key in [k for k, v in _recent.items() if not v]:
            del _recent[key]
        mine = _recent.setdefault(str(session), deque())
        if SESSION_PER_MINUTE and len(mine) >= SESSION_PER_MINUTE:
            raise RateLimited(f"질문이 너무 빠르게 이어지고 있어요. 1분에 {SESSION_PER_MINUTE}개까지 질문할 수 있으니 잠시 후 다시 시도해 주세요.")
        if GLOBAL_PER_MINUTE and len(_recent_all) >= GLOBAL_PER_MINUTE:
            raise RateLimited("지금 질문하는 사람이 많아요. 잠시 후 다시 시도해 주세요.")
        mine.append(now)
        _recent_all.append(now)


def route_of(result):
    """규칙 기반 선처리와 확인 질문은 rag_service가 route를 붙이지 않으므로 화면용 이름을 정합니다."""
    if result.get("route"):
        return result["route"]
    return "clarify" if (result.get("analysis") or {}).get("intent") == "clarify" else "rule"


def answer(payload):
    if not isinstance(payload, dict):
        raise ValueError("JSON 객체가 필요합니다.")
    question = payload.get("question")
    unit = payload.get("unit") or None
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000:
        raise ValueError("질문을 1~1000자로 입력하세요.")
    if unit is not None and unit not in DISPLAY_PRICE_UNITS:
        raise ValueError("지원하지 않는 가격 단위입니다.")
    session_id = MEMORY.check(payload.get("session_id"))
    remembered = MEMORY.size(session_id)  # 이번 질문 전에 서버가 기억하던 대화 수
    try:
        result = chat(session_id, question.strip(), unit=unit)
    except ValueError as error:
        # 지역 없음·보유 기간 밖 같은 안내도 오류 화면이 아니라 답변으로 보여 줍니다 (대화 메모리에는 chat이 기록).
        # 화면의 '기억 만료' 안내가 잘못 뜨지 않도록 이 경로에도 메모리 사용 수를 채웁니다.
        result = {"answer": str(error), "analysis": {"intent": "clarify", "reason": "guidance"},
                  "standalone_question": getattr(error, "standalone_question", "") or question.strip(),
                  "memory_used": remembered, "memory_turns": MEMORY.size(session_id)}
    response = {key: result.get(key, default) for key, default in
                (("answer", ""), ("facts", []), ("sources", []),
                 ("standalone_question", ""), ("followup", False), ("input_converted", False),
                 ("memory_turns", 0), ("memory_used", 0), ("timings", {}), ("cached", False),
                 ("choices", []), ("highlight", []))}
    response["route"] = route_of(result)
    # 화면 표기 정리: 금액 단위를 한 가지로 맞추고, 답변마다 같은 형식의 한 줄 출처를 붙입니다.
    display = (result.get("analysis") or {}).get("display_unit") or unit or DISPLAY_PRICE_UNIT
    response["answer"] = unify_units(response["answer"], display)
    response["source_note"] = source_note(response["sources"])
    response["answer_check"] = (result.get("analysis") or {}).get("answer_check", "")
    retrieval = (result.get("analysis") or {}).get("retrieval") or {}
    response["retrieved"] = [f"{doc_id} ({pool}, 거리 {distance})" for doc_id, pool, distance in retrieval.get("used", [])]
    return response

def reset(payload):
    if not isinstance(payload, dict):
        raise ValueError("JSON 객체가 필요합니다.")
    MEMORY.reset(MEMORY.check(payload.get("session_id")))
    return {"reset": True}


def coverage_note(corpus):
    """결측이 있는 지역 수와, 값이 있는 기간이 가장 짧은 지역 묶음을 원본에서 계산해 안내합니다."""
    if corpus.source_hash in _coverage_notes:
        return _coverage_notes[corpus.source_hash]
    months, names = defaultdict(list), {}
    for d in corpus.documents:
        m = d.metadata
        if m["kind"] == "monthly":
            names[m["region_id"]] = m["region_path"]
            if not m["missing"]:
                months[m["region_id"]].append(m["month"])
    partial = [rid for rid in names if len(months[rid]) < len(corpus.months)]
    note = "모든 지역에 전체 기간 값이 있습니다."
    if partial:
        note = f"{len(names)}개 지역 중 {len(partial)}곳은 신설·폐지·개편으로 일부 월 값이 없습니다."
        groups = defaultdict(list)
        for rid in partial:
            if months[rid]:
                groups[(len(months[rid]), min(months[rid]), max(months[rid]))].append(names[rid])
        if groups:
            (count, start, end), regions = min(groups.items())
            more = " 등" if len(regions) > 3 else ""
            note += f" 예: {', '.join(regions[:3])}{more} {len(regions)}곳은 {start} ~ {end}({count}개월)만 있습니다."
    _coverage_notes.clear()
    _coverage_notes[corpus.source_hash] = note
    return note


# API 신호등 (사이드바). 초록: 정상 응답, 노랑: 느리거나 한도·서버 오류, 빨강: 키 없음·인증 실패·연결 끊김.
HEALTH_SLOW_SECONDS = float(os.getenv("BUBOT_HEALTH_SLOW_SECONDS", "3"))
HEALTH_CACHE_SECONDS = float(os.getenv("BUBOT_HEALTH_CACHE_SECONDS", "20"))
_health = {"at": None, "result": None}
_health_lock = Lock()


def classify_health(ping):
    if ping["ok"]:
        if ping["latency"] is not None and ping["latency"] > HEALTH_SLOW_SECONDS:
            return "yellow", f"응답이 느려요 ({ping['latency']:.1f}초)"
        return "green", "AI 연결 정상"
    code, error = ping.get("status_code"), ping.get("error") or ""
    if error == "no_key":
        return "red", "API 키가 설정되지 않았어요"
    if code in (401, 403):
        return "red", "API 키 인증에 실패했어요"
    if code == 429 or (isinstance(code, int) and code >= 500) or "Timeout" in error:
        return "yellow", "응답 지연 또는 사용 한도 문제가 있어요"
    if code is not None:
        return "yellow", f"AI 응답에 문제가 있어요 (HTTP {code})"
    return "red", "AI 서버와 연결이 끊겼어요"


def api_health(now=None, ping=None):
    """API 연결 상태. 여러 화면이 동시에 물어도 HEALTH_CACHE_SECONDS 동안은 마지막 점검 결과를 씁니다."""
    now = time.monotonic() if now is None else now
    with _health_lock:
        if _health["at"] is not None and now - _health["at"] < HEALTH_CACHE_SECONDS:
            return _health["result"]
        if ping is None:
            from bubot.llm.gpt_api import ping_api as ping
        result = ping()
        level, message = classify_health(result)
        _health.update(at=now, result={"level": level, "message": message, "latency": result.get("latency")})
        return _health["result"]


def status():
    from bubot.llm.gpt_api import check_configuration
    from bubot.cache.data_cache import load_corpus
    from bubot.rag_service import open_index
    info = {"project": PROJECT_NAME, "source": SOURCE_LABEL, "memory_turns": MEMORY.turns,
            "key_ready": bool(check_configuration()["키 인식"])}
    try:
        from bubot.llm.usage import totals
        info["usage"] = totals()  # 오늘·이번 달 추정 API 비용과 한도 (USD)
    except Exception:
        pass
    try:
        corpus = load_corpus()
        info.update(period=f"{corpus.months[0]} ~ {corpus.months[-1]}", coverage_note=coverage_note(corpus))
    except Exception:
        info.update(period="원본 데이터 점검 필요", coverage_note="")
    try:
        open_index()
        info["index_ready"] = True
    except ValueError as error:
        info.update(index_ready=False, index_message=str(error))
    except Exception:
        info["index_ready"] = False
    return info
