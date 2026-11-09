# README 그림과 구현 근거

- [플랫폼 자동 배포](https://github.com/MSG-CTF/jm_devsecops/blob/b8a9a47dba13580ce1546ced39f685f173e49556/docs/development-vm-auto-deploy.md)

구성도는 데이터 흐름을 설명하며 실시간 서비스 상태나 전체 대상의 운영 검증 결과를 나타내지 않습니다

## SLA 수집·알림

서비스 API·전체 인스턴스 목록·노드 수집기를 Sentinel에 연결했습니다
Discord 알림과 Prometheus 메트릭 조회는 별도 경로로 표시했습니다

[구현 근거](https://github.com/MSG-CTF/msg-ctf.github.io/blob/main/docs/sla-flow.md) · [Sentinel 기준 커밋](https://github.com/MSG-CTF/sentinel/tree/849f267795a3878bf234eb5db7054177ad5614b8)
