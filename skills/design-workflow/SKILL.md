---
name: design-workflow
user-invocable: true
description: '설계를 결정 원장(obsidian-log decide)에 확정하고 리뷰 크기 슬라이스로 구현하는 절차. 체크포인트 커밋 트리거, 리스크 티어, 리뷰 산출물 규칙 포함. 설계→구현 작업을 시작하거나 커밋 분할 기준이 필요할 때 참조.'
---

# Design Workflow

Applies in **every project**, whenever a task is design-then-implement above the complexity gate below.
How a design becomes reviewed code: decisions live in a CLI-enforced ledger, implementation ships in
review-sized slices, and each slice passes a machine gate before a human reads the diff.

The workflow supplies the **procedure**. The project supplies the **facts** — its layers, conventions,
base branch, and risk areas are read from the project at runtime, never assumed from another repo.

---

## Decision Ledger

Design decisions live in the Obsidian task document, in four sections:
`## 현재 설계` (confirmed decisions only) · `## 작업 큐` (implementation slices) ·
`## 결정 로그` (append-only) · `## 진행 로그`.

| Rule | Detail |
|---|---|
| **No direct body append** | Never append a decision by editing the document body. Confirming or changing a decision goes through `obsidian-log decide`. |
| **Decision log is append-only** | Never edit an existing decision's body in `## 결정 로그`. Only the status may flip (`ACTIVE` → `SUPERSEDED by D-n`). |
| **Supersede, don't overwrite** | A changed decision is recorded with `decide --supersedes D-n`, which atomically replaces the topic block. |
| **Current section = confirmed only** | No TBD, no "고민 중", no unchosen options. Unconfirmed discussion belongs in `## 진행 로그` or stays in the session. Promote to the current section only at the moment it is decided. |

Leftover superseded prose in the current section means someone bypassed the CLI. Run `/design-reconcile`,
which moves — never deletes — such content back to the log.

---

## Reading Protocol

- **Never read the design document in full.** Use `obsidian-log current --file <task> [--topic <slug>]`.
- **When delegating, inject the `current` output into the prompt** — never hand a subagent a document path.

This is what keeps token cost independent of document length. A subagent that reads the whole document
re-reads every superseded decision.

`/design-reconcile` is the one exception: inconsistency detection requires comparing the current section
against the decision log, so it reads the raw document for analysis only.

---

## Project Facts Are Resolved, Never Assumed

These four are project-specific. Read them from the project at the start of a run; never carry them over
from another repo:

| Fact | Where it comes from |
|---|---|
| **Layer model** (the chain a change travels) | `CLAUDE.md`, `.claude/rules/`, `.claude/conventions/` — else inferred from the codebase and **confirmed with the user** |
| **New-feature checklist** | same — else derived from an existing comparable feature |
| **Base branch** | the project's git rules — else the branch the current one is nearest to. Never default to `main` silently |
| **Risk areas** (tier A) | `.claude/review-policy.md` if present — else the principles below |

A project with no convention docs is not an error. Infer, confirm, and mark the convention check
`SKIPPED` rather than failing the gate on it.

---

## Slices

- **A slice is a review boundary, and therefore a commit candidate.** When a slice lands, propose a
  checkpoint commit (see below). The agent never commits on its own — it proposes; the user decides.
- Decompose by decision (`D-n`) first; split a large decision along **the project's own layer boundary**
  (resolved above), in dependency order. Order independent slices first.
- Every slice carries **target file globs** and **acceptance criteria** — the gate uses them to detect
  out-of-design changes.
- Decomposition is confirmed with the user before implementation starts.

---

## Checkpoint Commits

**Applies to all work, not just the pipeline.** An uncommitted diff that grows past review size is the
single biggest cause of unreviewable change. Do not let it accumulate silently and dump it on the user
at the end. Surface it *while it is still small*, so that later the user can review one purpose at a
time and skip what they already accepted.

### Propose a checkpoint when ANY of these fires

| Trigger | Why |
|---|---|
| A slice passes the gate GREEN | It is a complete, self-contained, verified unit. The natural boundary. |
| Uncommitted diff reaches **~10 files or ~400 changed lines** | Past this, a human stops reading line by line and starts skimming. |
| **Purpose is about to shift** — the next change has a different intent than what is already uncommitted (refactor → feature, fix → chore, a new `D-n`) | Mixing purposes in one diff is what makes review hard. Size is secondary; a 3-file diff carrying two unrelated intents is already worse than a 20-file diff carrying one. |
| A migration, a lock/transaction change, or anything else tier A is about to be touched | The user should review the pre-state before it becomes archaeology. |

### How to propose

Stop and tell the user — do not keep implementing through the trigger.

1. State **why** it fired (which trigger, with the actual numbers).
2. Show the **purpose-based phase split** you would stage, not just a file count.
3. Ask for approval. Never `git add` / `git commit` before the user answers.

The proposal is a suggestion, not a gate. If the user says keep going, keep going — but do not re-ask
on every subsequent file. Re-ask at the next distinct trigger.

### Still true

- **The agent never commits or stages autonomously.** Approval is required every time, in every project.
- Commit message and phase split follow **the project's own git rules**, not this file's.

---

## Complexity Gate

**Simple work bypasses the pipeline.** If a change touches ≤2 files, or maps to a single decision with no
cross-layer impact, implement it in one pass and let the user review the diff normally.

Pipeline overhead must not exceed its benefit.

---

## Risk Tiers (when the project has no `review-policy.md`)

Tier A (정독 필수) = ① money / permissions / personal data · ② schema migration ·
③ transaction, lock, or concurrency boundary changed · ④ high fan-in symbol (many callers, most untouched).
Everything else is tier B; machine-certified boilerplate is tier C and gets folded away.

---

## Review Artifacts

- Gate results and walkthroughs go to `.claude/review/{branch-slug}/`. If that path is not gitignored in
  this repo, warn the user — never edit their `.gitignore`.
- **The raw diff is the truth; artifacts are an aid.** If an artifact fails to generate, review proceeds
  on the raw diff — report the failure, don't block. Degraded ≠ failed.
- Artifacts carry a SHA stamp. If the stamp differs from `HEAD`, the artifact is stale — regenerate it.

---

## No Explanatory Comments

Do not put change rationale or design justification in code comments. The review guide carries the "why".

Implementation agents report issues, design gaps, and unstated assumptions **in their report text**,
never as a code comment.

(Inline comments explaining non-obvious business logic remain required where the project's own convention
says so. The ban is on comments that exist to explain a *change* to a reviewer.)

---

## Skills

| Skill | Role |
|---|---|
| `/impl-pipeline` | Orchestrates the whole flow: load project context → load design → slice → delegate → gate → walkthrough → user review. |
| `/review-gate` | Machine gate per slice. GREEN/RED plus a non-blocking human-judgment list. |
| `/change-walkthrough` | Per-slice review guide, ordered by request flow, with real call chains. |
| `/design-reconcile` | Ledger hygiene. Moves stale content out of the current section — never deletes. |
