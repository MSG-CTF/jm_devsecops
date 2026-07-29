# DevSecOps 운영 절차

## 구성

- `nginx` : 외부에 유일하게 열리는 리버스 프록시 (포트 80)
- `web` : Django 애플리케이션 (nginx를 통해서만 접근 가능)
- `db` : PostgreSQL 16
- `redis` : Redis 7

## 파이프라인 1: CI (`.github/workflows/ci.yml`)

담당: 개발자 / CI

1. **security-scan**
   - Gitleaks로 저장소 전체를 스캔하여 시크릿(토큰, 자격증명, 비밀번호, API 키 등) 유출 여부를 확인한다. 발견 시 즉시 실패한다.
   - Trivy로 파일시스템(의존성 포함)의 Critical 취약점을 스캔한다. Critical 발견 시 즉시 실패한다.
   - 스캔 결과는 SARIF로 변환되어 GitHub Security 탭(Code Scanning)에 업로드된다.
2. **lint-and-test**
   - Black / Flake8 린트를 수행한다.
   - `manage.py`가 존재하면 Django 테스트를 실행한다 (postgres, redis 서비스 컨테이너 사용).
3. **docker-build-check**
   - Dockerfile 빌드가 성공하는지 확인한다.
   - 빌드된 이미지에 대해 Trivy 이미지 스캔(Critical)을 수행한다. Critical 발견 시 실패한다.

모든 단계를 통과해야 PR을 병합할 수 있다.

## 파이프라인 2: CD (`.github/workflows/cd.yml`)

담당: DevSecOps

`main` 브랜치 push 시에만 실행된다.

1. Gitleaks로 저장소를 다시 스캔한다.
2. Dockerfile로 이미지를 빌드하고 커밋 SHA 태그(`:${{ github.sha }}`)만 부여한다. **`latest` 태그는 사용하지 않는다.**
3. Trivy로 빌드된 이미지의 Critical 취약점을 스캔한다. 발견 시 배포를 중단한다.
4. 스캔을 통과한 이미지만 Docker Hub에 push한다.
5. push된 이미지의 manifest digest(`sha256:...`)를 추출한다.
6. 배포 서버에 SSH로 접속하여 `WEB_IMAGE=<repo>@sha256:<digest>` 형태로 **digest 기반 이미지 참조**를 주입하고 `docker compose pull web && docker compose up -d`를 실행한다.

Runtime은 mutable tag(`latest`)가 아닌 digest 기반 `image_ref`로만 배포해야 한다. `docker-compose.yml`의 `web` 서비스는 `image: ${WEB_IMAGE:-ctf-backend:local}`을 함께 선언하고 있어, 로컬 개발 시엔 `build`로 생성한 이미지를 쓰고 배포 시엔 CD가 주입한 digest 이미지를 pull해서 쓴다.

## Secret 관리

금지 사항:

- Docker build args로 시크릿 전달
- Docker 이미지 레이어에 시크릿 저장
- CI 로그 출력
- 저장소에 `.env` 커밋 (`.gitignore`, `.dockerignore`에 이미 등록됨)

권장:

- 실제 배포 환경에서는 `.env.example`을 참고해 배포 서버에서만 실제 값을 관리한다 (`SECRET_KEY`, `POSTGRES_PASSWORD` 등).
- 대회/운영 시작 전 `SECRET_KEY`, DB 비밀번호, Docker Hub 토큰, SSH 키를 교체한다.

## 실패 대응

### Gitleaks / Trivy 실패

- Actions 로그와 GitHub Security 탭(Code Scanning Alerts)에서 스캔 결과를 확인한다.
- 시크릿이 발견되면 해당 자격증명을 즉시 폐기하고 새 값으로 교체한 뒤, 히스토리에서 제거한다.
- Critical 취약점은 의존성(`requirements.txt`) 또는 베이스 이미지(`python:3.12.10-slim`)를 업데이트하여 해결한다.

### 배포 실패 / 롤백

- 이전 배포에 사용된 digest를 확인한다 (Actions 실행 로그의 `digest` 출력 참고).
- 배포 서버에서 `WEB_IMAGE=<repo>@<이전 digest>`로 다시 `docker compose pull web && docker compose up -d`를 실행하면 이전 상태로 복구된다.
- 태그 기반 롤백은 사용하지 않는다 (태그는 항상 최신 커밋을 가리키도록 재사용될 수 있어 신뢰할 수 없음).

### SSH 배포 실패

- `DEPLOY_HOST`, `DEPLOY_USERNAME`, `DEPLOY_SSH_KEY`, `DEPLOY_PORT` 시크릿 설정을 확인한다.
- 배포 서버의 `docker compose` 버전과 `/path/to/your/project` 경로(실제 배포 시 수정 필요)를 확인한다.

## 운영 전 점검

- [ ] Gitleaks / Trivy 스캔 통과 확인
- [ ] `.env`의 `DEBUG=False`, 운영용 `SECRET_KEY` 설정 확인
- [ ] `ALLOWED_HOSTS`에 실제 도메인 반영
- [ ] Docker Hub / SSH 시크릿 최신 상태 확인
- [ ] DB, Redis가 nginx를 거치지 않고 외부에 노출되지 않는지 확인 (현재 `docker-compose.yml`은 `db`, `redis` 포트를 호스트에 노출하고 있으므로, 운영 환경에서는 해당 `ports` 매핑 제거를 검토한다)
