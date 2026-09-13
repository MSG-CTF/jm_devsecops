# 개발 VM 자동배포 안내

## 한눈에 보기

이 자동배포는 `MSG-CTF/msg-backend`와 `MSG-CTF/front-team`의 `main` 변경을
`https://msg2.mjsec.kr` 개발 확인 사이트에 반영한다. CTF 당일 운영 배포용이
아니며, `/opt/msg-dev`에 PostgreSQL·Redis·백엔드·프론트가 함께 실행되는 현재
개발 VM 전용이다.

```text
팀 저장소 main push
  → 팀 CI 모두 성공
  → 중앙 reusable VM CD 호출
  → GitHub OIDC를 GCP WIF가 검사
  → IAP로 개발 VM 접속
  → 정확한 40자리 SHA 빌드·후보 검사
  → 백엔드는 DB 백업·migration
  → 한 서비스만 교체
  → 내부와 외부 HTTPS/API 검사
  → 실패하면 이전 앱 이미지로 복귀
```

## 반드시 먼저 알아둘 제한

- 이 흐름은 개발 확인용 단일 VM에 맞춘 것이다. CTF 운영 환경에는 그대로
  사용하지 않는다.
- 백엔드 migration이 성공한 뒤 앱 smoke test가 실패하면 앱 이미지는 이전
  것으로 복귀하지만 DB migration을 자동 역실행하지 않는다. 데이터가 사라질
  수 있기 때문이다. 백엔드는 이전·새 앱이 함께 읽을 수 있는 expand/contract
  migration을 사용해야 한다.
- PostgreSQL 백업은 `/opt/msg-dev/backups`에 남는다. 복원은 자동으로 하지 않고
  담당자가 원인을 확인한 뒤 수행한다.
- VM의 `.env`는 GitHub로 옮기지 않는다. Django, JWT, DB, Redis 값은 VM 안에만
  둔다.

## 중앙 워크플로의 보안 장치

1. `push`, `refs/heads/main`, 호출 저장소와 구성요소 조합을 확인한다.
2. 입력 SHA가 실제 `GITHUB_SHA`와 같은지 확인한다.
3. 중앙 배포 스크립트의 SHA-256을 확인해 다른 스크립트 바꿔치기를 막는다.
4. GCP JSON 키나 장기 SSH 키 대신 GitHub OIDC와 WIF를 사용한다.
5. `development` GitHub Environment에서만 배포 job을 실행한다.
6. IAP 터널로 VM에 접속하므로 인터넷 전체에 SSH를 열 필요가 없다.
7. 백엔드와 프론트 배포를 같은 concurrency 그룹으로 직렬화한다.
8. 이미지 기본 사용자가 root이면 배포하지 않는다.

## GCP에서 준비할 것

전용 서비스 계정 예시는 `github-vm-deployer-dev`이다. 이 계정에는 다음 최소
권한만 준다.

- IAP 터널 접속 권한
- 해당 개발 VM 조회 권한
- OS Login 관리자 권한(배포 스크립트가 제한된 `sudo` 작업을 수행)
- VM에 연결된 서비스 계정을 사용할 권한이 실제로 필요할 때만 추가

WIF provider 조건은 최소한 다음 네 가지를 동시에 확인해야 한다.

```text
repository가 MSG-CTF/msg-backend 또는 MSG-CTF/front-team
ref가 refs/heads/main
job_workflow_ref가 중앙 reusable-vm-deploy-dev.yml의 정확한 고정 SHA
sub가 repo:<팀 저장소>:environment:development
```

중앙 PR이 병합되기 전에는 정확한 SHA가 없으므로 WIF 조건을 먼저 느슨하게
만들지 않는다. 병합 commit SHA를 얻은 뒤 조건을 만들거나 갱신한다.

IAP 접속을 먼저 성공시킨 후 SSH 방화벽의 `0.0.0.0/0` 규칙을 제거하고,
TCP 22는 IAP 주소 범위 `35.235.240.0/20`만 허용한다. 순서를 바꾸면 VM에서
스스로 잠길 수 있다.

## 팀 저장소 호출 파일 모양

아래 `<CENTRAL_COMMIT_SHA>`는 중앙 PR 병합 commit의 40자리 SHA로 두 곳 모두
같아야 한다. GCP 식별자는 비밀번호가 아니므로 GitHub Repository 또는
Organization Variables에 둔다.

```yaml
deploy-development:
  needs: [기존-CI-job]
  if: github.event_name == 'push' && github.ref == 'refs/heads/main'
  permissions:
    contents: read
    id-token: write
  uses: MSG-CTF/jm_devsecops/.github/workflows/reusable-vm-deploy-dev.yml@<CENTRAL_COMMIT_SHA>
  with:
    component: frontend # 백엔드는 backend
    commit_sha: ${{ github.sha }}
    pipeline_ref: <CENTRAL_COMMIT_SHA>
    gcp_project_id: ${{ vars.GCP_PROJECT_ID }}
    workload_identity_provider: ${{ vars.GCP_VM_WIF_PROVIDER }}
    deployer_service_account: ${{ vars.GCP_VM_DEPLOYER_SERVICE_ACCOUNT }}
    vm_zone: asia-northeast3-a
    vm_name: backend-dev-vm
    public_domain: msg2.mjsec.kr
```

프론트는 현재 `push: main`이 꺼져 있으므로 이를 켜고, 기존 보안·이미지 검사
job이 모두 성공한 뒤 위 job이 시작되도록 `needs`를 연결한다. 백엔드도 기존
중앙 CI 성공 뒤에만 배포한다. PR에서는 CI만 실행하고 배포하지 않는다.

## 완료 검증

- GitHub Actions에 `deploy-frontend` 또는 `deploy-backend`가 성공으로 표시된다.
- VM의 `/opt/msg-dev/deploy-state/<component>.sha`가 해당 main SHA와 같다.
- `https://msg2.mjsec.kr/`가 정상 응답한다.
- `https://msg2.mjsec.kr/api/v1/auth/me`가 로그인 전 정상 응답인 HTTP 401을
  반환한다. 401은 서버 장애가 아니라 “로그인이 필요하다”는 뜻이다.
- 컨테이너 restart 횟수가 계속 증가하지 않는다.

