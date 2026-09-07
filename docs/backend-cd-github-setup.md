# 백엔드 GitHub CD 연결 안내

프론트·백엔드·GCP를 하나의 웹사이트로 연결하는 전체 개념과 팀별 작업 순서는 [`fullstack-gcp-workflow-easy-guide.md`](fullstack-gcp-workflow-easy-guide.md)를 먼저 읽는다.

## 결론

`v3.3.1`에서 실제 검증된 것은 백엔드 CI다. 기존 `reusable-cd.yml`은 Docker Hub에서 Cloud Run으로 바로 배포하는 초기 골격이며, migration과 개발 GCP 구조를 반영하지 못하므로 현재 백엔드에서 활성화하지 않는다.

새 CD 후보는 다음 두 reusable workflow로 역할을 나눈다.

```text
reusable-backend-build.yml
└─ 정확한 commit checkout
   → Gitleaks
   → GCP 인증 전에 Docker build
   → Trivy
   → Artifact Registry push
   → build provenance 생성·검증
   → image digest 출력

reusable-backend-deploy-dev.yml
└─ GCP 전제조건 확인
   → 같은 digest로 migration Job 배포·실행
   → migration 성공
   → 같은 digest를 traffic 없이 후보 revision으로 배포
   → 후보 tag URL에서 smoke와 여러 seed의 ZAP passive DAST
   → 검사와 보고서 업로드가 모두 성공한 뒤 100% traffic 전환
   → 전환 후 smoke 실패 때만 직전 revision으로 rollback
```

이 파일은 GitHub 쪽 배포 절차를 준비한다. 현재 결정에 따라 Cloud SQL과 Memorystore는 만들지 않는다. 사설 IP의 자체 운영 PostgreSQL·Redis, VPC, Artifact Registry, Secret Manager와 서비스 계정은 별도 인프라 작업으로 먼저 준비해야 실제 실행이 성공한다. 데이터 서버의 상세 계획은 [`self-managed-postgres-redis-deployment-plan.md`](self-managed-postgres-redis-deployment-plan.md)를 따른다.

## 기존 CD를 바로 바꾸지 않는 이유

- 아직 호출 중인 저장소가 있는지 확인하기 전에 public reusable workflow 입력을 바꾸면 기존 호출자가 깨질 수 있다.
- Docker Hub 입력을 Artifact Registry 입력으로 바꾸는 것은 호출 계약 변경이다.
- 새 파일을 추가하면 `v3.3.1` 사용자는 그대로 유지되고 백엔드만 검증된 시점에 새 파일로 이동할 수 있다.
- 기존 `reusable-cd.yml`은 deprecated 대상으로 문서화하고 다음 major 정리 때 삭제한다.

## 새 Build workflow가 보장하는 것

1. 호출한 백엔드 저장소의 정확한 40자리 commit을 checkout한다.
2. 병합 commit의 각 부모 diff까지 Gitleaks로 다시 검사한다.
3. `.dockerignore`가 `gha-creds-*.json`을 막는지 확인하고 GCP 인증 전에 이미지를 먼저 빌드한다.
4. WIF의 짧은 수명 access token으로 Artifact Registry에 로그인한다.
5. `latest` 대신 commit SHA를 image tag로 사용한다.
6. Artifact Registry immutable tag에 같은 commit image가 있으면 덮어쓰지 않고 기존 image를 pull해 다시 검사한다.
7. 수정본 유무와 관계없이 HIGH·CRITICAL 이미지 취약점을 차단한다.
8. 최종 image filesystem에도 GCP 임시 인증 파일이 없는지 확인한다.
9. 새 image의 GitHub build provenance를 만들고 저장소, source commit, builder workflow와 digest를 검증한다.
10. 실제 배포에는 tag가 아니라 `image@sha256:digest`를 출력한다.

## 새 Development Deploy workflow가 보장하는 것

1. image가 지정한 GCP project/repository/image의 digest인지 검사한다.
2. VPC·subnet과 여섯 개 Secret 숫자 version이 실제로 존재하고 활성 상태인지 확인한다.
3. migration Job과 backend service가 서로 다른 최소 권한 runtime 계정을 사용한다.
4. migration 전용 DB 사용자와 앱 전용 DB 사용자를 다르게 강제한다.
5. migration Job에는 migration DB 비밀번호만 Secret으로 주고, Django가 설정을 읽는 데 필요한 비서명용 placeholder만 일반 환경변수로 준다. 실제 Django·JWT 키는 backend에만 연결한다.
6. Direct VPC egress로 사설 IP의 자체 운영 PostgreSQL과 Redis에 접근한다.
7. 같은 image digest와 migration 사용자로 PostgreSQL `SELECT 1` 후 `python manage.py migrate --noinput`을 실행한다.
8. migration이 실패하면 workflow가 멈춰 backend deploy step이 실행되지 않는다.
9. migration 성공 후 새 revision을 `--no-traffic`과 후보 tag로 배포한다.
10. 후보 tag URL에서 `/admin/login/`을 확인한다. 고유한 가짜 사용자로 처음 10번은 401, 11번째는 429인지 확인해 PostgreSQL 사용자 조회와 실제 Redis `10/min` throttle을 함께 검사한다. Redis 장애 때 throttle이 fail-open되는 현재 백엔드 구현에서는 한 번의 401만으로 Redis 정상을 판정할 수 없기 때문이다.
11. 실제 사용자 URL이 아닌 후보 tag URL에서 최소 두 DAST seed를 검사한다.
12. ZAP 보고서 업로드까지 성공한 경우에만 새 revision으로 traffic 100%를 전환한다.
13. 전환 뒤 공개 URL smoke가 실패할 때만 직전 100% revision으로 되돌린다.
14. rollback에 성공해도 새 배포 workflow는 실패로 남겨 사람이 원인을 조사하게 한다.

Baseline 검사는 SQL injection 같은 공격 요청을 적극적으로 보내는 active scan이 아니다. 공용 개발환경의 응답을 안전하게 관찰하는 1단계 DAST다. 인증 DAST와 active API scan은 전용 계정, 삭제 가능한 데이터, 검사 범위를 합의한 뒤 별도 버전에서 추가한다.

주의: migration은 이미 DB에 적용된 뒤다. 자동 application rollback이 안전하려면 새 column/table을 먼저 추가하고 구버전과 신버전 앱이 함께 동작하는 expand/contract migration을 사용해야 한다. 파괴적인 migration은 별도 승인과 backup 없이는 배포하지 않는다.

## 백엔드 저장소에서 추가할 GitHub Repository Variables

다음 값은 비밀번호가 아니므로 `Settings → Secrets and variables → Actions → Variables`에 **Repository variable**로 등록한다.

| Variable | 개발 값/의미 |
|---|---|
| `ENABLE_DEV_CD` | 처음에는 `false`, 수동 검증 후 `true` |
| `DEV_GCP_PROJECT_ID` | `project-325bdf5b-3d83-4f27-9f6` |
| `DEV_GCP_REGION` | `asia-northeast3` |
| `DEV_ARTIFACT_REPOSITORY` | `msg-ctf-dev` |
| `DEV_GCP_WORKLOAD_IDENTITY_PROVIDER` | 새 개발 WIF provider 전체 resource 이름 |
| `DEV_GCP_PUBLISHER_SERVICE_ACCOUNT` | Artifact Registry repository 전용 게시 서비스 계정 이메일 |
| `DEV_GCP_DEPLOYER_SERVICE_ACCOUNT` | Cloud Run service·Job 전용 배포 서비스 계정 이메일 |
| `DEV_BACKEND_SERVICE` | `msg-backend-dev` |
| `DEV_MIGRATION_JOB` | `msg-backend-migrate-dev` |
| `DEV_BACKEND_RUNTIME_SERVICE_ACCOUNT` | backend runtime 서비스 계정 이메일 |
| `DEV_MIGRATION_RUNTIME_SERVICE_ACCOUNT` | migration runtime 서비스 계정 이메일 |
| `DEV_VPC_NETWORK` | `msg-dev-vpc` |
| `DEV_VPC_SUBNET` | `msg-dev-subnet` |
| `DEV_DATA_NETWORK_TAG` | 자체 운영 PostgreSQL·Redis 방화벽이 허용할 개발 workload tag. 예: `msg-backend-data-dev` |
| `DEV_DJANGO_ALLOWED_HOSTS` | scheme/path가 없는 정확한 Cloud Run host 목록 |
| `DEV_POSTGRES_DB` | `msg_backend` |
| `DEV_POSTGRES_USER` | `msg_app` |
| `DEV_MIGRATION_POSTGRES_USER` | schema 변경만 허용할 별도 사용자. 예: `msg_migrator` |
| `DEV_POSTGRES_HOST` | 자체 운영 PostgreSQL의 고정 사설 IP 또는 내부 DNS |
| `DEV_REDIS_SECRET_VERSION` | `redis-url-dev`의 숫자 version |
| `DEV_SCHEDULER_BASE_URL` | 경로가 없는 실제 개발 Scheduler origin |
| `DEV_DJANGO_SECRET_VERSION` | `django-secret-key-dev`의 숫자 version |
| `DEV_JWT_SECRET_VERSION` | `jwt-secret-dev`의 숫자 version |
| `DEV_POSTGRES_PASSWORD_SECRET_VERSION` | `postgres-password-dev`의 숫자 version |
| `DEV_MIGRATION_POSTGRES_PASSWORD_SECRET_VERSION` | `migration-postgres-password-dev`의 숫자 version |
| `DEV_KOTH_SECRET_VERSION` | `koth-team-token-secret-dev`의 숫자 version |
| `DEV_DAST_SEED_PATHS_JSON` | 실제 존재하는 path JSON 배열. 예: `["/admin/login/","/api/v1/timer"]` |

`DEV_DJANGO_ALLOWED_HOSTS` 예시는 다음과 같다. `https://`와 path를 넣지 않는다.

```text
msg-backend-dev-269174025178.asia-northeast3.run.app
```

Cloud Run이 실제로 발급한 URL에서 host를 확인한 뒤 정확한 값을 사용한다. `.run.app`처럼 지나치게 넓은 wildcard를 쓰지 않는다.

`DEV_SCHEDULER_BASE_URL` 예시는 다음과 같다.

```text
https://msg-scheduler-dev-269174025178.asia-northeast3.run.app
```

주소와 인증 계약이 확정되지 않았다면 임의 값을 넣어 배포 성공처럼 보이게 하지 않는다. Scheduler 담당자에게 실제 개발 주소를 받아야 한다.

## GitHub에 넣지 않는 Secret 값

다음 실제 값은 GitHub Repository Secret에 복사하지 않는다.

- Django secret key
- JWT signing key
- PostgreSQL password
- migration PostgreSQL password
- 인증정보를 포함한 Redis URL
- KOTH team token secret

실제 값은 GCP Secret Manager에 있고 GitHub에는 숫자 version만 기록한다.

WIF provider resource 이름과 서비스 계정 이메일도 실제 인증 key가 아니므로 Repository Variable로 저장한다. GitHub Actions는 `id-token: write`로 받은 짧은 OIDC token을 GCP WIF와 교환한다.

## GitHub `development` Environment

백엔드 저장소의 `Settings → Environments`에 `development`를 만든다.

- 허용 branch: `main`
- 처음 수동 시험 중에는 필요한 reviewer를 둘 수 있다.
- production Secret은 넣지 않는다.
- production Environment와 이름·설정을 공유하지 않는다.

called deploy workflow의 실제 job이 `environment: development`를 사용하므로 환경 보호 규칙을 통과해야 migration과 배포가 시작된다.

## 백엔드 caller 반영

최종 예제는 `docs/backend-cd-workflow-example.yml`에 있다. 백엔드의 기존 `.github/workflows/ci-cd.yml`을 통째로 지우지 말고 다음 세 job 구조로 변경한다.

```text
ci
└─ PR와 main에서 항상 실행

build
└─ CI 성공 + 수동 action=build/deploy 선택 또는 ENABLE_DEV_CD=true인 main push

deploy-development
└─ build가 출력한 exact image digest로 migration과 개발 배포
```

PR에서는 CI만 실행하므로 아직 검토 중인 코드가 공용 개발환경을 덮어쓰지 않는다.

## 안전한 연결 순서

### 1단계: 중앙 후보 검토

1. 중앙 feature branch에서 `actionlint`, shellcheck와 GitHub 자체 workflow parsing을 통과한다.
2. 중앙 PR은 반드시 최신 `main`을 대상으로 연다. 현재 GitHub 기본 branch `jm`은 최신 `main`보다 뒤에 있으므로 base를 명시한다.
3. 이 단계에서 버전 태그를 만들지 않는다.

### 2단계: 백엔드 임시 caller 검증

백엔드 팀의 branch/push 승인을 받은 뒤 최신 `main`에서 별도 branch를 만든다.

처음에는 `@v3.4.0` 대신 중앙 feature branch의 정확한 commit SHA를 사용한다.

```yaml
uses: MSG-CTF/jm_devsecops/.github/workflows/reusable-backend-build.yml@<40자리-중앙-commit-SHA>
```

이렇게 해야 아직 release하지 않은 코드를 `@main`으로 호출하지 않고 검증할 수 있다.

### 3단계: 자동 CD를 끈 상태로 백엔드 PR

```text
ENABLE_DEV_CD=false
```

caller 변경 PR에서는 기존 CI가 그대로 통과하는지 먼저 확인한다. 이 상태에서 main에 병합돼도 자동 GCP 배포는 실행되지 않는다.

### 4단계: 개발 GCP 수동 배포

1. GCP 자원과 Repository Variables를 준비한다.
2. Actions에서 `Backend CI and Development CD`를 선택한다.
3. 최초 bootstrap에서는 branch `main`, `action=build`를 선택해 image만 만든다.
4. 관리자가 그 digest로 개발 service와 migration Job을 한 번 생성하고 resource 단위 IAM을 설정한다.
5. 실제 배포 시험에서는 branch `main`, `action=deploy`를 선택한다.
6. build 결과의 commit SHA, image digest를 기록한다.
7. migration Job 성공을 확인한다.
8. backend service URL과 revision을 확인한다.
9. smoke와 rollback 시험을 확인한다.
10. Actions artifact에서 ZAP 보고서를 내려받아 Medium 이하 경고의 기준선을 정한다.

WIF가 `refs/heads/main`으로 제한되어 있으므로 다른 branch를 수동 선택하면 인증이 실패하는 것이 정상이다.

### 5단계: 중앙 release

실제 개발 GCP 수동 배포가 통과한 중앙 commit만 `main`에 병합한다. 중앙 Actions까지 성공한 뒤 `v3.4.0` 태그를 발행한다.

백엔드 caller의 임시 중앙 commit SHA를 다음 고정 버전으로 바꾼다.

```yaml
@v3.4.0
```

### 6단계: main 자동 개발 배포

수동 배포와 rollback을 반복 검증한 뒤에만 다음 값을 바꾼다.

```text
ENABLE_DEV_CD=true
```

이후 백엔드 `main` push는 다음 순서로 동작한다.

```text
CI
→ Artifact Registry build/publish
→ migration Job
→ 개발 Cloud Run
→ smoke
→ ZAP passive baseline DAST
→ 성공 또는 rollback 후 실패
```

## GCP GitHub 계정에 필요한 범위

GitHub 계정은 프로젝트 Owner가 아니어야 하고 이미지 게시와 배포 계정을 나눈다.

- publisher 계정: 지정 Artifact Registry repository의 reader/writer만 부여
- deployer 계정: 미리 생성한 `msg-backend-dev` service와 `msg-backend-migrate-dev` Job만 수정·실행
- deployer 계정: 지정 backend/migration runtime 서비스 계정 사용
- deployer 계정: 지정 VPC/subnet을 사용하는 데 필요한 권한
- Secret 값 읽기 권한은 GitHub 계정이 아니라 backend/migration runtime 서비스 계정에만 부여

프로젝트 전체 `roles/run.admin`은 부여하지 않는다. 첫 build가 Artifact Registry에 image digest를 만든 뒤 관리자가 다음 bootstrap을 한 번 수행한다.

1. 해당 digest로 `msg-backend-dev` service와 `msg-backend-migrate-dev` Job을 생성한다.
2. 개발 service를 외부에 공개할지 사람이 확인하고 service IAM에 `allUsers` invoker를 한 번만 설정한다.
3. deployer에는 해당 service와 Job 각각에 `roles/run.developer`를 부여한다.
4. 해당 Job에만 `roles/run.jobsExecutor`를 추가한다.

그 뒤 reusable deploy workflow는 공개 IAM을 변경하지 않고 기존 service와 Job의 revision만 갱신한다. 이렇게 하면 workflow가 프로젝트의 기존 `ctf-backend`나 나중에 만들 다른 서비스까지 변경하지 못한다.

WIF 조건도 저장소 이름만 확인하면 부족하다. 검증 중에는 중앙 후보의 정확한 commit SHA를, 공개 후에는 아래 두 `job_workflow_ref`와 백엔드 `main`을 함께 허용한다. deploy 쪽에는 GitHub Environment가 정확히 `development`라는 조건도 추가한다.

```text
MSG-CTF/jm_devsecops/.github/workflows/reusable-backend-build.yml@refs/tags/v3.4.0
MSG-CTF/jm_devsecops/.github/workflows/reusable-backend-deploy-dev.yml@refs/tags/v3.4.0
```

즉, `MSG-CTF/msg-backend` 안의 다른 workflow가 OIDC token을 요청해도 위 중앙 workflow를 통해 실행되지 않았다면 GCP 계정을 사용할 수 없어야 한다.

Build와 Deploy workflow 파일은 계속 분리한다. Build는 image를 만들고 Registry에 쓰는 권한만 필요하지만, Deploy는 DB migration과 Cloud Run traffic을 바꾸는 더 강한 권한 및 `development` 승인이 필요하기 때문이다. 한 파일에 합치면 작은 image build 변경도 배포 권한 문맥 안에서 실행되어 사고 범위가 커지고, build만 재실행하기도 어려워진다.

하지만 WIF provider 자체는 반드시 두 개일 필요가 없다. provider 하나를 쓴다면 다음 두 조건을 모두 만족해야 안전하다.

1. provider가 `repository=MSG-CTF/msg-backend`, `ref=refs/heads/main`, 정확한 `job_workflow_ref`를 확인하고 deploy일 때 `environment=development`도 확인한다.
2. publisher 서비스 계정의 `roles/iam.workloadIdentityUser`는 build workflow 주체에만, deployer 서비스 계정의 같은 역할은 deploy workflow 주체에만 연결한다. 저장소 전체 principalSet을 두 계정에 똑같이 연결하면 분리한 의미가 없다.

이를 쉽게 구성하려면 WIF에 `attribute.workflow_kind`를 추가해 정확한 build workflow는 `build`, 정확한 deploy workflow는 `deploy-development`로 매핑하고, 각 서비스 계정 IAM을 해당 값에 따로 묶는다. 구조를 이해하기 어려운 초기 운영 단계라면 provider도 build/deploy 두 개로 나누는 편이 설정 실수를 눈으로 찾기 쉽다. 보안 수준은 provider 개수보다 **조건과 서비스 계정 impersonation binding이 분리됐는지**로 결정된다.

현재 기존 `github-actions-deployer`의 `roles/run.admin`과 repo-only WIF 조건은 새 개발 계정이 정상 작동한 뒤 축소하거나 제거한다.

## 완료 판정

GitHub 파일이 존재하는 것만으로 CD 완료가 아니다. 다음 조건이 모두 필요하다.

- 중앙 `actionlint` 통과
- 백엔드 caller CI 통과
- Artifact Registry에 commit SHA image와 digest 존재
- migration Job이 빈 개발 DB와 기존 개발 DB에서 성공
- migration 실패를 의도했을 때 backend deploy가 실행되지 않음
- Cloud Run revision이 build가 출력한 digest 사용
- `/admin/login/` smoke 성공
- ZAP 보고서 artifact 생성, High 이상 발견 시 workflow 실패
- 후보 검사 전 기존 traffic이 그대로 유지됨
- 모든 검사 뒤 새 revision으로 100% traffic이 전환됨
- 전환 후 공개 URL 실패에서 직전 revision rollback 성공
- GitHub log에 실제 Secret 값이 없음
- 중앙 검증 commit과 `v3.4.0` 태그가 일치

현재 VPC, Registry, WIF, 서비스 계정과 기본 Secret은 준비됐고 Redis URL Secret 연결은 CD 후보에 반영했다. 자체 운영 PostgreSQL·Redis, 실제 `redis-url-dev` Secret과 Cloud Run bootstrap은 남아 있다. 실제 수동 배포 검증 전에는 `v3.4.0`을 발행하지 않는다.
