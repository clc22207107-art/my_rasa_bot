"""Local HTTP protocol and in-memory event delivery (standard library only)."""
import json
import os
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def request(url, data=None, timeout=10):
    req = Request(url, data=None if data is None else json.dumps(data).encode(),
                  headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=timeout) as response:
        return json.load(response)


def load_config(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class EventClient:
    """Retry event delivery in memory; never retry business commands."""
    def __init__(self, url, source, run_id, output, background=False):
        self.background = background
        self.url, self.source, self.run_id = url, source, run_id
        self.lock = threading.Lock()
        self.sequence = 0
        self.pending = []
        self.path = Path(output) / source
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if background:
            self.delivery_thread = threading.Thread(target=self._delivery_loop, daemon=True)
            self.delivery_thread.start()

    def _delivery_loop(self):
        while True:
            self.flush()
            time.sleep(.05)

    def emit(self, name, data=None, context=None, level="INFO"):
        stamp_ns, stamp_utc = time.monotonic_ns(), utc_now()
        with self.lock:
            self.sequence += 1
            event = dict(event_id=uuid.uuid4().hex, run_id=self.run_id,
                         session_id=None, turn_id=None, source=self.source,
                         pid=os.getpid(), sequence=self.sequence, time=stamp_utc,
                         monotonic_ns=stamp_ns, event_name=name,
                         level=level, operation_id=None, event_data=data or {})
            event.update(context or {})
            self.pending.append(event)
            if not self.background:
                self._flush()
            return event

    def _flush(self):
        while self.pending:
            try:
                request(self.url + "/events", self.pending[0], timeout=1)
                self.pending.pop(0)
            except Exception:
                break

    def flush(self):
        if not self.background:
            with self.lock:
                self._flush()
                return len(self.pending)
        # Only the delivery thread sends in background mode. Other callers wait
        # briefly for acknowledgement without blocking audio event production.
        if threading.current_thread() is not self.delivery_thread:
            deadline = time.monotonic() + 3
            while self.pending and time.monotonic() < deadline:
                time.sleep(.02)
            return len(self.pending)
        with self.lock:
            batch = list(self.pending)
        delivered = []
        for event in batch:
            try:
                request(self.url + "/events", event, timeout=1)
                delivered.append(event["event_id"])
            except Exception:
                break
        with self.lock:
            ids = set(delivered)
            self.pending = [e for e in self.pending if e["event_id"] not in ids]
            return len(self.pending)


class APIError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def serve(port, dispatch):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.handle_request()

        def do_POST(self):
            self.handle_request()

        def handle_request(self):
            try:
                size = int(self.headers.get("Content-Length", 0))
                if size < 0 or size > 16 * 1024 * 1024:
                    raise APIError("Request too large", 413)
                data = json.loads(self.rfile.read(size)) if size else {}
                result = dispatch(self.command, self.path, data)
                status = 200
            except APIError as exc:
                result, status = {"error": str(exc)}, exc.status
            except Exception as exc:
                traceback.print_exc()
                result, status = {"error": str(exc)}, 500
            body = json.dumps(result, ensure_ascii=False).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.serve_forever()
