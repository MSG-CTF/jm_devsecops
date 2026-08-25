# MSG CTF 중앙 DevSecOps 파이프라인

## 30초 요약

- 이 저장소는 `MSG-CTF/msg-backend`가 공통으로 사용할 **CI/CD 검사 설명서**를 보관한다.
- 현재 백엔드가 사용해야 하는 안정 버전은 **`v3.3.0`**이다.
- CI는 코드가 들어올 때마다 보안, Python/Django, PostgreSQL, Redis, Docker를 자동 검사한다.
- SAST는 Semgrep과 Bandit을 함께 사용한다. SCA와 컨테이너 취약점 검사는 Trivy가 담당하고, 유출된 비밀값은 Gitleaks가 찾는다.
- CD 코드는 준비되어 있지만 아직 백엔드에서 호출하지 않는다. Cloud SQL, Redis, Secret Manager, 운영 migration 절차와 승인 규칙을 준비한 뒤 켜야 한다.
- 백엔드는 움직이는 `@main`이 아니라 고정된 `@v3.3.0`을 호출해야 한다.
- 별도의 `/healthz` API는 요구하지 않는다. CI와 CD는 기존 `/admin/login/`을 한 번 요청해서 시작 여부만 확인한다.

```text
백엔드 개발자가 push 또는 PR 생성
                │
                ▼
        중앙 reusable-ci 호출
                │
     ┌──────────┼──────────┬──────────────┐
     ▼          ▼          ▼              ▼
 보안 검사   SAST 검사   Django 검사   Docker 검사
     └──────────┴──────────┴──────────────┘
                │
                ▼
       네 작업이 모두 성공해야 통과

운영 준비가 끝난 뒤 main push
                │
                ▼
        중앙 reusable-cd 호출
                │
                ▼
 빌드 → 보안 검사 → Docker Hub → 승인 → Cloud Run
```

## CI와 CD가 무엇인가

CI는 선생님이 숙제를 제출받자마자 틀린 곳이 없는지 검사하는 것과 비슷하다.

- 코드가 실행되는가?
- 테스트가 실제로 존재하고 통과하는가?
- 데이터베이스와 Redis를 사용할 수 있는가?
- 비밀번호나 API 키를 실수로 올리지 않았는가?
- 알려진 취약점이 있는가?
- Docker 컨테이너가 안전한 사용자로 실행되는가?

CD는 검사를 통과한 결과물을 실제 운영 서버까지 안전하게 배달하는 과정이다.

- 검사한 코드로 Docker 이미지를 만든다.
- 취약점 검사를 통과한 이미지만 Docker Hub에 올린다.
- 사람이 운영 배포를 승인한다.
- 비밀번호를 코드에 넣지 않고 GCP Secret Manager에서 가져온다.
- 정확히 검사한 이미지의 digest를 Cloud Run에 배포한다.

CI와 CD는 연결되지만 역할이 다르다. CI가 실패하면 CD를 시작하면 안 된다.

## 저장소 구조와 각 파일의 역할

```text
.github/workflows/
├── ci.yml                 중앙 저장소 자체 CI를 시작하는 파일
├── reusable-ci.yml        백엔드가 호출하는 실제 공통 CI
└── reusable-cd.yml        나중에 백엔드가 호출할 실제 공통 CD

docs/
├── backend-workflow-example.yml   백엔드용 짧은 호출 파일 예제
├── backend-files/
│   ├── Dockerfile                 백엔드용 Dockerfile 예제
│   └── .dockerignore              백엔드용 Docker 제외 목록 예제
└── devsecops-runbook.md           설정과 운영 작업 순서

Dockerfile / manage.py / config/ / requirements.txt
└── 중앙 reusable CI가 고장 나지 않았는지 확인하는 작은 Django 시험 프로젝트
```

### `ci.yml`이 필요한 이유

`reusable-ci.yml`은 다른 워크플로가 불러야 실행되는 재사용 설명서다. 혼자서는 push를 감지하지 못한다.

`ci.yml`은 다음 사건을 감지해서 중앙 저장소의 `reusable-ci.yml`을 실행한다.

- 모든 브랜치의 push
- `main` 또는 `develop`을 대상으로 만든 Pull Request

중앙 저장소에서는 `enforce_black: true`를 전달하므로 Black 포맷이 맞지 않아도 실패한다. 같은 브랜치에 push가 연속으로 들어오면 오래된 실행을 취소하고 최신 실행만 계속한다.

### `reusable-ci.yml`이 검사하는 코드

백엔드가 중앙 워크플로를 호출하면 중앙 저장소의 예제 코드가 아니라 **호출한 백엔드 저장소의 코드**를 checkout한다.

따라서 다음 파일은 백엔드 저장소에 있어야 한다.

```text
manage.py
requirements.txt
Dockerfile
Django 코드와 migration
최소 1개 이상의 테스트
```

중앙 저장소는 검사 방법을 제공하고, 백엔드 저장소는 검사받을 실제 코드를 제공한다.

## 전체 CI 동작 순서

CI는 네 작업을 병렬로 실행한다. 병렬은 네 사람이 각자 다른 검사를 동시에 시작하는 것과 같다. 한 작업이라도 실패하면 전체 CI는 실패한다.

### 1. `security-scan`: 비밀값과 알려진 취약점 검사

#### Gitleaks

Gitleaks는 현재 파일뿐 아니라 Git 과거 기록까지 읽는다.

- 실수로 커밋한 API 키
- 클라우드 자격증명
- 실제 비밀번호와 토큰

발견되면 값 일부를 가린 상태로 CI를 실패시킨다. 이미 Git에 올라간 실제 Secret은 파일만 지우면 끝나지 않는다. 먼저 해당 키를 폐기하고 Git 기록도 정리해야 한다.

#### Trivy 파일시스템 SCA

Trivy는 `requirements.txt` 같은 의존성 파일을 읽어 공개 취약점 데이터베이스와 비교한다.

- `HIGH` 또는 `CRITICAL`: 수정 버전 유무와 관계없이 CI 실패
- `UNKNOWN`, `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`: 모두 SARIF 보고서에 기록

SARIF는 GitHub Security 화면에서 파일과 줄을 보기 쉽게 만들어 주는 보안 성적표 형식이다.

### 2. `sast-scan`: 프로그램을 실행하지 않고 소스 코드 검사

SAST는 자동차를 운전하기 전에 설계도를 보고 위험한 부분을 찾는 검사다.

#### Semgrep

Semgrep은 다음 파일을 넓게 검사한다.

- Python과 Django 코드
- Dockerfile
- YAML과 GitHub Actions
- 저장소에서 발견한 다른 지원 언어

Semgrep Community의 `p/default` 규칙을 사용한다.

- `ERROR` 심각도: CI 실패
- `WARNING`, `INFO`: SARIF 보고서에 남겨 검토
- Semgrep 실행 오류: CI 실패

오탐이라고 판단할 때는 안전한 근거를 먼저 남기고 정확한 한 줄만 `nosemgrep`으로 제외한다. 폴더 전체를 제외하면 진짜 취약점도 같이 숨겨질 수 있다.

#### Bandit

Bandit은 Python 코드만 더 깊게 보는 두 번째 검사관이다.

- 위험한 `eval` 사용
- 안전하지 않은 subprocess 실행
- 약한 암호 방식
- 위험한 파일 또는 URL 처리
- 코드에 들어간 비밀번호 형태

모든 발견을 로그에 보여주지만 다음 두 조건이 모두 맞을 때만 CI를 막는다.

```text
심각도: MEDIUM 또는 HIGH
신뢰도: MEDIUM 또는 HIGH
```

테스트용 비밀번호처럼 LOW인 발견은 기록으로 확인하되 바로 병합을 막지는 않는다. Semgrep 또는 Bandit 중 하나라도 실패하면 `sast-scan` 작업이 실패한다.

### 3. `lint-and-test`: Django와 실제 서비스 연결 검사

이 작업은 PostgreSQL과 Redis 서비스 컨테이너를 먼저 실행한다. 이미지 태그만 믿지 않고 digest까지 고정해서 같은 대상을 사용한다.

검사는 다음 순서로 진행된다.

1. `manage.py`와 `requirements.txt`가 있는지 확인한다.
2. Python 3.12.14를 준비한다.
3. 고정된 의존성과 Black, Flake8, Redis 검사 도구를 설치한다.
4. Black으로 코드 포맷을 검사한다.
5. Flake8로 실행 자체를 깨뜨리는 치명적인 Python 오류를 찾는다.
6. Django가 PostgreSQL에 연결해 `SELECT 1`을 실행한다.
7. `makemigrations --check --dry-run`으로 빠진 migration을 찾는다.
8. `migrate --noinput`으로 빈 테스트 DB에 migration을 실제 적용한다.
9. Redis에 값을 저장하고 다시 읽는다.
10. `django-redis`가 있으면 Django cache를 통해서도 저장하고 읽는다.
11. Django 테스트를 실행한다.
12. 실행된 테스트가 0개면 실패시킨다.
13. `DEBUG=False`로 `check --deploy --fail-level WARNING`을 실행한다.

서비스 컨테이너만 켜 놓고 실제 연결을 하지 않으면 DB 설정이 깨져도 CI가 초록불이 될 수 있다. 그래서 이 파이프라인은 연결, 쿼리, migration, Redis 저장·조회를 실제로 수행한다.

### 4. `docker-build-check`: 운영용 상자 검사

Docker 검사는 다음 순서다.

1. 저장소 루트에 `Dockerfile`이 없으면 실패한다.
2. `ctf-backend:test` 이미지를 실제로 빌드한다.
3. 기본 사용자가 `root` 또는 UID 0이면 실패한다.
4. 운영과 비슷한 `DEBUG=False`, `ALLOWED_HOSTS=example.com` 값으로 컨테이너를 실행한다.
5. `Host: example.com`과 `X-Forwarded-Proto: https` 헤더를 넣는다.
6. 기존 Django 주소인 `/admin/login/`을 최대 20번 요청한다.
7. 응답하지 않으면 컨테이너 로그를 보여주고 실패한다.
8. 성공 여부와 관계없이 시험 컨테이너를 정리한다.
9. 완성된 이미지에서 Trivy 취약점과 Secret 검사를 다시 실행한다.

파일시스템 검사가 안전해도 베이스 OS 이미지에 취약점이 있을 수 있다. 그래서 소스 의존성과 완성된 Docker 이미지를 각각 검사한다.

## `/healthz`를 요구하지 않는 이유

CI와 CD의 smoke test는 새 프로그램이 한 번 정상적으로 시작했는지 확인한다. 이것은 학교에 도착했는지 한 번 확인하는 것과 같다.

SLA 모니터링은 배포가 끝난 뒤에도 서비스가 계속 정상인지 주기적으로 확인한다. 이것은 수업 시간 내내 상태를 지켜보는 것과 같다.

이 프로젝트에는 별도 SLA 모니터링 담당이 있으므로 파이프라인은 다음 항목을 강제로 만들지 않는다.

- `/healthz` URL
- `/healthz` 전용 view와 테스트
- Dockerfile `HEALTHCHECK`

대신 백엔드에 이미 존재하는 `/admin/login/`을 한 번 요청한다. 관리자 URL을 바꾸면 호출 파일의 `smoke_test_path`도 같이 바꿔야 한다.

## 현재 CD 상태

`reusable-cd.yml`에는 실제 CD 코드가 있지만, 현재 중앙 저장소와 백엔드는 이를 자동 호출하지 않는다. 즉, **CD 설계는 준비되어 있지만 운영 배포 스위치는 꺼져 있다.**

CD를 켜기 전에 다음 준비가 필요하다.

- Cloud SQL과 데이터베이스
- Cloud Run에서 Cloud SQL로 연결하는 방법
- Cloud Run이 접근할 수 있는 Redis
- GCP Secret Manager의 Django, JWT, PostgreSQL Secret
- Secret의 `latest`가 아닌 숫자 버전
- 운영 migration을 먼저 실행할 Cloud Run Job 같은 절차
- GitHub `production` Environment의 승인 규칙
- 저장소와 `main`만 허용하는 Workload Identity 조건
- 운영 도메인과 `DJANGO_ALLOWED_HOSTS`
- 배포 뒤 계속 상태를 확인할 SLA 모니터링

### CD가 켜졌을 때의 순서

#### `build-scan-push`

1. 배포할 commit SHA가 정확한 40자리인지 검사한다.
2. Secret 버전이 `latest`가 아니라 숫자인지 검사한다.
3. 요청받은 commit만 checkout한다.
4. Gitleaks로 Git 기록을 다시 검사한다.
5. Docker 이미지를 commit SHA 태그로 빌드한다.
6. Trivy로 이미지 취약점과 Secret을 검사한다.
7. 통과한 이미지만 Docker Hub에 push한다.
8. push된 이미지의 digest를 구한다.

commit SHA는 코드의 주민등록번호와 비슷하고, 이미지 digest는 완성된 상자의 지문과 비슷하다. 이름표인 Docker 태그는 움직일 수 있지만 digest가 같으면 내용도 같다.

#### `deploy`

1. GitHub `production` Environment의 승인 규칙을 거친다.
2. 저장된 장기 GCP 키 대신 Workload Identity Federation으로 인증한다.
3. Docker 태그가 아니라 검사한 digest로 Cloud Run에 배포한다.
4. 일반 설정값은 환경변수로 전달한다.
5. Django 키, JWT 키, DB 비밀번호는 Secret Manager의 고정 숫자 버전에서 가져온다.
6. Cloud Run URL의 `/admin/login/`을 한 번 요청한다.

주의: 현재 `reusable-cd.yml`은 운영 migration을 직접 실행하지 않는다. migration Job과 검증 절차가 준비되기 전에는 백엔드 deploy job을 켜면 안 된다.

## 백엔드가 CI를 연결하는 방법

백엔드 저장소에 `.github/workflows/ci-cd.yml`을 만들고 다음처럼 호출한다.

```yaml
name: Backend CI

on:
  push:
  pull_request:
    branches: ["main", "develop"]

permissions:
  contents: read
  security-events: write

concurrency:
  group: backend-ci-${{ github.workflow }}-${{ github.head_ref || github.ref }}
  cancel-in-progress: true

jobs:
  ci:
    uses: MSG-CTF/jm_devsecops/.github/workflows/reusable-ci.yml@v3.3.0
    with:
      smoke_test_path: /admin/login/
```

`@main`은 중앙 코드가 바뀌는 즉시 백엔드 결과도 바뀐다. `@v3.3.0`은 같은 버전이 항상 같은 코드를 가리키므로 재현과 문제 추적이 쉽다.

처음에는 Black을 경고로만 확인한다. 백엔드 포맷을 정리한 뒤 다음 값을 추가하면 Black 오류도 병합을 막는다.

```yaml
      enforce_black: true
```

전체 백엔드 파일 준비 방법과 나중에 CD를 연결하는 입력값은 [`docs/devsecops-runbook.md`](docs/devsecops-runbook.md)에서 확인한다.

## Branch protection에서 반드시 막아야 하는 작업

백엔드 `main`의 Ruleset 또는 Branch protection에서 다음 네 작업을 필수 검사로 지정한다.

```text
security-scan
sast-scan
lint-and-test
docker-build-check
```

이 설정이 없으면 CI가 빨간불이어도 사람이 merge할 수 있다. 파이프라인이 진짜 문지기가 되려면 GitHub 설정에서도 이 네 결과를 요구해야 한다.

## 버전 관리 규칙

현재 권장 안정 버전은 **`v3.3.0`**이다.

| 버전 | 핵심 변경 |
|---|---|
| `v1.0.0` | 재사용 CI/CD의 첫 버전 |
| `v2.0.0` | Docker 및 Google GitHub Action 업그레이드 |
| `v3.0.0` | `/healthz` 강제 계약을 제거하고 기존 주소 smoke test로 변경 |
| `v3.1.0` | Semgrep SAST 추가 |
| `v3.2.0` | Django·컨테이너 보안 강화, 수정본 없는 HIGH·CRITICAL도 차단 |
| `v3.3.0` | Bandit Python SAST 추가 |

이미 공개한 태그는 이동하거나 덮어쓰지 않는다. 같은 버전이 다른 코드를 가리키면 어느 검사를 실행했는지 믿을 수 없기 때문이다.

새 버전은 다음 순서로 만든다.

1. 중앙 저장소에서 변경한다.
2. 로컬 검사에 통과한다.
3. 중앙 `main`에 반영한다.
4. GitHub Actions 네 작업이 모두 통과하는지 확인한다.
5. 통과한 정확한 commit에 아직 쓰지 않은 새 태그를 만든다.
6. 태그 기준 GitHub Actions도 통과하는지 확인한다.
7. 그다음 백엔드 호출 버전을 새 태그로 올린다.

## 보안 도구와 고정 방식

| 대상 | 도구 | 역할 |
|---|---|---|
| Git 기록 | Gitleaks | 노출된 비밀번호, 토큰, 키 탐지 |
| Python 의존성·파일 | Trivy | SCA와 공개 취약점 탐지 |
| 전체 소스 | Semgrep | 여러 언어와 설정 파일 SAST |
| Python 소스 | Bandit | Python 전용 SAST |
| Docker 이미지 | Trivy | OS·라이브러리 취약점과 Secret 탐지 |
| Django | Django check/test | 배포 설정, 기능, migration 검사 |
| PostgreSQL·Redis | 실제 연결 명령 | 서비스 연결과 읽기·쓰기 검사 |

GitHub Action은 `v4` 같은 움직일 수 있는 이름만 사용하지 않고 commit SHA로 고정한다. Docker 서비스와 보안 도구 이미지도 가능한 경우 digest로 고정한다. 주석의 버전 이름은 사람이 읽기 위한 설명이고, 실제 실행 대상은 SHA와 digest가 결정한다.

## 이 파이프라인이 보장하지 못하는 것

보안 검사 결과가 0건이라고 해서 세상에 존재하는 모든 취약점이 없다는 뜻은 아니다.

이 파이프라인이 자동으로 찾는 범위는 다음과 같다.

- 공개 취약점 데이터베이스에 등록된 문제
- Semgrep과 Bandit 규칙이 알고 있는 위험한 코드 형태
- Git 기록에서 탐지 가능한 Secret
- Django 자동 검사와 작성된 테스트가 확인하는 동작
- Docker 이미지에서 Trivy가 찾을 수 있는 문제

다음 항목은 별도 준비가 필요하다.

- 아직 공개되지 않은 제로데이
- 실제 GCP IAM과 네트워크 설정 검토
- 로그인한 사용자 흐름을 공격하는 DAST
- 수동 침투 테스트
- 운영 부하와 장애 복구 시험
- 지속적인 SLA 모니터링

자동 CI/CD는 강한 안전망이지만, 모든 보안 업무를 대신하는 마법은 아니다.
