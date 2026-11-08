# MSG CTF Platform CI/CD

프론트·백엔드가 재사용하는 코드 검사와 개발 VM 배포 워크플로 저장소입니다
문제 이미지를 발행하는 공통 문제 CI와 구분해 플랫폼 애플리케이션의 빌드·검사·배포를 다룹니다

[플랫폼 전체 소개](https://github.com/MSG-CTF/msg-ctf.github.io)

## 맡은 일

- 호출한 저장소의 코드로 Python·Django·프론트 검사 실행
- Semgrep·Bandit·Trivy·Gitleaks 보안 검사
- PostgreSQL·Redis 연결, 마이그레이션·테스트와 컨테이너 빌드 검사
- 고정된 소스·스크립트로 VM 자동 배포
- 배포 전환 중 API 연결 유지와 실패 시 롤백
- 제한된 SSH 배포와 비밀값 파일·암호화 백업 경로 보존

## 함께 만든 사람들

| 기여자 | 기여 내용 | 관련 PR |
| --- | --- | --- |
| [woalseee](https://github.com/woalseee) | 공통 플랫폼 CI와 개발 VM 배포, 배포 파일·스토리지 관리 | [#8](https://github.com/MSG-CTF/jm_devsecops/pull/8) · [#10](https://github.com/MSG-CTF/jm_devsecops/pull/10) · [#12](https://github.com/MSG-CTF/jm_devsecops/pull/12) |
| [Giheon-Jeon](https://github.com/Giheon-Jeon) | 프론트 검사에 맞춘 Semgrep 규칙 수정 | [#7](https://github.com/MSG-CTF/jm_devsecops/pull/7) |
| [jongcoding](https://github.com/jongcoding) | 배포 전환·롤백, 프론트 제한 SSH 배포와 보안 키·백업 연동 | [#13](https://github.com/MSG-CTF/jm_devsecops/pull/13) · [#14](https://github.com/MSG-CTF/jm_devsecops/pull/14) · [#16](https://github.com/MSG-CTF/jm_devsecops/pull/16) |

2026-11-09까지 병합된 PR과 커밋을 기준으로 정리했습니다

## 기술 구성

GitHub Actions · Python · Docker · PostgreSQL · Redis · Nginx

## 개발·운영 문서

- [개발 VM 자동 배포](docs/development-vm-auto-deploy.md)
- [백엔드 배포 전환](docs/backend-deployment-availability.md)
- [CI/CD 실행 안내](docs/devsecops-runbook.md)
- [기존 개발·설정 참고 문서](README.reference.md)
