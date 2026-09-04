# 트랙 · 이미지 업로드 파이프라인 배포

## 메타
- **id**: upload-pipeline
- **생성일**: 2026-09-04
- **상태**: 진행중
- **설계**: [[0904-이미지-업로드-파이프라인]]

## 액터
| 세션 | 담당(T-n) | 원장(task 문서 접두사) |
|---|---|---|
| api-upload | T-1, T-3, T-5 | 0904-이미지-업로드 |
| infra-cdn | T-2, T-4 | 0904-이미지-업로드 |

## 스텝

### T-1 · presigned 발급 API dev 머지
- why: CDN 설정이 키 스키마를 전제로 한다. 스키마가 먼저 고정돼야 인프라가 못 어긋난다
- where: repo=web-api branch=feature/upload-presign
- gate: merge
- env: dev
- owner: api-upload
- task: 0904-이미지-업로드:S-1
- probe: `gh api repos/<org>/web-api/commits/dev --jq .sha` :: != :: <이전 sha>

### T-2 · CDN 오리진·서명 URL apply (dev)
- why: 썸네일 온디맨드가 CDN 캐시를 전제로 한다. 이게 없으면 S-3 검증이 무의미하다
- where: repo=infra branch=main
- gate: apply
- env: dev
- needs: T-1
- owner: infra-cdn
- probe-kind: plan
- probe: `terraform plan -detailed-exitcode` :: exit0
- note: 팀 — dev 배포 창에서만. 같은 시간에 다른 사람이 배포 스택을 건드리면 상태 잠금이 충돌한다

### T-3 · 업로드 완료 콜백 검증 dev 머지
- why: 격리 접두사가 CDN 규칙과 겹치면 안 된다. T-2 이후에 확인해야 의미가 있다
- where: repo=web-api branch=feature/upload-callback
- gate: merge
- env: dev
- needs: T-2
- owner: api-upload
- task: 0904-이미지-업로드:S-2

### T-4 · CDN prod apply
- why: prod 는 되돌리기 비싸다. dev 에서 캐시 히트를 확인한 뒤에만 연다
- where: repo=infra branch=main
- gate: apply
- env: prod
- needs: T-3
- owner: infra-cdn
- rests-on: 0904-이미지-업로드:D-3

### T-5 · 썸네일 온디맨드 릴리스
- why: 마지막. 앞이 다 서 있어야 캐시 히트가 재현된다
- where: repo=web-api branch=main
- gate: release
- env: prod
- needs: T-4
- owner: api-upload
- task: 0904-이미지-업로드:S-3

## 신호
- 2026-09-04T01:10:00+00:00 · api-upload · T-1 · merged · PR #128
- 2026-09-04T02:40:00+00:00 · infra-cdn · T-2 · blocked · dev 배포 창 대기 중
