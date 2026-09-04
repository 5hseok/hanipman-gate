---
name: impl-pipeline
description: |
  Orchestrate design → implementation → review as small, gated slices.
  Loads confirmed decisions from the decision ledger (never reads the whole doc),
  splits work into review-sized slices, delegates each slice, runs the machine gate,
  and hands the user a small diff plus a walkthrough for human review.
  Works in any repository — project-specific facts (layers, conventions, base branch) are
  resolved at runtime from the project itself, never assumed.
  Use when: implementing a confirmed design, 설계 구현, 슬라이스 구현, staged implementation,
  large feature implementation, 구현 파이프라인
argument-hint: "[task-file] [--slices N] [--resume S-n]"
allowed-tools:
  - Read
  - Edit
  - Grep
  - Glob
  - Bash
  - Agent
  - AskUserQuestion
  - Skill
user-invocable: true
---

# Implementation Pipeline

Turns a confirmed design into a sequence of **review-sized slices**. Each slice is implemented,
passed through a machine gate, and presented to the user with a small diff and a review guide.

The point is to fix two failure modes:

1. Agents re-read an ever-growing design doc and burn tokens on stale decisions.
2. A single bulk implementation produces a diff too large for a human to review.

This skill never commits or stages *autonomously*, and never adds explanatory comments to code.
It does **propose** checkpoint commits at slice boundaries — see Core Principles.

> MUST invoke `Skill(design-workflow)` before running this skill.
> If the current project also ships `.claude/rules/design-workflow.md`, read that too — a project rule
> narrows the global one, never contradicts it.

---

## Arguments

```
/impl-pipeline [task-file] [--slices N] [--resume S-n] [--explain]
```

| Argument | Meaning |
|---|---|
| `task-file` | Ledger task document (path or filename fragment). If omitted, find the most recent task doc and confirm with the user. |
| `--slices N` | Hint for target slice count. Advisory only — the decomposition axis wins. |
| `--resume S-n` | Skip to slice `S-n` (already-approved slices are not re-implemented). |
| `--explain` | Generate the interactive HTML understanding aid (`/explain-diff-html`) for every slice at Step 6 without asking. Off by default — otherwise it is offered per slice. |

---

## Core Principles

Read these before Step 0. They override convenience at every step.

- **The skill supplies the procedure; the project supplies the facts.** "Split a big decision along the
  layer boundary" is procedure and holds everywhere. *What the layers are* is a project fact — it is
  read from the project in Step 0, never assumed from another repo.
- **Slice boundary = review boundary = checkpoint commit candidate.** When a slice passes the gate,
  *propose* a commit (purpose-based phase split + why) and wait. Never `git add` / `git commit` before
  the user approves — but never let the uncommitted diff silently grow past review size either.
  Full trigger list in the `design-workflow` skill § Checkpoint Commits.
- **Never read the design document in full.** Use `ledger current` only. When delegating,
  inject the `current` output into the prompt — never pass a document path and let the subagent read it.
  This is what makes token cost independent of document length.
- **Complexity gate — Simple work bypasses the pipeline.** If the change touches ≤2 files or maps
  to a single decision with no cross-layer impact, do NOT run this pipeline. Fall back to the normal
  flow (implement in one pass → user reviews the diff). Pipeline overhead must not exceed its benefit.
  State the bypass out loud and proceed normally.
- **Sequential by default.** Slice N+1 starts only after slice N is approved. Pipelining is opt-in
  (see Step 6) and only for slices with non-overlapping file globs.
- **Artifacts are an aid, never the truth.** The raw diff is the truth. If the gate or the walkthrough
  fails to produce, review still proceeds — fall back to raw-diff review and report the failure.
- **No explanatory comments in code.** Issues, caveats, and design gaps found while implementing go in
  the subagent's **report text**, never in a code comment. The walkthrough carries the "why".

---

## Step 0 — Load project context

Nothing project-specific is hardcoded in this skill. Resolve it here, once, and write it to
`context.json` so the downstream skills reuse it instead of re-deriving it.

### 0a. Repo basics

```bash
ROOT=$(git rev-parse --show-toplevel)
BRANCH=$(git branch --show-current)
SLUG=$(echo "$BRANCH" | tr '/' '-')
mkdir -p "$ROOT/.claude/review/$SLUG"
```

### 0b. Base branch (never assume `dev` / `main`)

Resolution order — stop at the first that answers:

1. `.claude/review/{slug}/context.json` → `.base_branch` (a previous run already settled it).
2. The project's own git rules, if it documents one (`.claude/rules/git*.md`, `docs/**/git*.md`,
   `CONTRIBUTING.md`).
3. **Nearest merge-base wins.** Among the remote branches that exist, pick the one this branch is
   closest to — a feature branch cut from `dev` sits far fewer commits from `dev` than from `main`:
   ```bash
   for C in origin/dev origin/develop origin/main origin/master; do
     git rev-parse --verify -q "$C" >/dev/null && \
       echo "$(git rev-list --count "$C"..HEAD) $C"
   done | sort -n | head -1
   ```
4. Ambiguous or tied → **ask the user**. Do not guess.

Persist the answer as `base_branch` in `context.json`.

### 0c. Convention documents

Probe, in this order, and read what exists:

| Probe | Use |
|---|---|
| `CLAUDE.md` (repo root) | Architecture summary, language, commands |
| `.claude/CLAUDE.md` | Convention index |
| `.claude/rules/*.md` | Guardrails — architecture, conventions, exceptions |
| `.claude/conventions/*.md` | Detailed patterns per layer |
| `.claude/domains/*.md` | Domain knowledge |
| `.claude/review-policy.md` | Optional risk-tier policy (consumed by `/change-walkthrough`) |

From these, extract three things and state them out loud before slicing:

1. **The layer model** — the ordered chain a change travels through in *this* repo.
2. **The new-feature checklist** — every artifact a new feature must produce in *this* repo.
3. **The lint / diagnostics command** — how this repo checks a file (used by `/review-gate`).

<!-- 예시: 레이어드 Python 백엔드 모노레포. 형식만 참고할 것 — 규범이 아니다. -->
> e.g. a layered Python backend might resolve to
> `Model + migration → Repository → Service → DTO → Route → DI container`,
> a React frontend to `Route/page → hook → API client → type`,
> a data worker to `handler → transform → sink`.
> None of these is the default. Read the project.

### 0d. No convention documents?

Do not stall, and do not import another repo's conventions.

1. Infer the structure from the codebase — directory names, an existing feature's file set, imports.
2. State the inferred layer model and the inferred slicing axis to the user with `AskUserQuestion`,
   and get it confirmed **before** Step 2.
3. Record the confirmed answer in `context.json` (`layers`, `lint_cmd`) so later slices are consistent.
4. Mark `conventions: null` in `context.json` — this tells `/review-gate` to report the convention
   check as `SKIPPED (no convention docs)` rather than failing.

### 0e. Code graph project (optional, for `/change-walkthrough`)

Resolve the codebase-memory project name by **path**, never by guessing the name — indexed names are
not always path-derived — a repo may be indexed under a short service name while another sits under a
path-shaped name:

```
mcp__codebase-memory-mcp__list_projects()
```
→ pick the project whose `root_path` (or `git.canonical_root`) equals `git rev-parse --show-toplevel`.
In a git worktree, also compare against the main worktree root (`git worktree list | head -1`).

- Match → record its `name` as `project` in `context.json`.
- No match → record `"project": null`. The pipeline runs in **degraded mode**: the walkthrough drops
  the call-chain map. **Do not run `index_repository`** — it is expensive. Tell the user once:
  *"이 저장소는 코드 그래프에 인덱싱되어 있지 않다. 인덱싱하면 호출 체인 Map과 '영향받지만 미수정'
  경고를 받을 수 있다. 지금 인덱싱할까?"* — and only index if they say yes.

### 0f. Write `context.json`

`.claude/review/{branch-slug}/context.json` — the handoff record for `/review-gate` and
`/change-walkthrough`:

<!-- 키 이름만 계약이다. 아래 값은 전부 플레이스홀더 — Step 0에서 이 프로젝트를 해석한 결과로 채운다.
     특히 project / base_branch / lint_cmd / conventions / layers 를 다른 레포의 값으로 채우면
     엉뚱한 그래프를 조회하고 엉뚱한 base와 diff하며 없는 린터를 실행한다. -->

```json
{"task_file": "0713-task1-포인트-차감-설계",
 "project": "<Step 0e에서 list_projects → root_path 매칭으로 얻은 이름. 없으면 null>",
 "base_branch": "origin/<Step 0b에서 해석한 base>",
 "conventions": ["<Step 0c에서 실제로 찾은 컨벤션 문서 경로들. 없으면 null>"],
 "layers": ["<이 프로젝트의 레이어 체인, 의존 순서대로>"],
 "lint_cmd": "<이 프로젝트가 실제로 쓰는 린트 명령>"}
```

`project`, `conventions`, `layers`, `lint_cmd` may be `null` — a null is a fact, not a failure, and the
downstream skills degrade on it instead of aborting.

Also check that the artifact directory will not be committed:

```bash
git check-ignore -q .claude/review && echo IGNORED || echo TRACKED
```

`TRACKED` → warn the user once: *"`.claude/review/`가 gitignore되지 않는다. 산출물이 커밋에 섞일 수 있으니
.gitignore에 추가하거나, 커밋 전에 직접 제외하라."* Do not edit their `.gitignore` yourself.

### 0g. 리뷰 폴더 경로를 원장에 기록 (선택)

리뷰 산출물 폴더의 **절대경로**를 원장 문서에 한 번 적어둔다. 원장 백엔드가 대시보드를 렌더하는
경우 각 슬라이스의 워크스루를 거기서 이어준다 (실패해도 무시 — aid이지 gate 아님):

```bash
"$LEDGER" review-link --file <task> --dir "$ROOT/.claude/review/$SLUG"
```

---

## Step 1 — Load the design

Do **not** open the task document. Read only the ledger, via CLI:

```bash
LEDGER="${CLAUDE_PLUGIN_ROOT}/scripts/ledger/ledger"

"$LEDGER" current --file <task> --ids-only     # which decisions exist
"$LEDGER" current --file <task>                # body of the active decisions
```

- If `task-file` was not given, locate recent task docs and ask the user which one:
  ```bash
  "$LEDGER" ls
  ```
- If `current` returns nothing, the design is not confirmed yet. **Stop** and tell the user to confirm
  decisions first (`ledger decide`). Do not invent decisions from the document body.
- If the `current` subcommand is unavailable, report it and stop — do not fall back to reading the whole
  document, that defeats the purpose of the skill.

Optionally run `/design-reconcile <task>` first if the ledger looks inconsistent
(SUPERSEDED decisions still described in the current section, TBD language, etc.).

If the loaded design is dense or unfamiliar and the user wants to *understand it before slicing*, offer
`/explain-design-html <task>` — the design-phase understanding aid (문제 정의 → 설계 직관 → 결정·대안 비교
→ 영향 범위·리스크 → 설계 Playground → 퀴즈). Its Playground lets the user move the design's assumptions
and find where the decision flips ("언제 이 결정이 뒤집히는가") — the strongest signal available *before*
slicing that a decision is not yet safe to build on. Aid, never a gate: decomposition proceeds on the
ledger regardless.

---

## Step 2 — Decompose into slices

The lead does this — not a subagent. The lead holds the design context.

### Decomposition axis (in order)

1. **Per decision (`D-n`)** — the default. One decision → one slice.
2. **Per layer, when a single decision is too big** — split along **the layer model resolved in Step 0**,
   in dependency order (the layer nothing depends on goes first).
3. **Order independent slices first.** Later slices may depend on earlier ones; never the reverse.

### Completeness check

Cross-check the slices against **the new-feature checklist resolved in Step 0** so nothing is dropped.
Every checklist item that the design implies must land in exactly one slice.

If the project publishes no checklist, derive one from an existing comparable feature in the codebase
(list its files, in layer order) and show that derived checklist to the user with the decomposition.

### Each slice must carry

| Field | Purpose |
|---|---|
| **ID** | `S-1`, `S-2`, … |
| **Title** | one line |
| **Decisions** | the `D-n` IDs this slice implements |
| **Target file globs** | what this slice is allowed to touch — the gate uses this to detect out-of-design changes |
| **Acceptance** | observable criteria the gate checks |

### Record and confirm

Write the decomposition into the task document's `## 작업 큐` section with `Edit`.
The CLI does **not** manage this section — this skill owns it.

Block format:

<!-- 예시: 레이어드 Python 모노레포의 글롭. 경로 형태만 참고할 것 -->
```markdown
### S-1 · {title}
- status: 대기
- Decisions: D-1, D-3
- Files: `cores/db/db/models/foo.py`, `apps/app-api/app_api/repositories/foo_*.py`
- Acceptance:
  - ...
```

`- status:` 필드가 슬라이스 진척의 단일 진실원이다
(enum: 대기·구현중·게이트·검수대기·승인·커밋·보류). 최초 분해 시 전부 `대기`로 쓰고, 이후 전이는
본문을 직접 고치지 말고 **CLI로** 바꾼다 — `ledger slice-status --file <task> --slice N --status <상태>`.

Then present the plan to the user with `AskUserQuestion` — "splitting into N slices like this, OK?".
Options should include approving as-is, merging slices, or splitting further.
**Do not start implementing before the user confirms the decomposition.**

---

## Step 3 — Delegate one slice

Delegate to an implementation-capable subagent — `impl-executor` if available, else `general-purpose`. One slice per delegation.

The delegation prompt **must inline the decision text**, obtained with:

```bash
"$LEDGER" current --file <task> --topic <slug>
```

Required elements of the delegation prompt:

1. **TASK** — implement slice `S-n` only.
2. **DECISIONS** — the full `current --topic` output pasted inline. Never a document path.
3. **TARGET FILE GLOBS** — the slice's globs.
4. **ACCEPTANCE** — the slice's acceptance criteria.
5. **MUST NOT** —
   - Do not modify files outside the target globs. Exceptions, allowed without asking: mechanical
     collateral only — import cleanup, formatting, and the project's own boilerplate registrations
     (whatever Step 0's checklist marks as mandatory bookkeeping, e.g. a re-export or a DI registration).
   - Do not commit or stage anything.
   - Do not add explanatory comments describing *why* a change was made — that belongs in the report.
     (Inline comments explaining non-obvious business logic remain allowed per the project's own
     comment convention, in the project's own comment language.)
   - Do not refactor adjacent code that the slice does not require.
6. **CONVENTIONS** — the specific convention files from Step 0 that cover the layers this slice touches.
   Name the files; do not tell the agent to go hunting. If the project has none, say so explicitly and
   instead point at one existing comparable file to imitate.
7. **REPORT** — return: files changed, decisions covered, and any issue / design gap / unstated assumption
   discovered while implementing. These go in the report text, not in code.

---

## Step 4 — Gate

```
/review-gate S-{n} --task <task>
```

Always pass `--task` — the gate needs the ledger to check design conformance.

Produces `.claude/review/{branch-slug}/S-{n}-gate.json` plus a stdout summary, and a GREEN/RED verdict.

- **RED** → the gate auto-fixes and retries, up to **2 attempts**. Still RED after that → stop, report
  to the user, do not proceed to the next slice.
- **GREEN** → continue.
- Gate fails to run at all → report it and continue to Step 5 (fallback: raw-diff review).

The gate does **not** block on design-conformance findings. It classifies them and passes them up
as a user-judgment list (see Step 6).

---

## Step 5 — Review guide

```
/change-walkthrough S-{n}
```

Produces `.claude/review/{branch-slug}/S-{n}.md` — a code-flow map ordered by request flow
(entry point → … → data layer, per the project's layer model), a call-chain diagram, the `D-n` rationale
behind each change, gate badges, and a SHA stamp.

If the project is not in the code graph (`context.json.project` is `null`), the guide is generated in
degraded mode — everything except the call chain. If it fails to produce at all, say so and fall back to
raw-diff review.

---

## Step 6 — User review (the pipeline stops here)

Present, compactly:

1. **Changed files + stats** — `git diff --stat "$BASE"...HEAD`
2. **Gate summary** — badges from the gate result
3. **Review guide path** — `.claude/review/{branch-slug}/S-{n}.md`
   → tell the user: open the diff in the IDE and keep this guide alongside it
4. **Items needing your judgment** — from the gate, not blocking:
   - **Not implemented** — in the design, missing from the code
   - **Implemented differently** — in the design, but the code diverges
   - **Not in the design** — in the code, absent from the design
   - **Out-of-design changes** — edits outside the slice's target globs

### Deeper understanding aid (optional — `/explain-diff-html`)

The walkthrough (Step 5) is a fast skim map. When a slice is genuinely hard to internalize — dense
logic, an unfamiliar subsystem, or a change the user will have to *re-review later* — offer the
interactive HTML explainer instead of making the user re-derive it every time:

```
/explain-diff-html S-{n}
```

Pass the slice ID and the base branch so it scopes to `git diff <base>...HEAD -- <slice globs>`.
It produces a self-contained HTML file
(배경지식 → 핵심 직관 → 코드 워크스루 → 인터랙티브 Playground → 이해도 검증 퀴즈 5문항)
that turns the diff into a learn-once artifact — this is the skill's whole reason for existing: cut the
reviewer's cognitive debt so they stop being the bottleneck.

Its Playground makes the slice **operable**: toggle a hunk off and see what breaks, run the one failure
path, step a value through the request. For a tier A slice that is the difference between "I read it"
and "I could debug it at 3am" — so when a slice is tier A, prefer offering the explainer over the
walkthrough alone.

- `--explain` passed to the pipeline → generate it for every slice automatically, no prompt.
- Otherwise → offer it in the Step 6 presentation ("이 슬라이스는 복잡하다. HTML 이해 자료를 만들까?").
  Default to offering only when the diff is non-trivial; do not nag on boilerplate slices.

It is an **aid, never a gate** — same rule as the walkthrough. If it fails to generate, review still
proceeds on the raw diff and the walkthrough. Never block a slice on the explainer.

Then **wait**. Branch on the user's response:

| User says | Action |
|---|---|
| **Approve** | `ledger slice-status --file <task> --slice n --status 승인` (커밋까지 하면 `커밋`), then move to the next slice (Step 3). |
| **Change the design** ("let's go with B") | Lead updates the ledger: `ledger decide --supersedes D-n`. Then re-implement this slice. |
| **Fix the implementation** ("fix this bit") | Delegate the fix → back to Step 4. |
| **Accept the deviation** ("that addition was intended") | Lead records it with `ledger decide` — the design catches up to the code. Then approve. |
| **Put it on hold** ("이건 나중에") | `ledger slice-status --file <task> --slice n --status 보류` — parked, and the dashboard shows it separately from remaining work. |
| **Need to understand it first** ("이해가 안 된다") | Generate `/explain-diff-html S-{n}`, then return here. |

**슬라이스 상태 전이 (`status:` 필드가 단일 진실원)**:
Step 3 위임 시작 → `구현중`, Step 4 GREEN → `검수대기`, Step 6 승인 → `승인`(커밋 시 `커밋`), 보류 → `보류`.
전이는 전부 `ledger slice-status`로 — 본문 직접 편집 금지.
원장이 없는 프로젝트면 이 단계는 조용히 건너뛴다(SKIPPED).

### Pipelining (opt-in)

Default is sequential. If the user asks for it, slice N+1 may be implemented while slice N is being
reviewed — **only** when the two slices' target globs do not overlap. Never pipeline slices that
touch the same files.

---

## Step 7 — Completion

After every slice is approved:

- **Do not commit autonomously.** Whatever is still uncommitted goes back through the existing flow:
  stage in purpose-based phases → `/commit-msg` → user approves → user commits.
  (Slices the user already checkpoint-committed are done — do not restage them.)
- Update the task document status:
  ```bash
  "$LEDGER" task-status --file <task> --status 구현완료 --log "S-1~S-n 구현·검수 완료"
  ```
- If implementation revealed design gaps that the user accepted, make sure they were promoted with
  `decide` (Step 6) — the ledger must reflect the shipped code.

---

## Failure Handling

| Failure | Behavior |
|---|---|
| No convention docs in the project | Infer + confirm with the user (Step 0d). Never import another repo's layers. |
| Base branch undeterminable | Ask. Never default to `main` silently. |
| Project not in the code graph | Degraded mode — no call chain. Offer indexing; never index unasked. |
| `current` CLI unavailable / returns nothing | Stop. Do not read the doc in full. Report. |
| Decomposition rejected by user | Re-decompose with their feedback. Do not implement. |
| Gate RED after 2 auto-fix attempts | Stop the pipeline. Report the failing checks. |
| Gate or walkthrough fails to generate | Continue to review with the raw diff. Report the failure. |
| Subagent touched files outside its globs | Surface it in the Step 6 judgment list. Do not silently accept. |
