"""사람이 읽는 TXT/HTML 기록과 뷰어용 JSON 저장."""
import base64
import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4
from threading import RLock
from html import escape
from bubot.config import PROJECT_NAME
from bubot.source_display import display_sources

BASE = Path(__file__).resolve().parents[2]
DIRECTORY = BASE / "테스트기록"
LOCK = RLock()
ROUTE_LABELS = {"numeric": "원본 수치 계산 + 지식 검색 보강", "explain": "지식 문서 검색(RAG)",
                "rule": "원본 직접 집계·안내 (AI 답변 생성 없음)", "clarify": "확인 질문·범위 안내"}
STYLE = ("body{max-width:1000px;margin:40px auto;padding:0 20px;background:#f4f7f8;font-family:sans-serif;color:#243c45}"
         "article{background:white;padding:24px;margin:20px 0;border-radius:12px}"
         "pre{white-space:pre-wrap;overflow-wrap:anywhere;font:15px/1.8 sans-serif}")
# 파일로 열 때와 서버(/history)로 볼 때 같은 인라인 스타일을 쓰므로, 서버 CSP는 이 스타일의 해시만 허용합니다.
CSP = ("default-src 'none'; style-src 'sha256-" + base64.b64encode(hashlib.sha256(STYLE.encode("utf-8")).digest()).decode()
       + "'; frame-ancestors 'none'")

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
        "", "[처리 경로]", route_text(result.get("route", "")), "", "[검색된 지식 문서]", *result.get("retrieved", []),
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
        heading = escape(r['created_at'].replace('T', ' '))
        question = escape(r['question'])
        answer = escape(result.get('answer') or result.get('error', ''))
        sections += ('<article><small>' + heading + '</small><h2>' + question + '</h2><pre>' + answer
                     + '</pre><details><summary>출처·검색 문서·처리 정보 보기</summary><pre>'
                     + escape(readable(r)) + '</pre></details></article>')
    return ('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>BuBot 테스트 기록</title><style>' + STYLE + '</style>'
            '<h1>BuBot 테스트 기록</h1><p>프로젝트: ' + escape(PROJECT_NAME) + ' · 전체 ' + str(len(data))
            + '건 · 최신순 · 새 기록 저장 후 새로고침하세요.</p>' + sections + '</html>')

def refresh_view():
    atomic(DIRECTORY / "전체기록.html", render(load_all()))

def save(payload, result, status, elapsed):
    result = display_sources(result)
    record = {"id": uuid4().hex,
              "created_at": datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="microseconds"),
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
