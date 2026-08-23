# MSG 백엔드 DevSecOps 운영 절차

## 지금 만들려는 구조

`jm_devsecops`는 검사 방법을 보관하는 중앙 설명서이고, `msg-backend`는 검사할 실제 애플리케이션이다.

```text
MSG-CTF/jm_devsecops
└── reusable-ci.yml / reusable-cd.yml / 버전 태그
                         ↑ 호출
MSG-CTF/msg-backend
└── 짧은 ci-cd.yml + Dockerfile + Django 코드와 테스트
```

중앙 워크플로가 실행되어도 checkout되는 코드는 호출한 백엔드 저장소의 코드다. 따라서 Dockerfile, `/healthz`, Django 설정, migration과 테스트는 반드시 백엔드 저장소에 있어야 한다.

## 버전 규칙

- `v1.0.0`은 이미 배포한 버전이므로 이동하거나 덮어쓰지 않는다.
- 현재 변경은 Secret 이름과 CD 입력 방법이 달라지는 호환성 변경이므로 `v2.0.0` 후보로 준비한다.
- 중앙 CI와 CD, 백엔드용 파일을 모두 검증하기 전에는 `v2.0.0` 태그를 만들지 않는다.
- 백엔드에서는 `@main`이 아니라 검증된 `@v2.0.0`을 호출한다.

## 2026-08-23 로컬 검증 기록

- 중앙 workflow 4개의 `actionlint` 문법 검사 통과
- 중앙 Django 테스트 2개와 `check --deploy` 통과
- 중앙 PostgreSQL 쿼리·migration 및 Redis 저장·조회 통과
- 최신 백엔드 `main` 임시 복제본에 새 파일을 적용한 뒤 전체 테스트 41개 통과
- 임시 백엔드의 migration, Django cache, Flake8 치명 오류와 `check --deploy` 통과
- 중앙 이미지와 임시 백엔드 이미지 모두 Trivy HIGH·CRITICAL 0개
- 비루트 `app` 사용자, Gunicorn PID 1, 운영 Host 헤더의 `/healthz` 200 응답 확인
- Gitleaks Git 기록 검사에서 유출 0개

이 기록은 로컬·임시 복제본 결과다. 중앙 `main`에 push한 다음 GitHub Actions에서도 같은 결과를 확인해야 `v2.0.0` 태그를 만들 수 있다.

## 1단계: 중앙 CI에서 하는 검사

`reusable-ci.yml`은 다음 세 작업을 병렬로 실행한다.

### 보안 검사

1. Gitleaks가 Git 기록까지 확인한다.
2. Trivy가 HIGH·CRITICAL 취약점을 검사한다.
3. 수정 버전이 있는 HIGH·CRITICAL 취약점은 CI를 실패시킨다.
4. 수정 버전이 아직 없는 취약점도 SARIF 보고서에는 남긴다.

### Django 검사

1. PostgreSQL과 Redis 서비스 컨테이너를 실행한다.
2. `requirements.txt`와 고정 버전의 Black·Flake8을 설치한다.
3. Black과 치명적인 Flake8 오류를 검사한다. 기존 백엔드는 Black 정리 기간 동안 결과를 경고로 보여주고, 별도 포맷 PR 뒤 `enforce_black: true`로 병합을 차단한다.
4. PostgreSQL에 연결해서 `SELECT 1`을 실행한다.
5. `makemigrations --check --dry-run`으로 누락된 migration을 찾는다.
6. `migrate --noinput`으로 테스트 DB에 migration을 실제 적용한다.
7. Redis에 값을 저장하고 다시 읽는다.
8. `django-redis`를 사용하는 프로젝트는 Django cache를 통해서도 저장·조회한다.
9. 기존 API 기능 테스트는 HTTP test client와 맞도록 테스트 모드로 실행하고, 테스트가 0개면 실패한다.
10. 별도 단계에서 `DJANGO_DEBUG=False`와 `check --deploy --fail-level WARNING`을 사용해 운영 보안 설정을 확인한다.

### Docker 검사

1. 루트 `Dockerfile`이 없으면 실패한다.
2. 이미지 빌드가 되는지 확인한다.
3. 컨테이너 기본 사용자가 root면 실패한다.
4. Docker `HEALTHCHECK`가 없으면 실패한다.
5. `DJANGO_ALLOWED_HOSTS=example.com`인 운영 형태로 컨테이너를 실행한다.
6. `Host: example.com`으로 `/healthz`를 요청해서 200 응답을 확인한다.
7. Trivy로 만들어진 이미지도 검사한다.

Action과 서비스 이미지는 commit SHA 또는 image digest로 고정한다. 태그 설명은 사람이 버전을 알아보기 위한 주석일 뿐, 실제 실행 대상은 고정된 SHA다.

Python 베이스 이미지는 오래된 patch digest를 계속 붙잡지 않는다. Trivy에서 수정 가능한 HIGH·CRITICAL OS 취약점이 발견되면 최신 `python:3.12-slim` manifest로 바꾸고 Debian 보안 업데이트를 적용한 뒤 다시 검사한다. 배포에는 CI가 검사한 최종 image digest를 사용한다.

## 2단계: 백엔드의 새 브랜치 준비

PR #1 브랜치에서 이어서 작업하지 않는다. 최신 백엔드 `main`에서 새 브랜치를 만든다.

```bash
git switch main
git pull origin main
git switch -c chore/devsecops-v2
```

PR #1은 새 PR이 완성될 때까지 참고 자료로 남겨 둔다.

### 백엔드에 추가할 파일

중앙 저장소의 다음 예제를 백엔드에 맞는 위치로 복사한다.

```text
docs/backend-files/Dockerfile       → msg-backend/Dockerfile
docs/backend-files/.dockerignore    → msg-backend/.dockerignore
docs/backend-files/health.py        → msg-backend/config/health.py
docs/backend-files/test_health.py   → msg-backend/config/tests.py
docs/backend-workflow-example.yml   → msg-backend/.github/workflows/ci-cd.yml
```

`config/tests.py`가 이미 생겼다면 덮어쓰지 말고 `HealthCheckTest`만 합친다.

백엔드 `requirements.txt`에는 다음 고정 버전을 한 줄 추가한다.

```text
gunicorn==26.1.0
```

현재 `sqlparse==0.5.5`는 Trivy에서 수정 가능한 HIGH 취약점 3개가 확인되므로 다음처럼 올린다.

```text
sqlparse==0.6.0
```

백엔드 `config/urls.py`에는 다음 import와 URL을 추가한다.

```python
from config.health import healthz

urlpatterns = [
    path("healthz", healthz),
    # 기존 URL은 그대로 둔다.
]
```

### 백엔드 settings.py 수정

현재 백엔드 주석은 DEBUG 기본값이 False라고 적혀 있지만 실제 코드는 `True`다. 다음처럼 실제 기본값도 False로 바꾼다.

```python
DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() == "true"
```

기존 앱·JWT·dotenv·DB·Redis 설정은 지우지 말고 다음 운영 보안 설정을 추가한다.

```python
SECURE_SSL_REDIRECT = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_HSTS_SECONDS = 0 if DEBUG else 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
```

### 백엔드에 넣지 않을 파일

PR #1의 오래된 `.github/workflows/ci.yml`은 옮기지 않는다. 중앙 파일 전체를 복사하지 않고 `docs/backend-workflow-example.yml`의 짧은 호출 파일만 둔다.

## 3단계: 처음에는 CI만 연결

`docs/backend-workflow-example.yml`은 모든 브랜치 push와 `main`·`develop` 대상 PR에서 CI를 실행한다. 이 단계에는 Docker Hub나 GCP Secret이 필요 없다.

현재 최신 백엔드는 Black 기준으로 여러 기존 파일을 다시 포맷해야 한다. DevSecOps PR에 수십 개의 기계적 포맷 변경을 섞지 않기 위해 첫 호출은 기본값 `enforce_black: false`를 사용한다. 이후 포맷만 정리한 별도 PR을 병합하고 다음 입력을 추가한다.

```yaml
jobs:
  ci:
    uses: MSG-CTF/jm_devsecops/.github/workflows/reusable-ci.yml@v2.0.0
    with:
      enforce_black: true
```

다음 조건을 모두 확인하기 전에는 deploy job을 추가하지 않는다.

- Cloud SQL 인스턴스와 데이터베이스가 준비됨
- Cloud Run에서 Cloud SQL로 연결할 방법이 준비됨
- Redis가 준비되고 Cloud Run에서 접근 가능함
- `django-secret-key`, `jwt-secret`, `postgres-password`가 Secret Manager에 존재함
- 각 Secret의 `latest`가 아닌 숫자 버전을 정함
- 운영 migration을 Cloud Run Job 등으로 먼저 적용하는 절차가 준비됨
- GitHub `production` Environment에 승인 규칙을 설정함
- Workload Identity 조건이 `MSG-CTF/msg-backend`, `main` ref와 중앙 `reusable-cd.yml@v2.0.0` 호출만 허용함
- Cloud Run URL 또는 운영 도메인을 `DJANGO_ALLOWED_HOSTS`에 넣음
- `/healthz`가 인증 없이 200을 반환함

## 4단계: CD를 나중에 켜는 방법

위 준비가 끝나면 백엔드 호출 파일의 최상위 권한에 `id-token: write`를 추가하고 다음 job을 붙인다.

```yaml
  deploy:
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    needs: ci
    uses: MSG-CTF/jm_devsecops/.github/workflows/reusable-cd.yml@v2.0.0
    with:
      commit_sha: ${{ github.sha }}
      dockerhub_username: ${{ vars.DOCKERHUB_USERNAME }}
      image_name: ctf-backend
      cloud_run_service: ctf-backend
      region: asia-northeast3
      deployment_environment: production
      django_allowed_hosts: ${{ vars.DJANGO_ALLOWED_HOSTS }}
      healthcheck_host: ${{ vars.HEALTHCHECK_HOST }}
      postgres_db: ${{ vars.POSTGRES_DB }}
      postgres_user: ${{ vars.POSTGRES_USER }}
      postgres_host: ${{ vars.POSTGRES_HOST }}
      postgres_port: "5432"
      redis_url: ${{ vars.REDIS_URL }}
      django_secret_version: ${{ vars.DJANGO_SECRET_VERSION }}
      jwt_secret_version: ${{ vars.JWT_SECRET_VERSION }}
      postgres_password_secret_version: ${{ vars.POSTGRES_PASSWORD_SECRET_VERSION }}
    secrets:
      DOCKERHUB_TOKEN: ${{ secrets.DOCKERHUB_TOKEN }}
      GCP_WORKLOAD_IDENTITY_PROVIDER: ${{ secrets.GCP_WORKLOAD_IDENTITY_PROVIDER }}
      GCP_SERVICE_ACCOUNT: ${{ secrets.GCP_SERVICE_ACCOUNT }}
```

`secrets: inherit`는 사용하지 않는다. 호출받은 워크플로에 필요한 Secret 세 개만 명시적으로 전달한다.

CD는 다음 순서로 동작한다.

1. 배포할 commit SHA와 Secret 버전 번호를 검증한다.
2. Gitleaks를 다시 실행한다.
3. 이미지를 commit SHA 태그로 빌드한다.
4. Trivy를 통과한 이미지만 Docker Hub에 push한다.
5. push된 이미지의 digest를 구한다.
6. GitHub `production` Environment 승인 규칙을 거친다.
7. GCP Workload Identity Federation으로 인증한다.
8. Secret Manager의 고정 숫자 버전을 환경변수로 연결한다.
9. Cloud Run에는 태그가 아니라 digest로 배포한다.
10. 배포 URL의 `/healthz`를 확인한다.

주의: 현재 reusable CD는 운영 migration 자체를 실행하지 않는다. migration용 Cloud Run Job을 만들고 검증하기 전에는 이 deploy job을 켜면 안 된다.

## 5단계: 중앙 버전 공개와 백엔드 PR 순서

1. 중앙 저장소에서 `actionlint`, Django 테스트, Docker 실행 검사를 통과시킨다.
2. 중앙 변경을 `main`에 push한다.
3. GitHub Actions 결과가 모두 통과한 것을 확인한다.
4. 그 통과한 commit에만 `v2.0.0` 태그를 만든다.
5. 백엔드 최신 `main`에서 만든 `chore/devsecops-v2` 브랜치에 위 백엔드 파일을 추가한다.
6. 백엔드 CI가 실제로 모든 검사를 실행하고 통과하는지 로그를 확인한다.
7. 새 백엔드 PR을 만든다.
8. PR #1에 새 PR 링크와 대체 이유를 댓글로 남긴다.
9. 필요한 변경이 새 PR에 모두 있는지 확인한 다음 PR #1을 닫는다.
10. 마지막에 PR #1의 예전 브랜치를 삭제한다.

## Branch protection

백엔드 `main`의 Branch protection 또는 Ruleset에서 첫 CI 실행 후 표시되는 검사 중 아래 이름으로 끝나는 세 작업을 필수 검사로 지정한다. GitHub 화면에서는 호출 job 이름이 앞에 붙어 더 길게 보일 수 있다.

```text
security-scan
lint-and-test
docker-build-check
```

이 설정이 없으면 CI가 실패해도 사람이 그냥 merge할 수 있으므로 진짜 병합 게이트가 아니다.

## 실패했을 때 확인할 곳

- Gitleaks 실패: 노출된 자격증명을 즉시 폐기하고 Git 기록에서도 제거한다.
- Trivy 실패: `requirements.txt` 또는 베이스 이미지 digest를 안전한 버전으로 갱신한다.
- migration 실패: 모델 변경에 해당하는 migration이 커밋됐는지 확인한다.
- PostgreSQL 실패: `POSTGRES_*` 이름과 migration을 확인한다.
- Redis 실패: `REDIS_URL`과 Django `CACHES` 설정을 확인한다.
- Docker health 실패: `HEALTHCHECK_HOST`가 `DJANGO_ALLOWED_HOSTS`에 포함됐는지 확인한다.
- Cloud Run 실패: revision 로그, `$PORT`, Secret 버전과 DB·Redis 네트워크 연결을 확인한다.
- 배포 후 health 실패: 새 revision에 트래픽을 보내지 않도록 이전 정상 revision으로 롤백한다.
