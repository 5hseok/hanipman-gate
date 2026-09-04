---
name: track-ledger
description: |
  여러 세션·여러 레포에 걸친 작업의 순서와 상태를 사람의 기억이 아니라 실측으로 들고 있게 하는 트랙 원장을
  만들고 운영한다. 계획(순서·의존·게이트·왜)은 사람이 쓰고, 완료 여부는 읽기 전용 프로브가 재고,
  보드·세션 그래프·세션 브리프는 전부 그 둘에서 렌더된다. 상태는 손으로 쓸 수 없다.
  Use when: 트랙 만들기, 작업 순서 정리, 여러 레포 작업 순서, 여러 세션 조율, 배포 순서,
  지금 뭐부터 해야 하지, 어느 세션이 뭘 하고 있지, 트랙 원장, track ledger, 작업 큐 만들기,
  릴리즈 순서 관리, 게이트 순서, 팀에 뭘 요청해야 하나, 세션끼리 작업이 어긋난다
---

# 트랙 원장 만들기·운영

트리거·불변 규칙·대화 규율은 `track-ledger-policy` 스킬에 있다 — 이 스킬을 시작할 때 Skill(track-ledger-policy) 로 먼저 읽어라.
이 스킬은 **절차와 스키마**를 담는다.

## Step 0 — 이미 있는 트랙부터 찾는다 (건너뛰지 말 것)

```bash
track list --repo <현재 레포>
```
있으면 **새로 만들지 않는다.** `track join --steps <담당>` 으로 붙고 브리프를 받는다.
트랙이 갈라지면 이 시스템은 손으로 유지하는 보드 두 장이 된다.

## Step 1 — 스텝을 뽑는다

사용자와 대화하거나 기존 설계 원장(`ledger current --file <task>`)에서 확정 결정을 읽어 스텝을 만든다.
**설계 문서 전체를 읽지 마라** — 폐기된 결정이 대부분이다.

스텝 하나의 크기: **한 번의 게이트 통과**. 머지 하나, apply 하나, 릴리즈 하나.
"구현하고 배포한다" 는 두 스텝이다 — 게이트가 다르면 선행 조건도 다르기 때문이다.

## Step 2 — 문서를 만든다

```bash
track init --title "<트랙 제목>"      # tracks/YYYY-MM/MMDD-track-<slug>.md 생성, 경로 반환
```
그 파일을 열어 스텝을 채운다.

```markdown
### T-3 · dev·stage apply
- why:   머지만으로는 파이프라인이 바뀌지 않는다. apply 해야 트리거 경로에 들어간다
- where: repo=infra-terraform branch=main dir=/절대/경로
- gate:  merge | apply | deploy | release | notice
- env:   dev | stage | prod | dev·stage·prod | 전사
- needs: T-2
- owner: <세션 이름>
- team:  apply 창 동안 배포가 겹치지 않게. 승인 대기 토큰이 있으면 먼저 비운다
- note:  게이트 — trigger_paths 한 줄씩만 바뀌어 in-place update 다. replace 도 destroy 도 없다
- probe: `<읽기 전용 명령>` :: <연산자> <기대값>
```

| 필드 | 무엇 | 빠뜨리면 |
|---|---|---|
| `why` | 무엇을·왜 한 줄 | 나중에 왜 하는지 아무도 모른다 |
| `note` | **게이트 근거 — 왜 이 순서인가** | 의존만 남고 이해가 사라진다. 보드가 읽히던 이유가 이것이다 |
| `where` | `repo=` `branch=` `worktree=` `dir=`(프로브 실행 위치) | 어디서 하는지 매번 되묻는다 |
| `team` | 공지·중단·권한 요청 한 줄 | `track impact` 가 비고, 남의 배포가 깨진 뒤에 안다 |
| `probe` | 완료 판정 | 그 스텝은 영원히 **미검증** 이다 |
| `env` | UI 의 환경 레인 위치 | 전역 레인으로 밀린다 |

`status` 필드는 **없다.** 넣지 마라 — 프로브가 유일한 작성자다.

## Step 3 — 프로브를 쓴다

**연산자**: `==` · `!=` · `contains` · `!contains` · `!empty` · `empty` · `newer-than <ISO>` · `>=` · `exit0`

**원격을 직접 묻는다.** 로컬 `origin/*` 는 다른 세션의 fetch·gc 로 움직여 같은 프로브가 몇 분 사이 다른 답을 낸다.

```bash
# 좋다 — GitHub 를 직접
`gh pr view 51 --repo owner/repo --json state --jq .state` :: == MERGED
`gh api repos/o/r/compare/stage...feature/x --jq .ahead_by` :: == 0
`gh api repos/o/r/contents/llm.pin?ref=dev --jq .sha` :: !empty
`gh api repos/o/r/contents/path/f.yml?ref=dev -H "Accept: application/vnd.github.raw" | grep -c 패턴 | tr -d ' '` :: != 0
# AWS 실제 상태
`aws codepipeline get-pipeline --name X --query pipeline.triggers --output json` :: contains llm.pin
`aws lambda get-function-configuration --function-name X --query LastModified --output text` :: newer-than 2026-08-27T05:00:00Z
`aws iam get-role-policy --role-name R --policy-name P --query PolicyDocument --output json | grep -c 키 | tr -d ' '` :: != 0
```

**쓰기 동사는 실행 자체가 거부된다.** 쉘도 안 쓴다 — 파이프는 프로세스를 직접 잇는다.
`` ` `` `$( )` `>` `&&` `;` 는 문자열에 있기만 해도 거부. `terraform plan` 은 `probe-kind: plan` 을 명시한 스텝만
(state lock 을 잡아 남의 apply 를 막으므로 `-lock=false` 가 강제 주입된다).

**쓴 다음 반드시:**
```bash
track probes --explain    # 무엇이 돌지 전수 확인 (실행 안 함)
track probes --audit      # 거짓 양성이 나기 쉬운 형태를 잡는다
```
`--audit` 이 "기대값이 명령 인자를 되비친다" 를 띄우면 그 프로브는 **항상 참**이다. 반드시 고쳐라.
자문할 것 하나: **"이게 참인데 실제로는 미완일 수 있나?"**

프로브를 못 쓰겠으면 **비워 둔다.** 추측으로 채우지 마라 — 미검증이 조용히 틀린 완료보다 낫다.

## Step 4 — 액터를 등록한다

```bash
track join --session <세션 이름> --steps T-4,T-5 --ledger <그 세션의 task 문서 접두사>
```
등록해야 세션 그래프에 담당이 붙고, 결정 레인이 그 세션 원장을 읽고, `track brief` 가 동작한다.
세션 이름은 `~/.claude/sessions/*.json` 의 `name` 이다.

## Step 5 — 돌린다

```bash
track verify            # 실측 (상태의 유일한 작성자)
track next              # 지금 할 것 한 줄
track impact            # 팀에 요청할 것만
track serve             # 실시간 UI — 환경 레인 × 게이트 체인 · 결정 레인 · 세션 그래프
```

각 세션에는 **문서 경로가 아니라 브리프를 넘긴다**:
```bash
track brief --session <이름>     # 담당 스텝 + 의존 결정 + 보고 기준
```

세션이 뭔가 했다고 보고하면 상태를 고치지 말고:
```bash
track signal --step T-4 --kind merged --evidence <해시>
track verify --step T-4          # 실제로 그런지는 프로브가 판정한다
```

## 자주 하는 실수

| 실수 | 결과 |
|---|---|
| 스텝에 `status` 를 적는다 | 손 유지 보드로 회귀. 시스템의 이유가 사라진다 |
| `note` 를 비운다 | 순서는 남지만 왜인지가 사라져 보드가 안 읽힌다 |
| 프로브를 추측으로 채운다 | 조용히 틀린 완료. 미검증보다 나쁘다 |
| 기대값이 명령 인자에 있는 `contains` | 항상 참. `--audit` 이 잡는다 |
| 로컬 `git` 로 원격 상태를 잰다 | 다른 세션의 fetch 로 판정이 뒤집힌다 |
| 트랙을 새로 판다 | Step 0 을 건너뛴 것. 보드가 두 장이 된다 |

## 파일 위치

```
<ledger-root>/tracks/YYYY-MM/MMDD-track-<slug>.md   트랙 문서 (사람이 쓴다)
~/.claude/track-cache/<track-id>.json               실측 캐시 (프로브가 쓴다)
<plugin>/scripts/track/                             CLI·서버·UI
track                                               진입점 (PATH 에 두거나 플러그인 경로로 호출)

<ledger-root> 는 원장 백엔드가 정한다. 기본은 레포 안 `.claude/`, Obsidian 백엔드면 볼트 루트.
```
