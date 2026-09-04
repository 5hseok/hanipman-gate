---
name: convention-audit
description: |
  .claude/conventions/ 컨벤션 문서와 실제 코드 간 불일치를 감지하고 위반 사항을 리포트합니다.
  3가지 모드: 전체 코드 점검, 브랜치 diff 기반 점검, 현재 변경사항 점검.
  Use when: 컨벤션 점검, convention audit, 코드 규칙 검사, 컨벤션 위반,
  코드 품질 검사, naming convention, architecture check, 레이어 위반,
  DTO 패턴 점검, import 규칙, 컨벤션 감사, code compliance
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Agent
  - AskUserQuestion
user-invocable: true
argument-hint: "[full|branch [name]|changes]"
---

# Convention Audit Skill

`.claude/conventions/`에 정의된 코드 컨벤션과 도메인 지식을 기준으로,
실제 구현 코드가 규칙을 준수하는지 자동 점검한다.

## 인자 파싱

- `/convention-audit` → 모드 선택 질문
- `/convention-audit full` → 전체 점검
- `/convention-audit branch [branch-name]` → 브랜치 비교 (기본: main)
- `/convention-audit changes` → 현재 변경사항 점검


## 실행 모드

| 모드 | 설명 | 대상 파일 |
|------|------|-----------|
| **전체 점검** (`full`) | 프로젝트 전체 코드를 컨벤션 기준으로 감사 | 모든 Python 소스 |
| **브랜치 비교** (`branch`) | 특정 브랜치(기본: main) 대비 변경 파일만 점검 | `git diff <branch>...HEAD` |
| **변경사항 점검** (`changes`) | 현재 staged/unstaged 변경사항만 점검 | `git diff` + `git diff --cached` |

## 실행 절차 (기본 모드)

### Step 1: 대상 파일 수집

```bash
# 전체: apps/ cores/ 하위 *.py (alembic, __pycache__ 제외)
# 브랜치: git diff --name-only --diff-filter=ACMR <branch>...HEAD -- "*.py"
# 변경사항: git diff --name-only + git diff --cached --name-only
```

### Step 2: 컨벤션 문서 로드

`.claude/conventions/` 전체를 읽고 점검 규칙 추출.

**핵심 매핑:**

| 컨벤션 파일 | 점검 항목 | 대상 |
|-------------|-----------|------|
| 01-project-structure | 파일 위치 | 모든 파일 |
| 02-naming-conventions | snake_case, PascalCase, 동사 접두사, DTO suffix | 클래스, 함수, 변수명 |
| 03-layered-architecture | Routes→Services→Repositories 계층 위반 | import 관계 |
| 04-dto-patterns | DTO 기본 클래스, request/response suffix | DTO 파일 |
| 05-route-patterns | 라우트 설정, 엔드포인트 패턴 | route 파일 |
| 06-service-patterns | public try/catch, private 메서드 패턴 | service 파일 |
| 07-repository-patterns | 메서드명 상세도, 공통함수 문서화 | repository 파일 |
| 08-model-patterns | SQLModel 정의, Enum, 관계 | model 파일 |
| 09-exception-handling | 프로젝트 공통 예외 베이스 클래스 사용 | 예외 처리 코드 |
| 10-dependency-injection | Container DI 패턴 | DI 설정 |
| 11-database-session | @with_transaction 데코레이터 | DB 접근 코드 |
| 12-import-rules | Import 순서, Ruff 규칙 | 모든 파일 |
| 14-rest-api-design | REST 설계 원칙 | API 엔드포인트 |
| 16-best-practices | Do's/Don'ts | 모든 코드 |
| 17-testing-conventions | 테스트 구조, 패턴 | 테스트 파일 |

### Step 3: 병렬 점검 (에이전트 분할)

대상 파일이 많을 경우 4개 에이전트로 분할:

| 에이전트 | 담당 컨벤션 |
|----------|------------|
| **구조/네이밍** | 01, 02, 12 |
| **아키텍처/계층** | 03, 10, 11 |
| **패턴/DTO** | 04, 05, 06, 07, 08 |
| **예외/API/품질** | 09, 14, 16, 17 |

### Step 4: 결과 취합 및 리포트

```markdown
# Convention Audit Report

- **모드**: [전체 / 브랜치 비교 (vs main) / 변경사항]
- **점검 일시**: YYYY-MM-DD HH:mm
- **대상 파일 수**: N개
- **위반 건수**: N개 (Critical: N, Warning: N, Info: N)

## Summary
| 컨벤션 | 위반 수 | 심각도 |
|--------|---------|--------|

## 상세 위반 사항
### [Critical] 03-layered-architecture: 레이어 위반
- **파일**: `path/to/file.py:42`
- **내용**: 위반 설명
- **컨벤션 규칙**: 해당 규칙 인용
- **수정 제안**: 구체적 수정 방법
```

**심각도 기준:**
- **Critical**: 아키텍처 위반, 보안 관련, 예외 suppress
- **Warning**: 네이밍 불일치, 패턴 미준수, 문서화 누락
- **Info**: 개선 권장, 스타일 제안

### Step 5: 리포트 저장

`./convention-audit-report.md`에 저장.

---

## 자동 탐지 패턴

```python
# 레이어 위반: Service에서 직접 session/query 사용
r"from.*models.*import|session\.(query|exec|execute)"  # in service files

# DTO suffix 점검
r"class\s+\w+(?<!Request|Response|RequestQuery|DTO)\(.*Base"  # in dto files

# 예외 suppress 탐지
r"except.*:\s*(pass|\.\.\.)"

# @with_transaction 누락 (DB 수정 메서드)
r"def\s+(create|update|delete|save|remove)"  # without @with_transaction
```

## 주의사항

- 컨벤션 문서가 없는 프로젝트에서는 실행 불가
- 위반 사항 자동 수정은 하지 않는다. 리포트만 만든다
- `alembic/`, `__pycache__/`, `tests/` 기본 제외 (옵션으로 포함 가능)
