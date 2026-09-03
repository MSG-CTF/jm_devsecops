# 자체 운영 PostgreSQL·Redis 기준 배포 계획

이 문서는 개발 GCP에서 Cloud SQL과 Memorystore를 만들지 않고, 팀이 PostgreSQL과 Redis를 직접 운영한다는 결정에 맞춘 기준 계획이다.

## 한눈에 보는 결론

```text
사용자
  → 프론트 Cloud Run
      → 백엔드 Cloud Run
          → Direct VPC egress
              → 사설 IP의 데이터 전용 VM
                  ├── PostgreSQL
                  └── Redis
```

- Cloud SQL 생성 단계는 삭제한다.
- Memorystore 생성 단계도 삭제한다.
- PostgreSQL과 Redis는 인터넷에 공개하지 않는다.
- Cloud Run 백엔드와 migration Job만 VPC를 통해 접속한다.
- 비밀번호는 GitHub 변수나 저장소에 넣지 않고 Secret Manager에 둔다.
- `v3.4.0` 후보의 build, digest, migration, Cloud Run, smoke, DAST 흐름은 유지한다.
- 직접 운영이므로 설치, 패치, 용량, 백업, 복구와 장애 대응은 팀 책임이다.

## 먼저 결정받아야 하는 다섯 가지

실제 자원을 만들기 전에 PM, 백엔드와 인프라 담당자가 다음을 확정한다.

1. 개발 PostgreSQL과 Redis를 어느 서버에서 실행할 것인가?
2. 개발 단계에서 한 데이터 전용 VM을 함께 사용할 것인가, VM을 둘로 나눌 것인가?
3. PostgreSQL 백업 주기, 보관 기간과 복구 책임자는 누구인가?
4. Redis 데이터가 사라져도 되는가, AOF 또는 RDB persistence가 필요한가?
5. 장애가 발생했을 때 허용할 중단 시간과 허용할 데이터 손실량은 얼마인가?

권장 개발안은 **데이터 전용 VM 한 대에 PostgreSQL과 Redis를 컨테이너로 분리하고, 데이터는 별도 Persistent Disk에 저장하는 것**이다. 개발 검증 비용과 작업 시간을 줄일 수 있기 때문이다. 다만 이 VM 한 대가 멈추면 두 서비스가 같이 멈추므로 운영환경에는 그대로 복사하지 않는다.

현재 실행 중인 `backend-dev-vm`을 바로 데이터 서버로 바꾸지는 않는다. 기존 용도, 데이터와 접속자를 먼저 확인해야 하며, 애플리케이션·DB·Redis를 한 공개 VM에 섞으면 장애 범위와 공격 범위가 커진다.

## 바뀌지 않는 부분

다음 CD 기능은 데이터베이스 제공자가 Google인지 팀인지와 관계없이 그대로 필요하다.

1. 백엔드 CI가 성공한 commit만 image로 만든다.
2. Artifact Registry에 image를 저장한다.
3. tag가 아니라 검사한 정확한 image digest를 사용한다.
4. 같은 digest로 `python manage.py migrate --noinput` Job을 한 번 실행한다.
5. migration이 성공한 경우에만 백엔드 Cloud Run revision을 배포한다.
6. smoke test와 ZAP Baseline을 실행한다.
7. 애플리케이션 검사 실패 시 이전 revision으로 트래픽을 되돌린다.

애플리케이션 rollback은 이미 적용된 DB migration을 자동으로 되돌리지 못한다. 삭제나 이름 변경 같은 파괴적 migration은 백업과 별도 승인 없이 실행하지 않는다.

## 바뀌는 부분

### Cloud SQL 대신 자체 PostgreSQL

`DEV_POSTGRES_HOST`에는 Cloud SQL IP가 아니라 데이터 VM의 **고정 사설 IP** 또는 내부 DNS 이름을 넣는다.

```text
POSTGRES_DB       일반 GitHub Variable
POSTGRES_USER     일반 GitHub Variable
POSTGRES_HOST     고정 사설 IP 또는 내부 DNS
POSTGRES_PORT     5432
POSTGRES_PASSWORD Secret Manager
```

PostgreSQL은 다음 조건을 만족해야 한다.

- `listen_addresses`는 필요한 사설 인터페이스만 사용한다.
- `pg_hba.conf`는 Cloud Run 개발 subnet 등 합의한 출발지만 허용한다.
- GCP 방화벽은 DB 서버의 5432를 인터넷에 열지 않는다.
- 앱 전용 사용자는 superuser가 아니어야 한다.
- migration 전용 사용자의 권한은 백엔드 담당자와 따로 검토한다.
- 가능하면 TLS를 켜고 클라이언트가 인증서를 검증하게 한다.
- 데이터 디스크와 백업 위치를 OS 디스크와 구분한다.

### Memorystore 대신 자체 Redis

Redis도 데이터 VM의 고정 사설 IP에서만 듣게 한다.

- `bind`와 protected mode를 활성화한다.
- 6379를 인터넷 전체에 공개하지 않는다.
- 전용 ACL 사용자와 강한 비밀번호를 사용한다.
- 위험한 관리 명령 접근을 제한한다.
- 캐시인지 작업 큐인지 용도를 확정하고 persistence 정책을 정한다.
- PostgreSQL의 원본 데이터를 Redis에만 저장하지 않는다.

인증정보가 포함된 `REDIS_URL`을 GitHub Repository Variable로 두면 안 된다. 전체 URL을 `redis-url-dev` 같은 Secret Manager Secret으로 보관하고 Cloud Run의 `REDIS_URL`로 직접 연결하도록 CD 후보를 수정한다.

## 순서대로 하는 실제 작업

### 0단계: 백엔드 설정 계약 확인

백엔드 팀과 다음 환경변수 이름과 형식을 먼저 확인한다.

```text
POSTGRES_DB
POSTGRES_USER
POSTGRES_HOST
POSTGRES_PORT
POSTGRES_PASSWORD
REDIS_URL
```

또한 PostgreSQL TLS 옵션과 Redis TLS 사용 여부를 Django 설정이 어떻게 받는지 확인한다. 이름이 맞지 않으면 서버를 올려도 백엔드가 연결하지 못한다.

성공 기준:

- Django 설정 코드와 실제 변수 이름이 일치한다.
- Redis를 실제로 사용하는 코드 경로가 확인된다.
- 개발 PostgreSQL과 Redis 버전이 확정된다.

### 1단계: 데이터 서버 설계 승인

개발용 데이터 전용 VM 이름, 리전, 사양, 디스크 크기와 소유자를 결정한다. VM에는 가능하면 공인 IP를 붙이지 않는다. 관리 접속은 IAP, VPN, bastion 등 승인된 경로만 사용한다.

성공 기준:

- 비용 담당자의 승인이 있다.
- 누가 OS와 PostgreSQL·Redis 보안 업데이트를 하는지 정해졌다.
- 기존 `backend-dev-vm`을 재사용할지 새 VM을 만들지 서면으로 정해졌다.

### 2단계: 네트워크와 방화벽

데이터 VM을 `msg-dev-vpc`의 개발 subnet에 둔다. 백엔드 Cloud Run과 migration Job은 Direct VPC egress의 `private-ranges-only`로 사설 주소에 연결한다.

방화벽은 대상 서버와 출발지를 좁힌다.

```text
허용: Cloud Run 개발 워크로드 → 데이터 VM TCP 5432
허용: Cloud Run 개발 워크로드 → 데이터 VM TCP 6379
거부: 인터넷 → 데이터 VM TCP 5432/6379
```

Cloud Run backend와 migration Job에는 개발 데이터 접근 전용 network tag를 붙이고, 방화벽은 그 tag에서 오는 연결만 허용하도록 구성한다. 현재 CD 후보에는 이 tag 입력이 없으므로 배포 workflow에 추가해야 한다.

성공 기준:

- 외부 컴퓨터에서 5432와 6379에 접속할 수 없다.
- 허용된 Cloud Run 시험 Job에서는 접속된다.
- 방화벽 대상이 프로젝트의 모든 VM이 아니라 데이터 VM으로 제한된다.

### 3단계: PostgreSQL·Redis 설치

버전이 고정된 공식 image 또는 검토된 패키지를 사용한다. `latest`는 사용하지 않는다. 개발에서도 기본 비밀번호와 예제 비밀번호를 사용하지 않는다.

PostgreSQL에는 앱 database와 최소 권한 사용자를 만든다. Redis에는 전용 ACL 사용자를 만든다. 실제 비밀번호는 터미널 기록, 문서, Slack이나 GitHub에 붙여 넣지 않고 안전한 전달 경로를 통해 Secret Manager 새 숫자 version으로 넣는다.

성공 기준:

- 재부팅 후 두 서비스가 정상 시작한다.
- PostgreSQL 데이터가 Persistent Disk에 남는다.
- Redis persistence를 선택했다면 재부팅 시험 후 정책대로 데이터가 남는다.
- 기본 계정과 불필요한 database가 정리됐다.

### 4단계: 백업과 복구 시험

디스크 snapshot만으로 PostgreSQL의 논리적 일관성이 항상 보장된다고 가정하지 않는다. `pg_dump` 또는 합의한 PostgreSQL 전용 백업과 Persistent Disk snapshot을 함께 설계한다.

개발 권장 최소 기준:

- 매일 PostgreSQL 백업
- 정해진 보관 기간
- 백업 파일 암호화와 접근 권한 제한
- 다른 빈 database에 실제 restore 시험
- 복구 절차와 책임자 기록

성공 기준은 "백업 파일이 있다"가 아니라 **새 빈 환경에 복구해서 대표 데이터를 조회할 수 있다**는 것이다.

### 5단계: Secret Manager와 CD 계약 수정

기존 Secret은 유지한다.

```text
django-secret-key-dev
jwt-secret-dev
postgres-password-dev
koth-team-token-secret-dev
```

다음 Secret을 추가한다.

```text
redis-url-dev
```

중앙 `reusable-backend-deploy-dev.yml` 후보는 일반 입력 `redis_url` 대신 `redis_secret_id`와 숫자 `redis_secret_version`을 받고, `REDIS_URL`을 Cloud Run Secret으로 연결하도록 수정했다.

또한 backend service와 migration Job에 같은 개발 데이터 접근 전용 network tag를 전달하는 입력을 추가했다. 이 tag는 데이터 VM의 5432와 6379 방화벽 허용 규칙에만 사용한다.

성공 기준:

- Actions 로그와 Cloud Run 일반 환경변수 출력에 비밀번호가 없다.
- backend runtime과 migration runtime만 필요한 Secret을 읽는다.
- GitHub WIF deployer는 Secret 실제 값을 읽지 않는다.

### 6단계: 연결 전용 검증

서비스를 배포하기 전에 Cloud Run Job과 같은 network/runtime 계정으로 다음을 확인한다.

1. PostgreSQL DNS 또는 사설 IP 연결
2. TLS를 쓴다면 서버 인증서 검증
3. `SELECT 1`
4. 테스트 table에 제한된 쓰기와 정리
5. Redis `PING`
6. 짧은 만료시간을 가진 `SET`과 `GET`, 마지막에 삭제

비밀번호는 명령행 인자로 출력하지 않는다.

성공 기준:

- 허용된 Job은 성공한다.
- VPC 연결을 제거한 Job은 실패한다.
- 잘못된 비밀번호와 권한 부족도 정상적으로 실패한다.

### 7단계: migration과 백엔드 수동 배포

검사한 같은 image digest로 migration Job을 먼저 실행한다. migration 성공 후에만 `msg-backend-dev`를 배포한다.

성공 기준:

```text
migration 성공
→ Cloud Run Ready
→ Django smoke 성공
→ 실제 API가 PostgreSQL 데이터를 읽고 씀
→ 실제 Redis 사용 경로 성공
→ ZAP Baseline 보고서 생성
```

### 8단계: 프론트 연결

프론트 Nginx의 `/api/v1` proxy를 새 백엔드 Cloud Run URL에 연결한다. 프론트 URL 한 곳에서 로그인, JWT 갱신, 문제 목록과 대표 쓰기 기능을 시험한다.

성공 기준:

- 브라우저에 CORS나 mixed-content 오류가 없다.
- 화면에서 만든 데이터가 PostgreSQL에 기록된다.
- Redis를 쓰는 기능이 실제로 동작한다.
- Secret 값은 브라우저 응답과 로그에 나오지 않는다.

### 9단계: 자동 개발 CD 활성화

수동 배포, 실패 시험과 rollback 시험이 끝난 뒤에만 `ENABLE_DEV_CD=true`로 바꾼다. 그 뒤 백엔드 `main` 변경은 CI, build, migration, deploy, smoke와 DAST 순서로 진행한다.

## 장애와 rollback 기준

- 앱 revision 문제: 이전 Cloud Run revision으로 트래픽 rollback
- migration 문제: 자동 `migrate down` 대신 우선 forward fix 검토
- PostgreSQL 서버 장애: 문서화한 backup에서 복구
- Redis 장애: 캐시면 비우고 재생성, 큐나 상태 저장소면 합의한 persistence·복구 절차 사용
- 데이터 VM 전체 장애: PostgreSQL과 Redis가 함께 중단될 수 있음을 개발환경 위험으로 기록

## 완료 조건

다음 항목이 모두 증거와 함께 확인되어야 개발 배포 완료라고 말한다.

- [ ] Cloud SQL과 Memorystore가 생성되지 않았다.
- [ ] PostgreSQL과 Redis는 사설 IP로만 접근된다.
- [ ] 인터넷에서 5432와 6379가 닫혀 있다.
- [ ] PostgreSQL restore 시험이 성공했다.
- [ ] Redis의 데이터 보존 정책이 문서화됐다.
- [ ] Redis 인증정보가 GitHub 일반 변수에 없다.
- [ ] migration 실패 시 새 백엔드가 배포되지 않는다.
- [ ] 백엔드 API가 PostgreSQL과 Redis를 실제로 사용한다.
- [ ] 프론트 URL에서 실제 API 기능을 확인했다.
- [ ] 장애 담당자, 백업 담당자와 비용 담당자가 정해졌다.

## 과금에 대한 주의

Cloud SQL과 Memorystore 요금은 발생하지 않지만, 자체 운영 VM, Persistent Disk, snapshot, 네트워크와 로그 비용은 발생한다. 따라서 "직접 운영"은 무료라는 뜻이 아니라 관리형 서비스 비용과 운영 책임을 다른 자원으로 옮기는 선택이다.
