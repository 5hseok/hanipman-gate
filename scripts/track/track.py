#!/usr/bin/env python3
"""track — 다중 세션·다중 레포 트랙 원장 CLI.

  track list [--repo X]       모든 트랙 요약 — 이 레포에 걸린 트랙이 있는지
  track next                  지금 할 것 한 줄
  track ls                    스텝 큐 전체
  track verify [--step T-2]   프로브 일괄 실측 (상태는 이것만 쓴다)
  track probes --explain      실행될 명령을 전부 보여준다 (실행 안 함)
  track probes --audit        각 프로브가 거짓 양성을 낼 수 있는지 감사
  track impact                팀 공지·중단 요청이 필요한 스텝만
  track join --session X --steps T-2,T-3   자기 등록 + 브리프
  track brief --session X     그 세션이 알아야 할 것만
  track signal --step T-2 --kind merged --evidence 271e96f
  track conflicts             교차 원장 링크 중 한쪽만 폐기된 것
  track graph                 세션 그래프 JSON
  track serve [--port 4747]   실시간 UI

공통 옵션: --track <이름 일부>   (생략하면 가장 최근 트랙)
"""
import sys, os, json, argparse, re, subprocess
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trackcore as T

C = {"r": "\033[0m", "b": "\033[1m", "dim": "\033[2m", "red": "\033[31m", "grn": "\033[32m",
     "ylw": "\033[33m", "blu": "\033[36m", "mag": "\033[35m"}
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    C = {k: "" for k in C}

MARK = {"완료": ("✔", "grn"), "지금": ("▶", "ylw"), "가능": ("·", "blu"),
        "대기": ("·", "dim"), "미검증": ("?", "red"), "보류": ("⏸", "dim")}


def _repo_here():
    """현재 위치의 레포 이름 (워크트리면 상위 레포)."""
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return None
    if not top:
        return None
    if "/.claude/worktrees/" in top:
        top = top.split("/.claude/worktrees/")[0]
    return os.path.basename(top)


def _pick_track(name):
    """--track 이 없으면 현재 레포에 걸린 트랙을 우선한다.
    '가장 최근 트랙'만 보면 다른 프로젝트의 답을 조용히 내놓는다."""
    if name:
        return T.find_track(name), None
    import glob as _g
    repo = _repo_here()
    paths = sorted(_g.glob(os.path.join(T.TRACKS_DIR, "*", "*.md")))
    if repo:
        hits = []
        for p in paths:
            try:
                tr = T.parse_track(p)
            except Exception:
                continue
            if any(s["where"].get("repo") == repo for s in tr["steps"]):
                hits.append(p)
        if len(hits) == 1:
            return hits[0], None
        if len(hits) > 1:
            return hits[-1], f"{repo} 에 걸린 트랙이 {len(hits)}개다 — --track 으로 지정하는 편이 안전하다"
    return (paths[-1] if paths else None), (
        f"트랙이 {len(paths)}개다. 여기(레포 밖)에서는 가장 최근 것을 골랐다 — --track 으로 지정하라"
        if len(paths) > 1 else None)


def load(args, do_verify=False):
    p, warn = _pick_track(getattr(args, "track", None))
    if warn:
        print(f"{C['dim']}({warn}){C['r']}", file=sys.stderr)
    if not p:
        sys.exit(f"트랙 문서를 찾지 못했다: {T.TRACKS_DIR}/*/*.md  (track init 으로 만든다)")
    tr = T.parse_track(p)
    cache = T.verify(tr) if do_verify else T.load_cache(tr["id"])
    steps = T.resolve(tr, cache)
    stale = T.stale_steps(steps, T.track_ledgers(tr))
    for s in steps:
        s["stale"] = stale.get(s["id"], [])
    return tr, cache, steps


def fmt_where(s):
    w = s["where"]
    bits = [w.get("repo", "?")]
    if w.get("worktree"):
        bits.append("⑂ " + w["worktree"])
    if w.get("branch"):
        bits.append(w["branch"])
    return " · ".join(bits)


def stale(cache):
    if not cache.get("verifiedAt"):
        return "실측 없음"
    try:
        age = (datetime.now(timezone.utc) - T._parse_ts(cache["verifiedAt"])).total_seconds()
    except Exception:
        return "실측 시각 불명"
    if age < 90:
        return None
    m = int(age // 60)
    return f"{m}분 전 실측" if m < 90 else f"{m//60}시간 전 실측"


# ── next ───────────────────────────────────────────────────────
def cmd_next(args):
    tr, cache, steps = load(args, do_verify=not args.no_verify)
    st = stale(cache)
    cur = next((s for s in steps if s["status"] == "지금"), None)
    unk = [s for s in steps if s["status"] == "미검증"]
    done = sum(1 for s in steps if s["status"] == "완료")
    nstale = sum(1 for s in steps if s.get("stale"))
    print(f"{C['dim']}{tr['title']} — {done}/{len(steps)} 완료"
          + (f" · {C['red']}{st}{C['dim']}" if st else "") + f"{C['r']}")
    if nstale:
        print(f"{C['red']}⚠ 계획이 낡았다 — {nstale}개 스텝이 폐기된 결정 위에 서 있다. "
              f"'track ls' 로 확인. 실측은 이걸 잡지 못한다(계획이 아니라 세상을 잰다).{C['r']}")
    if not cur:
        if unk:
            print(f"{C['red']}▶ 다음이 정해지지 않았다 — 미검증 {len(unk)}건이 앞을 막고 있다.{C['r']}")
            for s in unk[:4]:
                print(f"  {C['red']}?{C['r']} {s['id']} {s['title']}  {C['dim']}{s['evidence'] or s['probe_err'] or '프로브 없음'}{C['r']}")
            return
        print(f"{C['grn']}▶ 남은 스텝 없음.{C['r']}")
        return
    print()
    print(f"{C['ylw']}{C['b']}▶ {cur['id']} · {cur['title']}{C['r']}")
    if cur["why"]:
        print(f"  {cur['why']}")
    print(f"  {C['blu']}{fmt_where(cur)}{C['r']}"
          + (f"   {C['dim']}gate={cur['gate']} env={cur['env']}{C['r']}" if cur["gate"] else ""))
    if cur["owner"]:
        print(f"  {C['dim']}담당 세션: {cur['owner']}{C['r']}")
    if cur["team"]:
        print(f"  {C['mag']}팀 요청: {cur['team']}{C['r']}")
    if cur["evidence"]:
        print(f"  {C['dim']}실측: {cur['evidence'][:120]}{C['r']}")
    nxt = [s for s in steps if s["status"] in ("가능", "대기")][:2]
    if nxt:
        print(f"\n{C['dim']}다음: " + " / ".join(f"{s['id']} {s['title'][:26]}" for s in nxt) + f"{C['r']}")
    if unk:
        print(f"{C['red']}주의: 미검증 {len(unk)}건 ({', '.join(s['id'] for s in unk)}) — 완료로 세지 않았다.{C['r']}")


# ── ls ─────────────────────────────────────────────────────────
def cmd_ls(args):
    tr, cache, steps = load(args, do_verify=args.verify)
    st = stale(cache)
    print(f"{C['b']}{tr['title']}{C['r']}  {C['dim']}{tr['id']}"
          + (f" · {C['red']}{st}" if st else f" · 실측 {cache.get('verifiedAt','—')}") + f"{C['r']}\n")
    for s in steps:
        mk, col = MARK.get(s["status"], ("·", "dim"))
        print(f"{C[col]}{mk}{C['r']} {s['id']:5} {C['b']}{s['title']}{C['r']}"
              f"  {C[col]}[{s['status']}]{C['r']}")
        line = f"      {C['dim']}{fmt_where(s)}"
        if s["gate"]:
            line += f" · {s['gate']}/{s['env']}"
        if s["owner"]:
            line += f" · {s['owner']}"
        print(line + C["r"])
        if s["status"] == "대기" and s["blockers"]:
            print(f"      {C['dim']}선행 대기: {', '.join(s['blockers'])}{C['r']}")
        for h in s.get("stale", []):
            print(f"      {C['red']}⚠ 낡음 — 딛고 선 {h['ref']} 폐기"
                  + (f" → 지금은 {h['latest']} {h['latest_title'][:44]}" if h.get('latest') else " (대상 없음)")
                  + f"{C['r']}")
        if s["status"] == "미검증":
            print(f"      {C['red']}미검증: {s['evidence'] or s['probe_err'] or '프로브 없음'}{C['r']}")
        elif s["evidence"]:
            print(f"      {C['dim']}실측: {s['evidence'][:110]}{C['r']}")
        if s["team"]:
            print(f"      {C['mag']}팀: {s['team']}{C['r']}")


# ── verify ─────────────────────────────────────────────────────
def cmd_verify(args):
    p = T.find_track(args.track)
    if not p:
        sys.exit("트랙 문서 없음")
    tr = T.parse_track(p)
    only = set(x.strip() for x in args.step.split(",")) if args.step else None
    t0 = datetime.now(timezone.utc)
    cache = T.verify(tr, only=only)
    steps = T.resolve(tr, cache)
    dt = (datetime.now(timezone.utc) - t0).total_seconds()
    for s in steps:
        if only and s["id"] not in only:
            continue
        mk, col = MARK.get(s["status"], ("·", "dim"))
        print(f"{C[col]}{mk} {s['id']:5} {s['status']:4}{C['r']} {s['title'][:46]:48}"
              f"{C['dim']}{(s['evidence'] or s['probe_err'])[:60]}{C['r']}")
    n = sum(1 for s in steps if s["status"] == "완료")
    u = sum(1 for s in steps if s["status"] == "미검증")
    print(f"\n{C['dim']}{len(steps)}스텝 · 완료 {n} · 미검증 {u} · {dt:.1f}s{C['r']}")
    if u:
        print(f"{C['red']}미검증 {u}건은 완료로 세지 않는다 — 프로브를 채우거나 판정 기준을 정해야 한다.{C['r']}")
    for msg in tr.get("lint", []):
        print(f"{C['red']}원장 결함: {msg}{C['r']}")


# ── probes --explain ───────────────────────────────────────────
def cmd_probes(args):
    tr, cache, steps = load(args)
    if args.audit:
        rows = T.audit_probes(steps)
        print(f"{C['dim']}프로브가 '완료'의 정직한 대리인인지 본다. "
              f"거짓 양성이 나기 쉬운 형태만 잡는다 — 의미가 맞는지는 사람이 판단한다.{C['r']}\n")
        order = {"none": 0, "weak": 1, "strong": 2}
        for r in sorted(rows, key=lambda x: order[x["grade"]]):
            tag = {"none": f"{C['red']}없음  {C['r']}", "weak": f"{C['ylw']}약함  {C['r']}",
                   "strong": f"{C['grn']}견고  {C['r']}"}[r["grade"]]
            print(f"{tag} {r['id']:5} {r['title'][:38]:40}{C['dim']}{r['op']} {r['expected'][:24]}{C['r']}")
            for w in r["why"]:
                print(f"        {C['ylw']}→ {w}{C['r']}")
            if r["grade"] != "strong" and r["status"] == "완료":
                print(f"        {C['red']}⚠ 이 스텝은 지금 '완료'로 세어지고 있다.{C['r']}")
        n = sum(1 for r in rows if r["grade"] == "strong")
        print(f"\n{C['dim']}{len(rows)}스텝 · 견고 {n} · 나머지 {len(rows)-n} 은 완료 판정을 믿을 근거가 약하다{C['r']}")
        return
    print(f"{C['dim']}실행될 명령 전부. 쓰기 동사는 실행 자체가 거부된다.{C['r']}\n")
    for s in steps:
        p = s.get("probe")
        if not p or not p.get("cmd"):
            print(f"{C['red']}?{C['r']} {s['id']:5} {C['dim']}프로브 없음 — {p['malformed'] if p else '미정의'}{C['r']}")
            continue
        allow_plan = s.get("probe_kind") == "plan"
        try:
            stages = T.check_probe_cmd(p["cmd"], allow_plan=allow_plan)
            ok = f"{C['grn']}허용{C['r']}"
            detail = f"{C['dim']}{len(stages)}단계 · {p['op']} {p['expected']}{C['r']}"
        except T.ProbeRejected as e:
            ok = f"{C['red']}거부{C['r']}"
            detail = f"{C['red']}{e}{C['r']}"
        print(f"{ok} {s['id']:5} {detail}")
        print(f"      {C['blu']}{p['cmd']}{C['r']}")


# ── impact ─────────────────────────────────────────────────────
def cmd_impact(args):
    tr, cache, steps = load(args, do_verify=args.verify)
    rows = [s for s in steps if s["team"] and s["status"] != "완료"]
    if not rows:
        print("팀에 요청할 것 없음.")
        return
    print(f"{C['b']}팀 공지·중단 요청이 필요한 스텝{C['r']}\n")
    for s in rows:
        mk, col = MARK.get(s["status"], ("·", "dim"))
        when = "지금" if s["status"] == "지금" else f"{s['id']} 시점"
        print(f"{C[col]}{mk}{C['r']} {C['b']}{when}{C['r']} — {s['title']}")
        print(f"   {C['mag']}{s['team']}{C['r']}")
        print(f"   {C['dim']}{fmt_where(s)} · {s['gate']}/{s['env']}{C['r']}\n")


# ── join / brief ───────────────────────────────────────────────
def _session_self():
    """현재 세션 이름을 sessions 레지스트리에서 추정 (부모 pid 계열)."""
    import glob
    best = None
    for p in glob.glob(os.path.join(T.SESS_DIR, "*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if d.get("cwd") == os.getcwd():
            if not best or d.get("updatedAt", 0) > best.get("updatedAt", 0):
                best = d
    return best.get("name") if best else None


def _brief(tr, cache, steps, session):
    mine = [s for s in steps if s["owner"] == session]
    acts = {a["session"]: a for a in tr["actors"]}
    if session in acts and acts[session]["steps"]:
        ids = set(acts[session]["steps"])
        mine = [s for s in steps if s["id"] in ids] or mine
    L = []
    L.append(f"# 트랙 브리프 — {session}")
    L.append(f"트랙: {tr['title']} ({tr['id']}) · 실측 {cache.get('verifiedAt','없음')}")
    L.append("")
    L.append("## 내 담당")
    if not mine:
        L.append("- (등록된 담당 스텝 없음 — track join --steps 로 등록한다)")
    for s in mine:
        L.append(f"- [{s['status']}] {s['id']} · {s['title']}")
        if s["why"]:
            L.append(f"      왜: {s['why']}")
        L.append(f"      위치: {fmt_where(s)}   gate={s['gate']} env={s['env']}")
        if s["blockers"]:
            L.append(f"      선행 대기: {', '.join(s['blockers'])}")
        if s["team"]:
            L.append(f"      팀 요청: {s['team']}")
        if s["status"] == "미검증":
            L.append(f"      ⚠ 미검증 — 완료 판정 기준(probe)이 없다. 정하고 문서에 넣어라.")
    dep = sorted({b for s in mine for b in s["blockers"]})
    if dep:
        L.append("")
        L.append("## 내가 기다리는 것")
        for d in dep:
            o = next((x for x in steps if x["id"] == d), None)
            if o:
                L.append(f"- {o['id']} · {o['title']} [{o['status']}] — 담당 {o['owner'] or '미지정'}")
    L.append("")
    L.append("## 보고 기준 — 이때만 보낸다")
    L.append("1. 스텝 상태가 실제로 움직였을 때 (추정 아닌 관측)")
    L.append("2. 결정이 새로 서거나 뒤집혔을 때")
    L.append("3. 보드에 없는 선행이 새로 생겼을 때")
    L.append("4. 막혀 있던 것이 풀렸을 때")
    L.append("5. 내가 남의 실측을 뒤집었을 때")
    L.append("예외) plan 에 예상 밖 항목이 섞였을 때 — 수치가 아니라 오너 결정이 필요한 사안으로")
    L.append("")
    L.append("보내지 않는 것: 진행 경과 · 같은 사실 재설명 · 상대 원장에 이미 있는 내용")
    L.append("상태는 문서에 쓰지 않는다. 신호만 남긴다:")
    L.append(f"  track signal --step <T-n> --kind merged|applied|deployed|blocked --evidence <해시·시각>")
    return "\n".join(L)


def cmd_brief(args):
    tr, cache, steps = load(args, do_verify=args.verify)
    s = args.session or _session_self()
    if not s:
        sys.exit("세션 이름을 알 수 없다 — --session 으로 지정한다")
    print(_brief(tr, cache, steps, s))


def cmd_join(args):
    p = T.find_track(args.track)
    if not p:
        sys.exit("트랙 문서 없음")
    session = args.session or _session_self()
    if not session:
        sys.exit("세션 이름을 알 수 없다 — --session 으로 지정한다")
    text = open(p, encoding="utf-8").read()
    sec = T._section(text, "## 액터")
    row = f"| {session} | {args.steps or ''} | {args.ledger or ''} |"
    if re.search(r"(?m)^\| *%s *\|" % re.escape(session), sec):
        text = re.sub(r"(?m)^\| *%s *\|.*$" % re.escape(session), row, text, count=1)
        act = "갱신"
    else:
        m = re.search(r"(?m)^## 액터 *$", text)
        rest = text[m.end():]
        nxt = re.search(r"(?m)^## ", rest)
        blk = rest[:nxt.start()] if nxt else rest
        newblk = blk.rstrip("\n") + "\n" + row + "\n\n"
        text = text[:m.end()] + newblk + (rest[nxt.start():] if nxt else "")
        act = "등록"
    open(p, "w", encoding="utf-8").write(text)
    tr = T.parse_track(p)
    cache = T.load_cache(tr["id"])
    steps = T.resolve(tr, cache)
    print(f"{C['grn']}✔ {session} {act}{C['r']}  {C['dim']}{p}{C['r']}\n")
    print(_brief(tr, cache, steps, session))


# ── signal ─────────────────────────────────────────────────────
def cmd_signal(args):
    p = T.find_track(args.track)
    if not p:
        sys.exit("트랙 문서 없음")
    session = args.by or _session_self() or "unknown"
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"- {ts} · {session} · {args.step} · {args.kind}" + (f" · {args.evidence}" if args.evidence else "")
    text = open(p, encoding="utf-8").read()
    m = re.search(r"(?m)^## 신호 *$", text)
    if not m:
        text = text.rstrip("\n") + "\n\n## 신호\n" + line + "\n"
    else:
        rest = text[m.end():]
        nxt = re.search(r"(?m)^## ", rest)
        blk = (rest[:nxt.start()] if nxt else rest).rstrip("\n")
        text = text[:m.end()] + blk + "\n" + line + "\n\n" + (rest[nxt.start():] if nxt else "")
    open(p, "w", encoding="utf-8").write(text)
    print(f"{C['grn']}✔ 신호 기록{C['r']} {line}")
    print(f"{C['dim']}상태는 바뀌지 않았다 — 프로브만 상태를 쓴다. track verify --step {args.step}{C['r']}")


# ── conflicts / graph / serve ──────────────────────────────────
def cmd_conflicts(args):
    tr = T.parse_track(T.find_track(args.track))
    ledgers = {}
    for a in tr["actors"]:
        if a["ledger"] and a["ledger"] not in ("—", "-", ""):
            ledgers[a["ledger"]] = T.read_ledger(a["ledger"])
    if not ledgers:
        print("액터에 등록된 원장이 없다. track join --ledger <task 파일명 일부>")
        return
    for name, L in ledgers.items():
        if not L:
            print(f"{C['red']}원장 못 찾음: {name}{C['r']}")
            continue
        act = [d for d in L["decisions"] if not d["sup"]]
        mir = [d for d in act if d["mirrors"]]
        print(f"{C['b']}{name}{C['r']} {C['dim']}유효 {len(act)} · 링크 {len(mir)}{C['r']}")
    cf = T.conflicts(ledgers)
    print()
    if cf:
        for c in cf:
            print(f"{C['red']}⚠ {c['kind']}{C['r']} {c['a']} ↔ {c['b']}\n   {c['msg']}")
    else:
        print(f"{C['grn']}링크된 결정 중 충돌 없음.{C['r']} "
              f"{C['dim']}단, mirrors 링크가 없는 결정은 감지 대상이 아니다.{C['r']}")

    cand = T.link_candidates(ledgers)
    if not cand:
        return
    print(f"\n{C['b']}링크 후보{C['r']} {C['dim']}— 제목이 겹쳐 같은 사안으로 보인다. "
          f"링크는 사람이 건다: 결정 본문에 `- **mirrors**: <원장>:<D-n>`{C['r']}\n")
    for c in cand[:args.limit]:
        head = f"{C['red']}⚠ 한쪽만 폐기{C['r']}" if c["split"] else f"{C['dim']}·{C['r']}"
        print(f"{head} {c['a']}{' (폐기)' if c['asup'] else ''} ↔ {c['b']}{' (폐기)' if c['bsup'] else ''}"
              f"  {C['dim']}겹침 {c['score']} · {' '.join(c['shared'][:4])}{C['r']}")
        print(f"     {c['at'][:64]}")
        print(f"     {c['bt'][:64]}")


def cmd_list(args):
    """모든 트랙을 훑어 레포·진행·지금 할 것을 요약한다. 훅과 새 세션이 쓰는 진입점."""
    import glob as _g
    paths = sorted(_g.glob(os.path.join(T.TRACKS_DIR, "*", "*.md")))
    if not paths:
        if not args.quiet:
            print("트랙 없음.")
        return
    rows = []
    for p in paths:
        try:
            tr = T.parse_track(p)
        except Exception:
            continue
        repos = []
        for s in tr["steps"]:
            r = s["where"].get("repo")
            if r and r not in repos:
                repos.append(r)
        if args.repo and not any(args.repo in r or r in args.repo for r in repos):
            continue
        cache = T.load_cache(tr["id"])
        steps = T.resolve(tr, cache)
        done = sum(1 for s in steps if s["status"] == "완료")
        unk = sum(1 for s in steps if s["status"] == "미검증")
        cur = next((s for s in steps if s["status"] == "지금"), None)
        rows.append({"tr": tr, "path": p, "repos": repos, "done": done, "unk": unk,
                     "total": len(steps), "cur": cur, "at": cache.get("verifiedAt"),
                     "status": tr["meta"].get("상태", "")})
    if not rows:
        if not args.quiet:
            print(f"'{args.repo}' 에 걸린 트랙 없음." if args.repo else "트랙 없음.")
        return
    for r in rows:
        tr = r["tr"]
        print(f"{C['b']}{tr['title']}{C['r']}  {C['dim']}{tr['id']} · {r['status']}{C['r']}")
        print(f"  {C['dim']}레포: {', '.join(r['repos']) or '—'}{C['r']}")
        bar = f"{r['done']}/{r['total']} 완료" + (f" · {C['red']}미검증 {r['unk']}{C['r']}" if r['unk'] else "")
        print(f"  {bar}" + (f"  {C['dim']}실측 {(r['at'] or '없음')[:16].replace('T',' ')}{C['r']}" if not args.quiet else ""))
        if r["cur"]:
            print(f"  {C['ylw']}▶ 지금: {r['cur']['id']} {r['cur']['title']}{C['r']}")
            w = r["cur"]["where"]
            print(f"     {C['dim']}{w.get('repo','?')}"
                  + (f" · {w['branch']}" if w.get("branch") else "")
                  + (f" · 담당 {r['cur']['owner']}" if r["cur"]["owner"] else "") + f"{C['r']}")
        if not args.quiet:
            print(f"  {C['dim']}{r['path']}{C['r']}")
        print()


def cmd_graph(args):
    print(json.dumps(T.session_graph(days=args.days), ensure_ascii=False, indent=1))


def cmd_serve(args):
    here = os.path.dirname(os.path.abspath(__file__))
    os.execv(sys.executable, [sys.executable, os.path.join(here, "trackd.py"),
                              "--port", str(args.port), "--host", args.host]
                             + (["--track", args.track] if args.track else []))


def cmd_init(args):
    now = datetime.now()
    d = os.path.join(T.TRACKS_DIR, now.strftime("%Y-%m"))
    os.makedirs(d, exist_ok=True)
    slug = re.sub(r"[^\w가-힣-]+", "-", args.title).strip("-")
    path = os.path.join(d, f"{now.strftime('%m%d')}-track-{slug}.md")
    if os.path.exists(path):
        sys.exit(f"이미 있다: {path}")
    open(path, "w", encoding="utf-8").write(f"""# 트랙 · {args.title}

## 메타
- **id**: {args.id or slug}
- **생성일**: {now.strftime('%Y-%m-%d')}
- **상태**: 진행중

> `note:` 는 게이트 근거 — 왜 이 순서인지. `needs:` 는 기계가 읽는 의존, `note:` 는 사람이 읽는 이유다.
> 상태 필드는 없다 — 완료 판정은 `probe:` 만 쓴다. 프로브를 못 쓰겠으면 비워 두고 미검증으로 남긴다.

## 액터
| 세션 | 담당 | 원장 |
|---|---|---|

## 스텝

### T-1 · (제목)
- why: (한 줄 — 왜 필요한가)
- where: repo= branch=
- gate: merge
- env: dev
- needs: —
- owner:
- team: (팀에 공지·중단·권한을 요청해야 하면 한 줄. 없으면 비운다)
- note: 게이트 — (왜 이 순서인가. needs 만으로는 이해가 사라진다)
- probe: `gh pr view 0 --repo owner/repo --json state --jq .state` :: == MERGED

## 신호
""")
    print(path)


def main():
    ap = argparse.ArgumentParser(prog="track", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--track", default=None, help="트랙 이름 일부 (생략 시 최근)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("next"); p.add_argument("--no-verify", action="store_true"); p.set_defaults(f=cmd_next)
    p = sub.add_parser("ls"); p.add_argument("--verify", action="store_true"); p.set_defaults(f=cmd_ls)
    p = sub.add_parser("verify"); p.add_argument("--step", default=None); p.set_defaults(f=cmd_verify)
    p = sub.add_parser("probes"); p.add_argument("--explain", action="store_true")
    p.add_argument("--audit", action="store_true", help="프로브의 거짓 양성 위험을 감사")
    p.set_defaults(f=cmd_probes)
    p = sub.add_parser("impact"); p.add_argument("--verify", action="store_true"); p.set_defaults(f=cmd_impact)
    p = sub.add_parser("brief"); p.add_argument("--session"); p.add_argument("--verify", action="store_true"); p.set_defaults(f=cmd_brief)
    p = sub.add_parser("join"); p.add_argument("--session"); p.add_argument("--steps"); p.add_argument("--ledger"); p.set_defaults(f=cmd_join)
    p = sub.add_parser("signal"); p.add_argument("--step", required=True); p.add_argument("--kind", required=True)
    p.add_argument("--evidence"); p.add_argument("--by"); p.set_defaults(f=cmd_signal)
    p = sub.add_parser("conflicts"); p.add_argument("--limit", type=int, default=6); p.set_defaults(f=cmd_conflicts)
    p = sub.add_parser("list", help="모든 트랙 요약 (--repo 로 필터)")
    p.add_argument("--repo", default=None); p.add_argument("--quiet", action="store_true")
    p.set_defaults(f=cmd_list)
    p = sub.add_parser("graph"); p.add_argument("--days", type=int, default=4); p.set_defaults(f=cmd_graph)
    p = sub.add_parser("serve"); p.add_argument("--port", type=int, default=4747)
    p.add_argument("--host", default="127.0.0.1", help="기본 루프백. 다른 기기에서 보려면 0.0.0.0")
    p.set_defaults(f=cmd_serve)
    p = sub.add_parser("init"); p.add_argument("--title", required=True); p.add_argument("--id"); p.set_defaults(f=cmd_init)

    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
