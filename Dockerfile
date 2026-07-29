FROM python:3.12.10-slim

# psycopg2(PostgreSQL 드라이버) 빌드에 필요한 시스템 패키지
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성만 먼저 복사해서 캐시 활용 (코드만 바뀌면 pip install 다시 안 돌게)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 나머지 소스 코드 복사
COPY . .

EXPOSE 8000

# 개발 확인용 실행 명령어. 운영 배포 시엔 gunicorn 등으로 교체 필요
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]