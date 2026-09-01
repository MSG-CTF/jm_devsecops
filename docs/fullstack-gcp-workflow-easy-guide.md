# 프론트·백엔드·GCP 전체 작업 쉬운 안내서

이 문서는 MSG CTF 개발 사이트를 만들면서 나오는 개념과 작업 순서를 12세도 따라갈 수 있는 수준으로 설명한다.

각 작업은 항상 다음 다섯 가지를 확인한다.

1. 무엇인가?
2. 왜 필요한가?
3. 어떤 보안 위험이 있는가?
4. 무엇을 확인하면 성공인가?
5. 누구의 승인이나 답이 필요한가?

## 1. 우리가 만들 최종 결과

사용자는 프론트 주소 하나만 방문한다.

```text
사용자 브라우저
       │
       ▼
프론트 Cloud Run의 Nginx
       ├── /            → React 화면
       └── /api/v1/*    → 백엔드 Cloud Run
                              ├── Cloud SQL PostgreSQL
                              ├── Memorystore Redis
                              └── Scheduler
```

프론트는 이미 API 주소로 `/api/v1`을 사용한다. 브라우저가 다른 도메인의 백엔드를 직접 부르는 대신 프론트 Nginx가 뒤에서 백엔드로 전달한다.

이 구조가 필요한 이유는 다음과 같다.

- 사용자는 주소 하나만 기억하면 된다.
- 브라우저의 복잡한 CORS 설정을 넓게 열지 않아도 된다.
- 개발 화면을 보면서 실제 백엔드 API 변경을 함께 확인할 수 있다.
- 나중에 팀 도메인을 받으면 앞에 Load Balancer만 추가하기 쉽다.

## 2. 가장 중요한 단어

### 저장소, branch, PR

- 저장소는 프로그램과 변경 기록을 보관하는 공책이다.
- branch는 공책의 안전한 복사본이다.
- PR은 복사본의 변경을 `main`에 합쳐도 되는지 검사해 달라는 요청이다.

개발자는 `main`을 직접 고치지 않고 branch에서 작업한 뒤 PR을 만든다. CI가 실패하거나 리뷰가 끝나지 않으면 병합하지 않는다.

### CI

CI는 코드를 서버에 올리기 전 검사하는 시험대다.

```text
코드 검사
→ 테스트
→ Secret 검사
→ SAST/SCA
→ Docker build 검사
```

CI 통과는 배포 가능성을 확인한 것이지 실제 서버에 올라갔다는 뜻은 아니다.

### CD

CD는 CI에 합격한 프로그램을 개발 서버까지 배달하는 과정이다.

```text
이미지 만들기
→ Registry에 보관
→ DB migration
→ Cloud Run 배포
→ 접속 및 보안 검사
```

### Docker image

Docker image는 프로그램, 실행 환경과 필요한 파일을 한 상자에 포장한 것이다. 어느 서버에서 열어도 같은 방법으로 실행하게 한다.

### Artifact Registry

Artifact Registry는 합격한 Docker image를 보관하는 GCP 창고다.

현재 개발 창고는 다음과 같다.

```text
asia-northeast3-docker.pkg.dev/
project-325bdf5b-3d83-4f27-9f6/
msg-ctf-dev/
```

이 창고는 immutable tags를 사용한다. 이미 사용한 commit 태그를 다른 image로 바꿀 수 없다.

### commit SHA와 image digest

- commit SHA는 소스 코드의 고유번호다.
- image digest는 완성된 Docker 상자의 지문이다.

CD는 이름표만 보고 배포하지 않고 `image@sha256:...` 지문을 사용한다. 그래야 검사한 상자와 배포한 상자가 같다는 것을 확인할 수 있다.

### VPC, subnet, private IP

- VPC는 GCP 안의 우리 팀 전용 네트워크 울타리다.
- subnet은 울타리 안에서 IP 주소를 나눠 쓰는 구역이다.
- private IP는 인터넷에서 직접 들어갈 수 없는 내부 주소다.

현재 새 개발 네트워크는 다음과 같다.

```text
msg-dev-vpc
├── msg-dev-subnet: 10.20.0.0/24
└── private services: 10.30.0.0/16
```

새 VPC에는 인터넷 전체에 SSH, RDP, DB 포트를 여는 방화벽을 만들지 않는다.

### Cloud SQL

Cloud SQL은 Google이 관리하는 PostgreSQL 서버다. 백엔드의 사용자, 문제, 점수 같은 오래 보관할 데이터를 저장한다.

개발 DB도 private IP만 사용한다. 인터넷 전체에서 PostgreSQL 5432 포트로 접속할 수 있게 만들면 안 된다.

### Redis

Redis는 빠른 임시 저장소다. Django cache, 짧은 상태와 반복 조회 결과 등에 사용한다.

Redis 데이터는 사라져도 복구 가능한 용도로만 사용한다. 사용자 원본 데이터처럼 반드시 보존해야 하는 내용은 PostgreSQL에 저장한다.

### Secret Manager

Secret Manager는 비밀번호와 서명 키를 보관하는 금고다.

```text
django-secret-key-dev:1
jwt-secret-dev:1
postgres-password-dev:1
koth-team-token-secret-dev:1
```

GitHub에는 실제 값을 넣지 않는다. CD는 `latest` 대신 숫자 버전 `1`을 지정한다. 어떤 배포가 어느 값을 사용했는지 알 수 있기 때문이다.

### IAM, 서비스 계정, WIF

- IAM은 누가 무엇을 할 수 있는지 정하는 권한표다.
- 서비스 계정은 프로그램이 사용하는 로봇 계정이다.
- WIF는 GitHub가 장기 JSON 키 없이 짧은 시간만 GCP 권한을 받는 방식이다.

이미지 게시, Cloud Run 배포, 백엔드 실행, migration 실행 계정을 분리한다. 한 계정이 탈취돼도 모든 자원을 조작하지 못하게 하기 위해서다.

### Cloud Run service, Job, revision

- service는 HTTP 요청을 계속 받는 웹 프로그램이다.
- Job은 migration처럼 한 번 실행하고 종료하는 작업이다.
- revision은 Cloud Run에 배포된 한 버전이다.

백엔드는 service로 실행하고 `python manage.py migrate --noinput`은 Job으로 실행한다.

### migration

migration은 새 코드에 맞게 DB 표 구조를 바꾸는 작업이다.

새 코드가 새 column을 기대하는데 migration을 하지 않으면 서버가 바로 실패할 수 있다. 그래서 migration 성공 뒤에만 새 backend revision을 배포한다.

이미 적용된 migration은 application rollback만으로 자동 취소되지 않는다. column 삭제 같은 파괴적 migration은 DB backup과 별도 승인을 받아야 한다.

### Nginx proxy와 같은 출처

프론트 Nginx는 `/api/v1` 요청을 백엔드로 전달하는 안내원이다.

```text
브라우저가 /api/v1/challenges 요청
→ 프론트 Nginx가 백엔드 URL로 전달
→ 응답을 브라우저에 반환
```

현재 프론트 설정의 `backend:8000`은 Docker Compose 안에서만 작동한다. Cloud Run에서는 실제 backend URL을 시작할 때 주입할 수 있도록 프론트 수정이 필요하다.

### smoke test, DAST, SLA monitoring

- smoke test는 새 서버가 최소한 켜졌는지 한 번 확인한다.
- DAST는 실행 중인 사이트의 HTTP 응답에서 보안 문제를 찾는다.
- SLA monitoring은 배포 후에도 계속 살아 있는지 주기적으로 확인한다.

현재 CD는 `/admin/login/` smoke와 OWASP ZAP passive baseline을 실행한다. passive DAST는 응답을 관찰하지만 로그인 후 공격 요청을 적극적으로 보내지는 않는다.

### rollback

rollback은 새 revision이 실패했을 때 사용자 traffic을 직전 정상 revision으로 돌리는 것이다.

코드는 돌아가지만 migration으로 이미 바뀐 DB는 자동으로 돌아가지 않는다. 그래서 구버전과 신버전이 잠시 함께 사용할 수 있는 expand/contract migration을 사용한다.

## 3. 팀별 책임

### 백엔드 팀

- Django API와 migration 작성
- 테스트 추가
- `$PORT`로 실행되는 Dockerfile 유지
- 필요한 환경변수 이름 공유
- `/api/v1` 계약과 오류 응답 공유
- migration이 구버전과 호환되는지 설명
- `SCHEDULER_BASE_URL`과 Scheduler 인증 계약 확정

백엔드 저장소에는 중앙 workflow 전체를 복사하지 않는다. `.github/workflows/ci-cd.yml`이라는 짧은 caller만 둔다.

### 프론트 팀

- React 화면과 API 호출 코드 작성
- `/api/v1` 상대경로 유지
- Nginx가 `BACKEND_URL`을 받아 proxy하도록 수정
- API 요청·응답 형식이 백엔드 계약과 같은지 확인
- 브라우저 로그인, token 갱신과 로그아웃 흐름 시험
- 운영 전 refresh token을 `localStorage`에 계속 둘지 보안 담당자와 결정

### DevSecOps

- reusable CI/CD와 DAST 관리
- Artifact Registry, Cloud Run, DB, Redis와 네트워크 준비
- WIF와 최소 권한 IAM 관리
- Secret을 GitHub에 노출하지 않게 연결
- 배포 digest, revision과 검사 결과 기록
- 실패 시 배포 중단과 rollback 확인

### PM·담당자에게 받아야 하는 결정

- 공용 개발 도메인과 운영 도메인
- Scheduler 주소와 인증 담당자
- 비용 한도와 자원 종료 시점
- production 배포 승인자
- active DAST 범위와 테스트 데이터 삭제 규칙
- 기존 VM과 legacy Cloud Run을 언제 중지·삭제할지

## 4. 전체 작업 순서

### 0단계: 계약 확인

무엇: 프론트 URL, API 경로, 환경변수, Scheduler와 Secret 이름을 팀이 합의한다.

왜: 서로 다른 주소와 이름으로 개발하면 각각 성공해도 연결했을 때 실패한다.

보안: 실제 Secret 값은 메신저나 문서에 적지 않는다.

성공 확인: API 문서와 환경변수 목록이 한 곳에 있고 담당자가 확인한다.

승인: 백엔드, 프론트, Scheduler 담당자와 PM.

### 1단계: PR CI

무엇: 각 팀이 branch와 PR에서 테스트와 보안 검사를 실행한다.

왜: 실패한 코드를 공용 개발 사이트에 올리면 다른 팀의 시험도 막힌다.

보안: Gitleaks, Semgrep, Bandit, Trivy 결과를 확인한다. CI가 초록이어도 과거에 노출된 키는 반드시 폐기한다.

성공 확인: 필수 검사가 모두 성공하고 리뷰가 끝난다.

승인: 해당 저장소 코드 소유자.

### 2단계: GCP 기반 준비

무엇: VPC, subnet, Artifact Registry, 서비스 계정, WIF와 Secret을 만든다.

왜: 앱이 올라오기 전에 안전한 네트워크와 권한 경계를 먼저 만들어야 한다.

보안: 공개 SSH를 만들지 않고, 서비스 계정 JSON 키를 만들지 않으며, 역할을 나눈다.

성공 확인: immutable registry, user-managed key 0개, 엄격한 WIF 조건, Secret 숫자 버전과 공개 방화벽 0개를 확인한다.

승인: DevSecOps. 현재 이 단계는 완료됐다.

### 3단계: 유료 데이터 자원

무엇: private Cloud SQL과 private Redis를 만든다.

왜: 실제 백엔드를 실행하려면 영구 DB와 cache가 필요하다.

보안: public IP를 사용하지 않고 DB password와 Redis AUTH를 Secret Manager에 둔다.

성공 확인: 사설 IP에서만 연결되고 간단한 PostgreSQL query와 Redis 저장·조회가 성공한다.

승인: 비용과 backup 정책을 책임지는 사람. 생성 즉시 계속 과금되므로 반드시 먼저 확인한다.

권장 개발 사양은 다음과 같다.

```text
Cloud SQL: PostgreSQL 16, db-f1-micro, SSD 10GB, zonal, private IP
Redis: Basic 1GiB, private access, AUTH
```

### 4단계: 백엔드 image만 최초 build

무엇: 백엔드 Actions에서 `action=build`를 선택한다.

왜: 아직 Cloud Run service와 Job이 없으므로 먼저 bootstrap에 사용할 합격 image digest가 필요하다.

보안: backend `main`과 중앙의 정확한 후보 commit만 WIF 인증을 통과한다.

성공 확인: Artifact Registry에 backend commit tag와 digest가 생기고 Trivy가 통과한다.

승인: 백엔드 caller branch/PR 작업은 백엔드 팀 승인 뒤 진행한다.

### 5단계: Cloud Run 최초 bootstrap

무엇: 관리자가 위 digest로 `msg-backend-dev`와 `msg-backend-migrate-dev`를 한 번 생성한다.

왜: GitHub에 프로젝트 전체 Cloud Run Admin 권한을 주지 않고 정확한 두 자원만 맡기기 위해서다.

보안: service의 공개 여부는 사람이 한 번 승인한다. GitHub deployer에는 해당 service의 developer, 해당 Job의 developer/executor만 준다.

성공 확인: deployer가 기존 `ctf-backend`를 변경하지 못하고 새 두 자원만 갱신할 수 있다.

승인: DevSecOps와 개발 사이트 공개 책임자.

### 6단계: migration과 백엔드 수동 배포

무엇: 백엔드 Actions에서 `action=deploy`를 선택한다.

왜: 자동 배포를 켜기 전에 실제 DB·Redis·Secret·Cloud Run 연결을 사람이 관찰한다.

보안: migration 실패 시 service 배포가 시작되지 않아야 한다. Secret 값이 Actions 로그에 나타나면 즉시 중단하고 키를 교체한다.

성공 확인:

```text
migration 성공
→ Cloud Run Ready
→ /admin/login/ smoke 성공
→ ZAP 보고서 생성
→ 배포 image digest 일치
```

승인: DevSecOps와 백엔드 담당자.

### 7단계: 프론트 배포와 API 연결

무엇: 프론트 image를 배포하고 Nginx `BACKEND_URL`을 백엔드 개발 URL에 연결한다.

왜: Cloud Run에서는 Docker Compose의 `backend:8000` 이름을 사용할 수 없다.

보안: 백엔드 URL을 브라우저 코드에 직접 박아 CORS를 `*`로 열지 않는다. Nginx proxy timeout, Host/SNI와 보안 헤더를 확인한다.

성공 확인:

1. 프론트 주소에서 React 화면이 보인다.
2. 브라우저 Network 탭의 `/api/v1` 요청이 성공한다.
3. 로그인, token 갱신, 로그아웃이 동작한다.
4. 화면에서 문제·보드·랭킹 등 실제 백엔드 데이터가 보인다.
5. 브라우저 콘솔에 CORS와 mixed-content 오류가 없다.

승인: 프론트 저장소 변경은 프론트 팀 허락 뒤 branch/PR로 진행한다.

### 8단계: 자동 개발 배포

무엇: 반복 수동 시험 뒤 `ENABLE_DEV_CD=true`로 변경한다.

왜: 이후 backend `main` 변경을 공용 개발 페이지에서 자동으로 확인하기 위해서다.

보안: PR에서는 배포하지 않고 CI만 실행한다. 오직 `main`만 공용 개발환경을 갱신한다.

성공 확인: `main` 병합 한 번에 CI, build, migration, deploy, smoke와 DAST가 순서대로 실행된다.

승인: 백엔드 팀과 DevSecOps.

### 9단계: 도메인과 운영 승격

무엇: 팀 도메인과 HTTPS Load Balancer를 준비하고 검증된 같은 digest를 운영에 승격한다.

왜: 개발에서 검사하지 않은 새 image를 운영 직전에 다시 만들면 결과가 달라질 수 있다.

보안: production은 별도 프로젝트·Secret·DB·승인 Environment를 사용한다. 개발 Secret이나 데이터를 복사하지 않는다.

성공 확인: 운영 digest가 개발에서 합격한 digest와 같고 승인 기록과 rollback 절차가 있다.

승인: PM, 운영 담당자, 보안 담당자.

## 5. 평소 개발자가 코드를 바꿀 때 흐름

```text
백엔드 또는 프론트 기능 branch
→ commit/push
→ PR
→ CI와 리뷰 통과
→ main 병합
→ 개발환경 자동 배포
→ 공용 프론트 URL에서 실제 화면 확인
```

API 형식을 바꿀 때는 순서를 지킨다.

1. 백엔드가 구버전과 신버전 프론트를 잠시 모두 지원한다.
2. 백엔드를 먼저 배포한다.
3. 프론트를 새 API로 바꿔 배포한다.
4. 모든 프론트가 이동한 뒤 오래된 API를 제거한다.

한 번에 API를 없애면 먼저 배포된 쪽과 나중에 배포된 쪽이 서로 대화하지 못할 수 있다.

## 6. 현재 상태

완료:

- 중앙 백엔드 CD `v3.4.0` 후보 PR
- 개발 VPC와 subnet
- private services IP 범위
- immutable Artifact Registry
- 분리된 무키 서비스 계정
- 백엔드 `main`과 중앙 후보 commit만 허용하는 WIF
- Django, JWT, PostgreSQL, KOTH Secret version 1

아직 필요:

- Cloud SQL과 Redis 비용 승인 및 생성
- Redis AUTH를 Secret으로 연결하는 CD 보완
- 실제 `SCHEDULER_BASE_URL`
- 백엔드 caller branch/PR 승인
- 최초 backend image build와 Cloud Run bootstrap
- 프론트 Nginx 수정 승인과 프론트 CD
- 브라우저에서 실제 API 연결 시험
- 도메인과 production 정책

## 7. 작업마다 남길 기록

Secret 값은 기록하지 않고 다음 정보만 남긴다.

```text
작업 날짜
작업자
Git commit SHA
pipeline version 또는 중앙 commit
image digest
Cloud Run revision
migration execution 결과
smoke 결과
ZAP 보고서 링크
승인자
rollback 여부
```

이 기록이 있어야 문제가 생겼을 때 어떤 코드, image, DB 변경과 Secret 버전이 사용됐는지 찾을 수 있다.
