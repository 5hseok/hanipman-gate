# 원장 어댑터

스킬은 원장이 어디 있는지 모른다. `ledger` 하나만 부른다.

원장이 떠받치는 규칙이 하나 있다. **설계 문서를 통째로 읽지 않는다.** 위임할 때 문서 경로를
넘기지 않고 확정된 결정 본문만 프롬프트에 넣는다. 그래야 토큰 비용이 문서 길이와 무관해진다.
어댑터는 그 규칙을 지키면서 저장 위치만 갈아끼울 수 있게 한다.

## 명령

```
ledger current      --file <ref> [--topic <slug>] [--with-queue] [--ids-only]
ledger decide       --file <ref> --topic <slug> --title <제목>
                    (--body <본문> | --body-file <경로> | stdin)
                    [--supersedes D-1,D-2] [--mirrors <ref>:<D-n>] [--init]
ledger slice-status --file <ref> --slice <n> --status <상태>
ledger task-log     --file <ref> [--summary <한 줄>] [--type <태그>] [--status <상태>] [--log <내용>]
ledger task-status  --file <ref> --status <상태> [--log <내용>]
ledger review-link  --file <ref> --dir <경로>
ledger ls           [--month YYYY-MM]
ledger resolve      <조각>
ledger raw-path     --file <ref>
ledger doctor
```

`--file` 은 절대경로도 되고 파일명 조각도 된다.

`ls` · `resolve` · `raw-path` 는 어댑터에서 새로 만든 것이다. 스킬이 하던
`ls $VAULT/tasks/$(date +%Y-%m)/` 같은 raw 셸 호출을 대신한다. `raw-path` 는 원문을
직접 읽어야 하는 한 곳(`design-reconcile` 의 정합성 검사)만 쓴다.

## 백엔드 선택

위에서부터 먼저 걸리는 것을 쓴다.

| 순서 | 근거 |
|---|---|
| ① | `$CLAUDE_LEDGER_BACKEND` (+ `$CLAUDE_LEDGER_ROOT`) |
| ② | `<repo>/.claude/ledger.json` |
| ③ | `~/.claude/ledger.json` |
| ④ | `$OBSIDIAN_VAULT` 아래에 `obsidian-log.py` 가 있으면 `obsidian` |
| ⑤ | 없으면 `markdown` — `<repo>/.claude/design` |

설정 파일은 이렇게 생겼다.

```json
{ "backend": "obsidian", "root": "/path/to/vault", "lang": "ko" }
```

**개인 경로는 설정 파일에만 둔다.** 이 레포에는 절대경로가 한 줄도 들어가지 않는다.
`ledger doctor` 가 어느 백엔드를 왜 골랐는지 알려준다.

## markdown 백엔드

원장을 레포 안에 둔다. `<root>/YYYY-MM/MMDD-<slug>.md`, root 기본값은 `<repo>/.claude/design`.
Obsidian 백엔드와 같은 섹션 구조·같은 마커를 쓰므로 파서를 공유한다.

없는 기능이 있다. daily 노트 연동, `[[wikilink]]` 역링크 보수, 대시보드 렌더는 Obsidian
고유 기능이라 여기선 동작하지 않는다. `task-log --summary` 를 쓰면 진행 로그에만 남기고
그 사실을 stderr 로 알린다.

`task-status` 도 다르다. Obsidian 은 파일명의 상태 마커를 바꾸고 이전 daily 의 링크까지
고쳐주지만, markdown 백엔드는 문서 안 메타 줄만 바꾼다. 파일명은 그대로 둔다.

## obsidian 백엔드

기존 헬퍼 CLI 에 인자를 그대로 넘기고 종료 코드를 그대로 돌려준다. 새로 하는 게 없다.
이미 그 CLI 를 쓰고 있던 볼트는 한 줄도 바뀌지 않는다.

`ls` · `resolve` · `raw-path` 세 개만 어댑터가 직접 처리한다. 원본 CLI 에 없는 명령이다.

### 실측 대조

실제 볼트(결정 81개짜리 문서)에서 신구 출력을 바이트 단위로 비교했다.

| 명령 | 결과 |
|---|---|
| `current --ids-only` | 동일 (3,882 bytes) |
| `current` | 동일 (60,329 bytes) |
| `current --with-queue` | 동일 (65,986 bytes) |
| `current --topic` | 동일 (762 bytes) |

쓰기 명령은 별도 임시 볼트에서 검증했다. `decide --supersedes` 가 현재 설계는 교체하고
결정 로그는 `SUPERSEDED by D-n` 으로 남기는 것, `task-status` 가 파일명 마커까지 바꾸는 것을
확인했다. 검증 중 실제 볼트는 한 파일도 바뀌지 않았다.

## 문서 규격

네 섹션과 두 마커가 계약이다. 자세한 건 [ledger-format.md](ledger-format.md).

```
## 현재 설계   <!-- CURRENT:START/END -->   확정된 결정만. 항상 교체된다
## 작업 큐                                  구현 슬라이스 S-n
## 결정 로그   <!-- LEDGER:START/END -->    append-only. status 만 뒤집힌다
## 진행 로그                                시간순. 항상 마지막 섹션
```

헤딩은 `scripts/ledger/headings.json` 의 별칭 맵으로 인식한다. 한국어로 쓰인 문서든
영어로 쓰인 문서든 같은 파서가 읽고, 쓸 때는 `lang` 이 가리키는 표기를 쓴다.

## 종료 코드

| 코드 | 뜻 |
|---|---|
| 0 | 정상 |
| 2 | 문서를 찾지 못했거나 형식이 맞지 않는다 |
| 3 | 백엔드를 정하지 못했다 |

obsidian 백엔드에서는 위임한 CLI 의 종료 코드가 그대로 나온다.
