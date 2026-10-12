"""외부 의존성 없는 로컬 전용 HTTP 서버. 정적 파일은 web 폴더만 제공합니다."""
import argparse
import ipaddress
import json
import socket
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


def lan_host(host, port):
    """--lan 모드에서 허용할 Host: 같은 포트의 사설 IPv4 주소(192.168.x.x·10.x.x.x·172.16~31.x.x)만."""
    name, _, host_port = host.rpartition(":")
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False  # 도메인 이름은 허용하지 않습니다 (DNS 재바인딩 방지)
    return (host_port == str(port) and address.version == 4 and address.is_private
            and not address.is_loopback and not address.is_link_local)


def lan_addresses():
    """같은 와이파이의 휴대폰에서 입력할 PC 주소 후보."""
    found = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.168.0.1", 9))  # 실제 전송 없이 기본 경로의 내 주소만 확인
            found.add(probe.getsockname()[0])
    except OSError:
        pass
    try:
        found.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    return sorted(a for a in found if lan_host(f"{a}:1", 1))

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
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origin = self.headers.get("Origin")
        ok = host in allowed or (getattr(self.server, "allow_lan", False) and lan_host(host, port))
        return ok and (not origin or origin == f"http://{host}")

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
    parser.add_argument("--lan", action="store_true",
                        help="같은 와이파이의 휴대폰 등에서 접속 허용 (사설 IP 주소로만, 테스트용)")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("0.0.0.0" if args.lan else "127.0.0.1", args.port), Handler)
    server.allow_lan = args.lan
    print(f"BuBot: http://127.0.0.1:{args.port} (종료: Ctrl+C)", flush=True)
    if args.lan:
        addresses = lan_addresses()
        for address in addresses:
            print(f"  같은 와이파이 기기: http://{address}:{args.port}", flush=True)
        if not addresses:
            print("  PC의 사설 IP를 찾지 못했습니다. ipconfig의 IPv4 주소를 확인하세요.", flush=True)
        print("  처음 실행 시 Windows 방화벽 창이 뜨면 '개인 네트워크' 허용을 선택하세요.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
