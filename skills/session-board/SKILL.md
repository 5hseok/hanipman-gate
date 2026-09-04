---
name: session-board
description: |
  지금 어느 Claude 세션이 어느 레포에서 무엇을 하고 있는지를 세션 레지스트리에서 읽고,
  스텝의 순서·의존과 함께 한 장의 보드로 만들어 Artifact 로 게시한다. 이후 같은 URL 로 갱신한다.
  원장 문서도, 로컬 서버도, CLI 도 없다. 근거 없는 완료는 쓰지 않고 미검증으로 남긴다.
  Use when: 지금 뭐가 어디까지 갔지, 세션 현황 보드, 작업 보드 만들어줘, 진행 상황 정리,
  여러 세션 뭐 하고 있나, 보드 갱신, session board, 작업 현황 아티팩트, 세션 상태 한눈에
user-invocable: true
allowed-tools: Read Write Bash Glob Grep Artifact Skill
---

# 세션 보드

**목적:** "지금 뭐가 어디까지 갔나" 를 한 장으로 본다. 출발점은 Claude 세션 레지스트리, 산출물은 Artifact 한 장이다.

`track-ledger` 와의 차이: 원장 문서·`rests-on`·프로브 샌드박스·`track` CLI·로컬 서버가 **전부 없다.**
계승하는 것은 셋뿐이다 — 스텝은 순서와 의존을 갖는다 · 근거 없는 완료는 쓰지 않는다 · 계획은 사람이 쓴다.

## Step 0 — 보드가 이미 있으면 새로 만들지 않는다

`Artifact(action: "list")` 로 확인한다. 이 일감의 보드가 있으면 **그 URL 을 갱신한다**(Step 4). 보드가 두 장이 되면 이 스킬의 의미가 사라진다.

## Step 1 — 세션에서 현재 상황을 읽는다

세션 레지스트리는 `~/.claude/sessions/<pid>.json` 이다. 파일명 숫자가 그 세션의 pid 다.

```bash
python3 - <<'PY'
import json, glob, os
for f in sorted(glob.glob(os.path.expanduser('~/.claude/sessions/*.json'))):
    try: d = json.load(open(f))
    except Exception: continue            # 스키마·형식이 다를 수 있다. 건너뛴다
    try: os.kill(d.get('pid') or -1, 0); alive = True   # 죽은 세션 파일이 남아 있다
    except Exception: alive = False
    print(d.get('name'), d.get('status'), d.get('kind'), d.get('cwd'), alive, d.get('updatedAt'))
PY
```

**실제로 확인된 필드** (이 목록 밖은 있다고 쓰지 마라):

| 필드 | 값 | 비고 |
|---|---|---|
| `name` | 세션 이름 | 보드의 담당자 축 |
| `status` | `idle` · `busy` | `busy` 는 "지금 돌고 있다" 일 뿐, 진척이 아니다 |
| `kind` | `interactive` · `bg` | `bg` 는 백그라운드 잡 |
| `cwd` | 작업 디렉터리 | 레포 판별의 유일한 단서 |
| `pid` · `sessionId` · `startedAt` · `updatedAt` · `statusUpdatedAt` | 문자열 · epoch ms | 신선도 판단용 |

**파일마다 스키마가 다르다.** `tmux`(문자열 `"세션:@창.%페인"`) · `nameSource` · `bridgeSessionId` 는 없는 파일이 있고,
`jobId` 는 `kind: "bg"` 세션에만 있다. 전부 `.get()` 으로 읽고 없으면 비워라.

**브랜치와 "무엇을 하는 중인지" 는 세션 파일에 없다.** 브랜치는 `cwd` 에서 따로 읽는다 —
`git -C "<cwd>" rev-parse --abbrev-ref HEAD` · `git -C "<cwd>" log -1 --format='%h %s'`.
하는 일은 어디에도 없다. **사람에게 묻거나 비워 둔다.** 추측으로 채우지 마라.

## Step 2 — 스텝은 사람이 쓴다

세션 목록은 사실이지 계획이 아니다. 순서와 왜는 사용자와 대화해서 만든다.

```
S-3 · dev·stage 배포
  why:    머지만으로는 파이프라인이 안 바뀐다. 배포해야 트리거 경로에 들어간다
  where:  repo=<레포> branch=<브랜치>
  needs:  S-2
  owner:  <세션 이름>   ← Step 1 의 name 과 맞춘다
  state:  미검증 | 진행중 | 완료 | 막힘
  why-so: 그 상태로 판단한 근거 (커밋 해시 · PR 번호 · 로그 · 사람의 확인)
```

**상태 규칙 — 예외 없다.** `완료` 는 `why-so` 에 확인 가능한 근거가 있을 때만 쓴다. 없으면 **`미검증`** 이다.
`busy` 세션이 붙어 있다는 것도, 세션이 완료했다고 보고한 것도 근거가 아니다.
미검증은 부끄러운 값이 아니다. **조용히 틀린 완료가 이 보드를 못 쓰게 만든다.**

## Step 3 — 보드를 게시한다

`Skill(artifact-design)` 으로 디자인 기준을 잡고, HTML 을 파일로 쓴 뒤 게시한다.

```
Artifact(file_path: "<보드.html>", favicon: "🧭", capabilities: {artifact: {}}, description: "<한 문장>")
```

`capabilities: {artifact: {}}` 를 선언해야 **페이지가 자기 새 버전을 저장**한다 — 보드에서 체크하거나 상태를 바꾼 게 남는다.

- `const artifact = await claude.use("artifact")` — `null` 이면 읽기 전용으로 렌더한다.
- 보드 상태를 **HTML 안에 데이터로 심고 거기서 화면을 그린다.** 살아 있는 DOM 을 직렬화하지 마라.
- 사람이 뭔가 바꿨을 때만 상태를 갱신해 문서를 다시 만들고 `await artifact.publish(html)`. **로드 시 게시 금지.**
- `conflict` 는 정상이다(모든 뷰가 승자로 새로고침된다). 재시도하지 마라.
- 보기 권한만 있는 뷰어는 `not_granted` · `not_writer` 로 거절된다 — 그 경우도 화면은 멀쩡해야 한다.

세부 계약은 `Skill(artifact-capabilities)`. 다른 capability 는 이 보드에 필요 없다.

## Step 4 — 갱신한다

같은 파일 경로로 다시 `Artifact` 를 호출하면 **같은 URL 로 재게시**된다. `favicon` 은 다시 넘기지 않는다.
다른 세션이 만든 보드거나 로컬 파일이 없으면 **읽기부터 한다**:

```
Artifact(action: "read", url: "<보드 URL>")   → 내용을 파일로 저장 → 수정
Artifact(file_path: "<같은 파일>", url: "<보드 URL>")
```

읽지 않고 게시하면 거절된다. 페이지가 스스로 저장한 새 버전이 있을 수 있기 때문이다.
충돌이 나면 **돌아온 최신본 위에 내 변경을 얹어 다시 게시한다.** `force` 는 쓰지 마라.

## 이건 안 된다

- **자동 갱신이 없다.** 보드는 게시 시점의 스냅샷이다. 최신으로 만들려면 이 스킬을 다시 돌려야 한다.
- **페이지가 세션 파일을 읽지 못한다.** 세션 정보는 게시하는 쪽이 넣어 주는 값이다.
- **실측 프로브가 없다.** GitHub·클라우드 상태를 자동으로 재지 않는다. 그게 필요하면 `track-ledger` 다.
- 세션 레지스트리는 **이 기계에만** 있다. 다른 기계에서 도는 세션은 보이지 않는다.

## 자주 하는 실수

| 실수 | 결과 |
|---|---|
| `status: busy` 를 진척으로 읽는다 | 붙어만 있는 세션이 "진행중" 으로 굳는다 |
| 죽은 세션 파일을 그대로 싣는다 | 없는 담당자가 보드에 남는다. pid 를 확인해라 |
| 근거 없이 `완료` 로 적는다 | 보드를 못 믿게 된다. 미검증이 낫다 |
| 로드할 때마다 `publish` | 버전이 무한히 쌓이고 서로 덮어쓴다 |
