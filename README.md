# 한입만 게이트 (hanipman-gate)

> **한입만.** 통째로 쏟지 말고.

AI가 코드를 쓰기 시작하면서 병목이 옮겨갔다. 생성은 빨라졌는데 이해는 그대로다. 60개 파일짜리 diff가 한 번에 떨어지면 사람은 3분 만에 정독을 포기하고 훑기로 전환한다.

이 레포는 그 병목을 규율이 아니라 구조로 옮긴 도구 모음이다. Claude Code 플러그인 마켓플레이스로 배포한다.

---

## 무엇을 하나

```
설계 논의
   │
   ├─ ledger decide ────────▶ 결정 원장
   │                          ├ 현재 설계  (확정된 것만 · 원자적 교체)
   │                          └ 결정 로그  (append-only · SUPERSEDED 보존)
   │                                │
   │                    current --topic 만 읽는다 (문서 전체를 읽지 않는다)
   │                                ▼
   └─ /impl-pipeline ──▶ 슬라이스 분해 ──▶ 위임 ──▶ /review-gate ──▶ /change-walkthrough
                                                        │                    │
                                                     GREEN만                 ▼
                                                     통과            사람이 읽는 건
                                                                    "판단 필요 N건"
```

세 가지 규칙이 전부다.

**설계 문서를 통째로 읽지 않는다.** 위임할 때 문서 경로를 넘기지 않고 확정된 결정 본문만 프롬프트에 넣는다. 토큰 비용이 문서 길이와 무관해진다.

**게이트를 통과한 diff만 사람에게 간다.** 진단·컨벤션·설계 정합·범위 이탈을 먼저 돌린다. 검수 시간이 diff 총량이 아니라 판단 필요 항목 수를 따라간다.

**상태는 손으로 쓸 수 없다.** 여러 레포에 걸친 작업의 완료 여부는 읽기 전용 프로브가 원격에 직접 물어서 잰다. 모르면 `미검증`으로 남는다. 거짓 `완료`보다 낫다.

---

## 설치

```bash
/plugin marketplace add 5hseok/hanipman-gate
/plugin install design-pipeline@hanipman-gate
```

플러그인은 셋으로 나뉜다. 필요한 것만 깔면 된다.

| 플러그인 | 무엇 | 언제 |
|---|---|---|
| `design-pipeline` | 결정 원장 → 슬라이스 → 게이트 → 리뷰 가이드 | 설계하고 구현할 때. 이게 본체다 |
| `track-ledger` | 여러 레포·세션의 순서를 프로브로 실측 | 세션을 여럿 돌리거나 배포 순서가 얽힐 때 |
| `explain-aids` | 설계·diff 이해용 인터랙티브 HTML | 큰 변경을 남에게(또는 미래의 나에게) 설명해야 할 때 |
| `session-board` | 세션 현황을 Artifact 한 장으로 | 트랙 문서까지는 과하고 "지금 뭐가 어디까지 갔나" 만 보고 싶을 때 |

---

## 스킬

### design-pipeline

| 스킬 | 역할 |
|---|---|
| `design-workflow` | 정책 원본. 원장 규칙·리딩 프로토콜·체크포인트 커밋·리스크 티어 |
| `impl-pipeline` | 오케스트레이터. 설계를 슬라이스로 잘라 게이트까지 돌린다 |
| `review-gate` | 자동 게이트. 진단·컨벤션·설계 정합·범위 이탈 |
| `change-walkthrough` | 리뷰 가이드. 변경을 요청 흐름 순서로 재배열한다 |
| `design-reconcile` | 원장 위생. 낡은 내용을 옮긴다 (지우지 않는다) |
| `convention-audit` | 컨벤션 검사. `review-gate`가 실제로 부른다 |

### track-ledger

| 스킬 | 역할 |
|---|---|
| `track-ledger` | 트랙 생성·운영 |
| `track-ledger-policy` | 불변 규칙과 세션 간 통신 기준 |

### session-board

| 스킬 | 역할 |
|---|---|
| `session-board` | Claude 세션 레지스트리를 읽어 보드를 Artifact 로 게시하고 같은 URL 로 갱신 |

CLI 도 서버도 없다. `track-ledger` 가 무겁게 느껴지면 이쪽부터 써도 된다.

### explain-aids

| 스킬 | 역할 |
|---|---|
| `explain-design-html` | 구현 전. 왜 이 방향인지, 뭘 버렸는지 |
| `explain-diff-html` | 구현 후. 뭐가 어떻게 바뀌었는지 |
| `explain-skills-workflow` | 위 둘을 언제 제안할지의 기준 |

---

## 설계 판단 몇 가지

**게이트는 비대칭이다.** 컨벤션 위반은 RED로 막지만 "설계와 다른가"는 막지 않고 판단 목록으로 넘긴다. 컨벤션은 규칙 기반이라 확실한데 설계 정합은 LLM의 판단이라 오탐이 섞이고, 오탐으로 정당한 구현이 계속 막히면 사람이 게이트를 꺼버린다.

**부분 실패를 전체 실패로 만들지 않는다.** raw diff가 진실이고 산출물은 보조다. 산출물 생성이 실패하면 실패를 보고하되 리뷰는 막지 않는다.

**슬라이스는 커밋이 아니다.** 리뷰 경계와 커밋 경계를 분리했다. 에이전트는 커밋을 제안할 수 있지만 실행하지 않는다.

**설명 주석을 달지 않는다.** 변경 이유를 코드 주석에 남기지 않는다. 그건 리뷰 가이드가 나른다. 주석은 코드를 더럽히지만 가이드는 코드 밖에 있다가 리뷰가 끝나면 버려진다.

---

## 요구사항

- Claude Code
- Python 3 (표준 라이브러리만. 서드파티 의존성 없음)
- git

선택이지만 있으면 크게 달라진다.

- **codebase-memory MCP** — `change-walkthrough`의 핵심 산출물인 "영향받지만 미수정"(바뀐 코드를 호출하는데 자기는 안 바뀐 코드)은 실제 콜체인을 뽑아야 나온다. 없으면 이 항목이 통째로 빠진다.
- `gh` · `aws` · `terraform` — 트랙 프로브가 쓴다. 없으면 해당 스텝이 `미검증`으로 남는다.

---

## 원장은 어디에 두든 된다

스킬은 원장이 어디 있는지 모른다. `ledger` 하나만 부른다.

```bash
ledger current --file <task> --topic <slug>   # 확정된 결정만. 문서 전체를 읽지 않는다
ledger decide  --file <task> --topic <slug> --title ... --supersedes D-3
ledger doctor                                  # 어느 백엔드가 왜 골라졌는지
```

기본은 레포 안 markdown(`.claude/design/`)이다. Obsidian 볼트를 쓰고 있으면
`~/.claude/ledger.json` 에 경로만 적으면 기존 문서를 그대로 쓴다.

```json
{ "backend": "obsidian", "root": "/path/to/vault" }
```

자세한 건 [docs/adapter-contract.md](docs/adapter-contract.md).

## 아직인 것

작업 중인 레포다. 지금은 다음이 미완이다.

- 예시 문서 (`examples/`)
- LICENSE (public 전환 시 결정)

---

## 이름

한입만. 남이 먹는 걸 통째로 달라고는 안 하고 한입만 달라고 하는 그 말이다. 이 도구가 에이전트한테 하는 말이기도 하다.

`gate`는 두 시스템의 공통어다. 파이프라인 쪽엔 `review-gate`가 있고, 트랙 원장 쪽 스텝은 필드 자체가 `gate: merge` / `gate: apply` / `gate: deploy`다.
