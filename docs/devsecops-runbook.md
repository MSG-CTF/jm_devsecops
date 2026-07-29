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
6. `google-github-actions/auth`로 Workload Identity Federation을 통해 GCP에 인증한다 (서비스 계정 장기 키 없음, GitHub OIDC 토큰으로 `github-actions-deployer` 서비스 계정을 impersonate).
7. `google-github-actions/deploy-cloudrun`으로 `<Docker Hub 이미지>@sha256:<digest>`를 Cloud Run 서비스 `ctf-backend`(리전 `asia-northeast3`)에 배포한다.

Runtime은 mutable tag(`latest`)가 아닌 digest 기반 `image_ref`로만 배포해야 한다. 로컬 개발(`docker-compose.yml`)에서는 `build`로 생성한 이미지를 쓰고, 실제 배포는 CD가 Docker Hub에서 digest로 고정한 이미지를 Cloud Run이 직접 pull한다 (별도 배포 서버/SSH 없음).

## Secret 관리

금지 사항:

- Docker build args로 시크릿 전달
- Docker 이미지 레이어에 시크릿 저장
- CI 로그 출력
- 저장소에 `.env` 커밋 (`.gitignore`, `.dockerignore`에 이미 등록됨)

권장:

- 실제 배포 환경에서는 `.env.example`을 참고해 실제 값을 관리한다 (`SECRET_KEY`, `POSTGRES_PASSWORD` 등). Cloud Run에는 GitHub Secret이나 GCP Secret Manager를 통해 환경변수로 주입한다 (현재 `config/settings.py`의 `SECRET_KEY` 기본값은 테스트용 fallback이며 운영 반영 전 교체 필요).
- 대회/운영 시작 전 `SECRET_KEY`, DB 비밀번호, Docker Hub 토큰을 교체한다.

## 실패 대응

### Gitleaks / Trivy 실패

- Actions 로그와 GitHub Security 탭(Code Scanning Alerts)에서 스캔 결과를 확인한다.
- 시크릿이 발견되면 해당 자격증명을 즉시 폐기하고 새 값으로 교체한 뒤, 히스토리에서 제거한다.
- Critical 취약점은 의존성(`requirements.txt`) 또는 베이스 이미지(`python:3.12.10-slim`)를 업데이트하여 해결한다.

### 배포 실패 / 롤백

- 이전 배포에 사용된 digest를 확인한다 (Actions 실행 로그의 `digest` 출력, 또는 `gcloud run revisions list --service ctf-backend --region asia-northeast3`).
- `gcloud run services update-traffic ctf-backend --region asia-northeast3 --to-revisions <이전 revision>=100`으로 이전 revision에 트래픽을 되돌리면 즉시 롤백된다.
- 태그 기반 롤백은 사용하지 않는다 (태그는 항상 최신 커밋을 가리키도록 재사용될 수 있어 신뢰할 수 없음).

### Cloud Run 배포 실패

- `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT` 시크릿 설정을 확인한다.
- 서비스 계정(`github-actions-deployer`)에 `roles/run.admin`, `roles/iam.serviceAccountUser` 권한이 있는지, Workload Identity Pool의 `attribute-condition`이 현재 저장소(`MSG-CTF/jm_devsecops`)를 가리키는지 확인한다.
- 컨테이너가 뜨자마자 죽는 경우 `gcloud run services logs read ctf-backend --region asia-northeast3`로 실제 애플리케이션 로그를 확인한다 (Cloud Run은 `$PORT`로 리슨 포트를 지정하므로 Dockerfile의 `CMD`가 이를 반영하는지도 함께 확인).

## 운영 전 점검

- [ ] Gitleaks / Trivy 스캔 통과 확인
- [ ] `.env`의 `DEBUG=False`, 운영용 `SECRET_KEY` 설정 확인
- [ ] `ALLOWED_HOSTS`에 실제 도메인 반영
- [ ] Docker Hub 시크릿, GCP Workload Identity Federation 설정 최신 상태 확인
- [ ] DB, Redis가 nginx를 거치지 않고 외부에 노출되지 않는지 확인 (`docker-compose.yml`의 `db`, `redis` 포트는 `127.0.0.1`에만 바인딩되어 있어 로컬 툴 접속용으로만 열려 있고 LAN/외부에는 노출되지 않음)
