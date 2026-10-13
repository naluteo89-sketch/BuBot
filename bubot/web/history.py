"""사람이 읽는 TXT/HTML 기록과 뷰어용 JSON 저장."""
import base64
import hashlib
import json
from pathlib import Path
from uuid import uuid4
from threading import RLock
from html import escape
from bubot.answers.answer_format import display_sources
from .layout import render_sidebar
from ..dates import now_kst

BASE = Path(__file__).resolve().parents[2]
DIRECTORY = BASE / "testlog"
LOCK = RLock()
ROUTE_LABELS = {"numeric": "원본 수치 계산 + 지식 검색 보강", "explain": "지식 문서 검색(RAG)",
                "rule": "원본 직접 집계·안내 (AI 답변 생성 없음)", "clarify": "확인 질문·범위 안내"}
STYLE = ("body{margin:0;background:#f8faf8;font-family:sans-serif;color:#243c35}"
         "article{background:white;padding:24px;margin:20px 0;border-radius:12px}"
         "pre{white-space:pre-wrap;overflow-wrap:anywhere;font:15px/1.8 sans-serif}")
# 저장 HTML의 기본 스타일은 해시로, 웹 화면의 공통 CSS와 아이콘은 동일 출처로 허용합니다.
# 스크립트는 동일 출처 파일(/history.js)만 허용합니다. 사이드바의 대화 제목·질문 수·상태를 다른 페이지와 같게 맞춥니다.
CSP = ("default-src 'none'; style-src 'sha256-" + base64.b64encode(hashlib.sha256(STYLE.encode("utf-8")).digest()).decode()
       + "' 'self'; img-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")

def atomic(path, text):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8-sig")
    temp.replace(path)

def readable(record):
    result = record["result"]
    return "\n".join([
        "BuBot 테스트 기록", "시간: " + record["created_at"],
        "상태: " + ("응답 생성 완료 (정답 판정 아님)" if record["status"] == "success" else "오류"),
        "선택 단위: " + (record.get("unit") or "자동"),
        "처리 시간: " + str(record["elapsed_seconds"]) + "초"
        + "".join(f" · {stage} {seconds}초" for stage, seconds in (result.get("timings") or {}).items()),
        "", "[질문]", record["question"],
        "", "[답변 / 오류]", result.get("answer") or result.get("error", ""),
        *(["", "[한/영 자판 변환]", "예 → " + result.get("standalone_question", "")] if result.get("input_converted") else []),
        "", "[이전 대화 반영]", ("예 → " + result.get("standalone_question", "")) if result.get("followup") else "아니오",
        "", "[처리 경로]", route_text(result.get("route", "")),
        *(["", "[답변 검증]", result["answer_check"]] if result.get("answer_check") else []),
        *(["", "[답변 캐시]", "예 (같은 질문의 이전 답변 재사용)"] if result.get("cached") else []),
        "", "[검색된 지식 문서]", *result.get("retrieved", []),
        "", "[원본 계산값]", *result.get("facts", []), "", "[출처]", *result.get("sources", [])])

def route_text(route):
    return f"{route} ({ROUTE_LABELS[route]})" if route in ROUTE_LABELS else route

def load_all():
    data = [json.loads(p.read_text(encoding="utf-8-sig")) for p in (DIRECTORY / "원본").glob("*.json")]
    return sorted(data, key=lambda r: (r["created_at"], r["id"]), reverse=True)

def write_record(record):
    raw = DIRECTORY / "원본"
    raw.mkdir(parents=True, exist_ok=True)
    name = record["created_at"][:19].replace(":", "-").replace("T", "_") + "_" + record["id"]
    atomic(DIRECTORY / (name + ".txt"), readable(record))
    atomic(raw / (record["id"] + ".json"), json.dumps(record, ensure_ascii=False, indent=2))

def render(data):
    sections = ""
    for r in data:
        result = r['result']
        heading = escape(r['created_at'][:16].replace('T', ' '))
        question = escape(r['question'])
        answer = escape(result.get('answer') or result.get('error', ''))
        evidence = result.get('facts', []) + result.get('sources', [])
        extra = ('<details class="answer-details"><summary>계산 근거와 출처 확인</summary><ul>'
                 + ''.join('<li>' + escape(str(item)) + '</li>' for item in evidence)
                 + '</ul></details>') if evidence else ''
        sections += ('<article class="history-card"><small>' + heading + '</small><h2>' + question
                     + '</h2><pre>' + answer + '</pre>' + extra + '</article>')
    if not sections:
        sections = ('<div class="history-empty"><span aria-hidden="true">◷</span><h2>아직 질문이 없어요</h2>'
                    '<p>궁금한 지역의 가격을 물어보세요.<br>질문과 답변을 여기서 다시 확인할 수 있어요.</p>'
                    '<a class="primary-link" href="/">첫 질문 시작하기 ↗</a></div>')
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>지난 질문 · BuBot</title><link rel="icon" type="image/svg+xml" href="/favicon.svg"><style>' + STYLE + '</style>'
            '<link rel="stylesheet" href="/css/styles.css"></head><body>'
            '<a class="skip-link" href="#history-content">지난 질문으로 건너뛰기</a>'
            '<div class="layout">' + render_sidebar("history") +
            '<main id="history-content" tabindex="-1"><header><span>다시 살펴보는 우리 동네</span>'
            '<a class="text-button" id="history-chat-link" href="/chat.html" hidden>진행 중인 대화로</a></header>'
            '<section class="intro"><div class="eyebrow">MY QUESTIONS</div><h1>지난 질문</h1>'
            '<p class="intro-description">궁금했던 지역과 답변을 다시 확인해 보세요.</p>'
            '<div class="history-count">전체 ' + str(len(data)) + '건 · 최근 질문부터 표시</div></section>'
            + sections + '<footer>BuBot <span>공공데이터로 살펴보는 아파트 가격</span></footer>'
            '</main></div><script type="module" src="/history.js"></script></body></html>')

def refresh_view():
    atomic(DIRECTORY / "전체기록.html", render(load_all()))

def save(payload, result, status, elapsed):
    result = display_sources(result)
    record = {"id": uuid4().hex,
              "created_at": now_kst().isoformat(timespec="microseconds"),
              "question": payload.get("question", "") if isinstance(payload, dict) else "",
              "unit": payload.get("unit") if isinstance(payload, dict) else None,
              "status": "success" if status == 200 else "error",
              "elapsed_seconds": round(elapsed, 3), "result": result}
    with LOCK:
        write_record(record)
        refresh_view()
    return record["id"]

def records(limit=100):
    with LOCK:
        data = load_all()
        return data if limit is None else data[:limit]

def html_view():
    return render(records(None))
