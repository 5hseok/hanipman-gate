"""obsidian 백엔드 — 기존 Obsidian 헬퍼 CLI 에 그대로 위임한다.

이 백엔드는 아무것도 새로 하지 않는다. 인자를 그대로 넘기고 종료 코드를 그대로 돌려준다.
이미 그 CLI 를 쓰고 있던 사람의 볼트는 한 줄도 바뀌지 않는다.

`ls` · `resolve` · `raw-path` 세 개만 여기서 직접 처리한다. 원본 CLI 에 없는 명령이고,
스킬 본문이 하던 `ls $VAULT/tasks/...` 같은 raw 셸 호출을 대신하려고 새로 만든 것이다.
"""

import datetime
import os
import subprocess
import sys


class Backend:
    name = "obsidian"

    def __init__(self, vault, lang="ko"):
        self.vault = vault
        self.lang = lang
        self.cli = os.path.join(vault, ".claude", "scripts", "obsidian-log.py")
        self.tasks = os.path.join(vault, "tasks")

    # ── 위임 ───────────────────────────────────────────────────────────
    def _run(self, *argv):
        env = dict(os.environ)
        env["OBSIDIAN_VAULT"] = self.vault
        return subprocess.call([sys.executable, self.cli] + [a for a in argv if a is not None], env=env)

    @staticmethod
    def _opt(flag, value):
        return [] if value in (None, False) else ([flag] if value is True else [flag, str(value)])

    # ── 파일 해석 ──────────────────────────────────────────────────────
    def resolve(self, ref):
        if os.path.isabs(ref) and os.path.exists(ref):
            return ref
        base = os.path.basename(ref)
        stem = base[:-3] if base.endswith(".md") else base
        for root, _dirs, files in os.walk(self.tasks):
            for fn in files:
                if fn == base:
                    return os.path.join(root, fn)
        low = stem.lower()
        for root, _dirs, files in os.walk(self.tasks):
            for fn in sorted(files):
                if fn.lower().startswith(low):
                    return os.path.join(root, fn)
        # 브랜치 키워드로 찾는 경우가 많다. 대소문자를 가리지 않는다.
        for root, _dirs, files in os.walk(self.tasks):
            for fn in sorted(files):
                if low in fn.lower():
                    return os.path.join(root, fn)
        return None

    def raw_path(self, ref):
        p = self.resolve(ref)
        if not p:
            sys.stderr.write("ERROR: 원장 문서를 찾지 못했다: %s\n" % ref)
            return 2
        print(p)
        return 0

    def ls(self, month=None):
        month = month or datetime.date.today().strftime("%Y-%m")
        d = os.path.join(self.tasks, month)
        if not os.path.isdir(d):
            return 0
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".md"):
                print(os.path.join(d, fn))
        return 0

    # ── 나머지는 전부 위임 ─────────────────────────────────────────────
    def current(self, ref, topic=None, with_queue=False, ids_only=False):
        return self._run("current", "--file", ref,
                         *self._opt("--topic", topic),
                         *self._opt("--with-queue", with_queue),
                         *self._opt("--ids-only", ids_only))

    def decide(self, ref, topic, title, body, supersedes=None, mirrors=None, init=False):
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".md", text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(body)
            return self._run("decide", "--file", ref, "--topic", topic, "--title", title,
                             "--body-file", tmp,
                             *self._opt("--supersedes", supersedes),
                             *self._opt("--mirrors", mirrors),
                             *self._opt("--init", init))
        finally:
            os.unlink(tmp)

    def slice_status(self, ref, n, status):
        return self._run("slice-status", "--file", ref, "--slice", str(n), "--status", status)

    def task_log(self, ref, summary=None, type_=None, status=None, log=None):
        return self._run("task-log", "--file", ref,
                         *self._opt("--summary", summary),
                         *self._opt("--type", type_),
                         *self._opt("--status", status),
                         *self._opt("--log", log))

    def task_status(self, ref, status, log=None):
        return self._run("task-status", "--file", ref, "--status", status,
                         *self._opt("--log", log))

    def review_link(self, ref, directory):
        return self._run("review-link", "--file", ref, "--dir", directory)
