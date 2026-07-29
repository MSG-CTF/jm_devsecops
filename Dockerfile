FROM python:3.12.10-slim

# 베이스 이미지의 알려진 취약점 패치 + psycopg2(PostgreSQL 드라이버) 빌드에 필요한 시스템 패키지
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성만 먼저 복사해서 캐시 활용 (코드만 바뀌면 pip install 다시 안 돌게)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 나머지 소스 코드 복사
COPY . .

# root로 컨테이너를 실행하지 않는다 - 컨테이너 탈출 시 피해 반경 최소화
RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && chown -R app:app /app
ENV HOME=/app
USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/')" || exit 1

# 운영 실행 명령어 (Cloud Run은 $PORT 환경변수로 리슨 포트를 지정함, 기본 8080)
# 로컬 docker-compose에서는 command를 runserver로 오버라이드해서 사용
CMD ["sh", "-c", "gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8080}"]