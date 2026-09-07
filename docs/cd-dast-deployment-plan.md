# MSG CTF CD·DAST·통합 배포 작업 계획서

용어와 전체 흐름을 더 쉽게 설명한 문서는 [`fullstack-gcp-workflow-easy-guide.md`](fullstack-gcp-workflow-easy-guide.md)다.

> 2026-09-03 데이터 계층 결정 변경: 개발 PostgreSQL과 Redis는 Cloud SQL·Memorystore가 아니라 팀이 직접 운영한다. 이 문서에 남아 있는 관리형 서비스 예시보다 [`self-managed-postgres-redis-deployment-plan.md`](self-managed-postgres-redis-deployment-plan.md)의 서버 배치, 방화벽, Secret, 백업·복구 기준을 우선 적용한다.

작성 기준일: 2026-08-26

대상: `MSG-CTF/jm_devsecops`, `MSG-CTF/msg-backend`, 프론트엔드 저장소, GCP

답변 반영일: 2026-08-27

> 2026-08-31 진행 상태: 백엔드용 Artifact Registry build, migration Job, 개발 Cloud Run 배포, smoke, rollback, ZAP passive baseline workflow는 `v3.4.0` 후보로 구현했다. 아직 GCP 자원에서 실제 실행하지 않았으므로 완료·release 상태가 아니다. 프론트 CD, 한 주소 연결, 인증/active DAST와 production 승격도 이 후보 범위에 포함되지 않는다.

> 2026-09-01 보안 보완: GitHub deployer에는 프로젝트 전체 Cloud Run Admin을 주지 않는다. 첫 image가 생성된 뒤 관리자가 개발 service/Job을 bootstrap하고 공개 IAM을 명시적으로 승인한 다음, deployer에는 그 두 resource의 수정·실행 권한만 부여한다.

## 0. 가장 쉬운 요약

현재 CI는 코드가 안전하게 합쳐질 수 있는지 검사하는 **출고 전 검사대**다. 이제 만들 CD는 검사에 합격한 프로그램을 실제 개발 사이트에 올리는 **배달 시스템**이다. DAST는 올려진 사이트를 밖에서 사용해 보며 보안 문제를 찾는 **모의 점검자**다.

완성 목표는 다음과 같다.

1. 개발자가 백엔드 또는 프론트엔드 PR을 만들면 CI만 실행한다. PR 코드는 실제 공용 서버에 자동 배포하지 않는다.
2. PR이 `main`에 병합되면 합격한 commit으로 컨테이너 이미지를 딱 한 번 만든다.
3. 이미지를 GCP Artifact Registry에 commit SHA 태그로 보관하고, 실제 배포에는 바뀌지 않는 digest를 사용한다.
4. 같은 백엔드 이미지로 DB migration 작업을 먼저 실행한다.
5. 백엔드와 프론트엔드를 공용 개발환경에 자동 배포한다.
6. `https://dev.<팀 도메인>`에서 프론트 화면과 `/api/v1/*` 백엔드 API를 같은 주소로 연결한다.
7. 배포 직후 smoke test와 ZAP DAST를 실행한다.
8. 개발환경 검사가 모두 통과한 **동일한 이미지 digest**만 사람의 승인을 받아 운영환경으로 보낸다. 운영 직전에 다시 빌드하지 않는다.
9. 문제가 생기면 Cloud Run 트래픽을 직전 revision으로 되돌린다.

핵심 흐름은 아래와 같다.

```text
기능 브랜치 → PR → CI 통과 → main 병합
                              │
                              ▼
                    이미지 1회 빌드·검사
                              │
                              ▼
                 Artifact Registry에 digest 보관
                              │
                   ┌──────────┴──────────┐
                   ▼                     ▼
            백엔드 migration Job    프론트 Cloud Run 배포
                   │                     │
                   ▼                     │
             백엔드 Cloud Run 배포       │
                   └──────────┬──────────┘
                              ▼
                   한 주소의 공용 개발 사이트
                              │
                    smoke test + ZAP DAST
                              │
                  ┌───────────┴───────────┐
                  ▼                       ▼
             실패: 승격 중단          성공: 승인 대기
                                              │
                                              ▼
                                  같은 digest를 운영에 승격
```

## 1. 지금 상태를 기준으로 내린 판단

### 이미 준비된 것

- 중앙 reusable CI `v3.3.1`이 있다.
- 백엔드는 중앙 CI를 `@v3.3.1`로 호출하도록 준비되어 있다.
- 중앙 저장소에 `reusable-cd.yml` 골격이 있다.
- GCP 프로젝트 `project-325bdf5b-3d83-4f27-9f6`에 Workload Identity Pool과 GitHub 배포용 서비스 계정이 있다.
- GCP `asia-northeast3`에 `ctf-backend` Cloud Run 서비스가 하나 있다.
- 프론트엔드는 React/Vite로 보이며 API 주소를 상대경로 `/api/v1`로 사용한다.

### 지금 바로 CD를 켜면 안 되는 이유

현재 중앙 CD 골격에는 이미지 빌드·Trivy·Docker Hub push·Cloud Run 배포가 있지만 다음 항목이 없다.

- Cloud SQL 인스턴스와 실제 연결 설정
- Redis 인스턴스와 VPC 연결
- `python manage.py migrate`를 안전하게 실행할 Cloud Run Job
- 백엔드가 요구하는 `SCHEDULER_BASE_URL`
- 프론트엔드 배포
- 프론트와 백엔드를 같은 주소로 묶는 HTTPS Load Balancer 경로 규칙
- DAST
- 새 revision으로 트래픽을 보내기 전 검사와 실패 시 자동 중단
- 운영 승인, 단계적 트래픽 전환, rollback 기록

현재 GCP의 `ctf-backend`는 예전에 만든 `ipbuktiger/ctf-backend` 이미지를 사용하고 기본 Compute 서비스 계정으로 실행된다. 환경변수 이름도 현재 백엔드가 요구하는 `DJANGO_SECRET_KEY`, `JWT_SECRET`, PostgreSQL, Redis 설정과 맞지 않는다. 따라서 이것을 곧바로 현재 백엔드 운영 서비스라고 가정하면 안 된다. 먼저 소유자와 용도를 확인하고, 새 개발 서비스는 `msg-backend-dev`처럼 이름을 분리한다.

또한 현재 프로젝트에는 확인 시점 기준으로 Cloud SQL, Memorystore Redis, Artifact Registry Docker 저장소가 준비되어 있지 않다. `django-secret-key`만 있고 `jwt-secret`, `postgres-password`는 없다. 즉, 지금은 **CI 완료 단계**이고 **실제 애플리케이션 CD 준비 전 단계**다.

### 2026-08-27 팀 답변으로 확정된 범위

| 항목 | 현재 결정 | 작업에 미치는 영향 |
|---|---|---|
| 프론트 저장소 | `MSG-CTF/front-team` 확정 | 이 저장소를 실제 프론트 CI/CD와 통합 테스트 대상으로 사용한다. |
| 개발 도메인 | 아직 없음 | 처음에는 Cloud Run이 제공하는 임시 `run.app` 주소로 개발 사이트를 연다. 도메인을 받은 뒤 Load Balancer 구조로 전환한다. |
| 개발 GCP | DevSecOps의 현재 GCP 프로젝트 | 개발용 자원만 만들고 운영 데이터는 넣지 않는다. |
| 운영 GCP | 나중에 Resource Broker가 계정/프로젝트 할당 | 인프라 코드에서 project ID를 변수로 만들어 개발 구성을 새 운영 계정에 다시 만들 수 있게 한다. |
| 사용자 목표 | 백엔드·프론트 변경사항을 실제 페이지로 확인 | 첫 목표를 production 배포가 아니라 **공용 개발 사이트 배포**로 제한한다. |
| scheduler | 주소와 인증 방식 미정 | 인스턴스 생성 기능은 아직 완성 합격 기준에 넣지 않는다. 백엔드 담당자 답을 받기 전 임의 URL이나 Secret을 만들지 않는다. |
| refresh token | 저장 방식 미정 | 개발에서는 현재 동작을 검증하되 위험을 기록한다. production 전에는 반드시 결정한다. |
| active DAST 데이터 | 범위 미정 | 데이터 변경이 없는 Baseline Scan만 먼저 연결한다. active API Scan은 격리 환경 합의 뒤 진행한다. |
| production 승인자 | 사용자 본인이 아님, 담당자 미정 | 개발 자동배포에는 영향이 없지만 production 배포 job은 만들더라도 활성화하지 않는다. |
| DB backup 정책 | 미정 | 개발 DB에는 기본 자동 backup을 켜되, production 보존기간과 복구 목표는 운영 담당자가 결정한다. |
| SLA 알림 담당자 | 미정 | 개발 단계에서는 로그와 smoke 결과를 남긴다. production 경보와 호출 정책은 활성화하지 않는다. |

이 결정에 따라 지금 진행 가능한 범위는 다음과 같다.

1. 로컬에서 실제 프론트와 백엔드를 연결한다.
2. DevSecOps GCP에 개발 전용 DB, Redis, registry와 Cloud Run 서비스를 만든다.
3. 임시 Cloud Run 주소로 팀이 페이지를 볼 수 있게 한다.
4. 배포 후 smoke test와 passive DAST를 실행한다.
5. 위 구성을 Terraform/OpenTofu 변수로 만들어 나중에 Resource Broker가 주는 운영 GCP로 옮길 수 있게 한다.

지금 진행하면 안 되는 범위는 production 공개, production active DAST, production DB 정책 확정, 실제 사용자 데이터 입력이다.

## 2. 목표 구조

### 저장소별 책임

한 저장소에 모든 코드를 복사하지 않는다. 각 팀이 자기 프로그램을 관리하고 중앙 저장소는 공통 검사·배포 방법만 관리한다.

| 저장소 | 보관할 내용 | 보관하지 않을 내용 |
|---|---|---|
| `jm_devsecops` | reusable CI, 백엔드 CD, 프론트 CD, DAST, 버전 태그, 운영 문서 | 백엔드·프론트 실제 앱 코드, 실제 Secret |
| `msg-backend` | Django 코드, migration, Dockerfile, 짧은 caller workflow, OpenAPI 문서 | 중앙 reusable 파일의 복사본, GCP 키 파일 |
| 프론트 저장소 | React 코드, Dockerfile, 짧은 caller workflow, 보안 헤더 설정 | 백엔드 코드, 실제 API Secret |
| 배포/인프라 저장소 | Terraform/OpenTofu, 환경별 서비스 이름과 이미지 digest 기록 | 평문 비밀번호·JWT 키 |

활성 프론트 저장소는 팀 답변에 따라 `MSG-CTF/front-team`으로 확정했다.

### GCP 요청 흐름

권장 방식은 프론트와 API를 서로 다른 출처로 공개하는 대신, 하나의 개발 도메인을 사용하는 것이다.

```text
사용자 브라우저
    │ https://dev.msgctf.kr
    ▼
External HTTPS Load Balancer + Google 관리 인증서
    ├── /api/*, /admin/*, /static/* → msg-backend-dev (Cloud Run)
    │                                      │
    │                          ┌───────────┴───────────┐
    │                          ▼                       ▼
    │                 Cloud SQL PostgreSQL     Memorystore Redis
    │                      private IP              private IP
    └── 그 밖의 모든 주소 → msg-frontend-dev (Cloud Run)
```

이 방식을 권장하는 근거는 다음과 같다.

- 프론트가 이미 `/api/v1` 상대경로를 쓰므로 코드 변경이 작다.
- 브라우저 관점에서 프론트와 API의 출처가 같아져 복잡한 CORS 허용 목록이 필요하지 않다.
- Django `ALLOWED_HOSTS`, CSRF trusted origin, 쿠키 범위를 한 도메인 기준으로 관리하기 쉽다.
- Cloud Run의 기본 `run.app` 주소를 직접 공개하지 않고 Load Balancer를 통해서만 들어오게 제한할 수 있다.
- URL map은 경로에 따라 서로 다른 Cloud Run backend service로 요청을 보낼 수 있다.

도메인을 받은 뒤에는 `dev.msgctf.kr` 같은 주소 하나를 공용 개발환경으로 사용한다. PR마다 별도 preview 환경을 만드는 기능은 DB 격리와 비용 관리가 필요하므로 2차 작업으로 미룬다.

다만 현재는 개발 도메인을 아직 받지 않았다. 따라서 첫 배포에서는 아래 임시 구조를 사용한다.

```text
사용자 브라우저
    │ https://msg-frontend-dev-....run.app
    ▼
msg-frontend-dev (Cloud Run의 Nginx)
    ├── 화면 파일 제공
    └── /api/v1/* 요청을 backend 임시 URL로 proxy
                         │
                         ▼
                 msg-backend-dev (Cloud Run)
                         │
                  ┌──────┴──────┐
                  ▼             ▼
              개발 DB       개발 Redis
```

이 임시 구조에서도 브라우저는 프론트 주소에만 요청하므로 프론트 코드의 상대경로 `/api/v1`을 유지할 수 있다. backend 임시 URL은 개발 중에만 공개하고 실제 인증·권한 검사는 그대로 적용한다. 도메인을 받으면 External HTTPS Load Balancer를 앞에 두고 backend의 직접 `run.app` 접근을 제한한다.

## 3. 버전 관리 규칙

여기에는 서로 다른 세 종류의 버전이 있다. 섞으면 안 된다.

### 3.1 파이프라인 버전

- 현재 중앙 CI 버전: `v3.3.1`
- CD와 DAST를 기존 입력을 깨지 않도록 새 reusable workflow로 추가하면: `v3.4.0`
- 기존 `reusable-cd.yml`의 필수 입력을 없애거나 Docker Hub 방식을 강제로 Artifact Registry 방식으로 바꾸는 등 호출 규칙이 깨지면: `v4.0.0`

이번 계획은 기존 `v3.3.1` 사용자를 깨지 않도록 새 workflow를 추가하는 방식이므로 우선 `v3.4.0`을 권장한다. 기존 `reusable-cd.yml`은 바로 삭제하지 않고 deprecated 표시를 한 뒤 실제 호출자가 없는지 확인하고 다음 major 버전에서 정리한다.

백엔드와 프론트 caller는 시험 중에 `@main`을 사용하지 않는다. 중앙 PR의 정확한 commit SHA로 시험한 뒤, 중앙 Actions가 통과하고 `v3.4.0` 태그가 발행되면 `@v3.4.0`으로 바꾼다.

### 3.2 애플리케이션 버전

- 백엔드 릴리스 예: `backend-v0.1.0`
- 프론트 릴리스 예: `frontend-v0.1.0`

앱 태그는 사람이 릴리스를 이해하기 위한 이름이다. 실제 배포 대상은 컨테이너 digest다.

백엔드 저장소의 짧은 caller workflow에는 중앙 파이프라인 버전 `@v3.4.0`이 기록된다. 그래서 중앙 파이프라인을 새 버전으로 올려도 백엔드는 자동으로 알 수 없는 코드로 바뀌지 않고, 백엔드 PR에서 호출 버전을 검토해 올릴 수 있다.

다만 프론트와 백엔드가 함께 어떤 조합으로 배포됐는지까지 백엔드 저장소 하나에 기록하면 프론트 팀이 백엔드 저장소를 매번 수정해야 한다. 공동 배포 버전은 별도의 배포 manifest에 다음처럼 기록하는 것이 책임 구분에 맞다.

```yaml
environment: development
pipeline: v3.4.0
backend:
  commit: <40자리 SHA>
  image_digest: sha256:<digest>
frontend:
  commit: <40자리 SHA>
  image_digest: sha256:<digest>
```

이 manifest에는 Secret을 넣지 않는다. 개발환경은 workflow가 성공할 때 자동으로 갱신하고, 운영환경 manifest 변경은 승인 PR로 관리한다.

### 3.3 컨테이너 버전

이미지를 다음처럼 보관한다.

```text
asia-northeast3-docker.pkg.dev/<project>/msg-ctf/backend:<40자리-commit-sha>
asia-northeast3-docker.pkg.dev/<project>/msg-ctf/backend@sha256:<digest>
```

- `latest`는 사용하지 않는다.
- commit SHA 태그는 다시 덮어쓰지 않는다.
- Artifact Registry의 immutable tag 설정을 켠다.
- 개발에서 검증한 digest와 운영에 배포하는 digest가 같아야 한다.

## 4. 최종 파이프라인 설계

### PR 단계: 배포하지 않고 검사만 한다

백엔드 PR:

1. 기존 reusable CI `v3.3.1` 이상의 검사 실행
2. Django 테스트, migration 누락 검사, PostgreSQL·Redis 연결 검사
3. Semgrep, Bandit, Gitleaks, Trivy/SCA
4. Docker 이미지 빌드·비루트 실행·smoke test
5. 합격한 PR만 `main` 병합 가능

프론트 PR:

1. `npm ci`
2. lint, unit test, production build
3. `npm audit` 또는 OSV/Trivy 기반 SCA
4. Semgrep과 Gitleaks
5. 프론트 Docker 이미지 빌드·Trivy 검사
6. 브라우저 E2E 기본 동작 검사

PR 단계에서 공용 개발환경에 자동 배포하지 않는 이유는 여러 PR이 한 환경을 덮어써서 다른 사람의 테스트를 망칠 수 있기 때문이다.

### `main` 병합 단계: 공용 개발환경 자동 배포

백엔드 기준 순서는 다음과 같다.

1. CI를 다시 확인한다.
2. 이미지를 한 번만 빌드한다.
3. 컨테이너와 소스 보안 검사를 통과시킨다.
4. GCP OIDC/WIF로 짧은 수명의 자격증명을 받는다. JSON 서비스 계정 키는 저장하지 않는다.
5. Artifact Registry에 commit SHA 태그로 push한다.
6. registry가 돌려준 digest를 workflow output과 배포 기록에 남긴다.
7. 동일 digest로 `msg-backend-migrate-dev` Cloud Run Job을 갱신한다.
8. `python manage.py migrate --noinput`을 실행하고 완료될 때까지 기다린다.
9. migration이 성공한 경우에만 `msg-backend-dev` 새 revision을 만든다.
10. 배포 직후 `/admin/login/`과 대표 읽기 전용 API에 smoke test를 보낸다.
11. 프론트가 배포되었으면 실제 개발 도메인에서 브라우저 E2E와 DAST를 실행한다.

프론트도 `main` 병합 시 별도 이미지로 빌드·검사·배포한다. 백엔드와 프론트가 반드시 동시에 병합될 필요는 없다. 대신 API 계약이 바뀔 때는 아래 호환성 규칙을 지킨다.

- 먼저 백엔드가 구버전과 신버전 프론트를 모두 지원하도록 배포한다.
- 그 다음 프론트를 배포한다.
- 마지막에 더 이상 쓰지 않는 구 API를 별도 릴리스에서 제거한다.

### 운영 승격 단계

처음부터 운영 자동배포를 켜지 않는다. 개발환경과 DAST가 안정된 뒤 다음 방식으로 켠다.

1. 배포 담당자가 `workflow_dispatch`에서 검증된 backend/frontend digest를 선택한다.
2. `production` GitHub Environment가 PM 또는 지정 운영자의 승인을 기다린다.
3. 승인을 받은 job만 production 환경 Secret과 WIF 권한을 얻는다.
4. production DB backup 상태를 확인한다.
5. 같은 backend digest로 production migration Job을 실행한다.
6. 새 Cloud Run revision은 처음에는 트래픽 0% 또는 revision tag로 생성한다.
7. 내부 smoke test가 통과하면 5%→25%→100%처럼 트래픽을 단계적으로 보낸다.
8. 오류율과 응답시간이 기준을 넘으면 즉시 직전 revision 100%로 되돌린다.

운영 promotion 과정에서 이미지를 다시 빌드하면 개발에서 검사하지 않은 다른 결과물이 생길 수 있으므로 금지한다.

## 5. DAST 설계

### DAST가 하는 일

SAST가 소스 코드를 읽어 위험한 작성법을 찾는 검사라면, DAST는 실제로 실행된 웹사이트에 HTTP 요청을 보내 다음과 같은 문제를 확인한다.

- 보안 헤더가 빠졌는가
- 쿠키의 `Secure`, `HttpOnly`, `SameSite` 속성이 잘못되었는가
- 서버 정보가 너무 많이 노출되는가
- 입력값을 비정상적으로 보냈을 때 오류나 보안 문제가 생기는가
- 인증 없이 보호된 API에 들어갈 수 있는가
- OpenAPI에 적힌 API와 실제 동작이 일치하는가

DAST는 소스의 모든 길을 알 수 없고 비즈니스 권한 오류도 완벽하게 찾지 못한다. 따라서 기존 SAST·SCA·테스트를 대체하지 않고 그 뒤에 추가한다.

### 사용할 도구와 두 단계 운영

OWASP ZAP을 권장한다. 무료 오픈소스이고 Docker 기반 자동화가 가능하며 웹 화면용 Baseline Scan과 OpenAPI 기반 API Scan을 분리해 운영할 수 있기 때문이다.

#### 1단계: ZAP Baseline Scan

- 대상: `https://dev.msgctf.kr`
- 시점: 개발환경 배포 후 매번
- 방식: spider와 passive scan
- 특징: 공격 요청을 수행하지 않아 공용 개발환경에 비교적 안전하다.
- 차단 기준: 팀이 FAIL로 정한 보안 헤더·쿠키·명백한 노출 항목
- 결과: HTML/JSON 보고서를 Actions artifact로 보관

#### 2단계: ZAP API Scan

- 대상: 백엔드 OpenAPI 문서와 격리된 staging API
- 시점: 야간 schedule 또는 수동 실행, 릴리스 후보 확정 전
- 방식: OpenAPI에 정의된 API에 active request를 보냄
- 조건: 테스트 전용 계정과 삭제 가능한 테스트 데이터 사용
- 금지: 운영 데이터, 실제 사용자 계정, CTF 문제 인스턴스에 active scan

여기서 staging은 공용 개발 DB를 그대로 쓰는 이름만 다른 서비스가 아니다. active scan이 데이터를 만들거나 바꿀 수 있으므로 별도의 Cloud Run 서비스, 별도 DB/database 또는 확실히 격리된 schema, 별도 Redis namespace와 테스트 계정이 필요하다. 이 격리가 준비되기 전에는 Baseline Scan만 실행한다.

백엔드에 신뢰할 수 있는 OpenAPI가 아직 없다면 먼저 `drf-spectacular` 같은 도구로 schema를 만들고 CI에서 schema 생성 오류를 검사한다. 문서가 없는 상태에서 API active scan부터 하면 검사 범위가 빠지고 파괴적인 API를 잘못 호출할 수 있다.

### DAST 오탐 처리 규칙

발견을 없애기 위해 전체 규칙을 끄지 않는다. 예외에는 다음 정보를 반드시 남긴다.

- ZAP rule ID
- 정확한 URL 또는 endpoint
- 실제 취약점이 아닌 근거
- 승인자
- 만료일
- 추후 수정 issue

새 High/Critical 발견은 배포 승격을 막는다. Medium은 첫 도입 1~2주 동안 보고만 모아 기준선을 만든 뒤, 팀이 고칠 수 있는 규칙부터 차단으로 바꾼다. 이미 알고 있는 문제를 무기한 IGNORE하지 않고 `in-progress` 목록과 만료일로 관리한다.

### 인증 DAST

로그인이 필요한 API를 검사할 때는 GitHub에 장기 JWT 문자열을 저장하지 않는다.

1. staging 전용 DAST 계정을 만든다.
2. workflow 시작 시 로그인 API를 호출해 짧은 수명의 access token을 새로 받는다.
3. 최소 권한 계정과 별도 테스트 데이터를 쓴다.
4. 로그에서 토큰을 마스킹한다.
5. scan 종료 후 계정의 세션과 생성 데이터를 정리한다.

현재 프론트가 JWT를 `localStorage`에 저장한다면 XSS가 발생했을 때 토큰을 읽힐 수 있다. 개발단계에서는 위험을 기록하고 임시 사용이 가능하지만, 운영 전에는 refresh token을 `HttpOnly + Secure + SameSite` 쿠키로 옮길지 백엔드·프론트가 함께 결정해야 한다. ZAP을 붙이는 것만으로 이 구조적 위험은 사라지지 않는다.

## 6. 백엔드에서 해야 할 변경

백엔드 팀이 담당할 작업은 다음과 같다.

1. PR #25가 최종 승인·병합된 뒤 `main` CI가 `v3.3.1`로 통과하는지 확인한다.
2. 운영용 DB 연결 방식을 확정한다. private IP를 쓰면 Cloud Run Direct VPC egress와 PostgreSQL host를 맞춘다.
3. `SCHEDULER_BASE_URL`과 scheduler 인증 방식·timeout을 배포 입력으로 명시한다.
4. OpenAPI schema를 추가하고 API 문서 생성이 깨지면 CI가 실패하도록 한다.
5. `/admin/login/` 외에 DB를 변경하지 않는 대표 API smoke test를 정한다.
6. migration이 이전 앱과 새 앱이 동시에 실행되는 동안에도 안전하도록 expand/contract 방식을 사용한다.
7. static 파일 정책을 정한다. 가장 간단한 초기 방식은 WhiteNoise와 `collectstatic`이고, 규모가 커지면 Cloud Storage/CDN으로 옮긴다.
8. `CSRF_TRUSTED_ORIGINS=https://dev.<도메인>`과 production 도메인을 환경변수로 관리한다.
9. Cloud Run proxy 환경에서 HTTPS 설정과 `ALLOWED_HOSTS`가 실제 도메인에 맞는지 확인한다.
10. DAST용 최소 권한 staging 계정과 삭제 가능한 seed data 생성 명령을 제공한다.

DB migration은 웹 서비스 시작 명령에 넣지 않는다. Cloud Run 인스턴스가 동시에 여러 개 시작하면 migration을 여러 번 경쟁 실행할 수 있기 때문이다. 별도 Cloud Run Job이 한 번 실행하고 성공한 뒤 서비스 트래픽을 바꾸는 방식이 더 명확하다.

## 7. 프론트엔드에서 해야 할 변경

1. 실제 대상 저장소가 `MSG-CTF/front-team`인지 확인한다.
2. mock backend만 사용하는 현재 Compose와 별도로 실제 `msg-backend` 연결 통합환경을 만든다.
3. 운영 API는 상대경로 `/api/v1`을 유지한다. 같은 도메인 Load Balancer를 사용할 때 가장 단순하다.
4. refresh token 갱신 TODO와 401 처리 흐름을 완성한다.
5. Vite build-time 환경변수에 Secret을 넣지 않는다. 브라우저 번들에 들어간 값은 누구나 볼 수 있다.
6. Node와 Nginx base image를 digest로 고정하고, 가능하면 비루트 Nginx runtime을 사용한다.
7. CSP는 처음에는 `Report-Only`로 관찰한 뒤 필요한 origin만 허용하고 enforcing으로 전환한다.
8. `X-Content-Type-Options`, `Referrer-Policy`, frame 제한 등의 헤더를 Nginx 또는 Load Balancer에서 설정한다.
9. 로그인→문제 목록→문제 상세→인스턴스 생성 요청 같은 핵심 브라우저 E2E 경로를 작성한다.
10. 프론트용 reusable CI/CD를 중앙 `v3.4.0`에 추가하고 caller는 고정 태그로 호출한다.

## 8. GCP 기반 작업

가능하면 개발과 운영을 서로 다른 GCP 프로젝트로 분리한다. 권한 실수나 삭제가 운영까지 번지는 것을 막을 수 있기 때문이다. 예산상 한 프로젝트만 쓴다면 서비스, DB, Secret, 서비스 계정을 환경별로 반드시 분리한다.

### 만들어야 할 자원

개발환경 최소 자원:

- Artifact Registry Docker repository: `msg-ctf`, immutable tag 활성화
- Cloud SQL for PostgreSQL: `msg-postgres-dev`
- Memorystore Redis: `msg-redis-dev`
- VPC/subnet 또는 사용할 기존 VPC 확정
- backend Cloud Run service: `msg-backend-dev`
- frontend Cloud Run service: `msg-frontend-dev`
- migration Cloud Run Job: `msg-backend-migrate-dev`
- External HTTPS Load Balancer, serverless NEG, URL map
- 관리형 TLS 인증서와 `dev.msgctf.kr` DNS
- Secret Manager: `django-secret-key-dev`, `jwt-secret-dev`, `postgres-password-dev`
- runtime 서비스 계정 3개: backend, frontend, migration
- GitHub deploy 서비스 계정과 제한된 WIF provider

### 서비스 계정 권한

- GitHub deploy 계정: 필요한 Artifact Registry push, Cloud Run service/job 배포, 지정 runtime 서비스 계정 사용 권한만 부여
- backend runtime 계정: 필요한 Secret 세 개 읽기, Cloud SQL 연결 등 실행 중 실제 필요한 권한만 부여
- migration runtime 계정: DB migration에 필요한 Secret과 Cloud SQL 연결 권한만 부여
- frontend runtime 계정: 보통 Secret Manager 권한이 필요 없다.

기본 Compute 서비스 계정을 runtime에 계속 쓰지 않는다. 다른 시스템 권한이 함께 붙을 가능성이 있어 최소 권한 원칙을 확인하기 어렵기 때문이다.

### Secret 규칙

- GitHub에는 GCP 서비스 계정 JSON 키를 저장하지 않는다. WIF/OIDC를 사용한다.
- Django·JWT·DB 비밀번호는 Secret Manager에 둔다.
- 배포에는 `latest`가 아니라 숫자 버전을 지정한다.
- Secret 교체 시 새 버전 배포→동작 확인→구버전 비활성화 순서로 진행한다.
- 개발과 운영은 서로 다른 Secret을 쓴다.
- 과거 Git 기록에 노출된 값은 Gitleaks가 초록이어도 폐기·회전된 상태를 별도로 증명한다.

### 인프라 코드화

위 자원은 콘솔에서 손으로만 만들지 않고 Terraform/OpenTofu로 기록한다. 그래야 누가 무엇을 만들었는지 PR로 검토하고 개발환경을 다시 만들 수 있다.

권장 구조 예:

```text
infra/
├── modules/
│   ├── artifact-registry/
│   ├── cloud-run-service/
│   ├── cloud-run-job/
│   ├── cloud-sql/
│   ├── redis/
│   └── load-balancer/
└── environments/
    ├── dev/
    └── prod/
```

Terraform state에는 민감정보가 들어갈 수 있으므로 private GCS bucket, versioning, 최소 권한을 적용한다. 평문 Secret 값을 코드나 plan artifact에 출력하지 않는다.

## 9. 중앙 reusable workflow 작업 목록

`v3.4.0` 후보에는 기존 CI를 깨지 않도록 다음 파일을 별도 추가하는 방법을 권장한다.

```text
.github/workflows/
├── reusable-ci.yml                 # 기존 백엔드 CI
├── reusable-backend-build.yml      # build, scan, AR push, digest 출력
├── reusable-backend-deploy.yml     # migration Job, Cloud Run 배포, smoke
├── reusable-frontend-ci.yml        # 프론트 검사
├── reusable-frontend-deploy.yml    # 프론트 build/scan/deploy
└── reusable-dast.yml               # ZAP baseline/API scan
```

중요 구현 조건:

- Action은 commit SHA로 고정한다.
- ZAP Docker 이미지도 digest로 고정한다.
- 모든 배포 입력은 환경명, 프로젝트 ID, 리전, 서비스명, 이미지 digest처럼 명시한다.
- `secrets: inherit`를 쓰지 않고 필요한 Secret만 전달한다.
- 배포 workflow는 이미지를 다시 빌드하지 않고 검증된 digest를 입력받는다.
- 환경별 `concurrency`를 사용해 두 배포가 DB migration과 트래픽을 동시에 바꾸지 못하게 한다.
- development는 `main` 병합 후 자동, production은 수동 승인으로 분리한다.
- workflow output으로 image digest, revision 이름, service URL을 남긴다.
- DAST와 smoke 실패 시 production 승격을 막는다.
- logs/artifact에는 Secret, JWT, DB URL을 출력하지 않는다.

기존 Docker Hub는 당장 삭제하지 않아도 되지만 GCP 주 배포 이미지는 Artifact Registry를 권장한다. GitHub WIF로 GCP 안에서 권한을 짧게 받아 push할 수 있고, 별도 장기 Docker Hub token 의존성을 줄이며 Cloud Run과 같은 리전에 이미지를 둘 수 있기 때문이다. Docker Hub가 외부 배포나 공개 배포에 필요하면 검사 통과 이미지의 mirror 용도로만 남긴다.

## 10. 단계별 실행 계획과 합격 기준

예상 기간은 한 명이 다른 팀의 답변과 DNS 승인을 기다리지 않고 작업할 때의 대략적인 개발일이다. 실제 일정은 도메인, GCP 예산, scheduler 준비 상태에 따라 달라진다.

### 0단계 — 결정과 현황 고정 (1~2일)

작업:

- 프론트 실제 저장소 `MSG-CTF/front-team` 확정
- `dev.msgctf.kr`과 production 도메인 확정
- 개발은 DevSecOps GCP, 운영은 추후 Resource Broker 할당 계정으로 분리
- 기존 `ctf-backend` Cloud Run 서비스의 소유자와 삭제 가능 여부 확인
- scheduler 배포 주소와 인증 방식 확인
- 운영 승인자와 장애 알림 수신자 지정
- 비용 상한과 로그·백업 보존기간 합의

합격 기준:

- 미정 항목의 담당자와 결정일이 issue에 기록됨
- 기존 자원을 덮어쓰지 않는 새 서비스 이름이 정해짐
- 백엔드/프론트 어느 저장소에도 실제 Secret이 추가되지 않음

### 1단계 — 로컬 실제 통합환경 (2~4일)

작업:

- PostgreSQL, Redis, 실제 Django, 실제 React를 함께 실행하는 integration Compose 작성
- frontend Nginx의 `/api/v1` proxy가 mock이 아닌 실제 backend를 보게 함
- migration, seed data, 로그인, JWT 갱신, 대표 API E2E 확인
- scheduler가 준비되지 않았으면 명확한 mock과 제한 범위를 문서화

합격 기준:

- 새 개발자가 문서대로 한 번 실행해 화면을 열 수 있음
- 프론트 로그인과 최소 한 개의 실제 백엔드 API가 동작함
- DB migration을 지운 새 volume에서도 재현할 수 있음
- 평문 운영 Secret 없이 실행됨

### 2단계 — GCP 기반과 IaC (3~5일)

작업:

- Artifact Registry, VPC, Cloud SQL, Redis, 서비스 계정, Secret 생성
- WIF 조건을 조직/저장소/branch/workflow 기준으로 좁힘
- Cloud Run Direct VPC egress와 private DB/Redis 연결
- Terraform/OpenTofu plan 검토

합격 기준:

- GitHub Actions가 장기 GCP 키 없이 Artifact Registry에 test image를 push함
- backend runtime 계정으로 Cloud SQL `SELECT 1`과 Redis set/get 성공
- 권한 검사에서 frontend 계정이 DB Secret을 읽지 못함
- IaC를 다시 plan했을 때 의도하지 않은 변경이 없음

### 3단계 — 백엔드 개발 CD (3~5일)

작업:

- 중앙 build/deploy reusable workflow 작성
- migration Cloud Run Job 작성
- `msg-backend` caller를 `main` 개발 배포에 연결
- service URL smoke, revision/digest 기록, concurrency 추가

합격 기준:

- 백엔드 `main` merge 한 번에 이미지가 한 번만 빌드됨
- 배포 기록의 commit SHA, image digest, Cloud Run revision이 서로 연결됨
- migration 실패 시 새 service revision에 트래픽이 가지 않음
- 재실행해도 같은 migration과 배포가 안전하게 처리됨

### 4단계 — 프론트 CD와 한 도메인 연결 (3~5일)

작업:

- 프론트 CI/CD, container hardening, security headers 적용
- frontend/backend serverless NEG와 URL map 생성
- TLS와 DNS 연결
- 브라우저 E2E 실행

합격 기준:

- `https://dev.<도메인>`에서 프론트가 열림
- 같은 주소의 `/api/v1/*`가 실제 backend로 전달됨
- 브라우저 CORS 오류가 없음
- `run.app` 직접 접근 정책이 설계대로 제한됨
- 로그인, 토큰 갱신, 대표 사용자 흐름이 성공함

### 5단계 — DAST 도입 (2~4일)

작업:

- ZAP Baseline reusable workflow와 고정 config 작성
- report artifact와 실패 기준 설정
- 별도 staging service와 테스트 DB/Redis 격리
- OpenAPI 준비 후 격리 staging API scan 추가
- 인증용 DAST 계정과 데이터 정리 절차 추가

합격 기준:

- 정상 사이트에서 scan이 재현 가능하게 끝남
- 의도적으로 빠뜨린 테스트 보안 헤더를 ZAP이 발견하고 pipeline을 실패시킴
- production과 CTF 문제 인스턴스가 active scan scope에서 제외됨
- 예외 하나마다 근거·승인자·만료일이 존재함

### 6단계 — 운영 승격과 복구 훈련 (3~5일)

작업:

- production GitHub Environment 승인 설정
- 동일 digest 승격, 단계적 traffic, monitoring gate 구현
- DB backup/restore와 application rollback runbook 작성
- 실제 rollback 훈련

합격 기준:

- 승인 전 production Secret을 job이 읽지 못함
- 승인 없는 branch가 production 배포를 시작할 수 없음
- 오류를 발생시킨 test revision을 직전 revision으로 복구함
- 복구 시간, 담당자, 명령과 결과가 기록됨

전체 1차 목표는 공용 개발 사이트와 passive DAST까지 약 2~4주, 운영 promotion과 active API scan까지 약 3~5주로 잡는 것이 현실적이다. 여러 팀의 API 계약 수정과 DNS 승인이 늦어지면 더 늘어난다.

## 11. 담당자 구분

| 분야 | 주 담당 | 반드시 함께 확인할 사람 |
|---|---|---|
| reusable CI/CD/DAST, WIF, IaC | DevSecOps | 백엔드·프론트·보안 담당 |
| Django 설정, migration, OpenAPI, seed | 백엔드 | DevSecOps, DB 담당 |
| React API client, auth refresh, CSP, E2E | 프론트 | 백엔드, DevSecOps |
| 도메인, production 승인, 공개 일정 | PM/서비스 책임자 | DevSecOps |
| ZAP scope, 예외와 위험 승인 | 보안 담당 | 각 개발팀 |
| SLA 모니터링과 알림 | 모니터링 담당 | DevSecOps, 서비스 책임자 |

DevSecOps가 다른 팀의 비즈니스 로직이나 API 계약을 임의로 고치지 않는다. 반대로 개발팀이 배포용 장기 키를 각자 만들어 저장소 Secret에 넣지 않는다.

## 12. 장애와 rollback 원칙

- 새 revision은 이전 revision을 삭제하지 않고 만든다.
- application 문제는 Cloud Run traffic을 직전 정상 revision 100%로 돌린다.
- DB migration은 무조건 `migrate down`으로 되돌리지 않는다. 이미 새 형식으로 저장된 데이터를 잃을 수 있으므로 보통 forward fix를 준비한다.
- 삭제·이름 변경 migration은 두 번의 릴리스로 나눈다.
  1. 새 column/table 추가, 양쪽 형식 지원
  2. 데이터 이동과 구버전 사용 중단 확인
  3. 다음 릴리스에서 오래된 column/table 삭제
- production의 파괴적 migration 전에는 backup과 복구 시험 상태를 확인한다.
- smoke 또는 DAST 실패는 새 운영 승격을 중단하지만, 이미 정상인 이전 운영 서비스는 건드리지 않는다.

## 13. 처음 시작할 때의 정확한 순서

다음 회차에는 한꺼번에 모든 파일을 만들지 않고 아래 순서로 진행한다.

1. 백엔드 PR #25의 병합 여부와 병합 후 `main` CI 결과를 확인한다.
2. `front-team`의 로그인, API client, Docker/Nginx 설정과 현재 Actions를 읽기 전용으로 점검한다.
3. 백엔드 담당자에게 scheduler 주소·인증 방식만 질문한다. 답이 늦으면 인스턴스 생성 기능을 제외하고 진행한다.
4. 각 팀 허락을 받기 전에는 백엔드·프론트에 branch를 만들거나 push하지 않는다.
5. 허락 후 별도 통합 작업 branch에서 PostgreSQL, Redis, 실제 Django, 실제 React를 연결하는 integration Compose를 만든다.
6. 로컬 화면에서 로그인과 안전한 대표 API 하나가 동작하는지 확인한다.
7. Terraform/OpenTofu로 DevSecOps GCP의 개발 자원을 만들 계획을 작성하고 비용·변경 대상을 먼저 검토한다.
8. 승인 후 `msg-backend-dev`, `msg-frontend-dev`, 개발 DB/Redis/registry를 새 이름으로 만든다. 기존 `ctf-backend`는 건드리지 않는다.
9. `jm_devsecops`에서 build/deploy/passive DAST reusable workflow를 구현하고 test caller로 검증한다.
10. 중앙 `v3.4.0`을 발행한다.
11. 팀 승인 후 백엔드와 프론트 caller를 `@v3.4.0`으로 변경한다.
12. `main` 병합으로 임시 `run.app` 공용 개발 사이트를 띄운다.
13. passive DAST 기준선을 만들고 보고서를 팀에 공유한다.
14. 도메인을 받으면 Load Balancer와 같은 출처 정식 구조로 전환한다.
15. scheduler, token, staging 데이터 정책이 결정된 뒤 active API Scan을 켠다.
16. Resource Broker 운영 계정과 승인자가 정해진 마지막 단계에서 production promotion을 연다.

## 14. 아직 담당자에게 받아야 하는 답

### 개발 사이트를 완성하기 전에 필요한 답

1. 현재 `ctf-backend` Cloud Run 서비스는 누가 사용하며 보존해야 하는가? 답을 받기 전에는 수정하거나 삭제하지 않는다.
2. instance scheduler는 어디에 배포되고 backend가 어떤 인증으로 호출하는가? 답이 없으면 인스턴스 생성 기능은 개발 사이트 합격 범위에서 제외한다.

### production 전에 필요한 답

1. 공용 개발 도메인과 운영 도메인은 무엇인가?
2. refresh token을 계속 `localStorage`에 둘 것인가, `HttpOnly` cookie로 바꿀 것인가?
3. production 배포 승인자는 누구인가?
4. production DB backup 주기, 보존기간, 허용 복구시간은 얼마인가?
5. SLA 알림을 받을 채널과 담당자는 누구인가?
6. Resource Broker가 할당할 GCP 프로젝트의 리전·권한·예산 제한은 무엇인가?

### active DAST 전에 필요한 답

“DAST가 생성·삭제해도 되는 데이터”는 ZAP이 가짜 사용자처럼 API를 시험하면서 만들어도 되는 테스트 자료를 뜻한다. 예를 들면 다음과 같다.

- 테스트 전용 회원을 새로 만들어도 되는가?
- 테스트 문제나 인스턴스를 만들었다가 지워도 되는가?
- 로그인 비밀번호를 여러 번 틀려 계정이 잠겨도 되는가?
- 게시물 입력칸에 공격 모양의 문자열을 보내도 되는가?

실제 사용자나 실제 CTF 문제에 이런 요청을 보내면 데이터가 망가지거나 서비스가 방해될 수 있다. 따라서 현재 결정은 **active scan 보류, 데이터를 바꾸지 않는 passive Baseline Scan만 실행**이다. 나중에 별도 staging DB와 테스트 계정을 만든 뒤 백엔드 담당자가 허용 endpoint를 정하면 active scan을 연다.

## 15. 세 팀이 3일 안에 개발 배포를 완성하는 실행 계획

### 15.1 3일 안에 완성한다는 말의 정확한 뜻

3일 안에 가능한 목표는 **운영 서비스 완성**이 아니라 다음 조건을 만족하는 **공용 개발 배포 MVP**다. 여기서 MVP는 팀이 실제 화면과 API 연결을 확인할 수 있는 가장 작은 완성품이라는 뜻이다.

3일째 끝날 때 다음 결과가 있어야 한다.

1. 팀원이 브라우저에서 프론트 Cloud Run `run.app` 주소를 열 수 있다.
2. 그 화면에서 실제 `msg-backend` API를 호출한다. mock API만 보이면 실패다.
3. 백엔드는 개발 Cloud SQL PostgreSQL과 개발 Redis를 사용한다.
4. DB migration은 별도 Cloud Run Job으로 성공한다.
5. 백엔드와 프론트 이미지는 commit SHA로 만들고 Artifact Registry digest로 배포한다.
6. 두 저장소의 `main`에 새 코드가 합쳐졌을 때 개발 배포를 다시 실행할 수 있다.
7. 배포 직후 smoke test와 ZAP Baseline passive scan 결과가 남는다.
8. 문제가 생기면 직전 Cloud Run revision으로 되돌리는 시험을 한 번 한다.

다음 항목은 3일 목표에서 제외한다.

- 사용자 지정 개발·운영 도메인
- production 공개와 실제 사용자 데이터
- Resource Broker가 줄 운영 GCP 계정
- scheduler가 필요한 인스턴스 생성 기능
- ZAP active API Scan
- PR별 임시 preview 환경
- production 승인자, SLA 호출 정책, production DB 복구 목표

제외하는 이유는 “중요하지 않아서”가 아니다. 아직 담당자나 안전한 시험환경이 없기 때문이다. 정해지지 않은 운영 사항을 추측해서 만들면 3일 안에 화면은 뜰 수 있어도 나중에 다시 뜯어고치거나 실제 데이터를 위험하게 만들 수 있다.

### 15.2 3일 완료가 가능한 전제조건

프론트·백엔드·DevSecOps 담당자가 순서대로 한 명씩 작업하면 3일은 부족하다. 최소 세 사람이 같은 시간에 병렬로 움직여야 한다.

```text
백엔드 담당자   ── Django·DB·API 준비 ─────────────┐
프론트 담당자   ── React·Nginx·화면 연결 ──────────┼─ 공용 개발 사이트
DevSecOps 담당자 ── GCP·이미지·배포·DAST ──────────┘
```

첫날 오전 시작 전에 다음 권한과 조건이 있어야 한다.

- 프론트·백엔드가 각 저장소에 작업 branch와 PR을 만들 수 있음
- 각 팀이 branch 생성과 push를 승인함
- DevSecOps가 현재 GCP 프로젝트에서 API 활성화와 개발 자원 생성 가능
- GCP billing이 활성화됨
- GitHub 저장소의 Actions·Variables·Secrets·Environment 설정 가능
- 세 담당자가 3일 동안 오전·오후 확인 시간에 응답 가능
- 백엔드 PR #25를 병합할 담당자와 병합 여부가 결정됨
- 개발 자원 비용을 발생시켜도 된다는 승인이 있음

이 중 하나가 없으면 보안 검사를 끄거나 비밀번호를 코드에 넣어서 시간을 맞추지 않는다. 3일 완료 조건을 다시 조정한다.

### 15.3 세 팀이 공유할 한 장짜리 작업판

첫날 오전에 GitHub issue 또는 팀 문서 하나를 만들고 다음 정보를 계속 갱신한다. 채팅에만 남기면 나중에 어느 commit과 이미지가 배포됐는지 찾기 어렵다.

| 기록 항목 | 예시 | 기록 담당 |
|---|---|---|
| 백엔드 branch/commit | `chore/dev-deploy`, 40자리 SHA | 백엔드 |
| 프론트 branch/commit | `chore/dev-deploy`, 40자리 SHA | 프론트 |
| 중앙 pipeline commit/tag | test SHA → `v3.4.0` | DevSecOps |
| 백엔드 image digest | `sha256:...` | DevSecOps |
| 프론트 image digest | `sha256:...` | DevSecOps |
| migration 실행 결과 | 성공/실패와 Actions URL | 백엔드·DevSecOps |
| backend service URL | `https://msg-backend-dev-....run.app` | DevSecOps |
| 사용자가 열 frontend URL | `https://msg-frontend-dev-....run.app` | DevSecOps |
| smoke/DAST 결과 | 성공 여부와 artifact URL | DevSecOps |
| 알려진 제한 | scheduler 미연결 등 | 세 팀 공동 |

상태는 `할 일`, `진행 중`, `검토 중`, `완료`, `막힘` 다섯 가지로만 쓴다. 막힘에는 반드시 담당자와 다음 확인 시간을 적는다.

### 15.4 시작 회의에서 60분 안에 정할 내용

#### 0~15분: 목표 고정

모두 다음 문장에 동의한다.

> 3일 목표는 임시 Cloud Run 주소에서 실제 프론트와 백엔드 API가 연결되고, 자동 재배포와 passive DAST가 재현되는 개발환경이다.

scheduler, custom domain, production은 이번 완료 조건이 아니라고 기록한다.

#### 15~30분: API 한 개와 사용자 흐름 선택

3일 동안 모든 기능을 시험하려고 하지 않는다. 다음처럼 가장 작은 실제 흐름을 하나 정한다.

```text
프론트 페이지 열기
  → 로그인 또는 인증 없이 가능한 API 호출
  → 백엔드가 PostgreSQL에서 데이터 조회
  → 프론트 화면에 결과 표시
```

로그인이 이미 안정적이면 로그인까지 포함한다. JWT 갱신이 아직 완성되지 않았다면 첫 합격 흐름에 억지로 넣지 않고 알려진 제한으로 남긴다.

#### 30~45분: 환경변수 계약 작성

백엔드는 필요한 변수 이름을, 프론트는 필요한 공개 설정을 표로 전달한다.

```text
백엔드 Secret
- DJANGO_SECRET_KEY
- JWT_SECRET
- POSTGRES_PASSWORD

백엔드 일반 설정
- DJANGO_DEBUG=False
- DJANGO_ALLOWED_HOSTS
- POSTGRES_DB / POSTGRES_USER / POSTGRES_HOST / POSTGRES_PORT
- REDIS_URL
- SCHEDULER_BASE_URL: 이번 범위에서는 미연결 표시

프론트 일반 설정
- BACKEND_ORIGIN: frontend Nginx가 proxy할 backend run.app 주소
```

Secret과 일반 설정을 나누는 이유는 서비스 주소는 숨길 비밀번호가 아니지만 Django 키와 DB 비밀번호는 노출되면 다른 사람이 위조하거나 DB에 접근할 수 있기 때문이다.

#### 45~60분: branch와 검토자 확정

- 각 저장소의 최신 `main`에서 새 작업 branch를 만든다.
- 오래된 PR branch를 재사용하지 않는다.
- 백엔드 PR 검토자, 프론트 PR 검토자, 중앙 pipeline 검토자를 한 명씩 정한다.
- DevSecOps는 팀 허락 없이 백엔드·프론트 branch를 만들거나 push하지 않는다.

### 15.5 1일차 — 로컬에서 진짜 프론트와 백엔드 연결

첫날의 목표는 “각자 컴퓨터에서는 된다”가 아니라 **한 integration Compose에서 네 구성요소가 함께 동작한다**는 것이다.

```text
frontend → backend → PostgreSQL
                 └→ Redis
```

#### 오전 10시~오후 1시: 세 팀 병렬 작업

백엔드 담당자:

1. 최신 `main`과 PR #25 상태를 확인한다.
2. 승인된 작업 branch에서 현재 Dockerfile로 이미지가 빌드되는지 확인한다.
3. `migrate --noinput`을 빈 개발 DB에 적용한다.
4. 환경변수가 없을 때 운영 Secret의 가짜 기본값으로 조용히 실행되지 않는지 확인한다.
5. 선택한 대표 API가 어떤 DB table을 읽는지 기록한다.
6. 테스트 데이터를 만드는 안전한 명령 또는 fixture를 준비한다.
7. scheduler가 없어도 서버 자체가 시작되는지 확인하고, scheduler 관련 기능의 예상 오류를 기록한다.

프론트 담당자:

1. `front-team`의 최신 `main`에서 production build가 되는지 확인한다.
2. API client가 상대경로 `/api/v1`을 사용하는지 확인한다.
3. Nginx의 `/api/v1/` proxy 대상을 고정된 mock 이름 대신 `BACKEND_ORIGIN`으로 받을 수 있게 준비한다.
4. proxy가 backend로 보낼 때 원래 HTTPS 요청임을 알리는 `X-Forwarded-Proto`와 올바른 `Host`를 전달한다.
5. 새로고침해도 React 화면이 열리도록 SPA fallback을 유지한다.
6. 브라우저 번들에 Django 키, JWT Secret, DB 비밀번호가 들어가지 않는지 확인한다.

DevSecOps 담당자:

1. 세 저장소의 정확한 base commit을 작업판에 적는다.
2. integration Compose 초안을 준비하되 실제 앱 코드를 중앙 저장소로 복사하지 않는다.
3. PostgreSQL과 Redis 버전을 CI와 가능한 한 맞춘다.
4. 컨테이너 이름이 아니라 Compose service name으로 서로 연결되게 한다.
5. Secret이 아닌 로컬 테스트 값은 별도 `.env.dev.example`에 이름만 안내하고 실제 `.env`는 Git에서 제외되는지 확인한다.
6. Gitleaks로 새 파일에 Secret 모양의 실제 값이 들어가지 않았는지 확인한다.

#### 오후 1시 체크포인트

세 팀이 다음 세 가지를 서로 보여준다.

- 백엔드: 빈 DB migration 성공 로그
- 프론트: production build 성공 로그
- DevSecOps: 네 service가 정의된 Compose 구조

하나라도 실패하면 오후에는 GCP 작업으로 넘어가지 않고 로컬 문제를 먼저 해결한다. 클라우드는 로컬 설정 오류를 자동으로 고쳐주지 않고, 원인을 찾기 더 어렵게 만들기 때문이다.

#### 오후 2시~5시: 통합 Compose 실행

실행 순서는 다음과 같다.

1. PostgreSQL과 Redis를 시작한다.
2. 두 서비스의 readiness가 확인될 때까지 backend를 시작하지 않는다.
3. migration 컨테이너가 `python manage.py migrate --noinput`을 한 번 실행한다.
4. migration 성공 후 backend Gunicorn을 시작한다.
5. backend가 `/admin/login/`과 대표 API에 응답하는지 확인한다.
6. frontend Nginx를 시작한다.
7. 브라우저에서 frontend 주소를 열고 `/api/v1` 요청이 실제 backend로 가는지 개발자 도구 Network 탭으로 확인한다.
8. backend log에서 같은 요청을 확인한다.
9. PostgreSQL에서 실제 조회가 발생했는지 기능 결과로 확인한다.
10. Redis set/get 또는 Django cache 경로를 확인한다.

#### 오후 5시~6시: 첫날 합격 검사

첫날 완료 조건:

- 네 service가 한 명령으로 시작됨
- 빈 DB에서 migration 성공
- frontend 화면이 열림
- mock이 아닌 실제 backend API 응답이 화면에 표시됨
- PostgreSQL과 Redis 연결 성공
- 실제 Secret이 commit 대상 파일에 없음
- scheduler 미연결 기능이 정확히 문서화됨

첫날 결과가 실패하면 둘째 날 오전 GCP 배포를 시작하지 않는다. 먼저 로컬 통합을 고친다.

### 15.6 2일차 — DevSecOps GCP에 사람이 볼 수 있는 사이트 배포

둘째 날 목표는 자동화 전체 완성이 아니라 **검사된 두 이미지를 수동 또는 test workflow로 GCP에 처음 올려 실제 URL을 얻는 것**이다. 처음부터 완전 자동화를 만들면 앱 문제와 pipeline 문제를 동시에 디버깅해야 한다.

#### 오전 9시~10시: 변경·비용 확인

DevSecOps는 생성할 자원을 목록으로 보여주고 승인을 받는다.

- 새 Artifact Registry repository
- 개발 Cloud SQL PostgreSQL
- 개발 Memorystore Redis와 VPC 연결
- backend/frontend/migration runtime 서비스 계정
- 개발 Secret 세 개
- `msg-backend-migrate-dev` Cloud Run Job
- `msg-backend-dev`, `msg-frontend-dev` Cloud Run 서비스

기존 `ctf-backend`는 수정·삭제 목록에 넣지 않는다.

#### 오전 10시~오후 1시: GCP 자원과 앱 수정 병렬 진행

DevSecOps 담당자:

1. Terraform/OpenTofu에서 project ID, region, environment를 변수로 만든다.
2. 개발환경 값으로 `project-325bdf5b-3d83-4f27-9f6`, `asia-northeast3`, `dev`를 전달한다.
3. Artifact Registry의 tag immutability를 켠다.
4. Cloud SQL과 Redis를 private network에서 접근하도록 구성한다.
5. backend와 migration runtime 계정을 분리한다.
6. WIF를 사용하고 서비스 계정 JSON key는 만들지 않는다.
7. Django, JWT, DB 비밀번호를 Secret Manager의 새 개발 Secret으로 만든다.
8. Secret의 숫자 버전을 배포 기록에 남기되 값 자체는 출력하지 않는다.

백엔드 담당자:

1. Cloud SQL 연결 환경변수와 현재 Django settings가 맞는지 확인한다.
2. Redis URL 형식과 Django cache 연결을 확인한다.
3. `DJANGO_ALLOWED_HOSTS`에 backend Cloud Run host를 넣을 수 있게 설정한다.
4. migration Job과 web service가 같은 이미지에서 서로 다른 command를 실행해도 되는지 확인한다.
5. 대표 API용 개발 seed를 넣되 실제 사용자 정보는 넣지 않는다.

프론트 담당자:

1. Nginx가 runtime의 `BACKEND_ORIGIN`을 읽어 설정 파일을 만들도록 확인한다.
2. backend Cloud Run HTTPS 주소로 proxy할 때 TLS server name과 Host가 맞는지 확인한다.
3. `/api/v1` 이외의 React route는 `index.html`로 돌아가는지 확인한다.
4. 최소 보안 헤더를 적용하고 ZAP에서 확인할 항목을 표시한다.

#### 오후 1시 체크포인트

- Cloud SQL과 Redis가 준비됨
- Artifact Registry가 준비됨
- 세 runtime 서비스 계정과 Secret 버전이 준비됨
- frontend/backend Docker build가 다시 통과함

Cloud SQL이나 Redis 생성이 아직 진행 중이면 기다리는 동안 중앙 reusable workflow를 작성한다. 준비되지 않은 DB를 무시하고 SQLite로 바꾸어 배포하지 않는다. CI에서 PostgreSQL을 검사한 의미가 사라지기 때문이다.

#### 오후 2시~4시: backend부터 배포

정확한 순서는 다음과 같다.

1. backend commit SHA로 이미지를 빌드한다.
2. Gitleaks와 Trivy를 통과시킨다.
3. Artifact Registry에 SHA tag로 push한다.
4. registry가 반환한 digest를 기록한다.
5. 같은 digest로 migration Job을 만든다.
6. migration Job을 실행하고 완료까지 기다린다.
7. 실패하면 web service를 배포하지 않고 Job log를 백엔드에 전달한다.
8. 성공하면 같은 digest로 `msg-backend-dev` revision을 만든다.
9. runtime 서비스 계정, Secret 숫자 버전, DB/Redis 네트워크를 연결한다.
10. `/admin/login/`과 대표 API를 직접 요청한다.
11. backend URL과 revision 이름을 작업판에 기록한다.

같은 digest를 사용하는 이유는 migration에 사용한 코드와 실제 서버 코드가 다르면 DB 구조가 맞지 않을 수 있기 때문이다.

#### 오후 4시~5시: frontend 배포

1. frontend commit SHA로 이미지를 빌드하고 검사한다.
2. Artifact Registry에 push하고 digest를 기록한다.
3. `BACKEND_ORIGIN`에 방금 검증한 backend URL을 설정한다.
4. `msg-frontend-dev`에 배포한다.
5. frontend `run.app` 주소를 브라우저로 연다.
6. Network 탭에서 브라우저 요청이 frontend의 `/api/v1`로 나가는지 확인한다.
7. backend log에서 proxy된 요청을 확인한다.
8. CORS 오류, 400 `DisallowedHost`, 502 proxy 오류가 없는지 확인한다.

#### 오후 5시~6시: 둘째 날 합격 검사

- 외부 팀원이 frontend URL을 열 수 있음
- 실제 backend API 결과가 화면에 표시됨
- backend가 Cloud SQL과 Redis를 사용함
- migration Job이 성공함
- 두 image digest와 두 Cloud Run revision이 기록됨
- 실제 Secret은 Secret Manager에만 있음
- 기존 `ctf-backend`는 변경되지 않음

여기까지 성공하면 “사이트가 떴다”고 말할 수 있다. 하지만 아직 main 변경이 자동으로 같은 결과를 만드는지 확인하지 않았으므로 CD 완성이라고 말하지는 않는다.

### 15.7 3일차 — 재현 가능한 CD와 passive DAST 완성

셋째 날 목표는 둘째 날 사람이 수행한 안전한 순서를 reusable workflow가 똑같이 반복하게 만드는 것이다.

#### 오전 9시~오후 12시: 중앙 pipeline 작성

DevSecOps는 기존 `v3.3.1` CI를 깨지 않고 다음 reusable workflow를 추가한다.

1. backend build workflow
   - exact commit checkout
   - Gitleaks·Trivy
   - Artifact Registry push
   - digest output
2. backend deploy workflow
   - 같은 digest migration Job
   - migration 성공 대기
   - backend Cloud Run deploy
   - smoke test
3. frontend build/deploy workflow
   - build·scan·digest 기록
   - backend origin 설정
   - frontend Cloud Run deploy
4. DAST workflow
   - frontend URL 입력
   - digest로 고정한 ZAP Baseline image
   - passive scan
   - HTML/JSON report artifact

각 환경에는 `concurrency`를 넣어 같은 개발 DB에 migration 두 개가 동시에 실행되지 않게 한다. 필요한 Secret만 이름으로 전달하고 `secrets: inherit`는 사용하지 않는다.

백엔드 담당자는 migration과 smoke 결과를, 프론트 담당자는 proxy와 화면 결과를 바로 검토한다. DevSecOps 혼자 초록불만 보고 앱이 정상이라고 판단하지 않는다.

#### 오후 12시 체크포인트

- 중앙 workflow YAML 구문 검사 통과
- Action은 commit SHA, 도구 image는 digest로 고정
- development와 production 입력이 섞이지 않음
- 실제 Secret 값이 workflow에 없음
- 기존 CI caller와 호환됨

#### 오후 1시~3시: caller 연결과 전체 재실행

각 팀의 승인을 받은 다음에만 caller를 추가하거나 수정한다.

백엔드 caller:

```text
PR → CI만 실행
main push → CI → backend build → migration → backend dev deploy
```

프론트 caller:

```text
PR → frontend CI만 실행
main push → CI → frontend build → frontend dev deploy
```

통합 DAST:

```text
backend와 frontend 개발 배포 성공
  → frontend URL smoke
  → ZAP Baseline passive scan
  → report 보관
```

테스트를 위해 의미 없는 README 변경을 main에 바로 넣지 않는다. 각 팀이 승인한 작은 실제 변경 PR 또는 `workflow_dispatch` test input으로 실행한다.

#### 오후 3시~4시: ZAP 결과 검토

1. scan 대상이 frontend 개발 URL인지 확인한다.
2. production URL이나 CTF 문제 인스턴스가 scope에 없는지 확인한다.
3. FAIL, WARN, INFO를 분리한다.
4. 새 High 또는 팀이 FAIL로 정한 항목이 있으면 배포 완료 판정을 보류한다.
5. 오탐은 rule ID, URL, 근거, 담당자, 만료일을 기록한다.
6. report를 Actions artifact로 남긴다.

Baseline Scan은 공격 요청을 보내지 않는 passive 중심 검사라서 첫 공용 개발 배포에 적합하다. active scan은 데이터를 바꿀 수 있으므로 이번 3일 범위에 넣지 않는다.

#### 오후 4시~5시: rollback 훈련

1. 현재 정상 backend/frontend revision 이름을 기록한다.
2. 새 test revision을 만들거나 smoke 실패 상황을 재현한다.
3. 새 revision을 정상으로 승격하지 않거나 직전 revision에 트래픽 100%를 돌린다.
4. frontend 화면과 대표 API가 다시 정상인지 확인한다.
5. 사용한 명령, 걸린 시간, 담당자를 runbook에 기록한다.

rollback 버튼이 있다는 사실만 확인하지 않고 실제로 한 번 되돌려 보는 이유는 장애 중에 권한 부족이나 revision 이름 오류를 처음 발견하면 복구가 늦어지기 때문이다.

#### 오후 5시~6시: 릴리스와 인수인계

1. 중앙 Actions 전체가 통과한다.
2. 기존 호출자를 깨지 않는 것을 확인한다.
3. 중앙 pipeline에 `v3.4.0` 태그를 발행한다.
4. 백엔드·프론트 caller가 `@main`이 아니라 승인된 `@v3.4.0`을 사용하게 한다.
5. 개발 사이트 URL, 알려진 제한, Secret 담당자, 비용 자원을 문서에 남긴다.
6. 팀원이 문서만 보고 Actions 재실행과 rollback 위치를 찾을 수 있는지 확인한다.

### 15.8 팀별 3일 책임표

| 날짜 | 백엔드 | 프론트 | DevSecOps |
|---|---|---|---|
| 1일차 | settings·migration·대표 API·seed | API client·Nginx proxy·화면 build | integration Compose·Secret 검사·통합 진행 |
| 2일차 | Cloud SQL/Redis 앱 연결·backend smoke | backend URL proxy·브라우저 확인 | GCP/IaC·registry·Job·Cloud Run 배포 |
| 3일차 | migration/API 배포 결과 승인 | 화면/proxy 배포 결과 승인 | reusable CD·DAST·version·rollback 훈련 |

한 팀의 변경이 다른 팀에 필요한 경우 다음 형식으로 전달한다.

```text
[보내는 팀]
- 바뀐 것:
- 상대 팀이 사용할 값 또는 URL:
- Secret인지 일반 설정인지:
- 확인 방법:
- 실패했을 때 볼 로그:
- 관련 commit/PR:
```

“다 됐어요”라고만 전달하지 않는다. 상대 팀이 확인할 URL, commit과 합격 방법이 있어야 다음 작업을 바로 시작할 수 있다.

### 15.9 매일 두 번 하는 공동 확인

오후 1시와 오후 5시에 15분씩 세 팀이 함께 확인한다.

1. 지금 배포 대상 commit은 무엇인가?
2. 마지막으로 통과한 단계는 어디인가?
3. 현재 막힌 것은 코드, 권한, GCP 자원 중 무엇인가?
4. 다음 3시간 동안 누가 무엇을 해결하는가?
5. scope를 벗어난 새 요구가 들어왔는가?

새 요구는 별도 issue로 보내고, 로그인과 대표 API를 막지 않는다면 3일 뒤로 미룬다. 중간에 기능을 계속 추가하면 세 팀 모두 서로 다른 목표를 보게 된다.

### 15.10 실패할 때 줄이지 말아야 할 것

시간이 부족해도 다음 항목은 제거하지 않는다.

- 실제 Secret을 코드에 넣지 않기
- Gitleaks와 이미지 취약점 검사
- migration 성공 후 backend 배포
- commit SHA와 image digest 기록
- backend/frontend의 실제 연결 확인
- production과 개발환경 분리
- 기존 `ctf-backend` 보존

대신 다음 기능을 더 뒤로 미룬다.

- scheduler 기능
- 로그인 refresh 자동화
- custom domain
- active DAST
- 모든 화면의 E2E
- PR별 preview 환경

보안 절차를 빼서 일정을 맞추면 겉으로는 배포가 빨라 보이지만, 어느 코드와 DB가 올라갔는지 모르는 환경이 생긴다. 그런 환경은 다음 배포 때 재현할 수 없으므로 CD가 완성된 것이 아니다.

### 15.11 3일 완료 최종 체크리스트

다음 항목이 모두 `예`여야 완료다.

#### 사용자가 보는 결과

- [ ] frontend `run.app` URL이 열리는가?
- [ ] mock이 아닌 실제 backend 응답이 화면에 보이는가?
- [ ] 다른 팀원 컴퓨터에서도 같은 URL이 열리는가?

#### 데이터와 앱

- [ ] 빈 DB에서 migration Job이 성공하는가?
- [ ] backend가 개발 Cloud SQL을 사용하는가?
- [ ] Redis set/get 또는 cache 확인이 성공하는가?
- [ ] scheduler 미연결 기능이 화면/문서에 알려져 있는가?

#### 배포 증거

- [ ] backend/frontend commit SHA가 기록됐는가?
- [ ] 두 image digest가 기록됐는가?
- [ ] Cloud Run revision과 digest가 연결되는가?
- [ ] `main` 또는 승인된 수동 실행으로 같은 배포를 재현할 수 있는가?
- [ ] 중앙 caller가 고정 pipeline 버전을 사용하는가?

#### 보안

- [ ] 실제 Secret이 GitHub 코드와 Actions log에 없는가?
- [ ] GCP JSON key 없이 WIF를 사용하는가?
- [ ] runtime 서비스 계정이 기본 Compute 계정과 분리됐는가?
- [ ] Gitleaks와 Trivy가 통과했는가?
- [ ] ZAP Baseline report가 남았는가?
- [ ] active scan이 production과 실제 CTF 대상을 검사하지 않는가?

#### 복구와 전달

- [ ] 직전 revision rollback 시험이 성공했는가?
- [ ] 개발 사이트 URL과 알려진 제한이 문서에 있는가?
- [ ] 각 자원의 비용과 삭제 담당자가 기록됐는가?
- [ ] Resource Broker 운영 GCP로 옮길 수 있도록 project ID가 변수화됐는가?

### 15.12 3일 후 남는 후속 작업

3일 배포가 성공한 뒤 다음 순서로 확장한다.

1. scheduler 주소와 인증을 받아 인스턴스 기능 연결
2. refresh token 보관 방식 결정과 인증 흐름 보강
3. 개발 도메인을 받아 HTTPS Load Balancer 구조로 전환
4. 별도 staging DB와 테스트 계정 준비
5. OpenAPI 기반 ZAP active API Scan 추가
6. production 승인자·backup·SLA 정책 확정
7. Resource Broker 운영 GCP에 같은 IaC 적용
8. 개발에서 검증한 image digest를 production으로 승격

## 16. 공식 근거

- [Cloud Run에서 Cloud SQL PostgreSQL 연결](https://docs.cloud.google.com/sql/docs/postgres/connect-run): Cloud Run과 Cloud SQL 연결 방식, 서비스 계정 권한, Unix socket/connector 설정
- [Cloud Run Job 실행](https://docs.cloud.google.com/run/docs/execute-jobs): 작업 완료까지 기다리는 실행 방식과 성공/실패 기록
- [Cloud Run에서 Memorystore Redis 연결](https://docs.cloud.google.com/memorystore/docs/redis/connect-redis-instance-cloud-run): Direct VPC egress 권장과 VPC 접근 조건
- [Cloud Run revision과 rollback](https://docs.cloud.google.com/run/docs/rollouts-rollbacks-traffic-migration): 트래픽 분할, 단계적 배포, 이전 revision 복구
- [Artifact Registry Docker image push](https://docs.cloud.google.com/artifact-registry/docs/docker/pushing-and-pulling): image digest 확인과 immutable tag
- [GitHub Deployment Environment](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments): required reviewer, branch 제한, environment Secret 보호
- [GitHub OIDC와 GCP](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-google-cloud-platform): 장기 GCP 키 없이 Workload Identity Federation 사용
- [OWASP ZAP Baseline Scan](https://www.zaproxy.org/docs/docker/baseline-scan/): passive 기반의 CI/CD용 짧은 scan
- [OWASP ZAP Docker scans](https://www.zaproxy.org/docs/docker/): Baseline, Full, OpenAPI/GraphQL API Scan의 차이
