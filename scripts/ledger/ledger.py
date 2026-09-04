#!/usr/bin/env python3
"""ledger — 결정 원장 CLI.

스킬은 원장이 어디 있는지 모른다. 이 명령만 부른다.

백엔드 선택 순서
  ① $CLAUDE_LEDGER_BACKEND  (+ $CLAUDE_LEDGER_ROOT)
  ② <repo>/.claude/ledger.json
  ③ ~/.claude/ledger.json
  ④ $OBSIDIAN_VAULT 아래에 obsidian-log.py 가 있으면 obsidian
  ⑤ 없으면 markdown — <repo>/.claude/design

설정 파일은 `{"backend": "obsidian"|"markdown", "root": "...", "lang": "ko"|"en"}`.
개인 경로는 설정 파일에만 둔다. 이 레포에는 어떤 절대경로도 들어가지 않는다.

표준 라이브러리만 쓴다.
"""

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backend_markdown  # noqa: E402
import backend_obsidian  # noqa: E402

EXIT_NO_BACKEND = 3


# ── 위치 해석 ──────────────────────────────────────────────────────────────

def git_root(start=None):
    try:
        out = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      cwd=start or os.getcwd(),
                                      stderr=subprocess.DEVNULL)
        return out.decode().strip()
    except Exception:
        return None


def read_config(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def user_config_path():
    return os.path.join(os.path.expanduser("~"), ".claude", "ledger.json")


def resolve_backend(explain=False):
    """(backend, 선택 근거 문자열)"""
    repo = git_root()
    lang = "ko"

    env_be = os.environ.get("CLAUDE_LEDGER_BACKEND")
    env_root = os.environ.get("CLAUDE_LEDGER_ROOT")
    if env_be:
        return _build(env_be, env_root, lang, repo), "환경변수 CLAUDE_LEDGER_BACKEND"

    for label, p in (("프로젝트 .claude/ledger.json", os.path.join(repo, ".claude", "ledger.json") if repo else None),
                     ("사용자 ~/.claude/ledger.json", user_config_path())):
        if not p:
            continue
        cfg = read_config(p)
        if cfg and cfg.get("backend"):
            return _build(cfg["backend"], cfg.get("root"), cfg.get("lang", "ko"), repo), "%s" % label

    vault = os.environ.get("OBSIDIAN_VAULT")
    if vault and os.path.isfile(os.path.join(vault, ".claude", "scripts", "obsidian-log.py")):
        return backend_obsidian.Backend(vault, lang), "환경변수 OBSIDIAN_VAULT"

    if repo:
        return backend_markdown.Backend(os.path.join(repo, ".claude", "design"), lang), "기본값 (레포 안 markdown)"

    return None, "레포 밖이고 설정도 없다"


def _build(name, root, lang, repo):
    if name == "obsidian":
        if not root:
            root = os.environ.get("OBSIDIAN_VAULT")
        if not root:
            sys.stderr.write("ERROR: obsidian 백엔드인데 볼트 경로(root)가 없다.\n")
            sys.exit(EXIT_NO_BACKEND)
        cli = os.path.join(root, ".claude", "scripts", "obsidian-log.py")
        if not os.path.isfile(cli):
            sys.stderr.write("ERROR: 헬퍼 CLI 가 없다: %s\n" % cli)
            sys.exit(EXIT_NO_BACKEND)
        return backend_obsidian.Backend(root, lang)
    if name == "markdown":
        if not root:
            root = os.path.join(repo or os.getcwd(), ".claude", "design")
        return backend_markdown.Backend(root, lang)
    sys.stderr.write("ERROR: 알 수 없는 백엔드: %s\n" % name)
    sys.exit(EXIT_NO_BACKEND)


def need_backend():
    be, why = resolve_backend()
    if be is None:
        sys.stderr.write(
            "ERROR: 원장 백엔드를 정하지 못했다 (%s).\n"
            "  · git 레포 안에서 실행하거나\n"
            "  · ~/.claude/ledger.json 에 {\"backend\": \"markdown\"} 를 쓰거나\n"
            "  · Obsidian 을 쓴다면 {\"backend\": \"obsidian\", \"root\": \"<볼트 경로>\"}\n" % why)
        sys.exit(EXIT_NO_BACKEND)
    return be


# ── body 입력 ──────────────────────────────────────────────────────────────

def read_body(args):
    if args.body_file:
        with open(args.body_file, encoding="utf-8") as f:
            return f.read().strip("\n")
    if args.body:
        return args.body
    if not sys.stdin.isatty():
        return sys.stdin.read().strip("\n")
    sys.stderr.write("ERROR: 본문이 없다. --body / --body-file / stdin 중 하나를 써라.\n")
    sys.exit(2)


# ── CLI ────────────────────────────────────────────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(prog="ledger", description="결정 원장 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("current", help="확정된 결정만 출력한다. 문서를 통째로 읽지 않는다")
    p.add_argument("--file", required=True)
    p.add_argument("--topic")
    p.add_argument("--with-queue", action="store_true")
    p.add_argument("--ids-only", action="store_true")

    p = sub.add_parser("decide", help="결정을 확정한다. 같은 topic 은 교체된다")
    p.add_argument("--file", required=True)
    p.add_argument("--topic", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--body")
    p.add_argument("--body-file")
    p.add_argument("--supersedes")
    p.add_argument("--mirrors")
    p.add_argument("--init", action="store_true", help="문서가 없으면 새로 만든다")

    p = sub.add_parser("slice-status", help="작업 큐 S-n 의 상태를 바꾼다")
    p.add_argument("--file", required=True)
    p.add_argument("--slice", type=int, required=True)
    p.add_argument("--status", required=True)

    p = sub.add_parser("task-log", help="진행 로그에 한 줄 남긴다")
    p.add_argument("--file", required=True)
    p.add_argument("--summary")
    p.add_argument("--type", dest="type_")
    p.add_argument("--status")
    p.add_argument("--log")

    p = sub.add_parser("task-status", help="문서 상태를 바꾼다")
    p.add_argument("--file", required=True)
    p.add_argument("--status", required=True)
    p.add_argument("--log")

    p = sub.add_parser("review-link", help="리뷰 산출물 경로를 문서에 기록한다")
    p.add_argument("--file", required=True)
    p.add_argument("--dir", dest="directory", required=True)

    p = sub.add_parser("ls", help="해당 달의 원장 문서 경로를 나열한다")
    p.add_argument("--month")

    p = sub.add_parser("resolve", help="조각으로 문서 경로를 찾는다")
    p.add_argument("fragment")

    p = sub.add_parser("raw-path", help="문서의 절대경로. 원문을 읽어야 할 때만 쓴다")
    p.add_argument("--file", required=True)

    sub.add_parser("doctor", help="어느 백엔드가 왜 선택됐는지 보여준다")

    a = ap.parse_args(argv)

    if a.cmd == "doctor":
        be, why = resolve_backend()
        print("backend : %s" % (be.name if be else "(없음)"))
        print("근거    : %s" % why)
        if be:
            print("root    : %s" % getattr(be, "root", getattr(be, "vault", "-")))
        print("repo    : %s" % (git_root() or "(git 레포 아님)"))
        print("설정    : %s" % user_config_path())
        return 0 if be else EXIT_NO_BACKEND

    be = need_backend()

    if a.cmd == "current":
        return be.current(a.file, a.topic, a.with_queue, a.ids_only)
    if a.cmd == "decide":
        return be.decide(a.file, a.topic, a.title, read_body(a), a.supersedes, a.mirrors, a.init)
    if a.cmd == "slice-status":
        return be.slice_status(a.file, a.slice, a.status)
    if a.cmd == "task-log":
        return be.task_log(a.file, a.summary, a.type_, a.status, a.log)
    if a.cmd == "task-status":
        return be.task_status(a.file, a.status, a.log)
    if a.cmd == "review-link":
        return be.review_link(a.file, a.directory)
    if a.cmd == "ls":
        return be.ls(a.month)
    if a.cmd == "resolve":
        p = be.resolve(a.fragment)
        if not p:
            sys.stderr.write("ERROR: 찾지 못했다: %s\n" % a.fragment)
            return 2
        print(p)
        return 0
    if a.cmd == "raw-path":
        return be.raw_path(a.file)
    return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
