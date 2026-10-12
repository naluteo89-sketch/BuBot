"""외부 의존성 없는 로컬 전용 HTTP 서버. 정적 파일은 web 폴더만 제공합니다."""
import argparse
import json
import mimetypes
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import unquote, urlsplit
from . import service, history
from .layout import render_layout

ROOT = Path(__file__).resolve().parents[2] / "web"
BUSY = Lock()
CSP = "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, code, body, content_type="application/json; charset=utf-8", csp=CSP):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", csp)
        self.end_headers()
        self.wfile.write(body)

    def trusted(self):
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        origin = self.headers.get("Origin")
        return host in allowed and (not origin or origin == f"http://{host}")

    def do_GET(self):
        if not self.trusted():
            return self.send(403, {"error": "허용되지 않은 요청입니다."})
        route = urlsplit(self.path).path
        if route == "/history":
            try:
                return self.send(200, history.html_view().encode("utf-8"), "text/html; charset=utf-8", history.CSP)
            except Exception:
                return self.send(503, {"error": "기록을 불러오지 못했습니다."})
        if route in {"/api/history", "/api/history/export"}:
            try:
                return self.send(200, {"records": history.records(None if route.endswith("/export") else 100)})
            except Exception:
                return self.send(503, {"error": "저장된 기록을 불러오지 못했습니다."})
        if route == "/api/status":
            try:
                return self.send(200, service.status())
            except Exception:
                return self.send(503, {"error": "서버 설정을 확인하세요."})
        path = (ROOT / (unquote(route).lstrip("/") or "index.html")).resolve()
        if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
            return self.send(404, {"error": "파일을 찾을 수 없습니다."})
        mime = "text/javascript" if path.suffix == ".js" else mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        body = path.read_bytes()
        if path.suffix == ".html":
            active = "data" if path.name == "data.html" else "home"
            body = render_layout(body.decode("utf-8-sig"), active).encode("utf-8")
        self.send(200, body, mime + "; charset=utf-8")

    def do_POST(self):
        if not self.trusted():
            return self.send(403, {"error": "허용되지 않은 요청입니다."})
        if self.path not in {"/api/chat", "/api/reset"}:
            return self.send(404, {"error": "없는 API입니다."})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.send(415, {"error": "JSON 요청이 필요합니다."})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 16000:
                raise ValueError()
            payload = json.loads(self.rfile.read(size))
        except (ValueError, UnicodeError):
            return self.send(400, {"error": "요청 형식 또는 크기를 확인하세요."})
        if self.path == "/api/reset":
            try:
                return self.send(200, service.reset(payload))
            except ValueError as error:
                return self.send(400, {"error": str(error)})
        if not BUSY.acquire(blocking=False):
            return self.send(429, {"error": "다른 질문을 처리 중입니다. 잠시 후 다시 시도하세요."})
        started = time.perf_counter()
        try:
            try:
                code, result = 200, service.answer(payload)
            except ValueError as error:
                code, result = 400, {"error": str(error)}
            except Exception as error:
                # 화면에는 상세를 보내지 않고, 원인은 서버 콘솔과 테스트 기록(오류 종류)에 남깁니다.
                print(f"[BuBot] /api/chat 처리 실패: {type(error).__name__}", file=sys.stderr, flush=True)
                traceback.print_exc()
                code, result = 502, {"error": "답변 생성에 실패했습니다. API 설정·사용 한도·네트워크를 확인하세요.",
                                     "error_type": type(error).__name__}
            elapsed = time.perf_counter() - started
            result = {**result, "elapsed_seconds": round(elapsed, 3)}
            try:
                record_id = history.save(payload, result, code, elapsed)
                result = {**result, "saved": True, "record_id": record_id}
            except Exception:
                warning = "테스트 기록 저장에 실패했습니다. 저장 공간과 폴더 권한을 확인하세요."
                result = {**result, "saved": False, "warning": warning}
                if code != 200:
                    result["error"] += " " + warning
            self.send(code, result)
        finally:
            BUSY.release()


def main():
    parser = argparse.ArgumentParser(description="BuBot 로컬 테스트 웹페이지")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"BuBot: http://127.0.0.1:{args.port} (종료: Ctrl+C)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
