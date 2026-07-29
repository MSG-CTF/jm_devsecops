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

EXPOSE 8080

# 운영 실행 명령어 (Cloud Run은 $PORT 환경변수로 리슨 포트를 지정함, 기본 8080)
# 로컬 docker-compose에서는 command를 runserver로 오버라이드해서 사용
CMD ["sh", "-c", "gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8080}"]