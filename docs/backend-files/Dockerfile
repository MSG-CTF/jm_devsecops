FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a

RUN apt-get update \
    && apt-get upgrade --yes \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app app

COPY --chown=app:app . .

ENV HOME=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER app

EXPOSE 8080

# HEALTHCHECK_HOST는 DJANGO_ALLOWED_HOSTS에도 들어 있는 운영 호스트 하나여야 한다.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; port=os.environ.get('PORT','8080'); host=os.environ.get('HEALTHCHECK_HOST','localhost'); request=urllib.request.Request(f'http://127.0.0.1:{port}/healthz',headers={'Host':host,'X-Forwarded-Proto':'https'}); urllib.request.urlopen(request,timeout=2)" || exit 1

# exec를 사용해야 Gunicorn이 컨테이너의 종료 신호를 직접 받는다.
CMD ["sh", "-c", "exec gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8080} --access-logfile - --error-logfile -"]
