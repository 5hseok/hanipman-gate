---
name: design-reconcile
description: |
  Hygiene check on an Obsidian design document's decision ledger.
  Detects status inconsistencies, leftover superseded decisions, and unconfirmed
  discussion that leaked into the current-design section. Proposes moves (never deletions)
  and requires user approval. Delegated to a Haiku subagent.
  Works for any project — it reasons about the ledger, not about the code.
  Use when: 설계 문서 정리, 설계 확정 전 점검, decision ledger 정합성, design doc hygiene,
  결정 로그 점검, 설계 위생 점검
argument-hint: "[task-file]"
allowed-tools:
  - Read
  - Bash
  - Agent
  - AskUserQuestion
  - Skill
user-invocable: true
---

# Design Reconcile

Checks that a task document's decision ledger is internally consistent, and that the
**current design section contains only confirmed decisions**.

Run it at two moments:

- **Before finalizing a design** — right before `/impl-pipeline` starts.
- **After a deviation** — when implementation revealed something the design did not anticipate
  and a decision was superseded.

Document editing is delegated to a **Haiku subagent** — it is a mechanical cut-and-paste job, and a
cheap model keeps the lead's context free.

> MUST invoke `Skill(design-workflow)` before running this skill.

This skill is project-agnostic by construction: it reasons about the ledger document, never about the
codebase. Nothing here needs to know what the project's layers or conventions are.

---

## Arguments

```
/design-reconcile [task-file]
```

If `task-file` is omitted, list recent task documents and ask the user which one.

---

## Ledger Shape

The task document has four sections. This skill reasons about the first three.

| Section | Content | Managed by |
|---|---|---|
| `## 현재 설계` | Confirmed decisions only, inside `<!-- CURRENT:START/END -->`. Blocks: `### D-n · {topic-slug} · {title}` | `obsidian-log decide` (atomic replace) |
| `## 작업 큐` | Implementation slices | `/impl-pipeline` |
| `## 결정 로그` | Append-only. Each entry: `ACTIVE` or `SUPERSEDED by D-n` | `obsidian-log decide` |
| `## 진행 로그` | Progress notes | `obsidian-log task-log` / `task-status` |

---

## Checks

### 1. Status consistency

- Every `SUPERSEDED by D-n` in the decision log points at a `D-n` that actually exists.
- Every decision marked `ACTIVE` in the log is present in the current-design section.
- Every decision present in the current-design section has a corresponding `ACTIVE` log entry.

Any mismatch in either direction is a finding.

### 2. Leftover superseded content

The current-design section still *describes*, in prose, a decision that has been superseded —
typically because a topic-block replacement did not catch narrative text written outside the block.

### 3. Unconfirmed discussion

The current-design section contains **non-final language**: "~할지 고민", "TBD", "아마도", "maybe",
"probably", "고민 중", "검토 필요", open questions, or two options presented without a chosen one.

This is the actual origin of document bloat. The current section must contain confirmed decisions only.

---

## Handling Rules (non-negotiable)

These are guards. State them explicitly in the delegation prompt.

- **Never delete.** A finding is resolved by **moving** content, never by removing it.
  - Leftover superseded prose → **move to the decision log** (as part of the superseded entry).
  - Unconfirmed discussion → **move to 진행 로그**.
- **Never rewrite an existing decision-log body.** Log entries are append-only; only the
  `ACTIVE` / `SUPERSEDED by D-n` status may flip.
- **Present a diff and get approval before any edit.** Every change, without exception.
- **Report CLI bypass.** `obsidian-log decide` performs an atomic topic-block replacement.
  Therefore leftover superseded content in the current section is evidence that **someone edited the
  document body directly instead of using the CLI**. Report that fact to the user explicitly —
  it is a process problem, not just a text problem.

---

## Steps

### Step 1 — Read the ledger

```bash
VAULT="${OBSIDIAN_VAULT:?원장 위치를 지정하십시오 — 아직 어댑터 이전 형태입니다}"
LOG="$VAULT/.claude/scripts/obsidian-log.py"

python3 "$LOG" current --file <task> --ids-only    # active decision IDs
python3 "$LOG" current --file <task>               # active decision bodies
```

Unlike `/impl-pipeline`, this skill **may** read the raw document — inconsistency detection requires
comparing the current section against the decision log, and the CLI only exposes the current view.
Read it once, for analysis only.

### Step 2 — Delegate the analysis (Haiku)

Delegate to a Haiku subagent with:

1. **TASK** — run checks 1–3 above against the task document.
2. **CONTEXT** — the document path, plus the `current --ids-only` and `current` output.
3. **OUTPUT** — a findings list. For each: check number, exact location (section + heading + line),
   the offending text, and the proposed **destination** (decision log or 진행 로그).
4. **MUST NOT** — do not edit the document; do not delete anything; do not reword decisions.
   Analysis only in this step.

### Step 3 — Present findings

For each finding show: what, where (section + `D-n` + line), and the proposed move.
If any finding is of type 2 (leftover superseded content), add the CLI-bypass warning.

If there are no findings: `설계 문서 정합성 이상 없음.` and stop.

### Step 4 — Get approval

Use `AskUserQuestion`. The user may approve all, approve a subset, or reject.
Reject → stop, change nothing.

### Step 5 — Apply (Haiku)

Delegate the approved moves to a Haiku subagent.

- Status flips (`ACTIVE` → `SUPERSEDED by D-n`) and decision replacement go through
  `obsidian-log decide` where possible — never by hand-editing the current section.
- Content moves that the CLI does not cover are applied with `Edit`, moving text to its
  destination section. Cut-and-paste, not delete.
- **MUST NOT**: delete content, reword decisions, touch `## 작업 큐`, or edit anything the user
  did not approve.

### Step 6 — Report

List what moved and where. If a CLI bypass was detected, restate it — future edits must go through
`obsidian-log decide`.
