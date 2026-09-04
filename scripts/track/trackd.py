#!/usr/bin/env python3
"""trackd — 트랙 원장 실시간 서버.

읽기 전용. 트랙 문서를 절대 쓰지 않는다 (verify 는 ~/.claude/track-cache/ 에만 쓴다).

  trackd.py [--port 4747] [--track <이름 일부>]

라우트:
  GET  /            ui/index.html
  GET  /api/state    현재 상태 JSON
  POST /api/verify   프로브 즉시 실행 → 새 상태 JSON
  GET  /events        SSE. 상태가 바뀔 때만 push, 15초마다 ping
"""
import argparse
import hashlib
import json
import os
import queue
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(HERE, "ui")
sys.path.insert(0, HERE)
import trackcore as T  # noqa: E402

# ─────────────────────────────────────────────────────────────
# 공유 상태
# ─────────────────────────────────────────────────────────────
state_lock = threading.Lock()
subscribers_lock = threading.Lock()
subscribers = []  # list[queue.Queue[str]]

current = {"state": None, "hash": None}
verify_event = threading.Event()
stop_event = threading.Event()

TRACK_ID_HINT = None
TRACK_PATH = None

graph_filter_lock = threading.Lock()
graph_filter = {"mode": "related", "activeHours": 6.0}


def _track_path():
    return T.find_track(TRACK_ID_HINT) or TRACK_PATH


def list_tracks():
    """모든 트랙 요약 — UI 의 전환 목록."""
    import glob as _g
    out = []
    for p in sorted(_g.glob(os.path.join(T.TRACKS_DIR, "*", "*.md"))):
        try:
            tr = T.parse_track(p)
        except Exception:
            continue
        cache = T.load_cache(tr["id"])
        steps = T.resolve(tr, cache)
        out.append({"id": tr["id"], "title": tr["title"], "path": p,
                    "total": len(steps),
                    "done": sum(1 for s in steps if s["status"] == "완료"),
                    "unknown": sum(1 for s in steps if s["status"] == "미검증")})
    return out


def switch_track(tid):
    """현재 보고 있는 트랙을 바꾼다. SSE 구독자도 함께 따라간다."""
    global TRACK_ID_HINT
    if not tid:
        return False
    p = T.find_track(tid)
    if not p:
        return False
    TRACK_ID_HINT = tid
    return True


def build_state():
    p = _track_path()
    if not p:
        return None
    tr = T.parse_track(p)
    cache = T.load_cache(tr["id"])
    steps = T.resolve(tr, cache)
    ledgers = T.track_ledgers(tr)
    conf = T.conflicts(ledgers)
    stale = T.stale_steps(steps, ledgers)
    for s in steps:
        s["stale"] = stale.get(s["id"], [])
    cands = T.link_candidates(ledgers)[:8]
    focus = {a["session"] for a in tr["actors"] if a.get("session")}
    with graph_filter_lock:
        gf = dict(graph_filter)
    graph = T.session_graph(focus=focus, mode=gf["mode"], active_hours=gf["activeHours"])
    return {
        "track": {"id": tr["id"], "title": tr["title"], "path": tr["path"]},
        "steps": steps,
        "actors": tr["actors"],
        "signals": tr["signals"],
        "verifiedAt": cache.get("verifiedAt"),
        "graph": graph,
        "ledgers": ledgers,
        "conflicts": conf,
        "staleCount": len(stale),
        "tracks": list_tracks(),
        "linkCandidates": cands,
    }


def _hash(obj):
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def publish(new_state):
    """new_state 를 현재 상태로 반영. 실제로 바뀌었을 때만 구독자에게 push."""
    if new_state is None:
        return False
    h = _hash(new_state)
    with state_lock:
        changed = h != current["hash"]
        current["hash"] = h
        out = dict(new_state)
        out["serverTime"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        current["state"] = out
    if changed:
        payload = ("data: " + json.dumps(out, ensure_ascii=False) + "\n\n").encode()
        with subscribers_lock:
            subs = list(subscribers)
        for q in subs:
            try:
                q.put_nowait(payload)
            except Exception:
                pass
    return changed


def do_verify():
    p = _track_path()
    if not p:
        return
    tr = T.parse_track(p)
    T.verify(tr)


def _sess_listing():
    try:
        return tuple(
            sorted(
                (f, os.path.getmtime(os.path.join(T.SESS_DIR, f)))
                for f in os.listdir(T.SESS_DIR)
            )
        )
    except OSError:
        return ()


def worker():
    last_doc_mtime = None
    last_sess = None
    last_verify = 0.0
    publish(build_state())
    while not stop_event.wait(2):
        p = _track_path()
        try:
            doc_mtime = os.path.getmtime(p) if p else None
        except OSError:
            doc_mtime = None
        sess = _sess_listing()

        need_verify = verify_event.is_set() or (time.time() - last_verify > 90)
        if need_verify:
            verify_event.clear()
            try:
                do_verify()
            except Exception:
                pass
            last_verify = time.time()
            last_doc_mtime = doc_mtime
            last_sess = sess
            publish(build_state())
            continue

        if doc_mtime != last_doc_mtime or sess != last_sess:
            last_doc_mtime = doc_mtime
            last_sess = sess
            publish(build_state())


# ─────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "trackd/1.0"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _apply_filter_from_qs(self, qs):
        """쿼리 파라미터로 서버 기본 필터를 갱신한다. 잘못된 값은 무시하고 사유를 돌려준다."""
        invalid = []
        updates = {}
        if "mode" in qs:
            m = (qs["mode"][0] or "").strip()
            if m in T._GRAPH_MODES:
                updates["mode"] = m
            else:
                invalid.append(f"mode={m!r}: related|recent|all 중 하나가 아니다 — 기본값 유지")
        if "hours" in qs:
            raw = qs["hours"][0]
            try:
                h = float(raw)
                if h <= 0:
                    raise ValueError("hours<=0")
                updates["activeHours"] = h
            except (TypeError, ValueError):
                invalid.append(f"hours={raw!r}: 0보다 큰 숫자가 아니다 — 기본값 유지")
        if updates:
            with graph_filter_lock:
                graph_filter.update(updates)
        return invalid

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/":
            self._index()
        elif path == "/api/tracks":
            self._json({"tracks": list_tracks(), "current": (T.parse_track(_track_path())["id"]
                                                             if _track_path() else None)})
        elif path == "/api/state":
            qs = parse_qs(query)
            switched = False
            if qs.get("track"):
                switched = switch_track(qs["track"][0])
            invalid = self._apply_filter_from_qs(qs) if query else []
            if qs.get("track") and not switched:
                invalid = list(invalid) + [f"track='{qs['track'][0]}': 찾지 못함 — 기존 트랙 유지"]
            if query:
                s = build_state()
                publish(s)
            else:
                with state_lock:
                    s = current["state"]
            if invalid and s:
                s = dict(s)
                g = dict(s.get("graph") or {})
                filt = dict(g.get("filter") or {})
                filt["invalidParams"] = invalid
                g["filter"] = filt
                s["graph"] = g
            self._json(s or {})
        elif path == "/events":
            self._sse()
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/verify":
            try:
                do_verify()
                s = build_state()
                publish(s)
                self._json(s or {})
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def _index(self):
        try:
            with open(os.path.join(UI_DIR, "index.html"), "rb") as f:
                body = f.read()
        except OSError:
            body = b"ui/index.html missing"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        q = queue.Queue()
        with state_lock:
            s = current["state"]
        try:
            if s:
                self.wfile.write(
                    ("data: " + json.dumps(s, ensure_ascii=False) + "\n\n").encode()
                )
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

        with subscribers_lock:
            subscribers.append(q)
        try:
            while not stop_event.is_set():
                try:
                    payload = q.get(timeout=15)
                    self.wfile.write(payload)
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            pass
        finally:
            with subscribers_lock:
                if q in subscribers:
                    subscribers.remove(q)


def main():
    global TRACK_ID_HINT, TRACK_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4747)
    # 보드는 프로브 결과·레포명·브랜치명을 그대로 싣는다 — 기본은 루프백만.
    # 다른 기기에서 보려면 --host 0.0.0.0 을 명시한다.
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--track", default=None)
    args = ap.parse_args()

    TRACK_ID_HINT = args.track
    p = T.find_track(args.track)
    if not p:
        sys.exit(f"트랙 문서를 찾지 못했다: {T.TRACKS_DIR}/*/*.md")
    TRACK_PATH = p
    tr = T.parse_track(p)

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"http://localhost:{args.port}")
    print(tr["title"])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
