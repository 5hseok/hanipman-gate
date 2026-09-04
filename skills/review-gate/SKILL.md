---
name: review-gate
description: Machine verification gate run after a slice is implemented, before a human ever reads the diff. Chains lsp diagnostics, convention audit, design-ledger conformance, and out-of-scope change detection into a single GREEN/RED verdict plus a "needs human judgment" list. Project-agnostic — base branch, lint command, and convention docs are resolved at runtime.
user-invocable: true
argument-hint: "[slice-id] [--no-fix] [--task <task-file>]"
allowed-tools: Read Bash Grep Glob Write Agent Skill
---

## Review Gate

The reviewer's time should scale with **the number of items the machine could not certify**, not with the size of the diff. This skill is the certifier. It runs after a slice is implemented and before the human opens the diff.

It never edits source files itself, never stages, never commits. It verifies, optionally delegates fixes, and emits a verdict.

> **The gate certifies. It does not decide.** Anything ambiguous is escalated to the user, never auto-resolved.

> MUST invoke `Skill(design-workflow)` before running this skill.

---

### Arguments

```
/review-gate [slice-id] [--no-fix] [--task <task-file>]
```

- `slice-id` — e.g. `S-2`. If omitted, infer from the ledger's 작업 큐 (last slice whose target files intersect the current diff). If still ambiguous, ask the user.
- `--no-fix` — report only. Never delegate fixes, never re-run. Verdict is emitted as-is.
- `--task <task-file>` — the Obsidian ledger task document. Resolution order below.

### Resolving the ledger task document

1. `--task` argument, if given.
2. `.claude/review/{branch-slug}/context.json` → `.task_file` (written by `/impl-pipeline`).
3. Search the vault by branch keyword:
   ```bash
   VAULT="${OBSIDIAN_VAULT:?원장 위치를 지정하십시오 — 아직 어댑터 이전 형태입니다}"
   BRANCH=$(git branch --show-current)
   KEYWORD="${BRANCH##*/}"
   ls "$VAULT/tasks/"*/ | grep -i "$KEYWORD"
   ```
4. Still not found → ask the user. Do **not** guess.

`{branch-slug}` = `git branch --show-current | tr '/' '-'`.

---

## Step 0 — Collect inputs

### 0a. Project context — resolve, never assume

Read `.claude/review/{branch-slug}/context.json` if `/impl-pipeline` wrote one. It carries
`base_branch`, `conventions`, `layers`, `lint_cmd`, `project`. Any field may be `null` — a null is a
fact, not a failure.

Invoked standalone (no `context.json`), derive the two fields this skill actually needs:

- **Base branch** — never hardcode `dev` or `main`. Among the remotes that exist, take the one this
  branch is nearest to (a branch cut from `dev` is far fewer commits from `dev` than from `main`):
  ```bash
  BASE=$(for C in origin/dev origin/develop origin/main origin/master; do
           git rev-parse --verify -q "$C" >/dev/null && echo "$(git rev-list --count "$C"..HEAD) $C"
         done | sort -n | head -1 | cut -d' ' -f2)
  ```
  Tie or nothing found → ask the user.
- **Lint command** — from `context.json.lint_cmd`, else detect from the project's own config
  (see Check 1).

### 0b. Repo basics

```bash
VAULT="${OBSIDIAN_VAULT:?원장 위치를 지정하십시오 — 아직 어댑터 이전 형태입니다}"
LOG="$VAULT/.claude/scripts/obsidian-log.py"
BRANCH=$(git branch --show-current)
SLUG=$(echo "$BRANCH" | tr '/' '-')
HEAD_SHA=$(git rev-parse --short HEAD)
mkdir -p ".claude/review/$SLUG"

# Changed files (working tree + staged + committed-on-branch, vs the resolved base)
git fetch origin "${BASE#origin/}" --quiet
git diff --name-only --diff-filter=ACMR "$BASE"...HEAD
git diff --name-only --diff-filter=ACMR          # unstaged
git diff --cached --name-only --diff-filter=ACMR # staged
```

Confirm the artifact directory stays out of the commit — repos differ, and this skill is global:

```bash
git check-ignore -q .claude/review && echo IGNORED || echo TRACKED
```

`TRACKED` → warn the user in the stdout summary: *"⚠️ `.claude/review/`가 gitignore되지 않는다 — 커밋 시 제외하라."*
Never edit the project's `.gitignore` yourself.

### 0c. Read the ledger

**Never `Read` the whole task document** — the CLI exists to keep this cheap:

```bash
# Decisions owned by this slice + the 작업 큐 (target-file globs, acceptance criteria)
python3 "$LOG" current --file "$TASK" --with-queue
# Narrow to one topic when a slice maps to a single topic slug
python3 "$LOG" current --file "$TASK" --topic <topic-slug>
```

**Fallback** — if `current` is unavailable (`invalid choice: 'current'`), the ledger CLI has not shipped that subcommand yet. Then:
- `Read` only the `## 현재 설계` and `## 작업 큐` sections of the task file (use `Grep -n` to find the section line ranges first, then `Read` with `offset`/`limit`).
- Mark checks 3 and 4 as `SKIPPED (ledger CLI unavailable — 설계 정합/설계 밖 변경 미검증)` if even that fails, and continue. Checks 1 and 2 must still run.

---

## The verification chain

Run in order. **A failure in any single check never aborts the gate** — mark it `SKIPPED (reason)` and continue, so the human can still review. See § Fallbacks.

---

### Check 1 — Diagnostics (`lsp`)

Target: the changed files only.

Use the IDE/LSP diagnostics tool if one is available in the session. If not, fall back to **the project's
own linter** — take it from `context.json.lint_cmd`, or detect it:

| Signal in the repo | Command |
|---|---|
| `pyproject.toml` with `[tool.ruff]` | `ruff check <changed .py files>` (via the project's venv/poetry) |
| `package.json` with a `lint` script | `npm run lint -- <changed files>` (or the pnpm/yarn equivalent) |
| `Makefile` with a `lint` target | `make lint` |
| `.golangci.yml` / `go.mod` | `go vet ./...` |
| None of the above | `lsp: SKIPPED (린터 미발견)` — continue |

<!-- 예시: Python 모노레포. 명령 형태만 참고할 것 — 규범이 아니다 -->
```bash
# e.g. a Poetry-managed Python repo:
poetry run ruff check $(git diff --name-only --diff-filter=ACMR "$BASE"...HEAD -- '*.py')
```

#### Judge cumulatively, not per-slice

A mid-sequence slice legitimately references symbols that a later slice has not created yet. Failing it for that is noise.

1. Read the 작업 큐. Collect the slices **after** this one that are not yet done.
2. For each diagnostic, classify as **unresolved-dependency** when *all* hold:
   - it is an undefined-name / unresolved-import class of error (`F821`, `F401`-on-import-of-missing, TS2304, LSP "cannot resolve"), **and**
   - the missing symbol lives in a file matched by a **later slice's target-file glob**, or is named in a decision owned by a later slice.
3. Unresolved-dependency → **demote to warning**, tagged with the slice that will resolve it (`S-3에서 해소 예정`).
4. Every other error → **RED**.

If there are **no remaining slices** in the queue, no demotion applies — every error is RED.

---

### Check 2 — Convention

**Reuse the existing skill. Do not write new convention logic.**

`/convention-audit` reads the project's `.claude/conventions/`. **If the project has no convention docs**
(`context.json.conventions` is `null`, and neither `.claude/conventions/` nor `.claude/rules/` exists):

> `convention: SKIPPED (no convention docs)` — and **continue**. A project without convention docs is not
> a violation; it just cannot be certified on this axis. **This never makes the gate RED.**

Otherwise:

```
/convention-audit changes
```

It writes `./convention-audit-report.md` **at the repo root**. This skill is global and must not litter
other people's repos — after parsing, **move it into the artifact directory**:

```bash
[ -f convention-audit-report.md ] && \
  mv convention-audit-report.md ".claude/review/$SLUG/S-{n}-convention-audit.md"
```

Parse it for violations and severity before moving.

| Severity | Effect |
|---|---|
| Critical | RED |
| Warning | RED |
| Info | Not RED — goes to the 사용자 판단 목록 |

Copy the parsed violations into the gate JSON. Do not stage, do not delete the moved report.

---

### Check 3 — Design conformance (설계 정합 대조)

Compare the decisions this slice owns (`D-n`, from `current --topic`) and their acceptance criteria against the actual diff. Classify every gap into exactly one of three buckets:

| Bucket | Meaning |
|---|---|
| **미구현** | The decision says it, the code does not have it. |
| **설계와 다른 구현** | The code implements it differently from the decision. |
| **설계에 없던 추가** | The code has it, no decision covers it. |

Delegate this comparison to a subagent (it is a reading task, and it keeps the gate's own context small):

```
Agent(subagent_type="general-purpose", description="Design conformance check", prompt="""
TASK: Compare a design ledger's decisions against an actual git diff and classify every gap.

INPUT 1 — 담당 결정 (obsidian-log current --topic 출력):
{paste current --topic output}

INPUT 2 — 슬라이스 acceptance 기준 (작업 큐에서 해당 슬라이스):
{paste the S-n block}

INPUT 3 — diff:
{paste `git diff "$BASE"...HEAD -- <slice target globs>`}

EXPECTED OUTCOME: JSON only, no prose:
{"missing":[{"decision":"D-2","detail":"..."}],
 "divergent":[{"decision":"D-4","file":"...","symbol":"...","detail":"결정은 X, 코드는 Y"}],
 "unplanned":[{"file":"...","symbol":"...","detail":"..."}]}

MUST DO: cite file:line for every divergent/unplanned item. Be conservative — if a decision is
  arguably satisfied, do NOT report it as missing.
MUST NOT DO: edit any file. suggest code comments. judge whether a gap is acceptable —
  classification only; the user decides.
""")
```

**This check never turns the gate RED.** A subagent's reading of intent has false positives. Its output goes to `user_review_items` for the human.

---

### Check 4 — Out-of-scope change detection (설계 밖 변경)

Any changed file that matches **no slice's target-file glob** is flagged.

Whitelist — expected collateral, **not** flagged. The first two rows are universal; the rest are
**whatever the project's own new-feature checklist marks as mandatory bookkeeping**
(from `context.json.layers` / the convention docs — not a fixed list):

| Pattern | Why |
|---|---|
| Import-only edits (isort reordering, unused-import removal) | Mechanical |
| Pure formatting hunks (formatter output, whitespace-only) | Mechanical |
| The project's mandatory registration/re-export files | Required by its own convention — e.g. a DI container registration, a barrel/`__init__` export, a module index |
| Mechanical signature updates in existing tests | Follows from a signature change already in scope |

<!-- 예시: 레이어드 Python 모노레포에서는 apps/*/{app}/models/** re-export, container.py 등록,
     **/__init__.py 가 여기에 해당한다. 프로젝트마다 다르다 — Step 0의 체크리스트에서 읽어라. -->

Determine "import-only" / "pure formatting" from the diff hunks — a file whose every hunk is inside the import block or is whitespace-only qualifies.

**This check never turns the gate RED either.** It goes to `user_review_items`.

---

## Verdict

```
RED   ⟺  (Check 1 has any non-demoted error)  OR  (Check 2 has any Critical/Warning violation)
GREEN ⟺  otherwise
```

Checks 3 and 4 **never** produce RED. They produce 사용자 판단 항목.
A `SKIPPED` check is **never** RED either — it is an uncertified axis, and the summary must say so.

### On RED

Unless `--no-fix`:

1. Delegate the fix to a subagent:
   ```
   Agent(subagent_type="impl-executor", description="Fix gate violations", prompt="""
   TASK: Fix ONLY the diagnostics errors and convention violations listed below.

   {list of lsp errors + convention violations with file:line}

   REQUIRED TOOLS: Read, Edit, Bash
   MUST DO: fix each listed item at its cited location. Follow the project's own convention docs
     — {paste the paths from context.json.conventions; if null, say "이 프로젝트에는 컨벤션 문서가
     없다. 같은 디렉토리의 기존 코드 스타일을 그대로 따르라."}.
     Re-run the project's linter ({lint_cmd}) on the changed files when done.
   MUST NOT DO:
     - add ANY code comment or docstring explaining the fix (project policy: comments state why, never rationale dumps)
     - change behavior beyond what the violation requires
     - touch files not listed above
     - git add, git commit, git stash
     - "fix" a design-conformance or out-of-scope item — those are not yours
   """)
   ```
2. Re-run the whole gate.
3. **Retry cap: 2.** On the 3rd RED, stop. Report to the user with the full history of what was attempted and what is still failing. Do not keep fixing.

With `--no-fix`, skip all of the above — emit the RED verdict and the item list, and stop.

### Recording deviations in the ledger

If Check 3 or Check 4 found anything, append **one line per deviation to 진행 로그** — never to the 결정 로그. Writing pendings into the decision ledger pollutes it, and the subagent's judgment is not authoritative enough to be a decision.

```bash
python3 "$LOG" task-log --file "$TASK" --type 검수 \
  --summary "S-2 게이트: 설계 이탈 2건 (사용자 판단 대기)" \
  --log "[DEVIATION] 설계에 없던 추가: PointService._validate_balance() — D-2 미반영"
```

**The skill stops there.** Only after the user rules "설계 갱신" does the *lead* call `obsidian-log decide` to promote it into the ledger. The gate never calls `decide`.

---

## Output

### Artifact — `.claude/review/{branch-slug}/S-{n}-gate.json`

<!-- 예시: 레이어드 Python 모노레포의 경로. 스키마(키 이름·타입)만 계약이다 — 경로는 예시다. -->

```json
{
  "slice_id": "S-2",
  "slice_title": "포인트 차감 로직",
  "task_file": "0713-task1-포인트-차감-설계",
  "project": "<context.json에서 그대로 옮겨온 그래프 프로젝트명. 미인덱싱이면 null>",
  "base_branch": "origin/<Step 0a에서 해석한 base>",
  "head_sha": "a3f9c21",
  "generated_at": "2026-07-13T14:20:00+09:00",
  "verdict": "GREEN",
  "fix_attempts": 1,
  "changed_files": [
    {"path": "apps/app-api/app_api/services/point_service.py", "status": "M", "blob_sha": "e21ab99"}
  ],
  "checks": {
    "lsp": {
      "status": "PASS",
      "errors": [],
      "warnings": [
        {"file": "apps/app-api/app_api/services/point_service.py", "line": 41,
         "code": "F821", "message": "undefined name 'PointLedgerRepository'",
         "demoted": true, "resolved_by": "S-3"}
      ]
    },
    "convention": {"status": "PASS", "violations": []},
    "design_match": {
      "status": "DONE",
      "missing": [],
      "divergent": [],
      "unplanned": [
        {"decision": null, "file": "apps/app-api/app_api/services/point_service.py",
         "symbol": "PointService._validate_balance", "detail": "결정에 없는 private 헬퍼"}
      ]
    },
    "out_of_scope": {
      "status": "DONE",
      "flagged": [{"path": "cores/db/db/models/point.py", "reason": "어느 슬라이스 글롭에도 없음"}],
      "whitelisted": [{"path": "apps/app-api/app_api/container.py", "reason": "DI 등록"}]
    }
  },
  "user_review_items": [
    {"kind": "unplanned", "ref": null, "file": "apps/app-api/app_api/services/point_service.py",
     "symbol": "PointService._validate_balance", "detail": "설계에 없던 추가"},
    {"kind": "out_of_scope", "ref": null, "file": "cores/db/db/models/point.py",
     "symbol": null, "detail": "어느 슬라이스 글롭에도 속하지 않음"}
  ]
}
```

`checks.*.status` ∈ `PASS | FAIL | DONE | SKIPPED`. When `SKIPPED`, a `"reason"` field is mandatory.
`project` may be `null` (repo not in the code graph) — `/change-walkthrough` reads it and degrades.

### stdout summary (Korean, user-facing)

```
GATE: 🟢 GREEN  (S-2 · 포인트 차감 로직)  @ a3f9c21
  lsp        ✓ 0 errors (2 warnings: 미완결 의존 — S-3에서 해소 예정)
  convention ✓ 0 violations
  설계 정합   ⚠ 1건 — 설계에 없던 추가: PointService._validate_balance()
  설계 밖 변경 ⚠ 1건 — cores/db/db/models/point.py (어느 슬라이스 글롭에도 없음)
  → 사용자 판단 필요 2건 — /change-walkthrough S-2 로 검수 가이드 생성 후 ⚠ 챕터 확인
```

RED example:

```
GATE: 🔴 RED  (S-2 · 포인트 차감 로직)  @ a3f9c21  [수정 시도 2/2 — 중단]
  lsp        ✗ 1 error   .../services/point_service.py:58 F821 undefined name 'PointCursorDto'
  convention ✗ 2 violations (Critical 1, Warning 1)
                [Critical] 03-layered-architecture: Service에서 session 직접 사용 (point_service.py:73)
                [Warning]  02-naming: find_balance() → Service는 get_ 접두사 사용
  설계 정합   — (RED로 인해 미실행)
  설계 밖 변경 — (RED로 인해 미실행)
  → 자동 수정 상한(2회) 초과. 사람 개입 필요.
```

SKIPPED example (a repo with no convention docs — GREEN is still correct):

```
GATE: 🟢 GREEN  (S-2 · 포인트 차감 로직)  @ a3f9c21
  lsp        ✓ 0 errors
  convention ⊘ SKIPPED (컨벤션 문서 없음 — .claude/conventions/ 부재)
                ⚠️ 이 단계는 검증되지 않았다. 컨벤션은 직접 확인해야 한다.
  설계 정합   ✓ 이탈 없음
  설계 밖 변경 ✓ 없음
```

---

## Fallbacks

Every step degrades instead of aborting. The gate's job is to *reduce* review load, never to *block* review.

| Failure | Behavior |
|---|---|
| LSP tool absent | Fall back to the project's own linter (Check 1 detection table). |
| No linter detectable / it fails to run | `lsp: SKIPPED (린터 실행 불가 — 사유)`. Continue. |
| Project has no convention docs | `convention: SKIPPED (no convention docs)`. **Not RED.** Continue. |
| `/convention-audit` errors or writes no report | `convention: SKIPPED (사유)`. Continue. **Never** hand-roll a replacement check. |
| Base branch undeterminable | Ask the user. Never silently default to `main`. |
| `obsidian-log current` unavailable | Read the `## 현재 설계` / `## 작업 큐` sections directly (Grep for line range → Read with offset). If that fails too: checks 3, 4 → `SKIPPED`. |
| Slice's target-file glob missing from the 작업 큐 | Check 4 → `SKIPPED (글롭 미정의)`. Check 3 still runs. |
| Fix subagent fails twice | Stop. Report. Never a 3rd attempt. |

Whenever a check is `SKIPPED`, the stdout summary **must** carry the line:
`⚠️ 이 단계는 검증되지 않았다. 직접 확인이 필요하다.`

---

## Hard constraints

- **Never** add a code comment, or instruct a subagent to. Explanation lives in the sidecar guide, not the source.
- **Never** `git add`, `git commit`, `git stash`. Commit is the user's call, always.
- **Never** call `obsidian-log decide`. Deviations go to 진행 로그 only.
- **Never** rewrite convention checking — `/convention-audit` owns it.
- **Never** leave `convention-audit-report.md` at the repo root — move it into `.claude/review/{slug}/`.
- **Never** edit the project's `.gitignore`. Warn instead.
- **Never** hardcode a base branch, a linter, or a layer name. Resolve them (Step 0).
- Checks 3 and 4 are advisory. Only checks 1 and 2 can be RED. A `SKIPPED` check is never RED.

---

## Interface for `/impl-pipeline`

**In** — `/impl-pipeline` writes `.claude/review/{branch-slug}/context.json` before invoking.
<!-- 키 이름만 계약이다. 값은 전부 플레이스홀더 — 다른 레포의 값을 복사하면 엉뚱한 그래프를 조회하고
     엉뚱한 base와 diff한다. -->
```json
{"task_file": "0713-task1-포인트-차감-설계",
 "project": "<그래프 프로젝트명 (list_projects → root_path 매칭). 미인덱싱이면 null>",
 "base_branch": "origin/<해석된 base>",
 "conventions": ["<이 프로젝트에서 실제로 찾은 컨벤션 문서 경로들. 없으면 null>"],
 "layers": ["<이 프로젝트의 레이어 체인, 의존 순서대로>"],
 "lint_cmd": "<이 프로젝트가 실제로 쓰는 린트 명령>"}
```
`project`, `conventions`, `layers`, `lint_cmd` may be `null` — resolve or degrade, do not abort.
Then: `/review-gate S-2`

**Out** — writes `.claude/review/{branch-slug}/S-{n}-gate.json`:
- `.verdict` — `GREEN` → proceed to `/change-walkthrough S-2`. `RED` → halt, surface to user.
- `.task_file` — the ledger doc; `/change-walkthrough` reads it from here.
- `.project` — the code-graph project name or `null`; `/change-walkthrough` reads it from here.
- `.user_review_items` — non-empty means the walkthrough must render a ⚠ chapter per item.
