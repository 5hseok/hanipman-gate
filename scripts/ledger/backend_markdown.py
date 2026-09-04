"""markdown 백엔드 — 원장을 레포 안에 둔다.

문서는 `<root>/YYYY-MM/MMDD-<slug>.md`. root 기본값은 `<repo>/.claude/design`.
Obsidian 백엔드와 같은 섹션 구조·같은 마커를 쓰므로 파서를 공유한다.

Obsidian 전용 기능은 여기 없다. daily 노트, `[[wikilink]]` 역링크 보수,
대시보드 렌더는 전부 no-op 이고 그 사실을 stderr 로 알린다.
"""

import datetime
import os
import re
import sys

import docformat as D


class Backend:
    name = "markdown"

    def __init__(self, root, lang="ko"):
        self.root = root
        self.lang = lang

    # ── 파일 해석 ──────────────────────────────────────────────────────
    def _walk(self):
        for base, _dirs, files in os.walk(self.root):
            for fn in sorted(files):
                if fn.endswith(".md"):
                    yield os.path.join(base, fn)

    def resolve(self, ref):
        if os.path.isabs(ref) and os.path.exists(ref):
            return ref
        cand = os.path.join(self.root, ref)
        if os.path.exists(cand):
            return cand
        base = os.path.basename(ref)
        stem = base[:-3] if base.endswith(".md") else base
        exact = [p for p in self._walk() if os.path.basename(p) == base]
        if exact:
            return exact[0]
        pref = [p for p in self._walk() if os.path.basename(p).startswith(stem)]
        if pref:
            return pref[0]
        part = [p for p in self._walk() if stem in os.path.basename(p)]
        return part[0] if part else None

    def _need(self, ref):
        p = self.resolve(ref)
        if not p:
            sys.stderr.write("ERROR: 원장 문서를 찾지 못했다: %s\n(root: %s)\n" % (ref, self.root))
            sys.exit(2)
        return p

    @staticmethod
    def _read(p):
        with open(p, encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def _write(p, text):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    # ── 명령 ───────────────────────────────────────────────────────────
    def ls(self, month=None):
        month = month or datetime.date.today().strftime("%Y-%m")
        d = os.path.join(self.root, month)
        if not os.path.isdir(d):
            return 0
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".md"):
                print(os.path.join(d, fn))
        return 0

    def raw_path(self, ref):
        print(self._need(ref))
        return 0

    def current(self, ref, topic=None, with_queue=False, ids_only=False):
        path = self._need(ref)
        text = self._read(path)
        inner = D.extract_marked(text, D.CURRENT_START, D.CURRENT_END)
        if inner is None:
            sys.stderr.write("WARNING: 마커 없는 구형 문서: %s\n" % path)
            return 2
        entries = [D.parse_current_block(b) for b in D.split_blocks(inner)]
        if topic:
            entries = [e for e in entries if e["topic"] == topic]

        if ids_only:
            for e in entries:
                print("D-%d %s | %s" % (e["id"], e["topic"], e["title"]))
            return 0

        if entries:
            parts = ["\n\n".join(D.render_current_block(e) for e in entries)]
        else:
            parts = ["(현재 유효한 결정 없음)"]

        if with_queue:
            q = D.extract_heading_section(text, "queue")
            if q is not None:
                parts.append(D.heading_for("queue", self.lang) + "\n\n" + q)

        print("\n\n---\n\n".join(parts))
        return 0

    def decide(self, ref, topic, title, body, supersedes=None, mirrors=None, init=False):
        path = self.resolve(ref)
        if not path:
            if not init:
                sys.stderr.write(
                    "ERROR: 원장 문서를 찾지 못했다: %s\n"
                    "새로 만들려면 --init 을 붙여라.\n" % ref)
                sys.exit(2)
            today = datetime.date.today()
            stem = os.path.basename(ref)
            if not stem.endswith(".md"):
                stem = "%s-%s.md" % (today.strftime("%m%d"), stem)
            path = os.path.join(self.root, today.strftime("%Y-%m"), stem)
            self._write(path, D.new_document(
                title=os.path.splitext(os.path.basename(path))[0],
                lang=self.lang, date=today.isoformat()))
            sys.stderr.write("새 원장 문서: %s\n" % path)

        text = self._read(path)
        text, changed = D.ensure_markers(text, self.lang)
        if changed:
            sys.stderr.write("마커가 없어 섹션을 추가했다: %s\n" % path)

        led_inner = D.extract_marked(text, D.LEDGER_START, D.LEDGER_END) or ""
        entries = [D.parse_ledger_block(b) for b in D.split_blocks(led_inner)]
        next_id = max([e["id"] for e in entries], default=0) + 1
        today = datetime.date.today().isoformat()

        if supersedes:
            ids = [int(s.strip().lstrip("Dd-")) for s in supersedes.split(",") if s.strip()]
            known = {e["id"] for e in entries}
            missing = [i for i in ids if i not in known]
            if missing:
                sys.stderr.write("ERROR: --supersedes 대상을 결정 로그에서 찾지 못했다: %s\n"
                                 % ", ".join("D-%d" % i for i in missing))
                sys.exit(2)
            for e in entries:
                if e["id"] in ids:
                    e["status"] = "SUPERSEDED by D-%d" % next_id

        entries.append({"id": next_id, "title": title, "status": "ACTIVE",
                        "topic": topic, "decided": today,
                        "superseded": None, "mirrors": mirrors, "body": body})
        text = D.replace_marked(text, D.LEDGER_START, D.LEDGER_END,
                                "\n\n".join(D.render_ledger_block(e) for e in entries))

        # 현재 설계 — 같은 topic 블록을 통째로 교체한다. 덧붙이지 않는다.
        cur_inner = D.extract_marked(text, D.CURRENT_START, D.CURRENT_END) or ""
        cur = [D.parse_current_block(b) for b in D.split_blocks(cur_inner)]
        cur = [e for e in cur if e["topic"] != topic]
        cur.append({"id": next_id, "topic": topic, "title": title, "body": body})
        cur.sort(key=lambda e: e["id"])
        text = D.replace_marked(text, D.CURRENT_START, D.CURRENT_END,
                                "\n\n".join(D.render_current_block(e) for e in cur))

        sup = (" (supersedes %s)" % supersedes) if supersedes else ""
        text = D.append_progress(text, "- %s: D-%d 확정 — %s%s" % (today, next_id, title, sup), self.lang)
        self._write(path, text)
        print("D-%d" % next_id)
        return 0

    def slice_status(self, ref, n, status):
        allowed = D.slice_statuses(self.lang)
        if status not in allowed:
            sys.stderr.write("ERROR: status 는 %s 중 하나여야 한다\n" % " | ".join(allowed))
            sys.exit(2)
        path = self._need(ref)
        text = self._read(path)
        try:
            text = D.set_slice_status(text, n, status)
        except KeyError as e:
            sys.stderr.write("ERROR: %s\n" % e)
            sys.exit(2)
        self._write(path, text)
        print("S-%d → %s" % (n, status))
        return 0

    def task_log(self, ref, summary=None, type_=None, status=None, log=None):
        path = self._need(ref)
        text = self._read(path)
        line = log or summary or ""
        if line:
            tag = ("[%s] " % type_) if type_ else ""
            text = D.append_progress(
                text, "- %s: %s%s" % (datetime.date.today().isoformat(), tag, line), self.lang)
        if status:
            text = self._set_meta_status(text, status)
        self._write(path, text)
        if summary:
            sys.stderr.write("note: markdown 백엔드에는 daily 노트가 없다. 진행 로그에만 남겼다.\n")
        return 0

    def task_status(self, ref, status, log=None):
        path = self._need(ref)
        text = self._set_meta_status(self._read(path), status)
        if log:
            text = D.append_progress(
                text, "- %s: %s" % (datetime.date.today().isoformat(), log), self.lang)
        self._write(path, text)
        print(status)
        return 0

    @staticmethod
    def _set_meta_status(text, status):
        pat = re.compile(r"(?m)^(- \*\*상태\*\*:\s*).*$")
        if pat.search(text):
            return pat.sub(lambda m: m.group(1) + status, text, count=1)
        pat_en = re.compile(r"(?m)^(- \*\*status\*\*:\s*).*$")
        if pat_en.search(text):
            return pat_en.sub(lambda m: m.group(1) + status, text, count=1)
        return text

    def review_link(self, ref, directory):
        path = self._need(ref)
        text = self._read(path)
        pat = re.compile(r"(?m)^- \*\*리뷰 산출물 경로\*\*:\s*.*$")
        line = "- **리뷰 산출물 경로**: %s" % directory
        if pat.search(text):
            text = pat.sub(line, text, count=1)
        else:
            pos, alias = D.find_heading(text, "meta")
            if pos >= 0:
                end = text.index("\n", pos) + 1
                text = text[:end] + line + "\n" + text[end:]
            else:
                text = D.append_progress(text, line, self.lang)
        self._write(path, text)
        print(directory)
        return 0
