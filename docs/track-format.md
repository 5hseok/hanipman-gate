# 트랙 문서 규격

여러 레포·여러 세션에 걸친 작업의 순서를 들고 있는 문서다. 계획은 사람이 쓰고,
완료 여부는 읽기 전용 프로브가 원격에 물어서 잰다.

**스텝에 `status` 필드는 없다.** 상태는 매번 계산된다.

## 섹션

```markdown
## 메타
- **id**: <track-id>
- **생성일**: YYYY-MM-DD
- **상태**: 진행중

## 액터
| 세션 | 담당 | 원장 |
|---|---|---|
| <세션 이름> | T-4, T-5 | <task 문서 접두사> |

## 스텝
### T-4 · 백엔드 dev 머지
- why: 인프라 apply 가 이 스키마를 전제로 한다
- where: repo=<레포> branch=dev dir=<작업 경로>
- gate: merge
- env: dev
- needs: T-2, T-3
- owner: <세션 이름>
- probe: `<읽기 전용 명령>` :: <연산자> :: <기댓값>

## 신호
- <ISO 시각> · <세션> · T-4 · merged · <근거>
```

## 필드

| 필드 | 뜻 |
|---|---|
| `why` | 왜 이 순서인가. 사람만 쓸 수 있는 것 |
| `where` | `repo=` `branch=` `dir=` — 프로브가 도는 자리 |
| `gate` | `merge` · `apply` · `deploy` · `release` · `notice` |
| `env` | `dev` · `stage` · `prod` — 보드의 레인이 된다 |
| `needs` | 선행 스텝. 못 채우면 `대기` |
| `owner` | 담당 세션 |
| `probe` | 완료 여부를 재는 읽기 전용 명령 |
| `note` | 팀에 알릴 것. 여러 줄 가능 |
| `rests-on` | `<원장>:D-n` — 그 결정이 SUPERSEDED 되면 스텝이 `낡음` |

인식하지 못하는 필드는 조용히 버리지 않고 린트로 알린다.

## 상태

계산으로만 나온다.

| 상태 | 조건 |
|---|---|
| `완료` | 프로브가 참 |
| `미검증` | 프로브가 없거나 판정 못 함 |
| `대기` | `needs` 가 안 채워짐 |
| `지금` | 막힌 것 없는 첫 스텝 |
| `가능` | 나머지 |

모르면 `미검증` 으로 남는다. 거짓 `완료` 보다 낫다.

## 프로브

`` `<명령>` :: <연산자> :: <기댓값> `` 형태다.
연산자: `==` `!=` `contains` `!contains` `empty` `!empty` `newer-than` `>=` `exit0`

읽기 전용이 강제된다. 셸 메타문자는 전면 거부하고, 파이프 각 단계를 화이트리스트로 검사한다.
`aws` 는 읽기 동사만, `gh api` 는 GET 만, `terraform plan` 은 `probe-kind: plan` 을 선언한
스텝에서만 그것도 `-lock=false` 를 강제 주입해서 돈다.

## 위치

`<root>/tracks/YYYY-MM/MMDD-track-<slug>.md`. `<root>` 는 원장 어댑터와 같은 설정을 따른다
([adapter-contract.md](adapter-contract.md) 참조).
