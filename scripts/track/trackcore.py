#!/usr/bin/env python3
"""트랙 원장 — 코어. 문서 파싱 · 프로브 실행 · 세션 그래프 · 결정 원장 읽기.

세 층 (설계: tasks/2026-08/0827-task1-...):
  계획 = tracks/YYYY-MM/*.md 에 사람이 쓴다
  실측 = 프로브가 잰다. 상태는 손으로 쓸 수 없다
  렌더 = next / board / graph 는 전부 파생
"""
import os, re, json, shlex, subprocess, time, glob, threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

HOME = os.path.expanduser("~")


def _ledger_config():
    """원장 어댑터와 같은 설정을 읽는다. 트랙 문서는 원장 옆에 산다."""
    for p in (os.path.join(os.getcwd(), ".claude", "ledger.json"),
              os.path.join(HOME, ".claude", "ledger.json")):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and d.get("root"):
                return d
        except Exception:
            pass
    return {}


def _resolve_root():
    """트랙 문서·원장 문서가 있는 뿌리.
    $CLAUDE_TRACK_ROOT → ledger.json 의 root → $OBSIDIAN_VAULT → 현재 레포의 .claude
    개인 경로를 코드에 두지 않는다."""
    r = os.environ.get("CLAUDE_TRACK_ROOT")
    if r:
        return r
    cfg = _ledger_config()
    if cfg.get("root"):
        return cfg["root"]
    v = os.environ.get("OBSIDIAN_VAULT")
    if v:
        return v
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        top = os.getcwd()
    return os.path.join(top, ".claude")


ROOT = _resolve_root()
TRACKS_DIR = os.path.join(ROOT, "tracks")
TASKS_DIR = os.path.join(ROOT, "tasks")
SESS_DIR = os.path.join(HOME, ".claude/sessions")
PROJ_DIR = os.path.join(HOME, ".claude/projects")
CACHE_DIR = os.path.join(HOME, ".claude/track-cache")

# ─────────────────────────────────────────────────────────────
# 프로브 화이트리스트 — 쓰기 동사는 실행 자체가 거부된다
# ─────────────────────────────────────────────────────────────
AWS_VERB_RE = re.compile(r"^(get|describe|list|lookup|head|batch-get|search)-|^(get-caller-identity)$")
GIT_OK = {"ls-remote", "rev-parse", "log", "show", "cat-file", "merge-base",
          "rev-list", "for-each-ref", "describe", "status", "diff", "branch", "tag",
          "grep", "ls-tree", "ls-files"}
GH_OK = {("pr", "view"), ("pr", "list"), ("pr", "diff"), ("run", "list"), ("run", "view"),
         ("release", "view"), ("release", "list"), ("api",), ("repo", "view"), ("issue", "view"),
         ("issue", "list"), ("workflow", "list")}
FILTERS = {"cut", "head", "tail", "tr", "grep", "wc", "sort", "uniq", "jq", "awk", "sed", "rev", "basename"}
SED_WRITE = re.compile(r"(^|\s)-i\b")
AWK_WRITE = re.compile(r"\b(system|print\s*>|printf\s*>)\b")


class ProbeRejected(Exception):
    pass


def _check_stage(argv, allow_plan=False):
    """파이프 한 칸을 검사. 통과하면 None, 아니면 ProbeRejected."""
    if not argv:
        raise ProbeRejected("빈 명령")
    b = os.path.basename(argv[0])
    a = argv[1:]
    if b == "aws":
        pos = [x for x in a if not x.startswith("-")]
        # aws <service> <verb> ...
        if len(pos) < 2:
            raise ProbeRejected("aws: service/verb 없음")
        verb = pos[1]
        if not AWS_VERB_RE.match(verb):
            raise ProbeRejected(f"aws {pos[0]} {verb}: 읽기 동사가 아니다 (get-/describe-/list-/head-/batch-get-/lookup- 만)")
        return
    if b == "git":
        pos = [x for x in a if not x.startswith("-")]
        if not pos or pos[0] not in GIT_OK:
            raise ProbeRejected(f"git {pos[0] if pos else ''}: 읽기 서브커맨드가 아니다")
        if pos[0] == "diff" and any(x in ("--exit-code",) for x in a):
            pass
        return
    if b == "gh":
        pos = [x for x in a if not x.startswith("-")]
        key2 = tuple(pos[:2]); key1 = tuple(pos[:1])
        if key2 not in GH_OK and key1 not in GH_OK:
            raise ProbeRejected(f"gh {' '.join(pos[:2])}: 허용 목록에 없다")
        if key1 == ("api",):
            for i, x in enumerate(a):
                if x in ("-X", "--method"):
                    m = a[i + 1] if i + 1 < len(a) else ""
                    if m.upper() != "GET":
                        raise ProbeRejected(f"gh api -X {m}: GET 만 허용")
                if x.startswith("--method=") and x.split("=", 1)[1].upper() != "GET":
                    raise ProbeRejected("gh api --method: GET 만 허용")
                if x in ("-f", "-F", "--field", "--raw-field", "--input"):
                    raise ProbeRejected("gh api: 필드 전송(-f/-F/--input)은 쓰기로 간주")
        return
    if b == "terraform":
        if not allow_plan:
            raise ProbeRejected("terraform: probe-kind: plan 을 명시한 스텝만 허용")
        pos = [x for x in a if not x.startswith("-")]
        if not pos or pos[0] != "plan":
            raise ProbeRejected("terraform: plan 외 서브커맨드 금지")
        return
    if b in FILTERS:
        if b == "sed" and SED_WRITE.search(" ".join(a)):
            raise ProbeRejected("sed -i: 파일 수정")
        if b == "awk" and AWK_WRITE.search(" ".join(a)):
            raise ProbeRejected("awk: system()/리다이렉션 금지")
        return
    if b in ("echo", "true", "false", "date", "printf"):
        return
    raise ProbeRejected(f"{b}: 화이트리스트에 없는 명령")


def check_probe_cmd(cmd, allow_plan=False):
    """프로브 명령 전체를 검사하고 파이프 단계 argv 리스트를 돌려준다."""
    if any(ch in cmd for ch in ("`", "$(", ">", "<", "&&", "||", ";", "&")):
        raise ProbeRejected("쉘 메타문자 금지 (명령치환·리다이렉션·연쇄 불가)")
    stages = []
    for part in cmd.split("|"):
        part = part.strip()
        if not part:
            raise ProbeRejected("빈 파이프 단계")
        argv = shlex.split(part)
        _check_stage(argv, allow_plan=allow_plan)
        stages.append(argv)
    return stages


def run_probe(cmd, timeout=25, allow_plan=False, cwd=None):
    """→ dict(ok, value, err, rc, at). 쉘 없이 파이프를 직접 잇는다."""
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        stages = check_probe_cmd(cmd, allow_plan=allow_plan)
    except ProbeRejected as e:
        return {"ok": False, "value": "", "err": f"거부됨 — {e}", "rc": -2, "at": at, "rejected": True}
    if allow_plan:
        for st in stages:
            if os.path.basename(st[0]) == "terraform" and "-lock=false" not in st:
                st.append("-lock=false")
                st.append("-input=false")
    procs, prev = [], None
    try:
        for i, argv in enumerate(stages):
            p = subprocess.Popen(argv, stdin=(prev.stdout if prev else subprocess.DEVNULL),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
            if prev:
                prev.stdout.close()
            procs.append(p)
            prev = p
        out, err = procs[-1].communicate(timeout=timeout)
        for p in procs[:-1]:
            try: p.wait(timeout=2)
            except Exception: p.kill()
        rc = procs[-1].returncode
        return {"ok": rc == 0, "value": out.decode(errors="replace").strip(),
                "err": err.decode(errors="replace").strip()[:400], "rc": rc, "at": at}
    except subprocess.TimeoutExpired:
        for p in procs:
            try: p.kill()
            except Exception: pass
        return {"ok": False, "value": "", "err": f"타임아웃 {timeout}s", "rc": -1, "at": at, "timeout": True}
    except FileNotFoundError as e:
        return {"ok": False, "value": "", "err": f"명령 없음: {e}", "rc": -3, "at": at}
    except Exception as e:
        return {"ok": False, "value": "", "err": f"{type(e).__name__}: {e}", "rc": -4, "at": at}


# ─────────────────────────────────────────────────────────────
# 판정 연산자
# ─────────────────────────────────────────────────────────────
def _flat(s, n=140):
    s = re.sub(r"\s+", " ", (s or "")).strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def judge(op, expected, res):
    """(verdict, 설명). verdict: True(완료) / False(미완) / None(미검증)"""
    if res.get("rejected") or res.get("rc", 0) in (-2, -3, -4):
        return None, res.get("err", "실행 실패")
    if res.get("timeout"):
        return None, res["err"]
    v = res["value"]
    if op == "exit0":
        return (res["rc"] == 0), f"rc={res['rc']}"
    if not res["ok"] and op not in ("empty",):
        return None, _flat(f"rc={res['rc']} {res.get('err','')}")
    if op == "==":
        ok = (v == expected)
        return ok, (_flat(v) or "(빈 출력)") if ok else _flat(f"{v or '(빈 출력)'} ≠ {expected}")
    if op == "!=":
        return (v != expected), _flat(v) or "(빈 출력)"
    if op == "contains":
        hit = expected in v
        return hit, (f"'{expected}' 포함 ✓" if hit else f"'{expected}' 없음 · 출력 {len(v)}B")
    if op == "!contains":
        hit = expected not in v
        return hit, (f"'{expected}' 없음 ✓" if hit else f"'{expected}' 포함됨")
    if op == "!empty":
        return (v != ""), _flat(v) or "(빈 출력)"
    if op == "empty":
        return (v == ""), _flat(v) or "(빈 출력)"
    if op == "newer-than":
        try:
            got = _parse_ts(v); want = _parse_ts(expected)
        except Exception as e:
            return None, f"시각 파싱 실패: {v[:60]} ({e})"
        return (got > want), _flat(v)
    if op in (">=", ">"):
        try:
            a = float(re.sub(r"[^0-9.\-]", "", v)); b = float(expected)
        except Exception:
            return None, f"숫자 아님: {v[:60]}"
        return (a >= b if op == ">=" else a > b), _flat(v)
    return None, f"알 수 없는 연산자: {op}"


def _parse_ts(s):
    """ISO8601 관용 파서. AWS 는 '2026-08-27T05:46:08.000+0000' 처럼 콜론 없는 오프셋을 준다."""
    s = s.strip().strip('"').strip()
    s = s.replace("Z", "+00:00")
    m = re.match(r"^(.*?)([+-]\d{2})(\d{2})$", s)   # +0000 → +00:00
    if m:
        s = m.group(1) + m.group(2) + ":" + m.group(3)
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


# ─────────────────────────────────────────────────────────────
# 트랙 문서 파싱
# ─────────────────────────────────────────────────────────────
STEP_RE = re.compile(r"(?m)^### +(T-\d+) +· +(.+?)\s*$")
HEADING_RE = re.compile(r"(?m)^### +.*$")
REPEATABLE_FIELDS = {"note"}
KNOWN_FIELDS = {"why","where","gate","env","needs","owner","team","probe","probe-kind","note","task","rests-on"}
FIELD_RE = re.compile(r"(?m)^- +(why|where|gate|env|needs|owner|team|probe|probe-kind|note|task|rests-on) *: *(.*)$")
# 필드꼴이지만 인식되지 않는 줄 — 조용히 버려지는 것을 막는다
ANYFIELD_RE = re.compile(r"(?m)^- +([a-z][a-z0-9-]{1,20}) *: *(?=\S)")
PROBE_RE = re.compile(r"^`(.+?)`\s*::\s*(\S+)\s*(.*)$")
META_RE = re.compile(r"(?m)^- +\*\*(\w+)\*\* *: *(.*)$")
ACTOR_RE = re.compile(r"(?m)^\| *([^|]+?) *\| *([^|]*?) *\| *([^|]*?) *\|\s*$")
SIGNAL_RE = re.compile(r"(?m)^- +(\S+) +· +([^·]+?) +· +([^·]+?) +· +([^·]+?)(?: +· +(.*))?$")


def _section(text, heading):
    m = re.search(r"(?m)^%s *$" % re.escape(heading), text)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"(?m)^## ", rest)
    return rest[:nxt.start()] if nxt else rest


def parse_track(path):
    text = open(path, encoding="utf-8").read()
    meta = dict(META_RE.findall(_section(text, "## 메타")))
    tid = meta.get("id") or os.path.splitext(os.path.basename(path))[0]

    actors = []
    for row in ACTOR_RE.findall(_section(text, "## 액터")):
        s, own, led = (x.strip() for x in row)
        if s in ("세션", "---") or set(s) <= {"-", " ", ":"}:
            continue
        actors.append({"session": s, "steps": [x.strip() for x in own.split(",") if x.strip()],
                       "ledger": led})

    sec = _section(text, "## 스텝")
    hits = list(STEP_RE.finditer(sec))
    steps = []
    lint = []
    for hm in HEADING_RE.finditer(sec):
        if not STEP_RE.match(hm.group(0)):
            lint.append("미인식 스텝 헤더 — 앞 스텝에 흡수된다: %s" % hm.group(0).strip())
    for i, m in enumerate(hits):
        blk = sec[m.end(): hits[i + 1].start() if i + 1 < len(hits) else len(sec)]
        pairs = FIELD_RE.findall(blk)
        seen = {}
        for k, _ in pairs:
            seen[k] = seen.get(k, 0) + 1
        for k, n in seen.items():
            if n > 1 and k not in REPEATABLE_FIELDS:
                lint.append("%s: '- %s:' 가 %d번 — 마지막 값만 쓰인다" % (m.group(1), k, n))
        for uk in sorted({u for u in ANYFIELD_RE.findall(blk) if u not in KNOWN_FIELDS}):
            lint.append("%s: '- %s:' 는 인식되지 않는 필드 — 파싱되지 않고 조용히 버려진다" % (m.group(1), uk))
        f = {k: v.strip() for k, v in pairs}
        probe = None
        if f.get("probe") and f["probe"] not in ("—", "-", "없음"):
            pm = PROBE_RE.match(f["probe"])
            if pm:
                probe = {"cmd": pm.group(1), "op": pm.group(2), "expected": pm.group(3).strip()}
            else:
                probe = {"cmd": None, "op": None, "expected": None, "malformed": f["probe"]}
        needs = [x.strip() for x in re.split(r"[,\s]+", f.get("needs", "")) if x.strip() and x.strip() not in ("—", "-", "없음")]
        where = {}
        for kv in re.finditer(r"(\w+)=(\S+)", f.get("where", "")):
            where[kv.group(1)] = kv.group(2)
        steps.append({
            "id": m.group(1), "title": m.group(2).strip(),
            "why": f.get("why", ""), "where": where, "gate": f.get("gate", ""),
            "env": f.get("env", ""), "needs": needs, "owner": f.get("owner", ""),
            "team": f.get("team", ""), "task": f.get("task", ""),
            "probe": probe, "probe_kind": f.get("probe-kind", ""),
            "note": f.get("note", ""),
            "rests_on": [x.strip() for x in re.split(r"[,\s]+", f.get("rests-on", ""))
                         if x.strip() and x.strip() not in ("—", "-", "없음")],
        })

    known = {st["id"] for st in steps}
    for st in steps:
        for n in st["needs"]:
            if n not in known:
                lint.append("%s: needs '%s' 가 없는 스텝 — 충족으로 취급되어 게이트가 조용히 열린다" % (st["id"], n))
        if st["probe"] and st["probe"].get("malformed"):
            lint.append("%s: probe 형식 오류 — `cmd` :: op 형태여야 한다" % st["id"])
        _d = st["where"].get("dir")
        if _d and _d not in ("—", "-", "없음") and not os.path.isdir(_d):
            lint.append("%s: where dir '%s' 가 없다 — 프로브가 그 자리에서 돌지 못한다" % (st["id"], _d))

    for st in steps:
        if not st["owner"] or st["owner"] in ("—", "-", "없음"):
            lint.append("%s: owner 가 비어 있다 — 아무도 안 든 스텝은 아무도 안 한다" % st["id"])
    actor_names = {a["session"] for a in actors}
    owners = {st["owner"] for st in steps if st["owner"] and st["owner"] not in ("—", "-", "없음")}
    for own in sorted(owners - actor_names):
        lint.append("owner '%s' 가 액터 표에 없다 — 이름이 비슷한 다른 세션과 조용히 뒤섞인다" % own)
    for a in actors:
        declared = set(a["steps"]); actual = {st["id"] for st in steps if st["owner"] == a["session"]}
        if declared != actual:
            lint.append("액터 '%s' 담당 표기 %s ≠ 실제 owner %s" % (a["session"], sorted(declared), sorted(actual)))
    for a in sorted(actor_names - owners):
        if any(x for x in [next((y for y in actors if y["session"] == a), {})].pop().get("steps", [])):
            lint.append("액터 '%s' 가 어느 스텝의 owner 도 아니다 — 담당 표기가 실제와 어긋난다" % a)

    signals = []
    for s in SIGNAL_RE.findall(_section(text, "## 신호")):
        signals.append({"at": s[0], "by": s[1].strip(), "step": s[2].strip(),
                        "kind": s[3].strip(), "evidence": (s[4] or "").strip()})

    return {"path": path, "id": tid, "title": (text.split("\n", 1)[0].lstrip("# ").strip()),
            "meta": meta, "actors": actors, "steps": steps, "signals": signals, "lint": lint}


def find_track(name=None):
    cands = sorted(glob.glob(os.path.join(TRACKS_DIR, "*", "*.md")))
    if not cands:
        return None
    if not name:
        return cands[-1]
    for c in cands:
        if name in os.path.basename(c):
            return c
    for c in cands:
        try:
            if parse_track(c)["id"] == name:
                return c
        except Exception:
            pass
    return None


# ─────────────────────────────────────────────────────────────
# 상태 판정 — 상태는 손으로 쓰지 않는다
# ─────────────────────────────────────────────────────────────
def _cache_path(tid):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{re.sub(r'[^A-Za-z0-9_.-]', '_', tid)}.json")


def load_cache(tid):
    try:
        return json.load(open(_cache_path(tid), encoding="utf-8"))
    except Exception:
        return {"probes": {}, "verifiedAt": None}


def save_cache(tid, c):
    tmp = _cache_path(tid) + ".tmp"
    json.dump(c, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, _cache_path(tid))


def _probe_sig(step):
    """프로브의 정체성. 이게 바뀌면 옛 실측 결과는 무효다."""
    p = step.get("probe")
    if not p or not p.get("cmd"):
        return None
    return f"{p['cmd']}::{p['op']}::{p.get('expected','')}"


def verify(track, only=None, workers=8):
    """모든 프로브를 배치 실행. 캐시에 기록하고 캐시를 돌려준다."""
    cache = load_cache(track["id"])
    jobs = []
    for st in track["steps"]:
        if only and st["id"] not in only:
            continue
        p = st.get("probe")
        if not p or not p.get("cmd"):
            continue
        allow_plan = st.get("probe_kind") == "plan"
        tmo = 300 if allow_plan else 25
        cwd = st["where"].get("dir")
        # 플레이스홀더는 디렉토리가 아니다 — 그대로 넘기면 FileNotFoundError 로 미검증이 된다.
        # 반면 '적었는데 없는 경로'는 None 으로 되돌리지 않는다 — 엉뚱한 cwd 에서 도는 것보다
        # 미검증으로 남는 편이 낫다(거짓 완료 금지).
        if cwd in (None, "", "—", "-", "없음"):
            cwd = None
        jobs.append((st, p, allow_plan, tmo, cwd))
    if jobs:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(run_probe, p["cmd"], tmo, allow_plan, cwd): st
                    for (st, p, allow_plan, tmo, cwd) in jobs}
            for fut, st in futs.items():
                res = fut.result()
                vd, expl = judge(st["probe"]["op"], st["probe"]["expected"], res)
                cache["probes"][st["id"]] = {"verdict": vd, "explain": expl, "raw": res,
                                             "sig": _probe_sig(st)}
    # 현재 트랙에 없는 스텝의 실측 결과는 버린다 — 스텝이 재편되면
    # 옛 번호의 결과가 새 스텝의 근거로 되살아난다(실제로 발생했다)
    # 스텝 번호는 재사용된다. 같은 T-23 이라도 프로브가 바뀌면 옛 결과는 남의 것이다.
    # (실제로 발생: 스텝을 재편했더니 옛 T-23 의 GZIP 실측이 새 T-23 의 근거로 붙었다)
    sig = {s["id"]: _probe_sig(s) for s in track["steps"]}
    for k in [k for k, v in cache["probes"].items()
              if k not in sig or sig[k] is None or v.get("sig") != sig[k]]:
        del cache["probes"][k]
    cache["verifiedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_cache(track["id"], cache)
    return cache


def resolve(track, cache):
    """스텝마다 상태를 붙인다: 완료 / 지금 / 대기 / 미검증 / 보류"""
    byid = {s["id"]: s for s in track["steps"]}
    cache = {"probes": {k: v for k, v in cache.get("probes", {}).items()
                        if k in byid and v.get("sig") == _probe_sig(byid[k])},
             "verifiedAt": cache.get("verifiedAt")}
    done, unknown = set(), set()
    for s in track["steps"]:
        pr = cache["probes"].get(s["id"])
        p = s.get("probe")
        if not p or not p.get("cmd"):
            unknown.add(s["id"]); continue
        if not pr:
            unknown.add(s["id"]); continue
        if pr["verdict"] is True:
            done.add(s["id"])
        elif pr["verdict"] is None:
            unknown.add(s["id"])
    out = []
    now_taken = False
    for s in track["steps"]:
        pr = cache["probes"].get(s["id"], {})
        blockers = [n for n in s["needs"] if n in byid and n not in done]
        if s["id"] in done:
            stt = "완료"
        elif s["id"] in unknown:
            stt = "미검증"
        elif blockers:
            stt = "대기"
        else:
            stt = "지금" if not now_taken else "가능"
            now_taken = True
        e = dict(s)
        e["status"] = stt
        e["blockers"] = blockers
        e["evidence"] = pr.get("explain", "")
        e["probe_err"] = (pr.get("raw") or {}).get("err", "")
        out.append(e)
    return out


# ─────────────────────────────────────────────────────────────
# 세션 그래프 (계측 없음 — 기존 파일만 읽는다)
# ─────────────────────────────────────────────────────────────
# 세션 cwd 를 보드에 짧게 표시할 때 떼어낼 접두. 없으면 홈만 ~ 로 줄인다.
REPO_ROOT = os.environ.get("CLAUDE_TRACK_REPO_ROOT", "")
_graph_lock = threading.Lock()
_graph_cache = {}  # key -> {"at": ts, "data": ...}
_GRAPH_MODES = {"related", "recent", "all"}


def short_cwd(c):
    if not c:
        return "?"
    if REPO_ROOT:
        c = c.replace(REPO_ROOT, "")
    c = c.replace(HOME, "~")
    return c.replace("/.claude/worktrees/", " ⑂ ")


def _graph_cache_key(days, mode, active_hours, focus):
    return (days, mode, active_hours, tuple(sorted(focus or ())))


def session_graph(days=4, ttl=8, active_hours=None, focus=None, mode="related"):
    focus = set(focus or ())
    if mode not in _GRAPH_MODES:
        mode = "related"
    requested_mode = mode
    fell_back = False
    if mode == "related" and not focus:
        mode = "recent"
        fell_back = True
    if active_hours is None:
        active_hours = 6 if mode == "recent" else None

    cache_key = _graph_cache_key(days, mode, active_hours, focus)
    with _graph_lock:
        cached = _graph_cache.get(cache_key)
        if cached and time.time() - cached["at"] < ttl:
            return cached["data"]

    nodes = {}
    for p in glob.glob(os.path.join(SESS_DIR, "*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        sid = d.get("sessionId")
        if not sid:
            continue
        updated_at = d.get("updatedAt")
        last_seen = (updated_at / 1000.0) if isinstance(updated_at, (int, float)) else None
        nodes[sid] = {"sid": sid, "name": d.get("name") or sid[:8], "where": short_cwd(d.get("cwd", "")),
                      "cwd": d.get("cwd", ""), "status": d.get("status", "?"), "kind": d.get("kind", "?"),
                      "branch": None, "deg": 0, "_lastSeen": last_seen}
    byname = {n["name"]: sid for sid, n in nodes.items()}
    cutoff = time.time() - days * 86400
    edges = {}
    for dp, _, fns in os.walk(PROJ_DIR):
        for fn in fns:
            if not fn.endswith(".jsonl"):
                continue
            fp = os.path.join(dp, fn)
            try:
                if os.path.getmtime(fp) < cutoff:
                    continue
            except OSError:
                continue
            sid = fn[:-6]
            if sid not in nodes:
                continue
            n = nodes[sid]
            try:
                lines = open(fp, errors="ignore").readlines()
            except OSError:
                continue
            for line in lines:
                if '"gitBranch"' in line and not n["branch"]:
                    try:
                        b = json.loads(line).get("gitBranch")
                        if b and b != "HEAD":
                            n["branch"] = b
                    except Exception:
                        pass
                if '"SendMessage"' in line:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    ts = d.get("timestamp", "")
                    for blk in (d.get("message", {}) or {}).get("content", []) or []:
                        if not isinstance(blk, dict) or blk.get("name") != "SendMessage":
                            continue
                        to = (blk.get("input") or {}).get("to")
                        if not to:
                            continue
                        dst = byname.get(to, to)
                        e = edges.setdefault((sid, dst), {"src": sid, "dst": dst, "n": 0, "last": "", "labels": []})
                        e["n"] += 1
                        if ts > e["last"]:
                            e["last"] = ts
                        s = ((blk.get("input") or {}).get("summary") or "")[:80]
                        if s and len(e["labels"]) < 8:
                            e["labels"].append({"t": ts, "s": s})
    for e in edges.values():
        for side in ("src", "dst"):
            if e[side] not in nodes:
                raw = e[side]
                nm = raw
                if raw.startswith("uds:"):
                    nm = "sock:" + os.path.basename(raw).replace(".sock", "")
                elif len(raw) > 28:
                    nm = raw[:26] + "…"
                nodes[raw] = {"sid": raw, "name": nm, "where": "(종료됨)", "cwd": "",
                              "status": "gone", "kind": "?", "branch": None, "deg": 0, "_lastSeen": None}
        nodes[e["src"]]["deg"] += e["n"]
        nodes[e["dst"]]["deg"] += e["n"]
        try:
            e_epoch = _parse_ts(e["last"]).timestamp() if e["last"] else None
        except Exception:
            e_epoch = None
        e["_lastEpoch"] = e_epoch
        if e_epoch is not None:
            for side in ("src", "dst"):
                n = nodes[e[side]]
                if n["_lastSeen"] is None or e_epoch > n["_lastSeen"]:
                    n["_lastSeen"] = e_epoch
    ns = [n for n in nodes.values() if n["deg"] >= 1 or n["status"] in ("busy", "idle")]
    total_before_filter = len(ns)
    keep = {n["sid"] for n in ns}
    es = [e for e in edges.values() if e["src"] in keep and e["dst"] in keep]

    # ── 필터 적용 ──
    if mode == "recent" and active_hours:
        now = time.time()
        window = active_hours * 3600
        ns = [n for n in ns if n["_lastSeen"] is None or (now - n["_lastSeen"]) <= window]
    elif mode == "related":
        focus_sids = {sid for sid, n in nodes.items() if n["name"] in focus}
        neighbor_sids = set(focus_sids)
        for e in es:
            if e["src"] in focus_sids:
                neighbor_sids.add(e["dst"])
            if e["dst"] in focus_sids:
                neighbor_sids.add(e["src"])
        ns = [n for n in ns if n["sid"] in neighbor_sids]
    # mode == "all" → 필터 없음

    keep = {n["sid"] for n in ns}
    es = [e for e in es if e["src"] in keep and e["dst"] in keep]

    # deg 는 필터 후 남은 엣지 기준으로 다시 계산
    for n in ns:
        n["deg"] = 0
    for e in es:
        if e["src"] in keep:
            nodes[e["src"]]["deg"] += e["n"]
        if e["dst"] in keep:
            nodes[e["dst"]]["deg"] += e["n"]

    for n in ns:
        n["repo"] = ("홈 (원장·설계)" if n["where"].startswith("~")
                     else (n["where"].split(" ⑂ ")[0] + " ⑂") if "⑂" in n["where"]
                     else (n["where"] or "?"))
        n.pop("_lastSeen", None)
    for e in es:
        e.pop("_lastEpoch", None)

    hidden = max(0, total_before_filter - len(ns))
    data = {"generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "nodes": ns, "edges": es,
            "filter": {"mode": mode, "requestedMode": requested_mode,
                       "activeHours": active_hours, "focus": sorted(focus),
                       "hidden": hidden, "total": total_before_filter,
                       "fellBack": fell_back}}
    with _graph_lock:
        _graph_cache[cache_key] = {"at": time.time(), "data": data}
    return data


# ─────────────────────────────────────────────────────────────
# 결정 원장 읽기
# ─────────────────────────────────────────────────────────────
DEC_RE = re.compile(r"(?m)^### +(D-\d+) +· +(.+?)\s*$")


def read_ledger(task_hint):
    path = None
    for c in glob.glob(os.path.join(TASKS_DIR, "*", "*.md")):
        if task_hint and task_hint in os.path.basename(c):
            path = c
            break
    if not path:
        return None
    text = open(path, encoding="utf-8").read()
    if "## 결정 로그" not in text:
        return None
    sec = text.split("## 결정 로그", 1)[1]
    hits = list(DEC_RE.finditer(sec))
    decs = []
    for i, m in enumerate(hits):
        body = sec[m.end(): hits[i + 1].start() if i + 1 < len(hits) else len(sec)]
        sup = re.search(r"SUPERSEDED by (D-\d+)", body)
        topic = re.search(r"\*\*topic\*\* *: *(\S+)", body) or re.search(r"topic\*\* *: *(\S+)", body)
        mirrors = re.search(r"\*\*mirrors\*\* *: *(\S+)", body)
        prose = [l.strip() for l in body.split("\n") if l.strip() and not l.strip().startswith("- **")]
        decs.append({"id": m.group(1), "title": m.group(2).strip(),
                     "sup": sup.group(1) if sup else "", "topic": topic.group(1) if topic else "",
                     "mirrors": mirrors.group(1) if mirrors else "",
                     "body": (prose[0] if prose else "")[:400]})
    return {"path": path, "label": os.path.basename(path)[:24], "decisions": decs}


def conflicts(ledgers):
    """mirrors 링크가 걸린 결정 중, 내 쪽은 유효한데 상대 쪽이 폐기된 것.

    폐기된 결정에 붙은 링크는 역사일 뿐이라 건너뛴다 — 그것까지 세면
    한 원장 안에서 정상적으로 갱신한 것도 전부 경고가 된다."""
    idx, succ = {}, {}
    for name, L in ledgers.items():
        if not L:
            continue
        for d in L["decisions"]:
            idx[f"{name}:{d['id']}"] = (name, d)
            if d["sup"]:
                succ[f"{name}:{d['id']}"] = f"{name}:{d['sup']}"
    out = []
    for key, (name, d) in idx.items():
        if not d["mirrors"] or d["sup"]:
            continue                      # 폐기된 결정의 링크는 역사다
        other = idx.get(d["mirrors"])
        if not other:
            out.append({"kind": "링크 끊김", "a": key, "b": d["mirrors"],
                        "msg": f"{key} 가 가리키는 {d['mirrors']} 를 원장에서 찾지 못했다"})
            continue
        _, od = other
        if od["sup"]:
            # 상대가 폐기됐다 — 후속을 끝까지 따라가 무엇으로 대체됐는지 알려 준다
            cur, seen = d["mirrors"], set()
            while cur in succ and cur not in seen:
                seen.add(cur)
                cur = succ[cur]
            out.append({"kind": "한쪽만 폐기", "a": key, "b": d["mirrors"],
                        "msg": f"{key} 는 ACTIVE 인데 링크 대상 {d['mirrors']} 는 폐기됐다"
                               + (f" → 지금 유효한 것은 {cur}" if cur != d["mirrors"] else "")
                               + ". 이쪽 전제가 낡았을 수 있다."})
    return out


_TOK = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_STOP = {"한다", "된다", "하는", "이다", "에서", "으로", "하고", "그리고", "the", "and", "for"}


_SUF = ("한다", "했다", "된다", "하는", "하고", "이다", "된", "함", "들")


def _stem(w):
    for s in _SUF:
        if len(w) > len(s) + 1 and w.endswith(s):
            return w[: -len(s)]
    return w


def _tokens(s):
    return {_stem(w) for w in _TOK.findall(s or "") if w not in _STOP}


def link_candidates(ledgers, threshold=0.15):
    """mirrors 링크가 없는 결정들 중, 제목이 겹쳐 같은 사안으로 보이는 교차 원장 쌍.
    감지가 아니라 후보 제시다 — 링크는 사람이 건다."""
    items = []
    for name, L in ledgers.items():
        if not L:
            continue
        for d in L["decisions"]:
            if d["mirrors"]:
                continue
            items.append((name, d, _tokens(d["title"])))
    out = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            na, da, ta = items[i]
            nb, db, tb = items[j]
            if na == nb or not ta or not tb:
                continue
            inter = ta & tb
            score = len(inter) / len(ta | tb)
            if score < threshold or len(inter) < 2:
                continue
            split = bool(da["sup"]) != bool(db["sup"])
            out.append({"a": f"{na}:{da['id']}", "at": da["title"], "asup": da["sup"],
                        "b": f"{nb}:{db['id']}", "bt": db["title"], "bsup": db["sup"],
                        "score": round(score, 2), "shared": sorted(inter)[:6], "split": split})
    out.sort(key=lambda x: (not x["split"], -x["score"]))
    return out


# ─────────────────────────────────────────────────────────────
# 프로브 감사 — "이 프로브가 참인데 실제로는 미완일 수 있나"
# 상태를 기계가 쓴다고 해서 그 상태가 옳아지지는 않는다. 옳은지는
# 프로브가 '완료'의 정직한 대리인이냐에 달렸고, 그건 사람이 판단한다.
# 여기서는 구조적으로 거짓 양성이 나기 쉬운 형태만 걸러 준다.
# ─────────────────────────────────────────────────────────────
WEAK_OPS = {
    "exit0": "명령이 성공하기만 하면 참이다 — 대부분의 읽기 명령은 대상이 미완이어도 성공한다",
    "!empty": "출력이 비지 않기만 하면 참이다 — 조회가 되는 한 거의 항상 참",
    "contains": None,   # 아래에서 개별 판정
}


def _words(s):
    return {w.lower() for w in re.split(r"[^0-9A-Za-z가-힣]+", s or "") if len(w) >= 3}


def probe_strength(step):
    """→ (등급, 사유들). 등급: strong | weak | none

    거짓 양성이 구조적으로 나기 쉬운 형태만 잡는다. 의미가 맞는지는 판정하지 못한다."""
    p = step.get("probe")
    if not p or not p.get("cmd"):
        return "none", ["프로브가 없다 — 완료 판정 기준이 정해지지 않았다"]
    op, exp, cmd = p["op"], (p["expected"] or ""), p["cmd"]
    why = []
    if op in ("exit0", "!empty"):
        why.append(WEAK_OPS[op])
    if op == "empty":
        why.append("출력이 비어 있기만 하면 참이다 — 권한 오류·오타로도 비어 있다")
    if op in ("contains", "==") and exp:
        head = cmd.split("|")[0]                 # 첫 단계(실제 조회) 인자만 본다
        ew, cw = _words(exp), _words(head)
        if ew:
            hit = ew & cw
            if len(hit) / len(ew) >= 0.6 and hit:
                why.append("기대값이 명령 인자를 되비친다 — 겹치는 말: "
                           + ", ".join(sorted(hit))
                           + ". 조회가 되기만 하면 참이 될 수 있다")
    if not exp and op not in ("exit0", "!empty", "empty"):
        why.append("기대값이 비어 있다")
    return ("weak" if why else "strong"), why


def audit_probes(steps):
    out = []
    for s in steps:
        g, why = probe_strength(s)
        out.append({"id": s["id"], "title": s["title"], "grade": g, "why": why,
                    "cmd": (s.get("probe") or {}).get("cmd", ""),
                    "op": (s.get("probe") or {}).get("op", ""),
                    "expected": (s.get("probe") or {}).get("expected", ""),
                    "status": s.get("status", "")})
    return out


# ─────────────────────────────────────────────────────────────
# 계획 위생 — 프로브는 "계획대로 됐나"만 잰다. "계획이 아직 맞나"는 여기서 잰다.
# ─────────────────────────────────────────────────────────────
def stale_steps(steps, ledgers):
    """rests-on 이 가리키는 결정이 폐기됐으면 그 스텝은 낡았다.
    → {step_id: [{"ref", "title", "sup", "latest", "latest_title"}]}"""
    idx, succ = {}, {}
    for name, L in (ledgers or {}).items():
        if not L:
            continue
        for d in L["decisions"]:
            idx[f"{name}:{d['id']}"] = d
            if d["sup"]:
                succ[f"{name}:{d['id']}"] = f"{name}:{d['sup']}"
    out = {}
    for s in steps:
        hits = []
        for ref in s.get("rests_on", []):
            d = idx.get(ref)
            if d is None:
                hits.append({"ref": ref, "title": "", "sup": "", "latest": "",
                             "latest_title": "", "missing": True})
                continue
            if not d["sup"]:
                continue
            cur, seen = ref, set()
            while cur in succ and cur not in seen:
                seen.add(cur)
                cur = succ[cur]
            nd = idx.get(cur)
            hits.append({"ref": ref, "title": d["title"], "sup": d["sup"], "latest": cur,
                         "latest_title": nd["title"] if nd else "", "missing": False})
        if hits:
            out[s["id"]] = hits
    return out


def track_ledgers(track):
    """트랙 액터가 등록한 원장 + rests-on 이 참조하는 원장을 전부 로드한다."""
    names = set()
    for a in track.get("actors", []):
        led = a.get("ledger")
        if led and led not in ("—", "-", ""):
            names.add(led)
    for s in track.get("steps", []):
        for ref in s.get("rests_on", []):
            if ":" in ref:
                names.add(ref.rsplit(":", 1)[0])
    return {n: read_ledger(n) for n in sorted(names)}
