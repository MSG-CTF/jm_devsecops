# 프론트 중앙 CI 연결 안내

## 현재 확인 결과

`MSG-CTF/front-team`의 `.github/workflows/ci.yml`은 존재하지 않는 중앙 파일을
`MSG-CTF/jm_devsecops/.github/workflows/frontend-ci.yml@jm`으로 호출하고 있다.
중앙 `jm`과 `main`에는 그 파일이 없고 프론트 Actions 실행 기록도 없으므로 현재
프론트 CI는 실제 병합 검사로 동작하지 않는다.

이 후보 branch에는 `.github/workflows/frontend-ci.yml`을 추가했다. 다음 검사를 한다.

```text
frontend contract
└─ package.json, package-lock.json, Dockerfile과 입력 경로 확인

frontend secrets and SCA
└─ Gitleaks 전체 기록 → Trivy filesystem SCA → SARIF

frontend SAST and npm audit
└─ Semgrep JavaScript·React → npm audit HIGH·CRITICAL 차단 → 보고서

frontend build and container
└─ npm ci → Vite build → Docker build → 비루트 확인
   → Nginx 정적 페이지 smoke → Trivy image·secret 검사 → SARIF
```

## 프론트 저장소에서 나중에 바꿀 caller

프론트팀의 branch와 push 허락을 받은 뒤 기존 `.github/workflows/ci.yml`의 중앙
호출 부분을 다음 구조로 바꾼다. 후보 검증 중에는 태그 대신 중앙의 정확한 40자리
commit SHA를 사용한다.

```yaml
name: Frontend CI

on:
  push:
    branches: ["main"]
  pull_request:
    branches: ["main"]
  workflow_dispatch:

permissions:
  contents: read
  security-events: write

concurrency:
  group: frontend-ci-${{ github.workflow }}-${{ github.head_ref || github.ref }}
  cancel-in-progress: true

jobs:
  frontend-ci:
    uses: MSG-CTF/jm_devsecops/.github/workflows/frontend-ci.yml@<중앙-40자리-commit-SHA>
    with:
      working-directory: .
      dockerfile-path: ./Dockerfile
      node-version: "20.19.5"
      container-port: 80
```

이 CI는 Secret을 요구하지 않으므로 `secrets: inherit`를 사용하지 않는다. 중앙 후보가
실제 프론트에서 검증되고 `v3.4.0`으로 발행된 뒤 SHA를 `@v3.4.0`으로 바꾼다.

## 현재 프론트 main에서 예상되는 첫 실패

2026-09-06 기준으로 임시 디렉터리에서 `npm ci`, `npm run build`, `npm audit`을
실행했다. Vite build는 성공했지만 npm audit은 HIGH 2건과 MODERATE 1건을 보고했다.
또한 현재 최종 `nginx:1.27-alpine` image에는 `USER`가 없어 root로 실행된다.

따라서 새 중앙 CI를 연결하면 처음에는 다음 두 gate가 실패하는 것이 정상이다.

1. `frontend SAST and npm audit`: `nanoid` 등 HIGH 의존성 해결 필요
2. `frontend build and container`: 비루트 Nginx runtime 전환 필요

프론트팀이 `package-lock.json`을 안전한 버전으로 갱신하고 테스트한 뒤 커밋해야 한다.
`npm audit fix --force`를 자동 실행하면 major 버전이 바뀔 수 있으므로 사용하지 않는다.
Nginx는 Cloud Run의 `PORT`와 `BACKEND_URL`을 시작 시점에 template으로 적용하고
비루트 사용자가 8080 같은 비특권 port에서 실행하도록 변경하는 방식을 권장한다.

## 완료 기준

- PR에서 중앙 호출 job 세 개가 모두 실행된다.
- npm HIGH·CRITICAL 취약점이 0건이다.
- Vite build가 성공한다.
- Nginx runtime 사용자가 root가 아니다.
- 컨테이너 `/`가 성공한다.
- Trivy image HIGH·CRITICAL 및 Secret 발견이 0건이다.
- SARIF와 npm audit artifact가 만들어진다.
- 기능 branch push와 열린 PR에서 같은 commit 검사가 중복 실행되지 않는다.
