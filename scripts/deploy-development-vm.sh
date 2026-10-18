#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly COMPONENT="${1:-}"
readonly REPOSITORY="${2:-}"
readonly COMMIT_SHA="${3:-}"
readonly PUBLIC_DOMAIN="${4:-}"
readonly DEPLOYMENT_ID="${5:-manual}"
readonly BRIDGE_HELPER="${6:-$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/backend_proxy_bridge.py}"

readonly DEPLOY_ROOT="/opt/msg-dev"
readonly COMPOSE_FILE="${DEPLOY_ROOT}/compose.yml"
readonly ENV_FILE="${DEPLOY_ROOT}/.env"
readonly COMPOSE_PROJECT="msg-dev"
readonly DOCKER_NETWORK="msg-dev_default"
readonly STATE_DIR="${DEPLOY_ROOT}/deploy-state"
readonly BACKUP_DIR="${DEPLOY_ROOT}/backups"

case "${COMPONENT}:${REPOSITORY}" in
  backend:MSG-CTF/msg-backend | frontend:MSG-CTF/front-team) ;;
  *)
    echo "허용되지 않은 component/repository 조합입니다." >&2
    exit 2
    ;;
esac

[[ "$COMMIT_SHA" =~ ^[0-9a-f]{40}$ ]] || {
  echo "commit SHA는 소문자 40자리 Git SHA여야 합니다." >&2
  exit 2
}
[[ "$PUBLIC_DOMAIN" =~ ^[a-z0-9.-]+$ ]] || {
  echo "개발 도메인 형식이 올바르지 않습니다." >&2
  exit 2
}
[[ "$DEPLOYMENT_ID" =~ ^[A-Za-z0-9._-]+$ ]] || {
  echo "deployment ID 형식이 올바르지 않습니다." >&2
  exit 2
}

for required_file in "$COMPOSE_FILE" "$ENV_FILE"; do
  test -f "$required_file" || {
    echo "필수 파일이 없습니다: $required_file" >&2
    exit 1
  }
done

command -v docker >/dev/null
command -v git >/dev/null
command -v curl >/dev/null
command -v flock >/dev/null
command -v python3 >/dev/null
test -f "$BRIDGE_HELPER"

mkdir -p "$STATE_DIR" "$BACKUP_DIR"
exec 9>"/var/lock/msg-dev-deploy.lock"
flock -w 900 9 || {
  echo "다른 개발 배포가 15분 넘게 실행 중입니다." >&2
  exit 1
}
if [[ -e "$STATE_DIR/backend-bridge.json" || -e "$STATE_DIR/backend-bridge.nginx.conf" ]]; then
  echo "완료되지 않은 백엔드 전환이 있습니다. 후보를 유지하고 관리자 복구를 기다립니다." >&2
  exit 1
fi

work_dir="$(mktemp -d "/tmp/msg-dev-${COMPONENT}.XXXXXX")"
candidate_container="msg-dev-${COMPONENT}-candidate-${DEPLOYMENT_ID}"
candidate_image="msg-dev-${COMPONENT}:candidate-${COMMIT_SHA:0:12}"
stable_image="msg-dev-${COMPONENT}:latest"
rollback_image="msg-dev-${COMPONENT}:rollback"
rollback_available=false
switched=false
bridge_active=false

cleanup() {
  # The helper persists state before changing Nginx and removes it only after
  # draining candidate workers. No state means cleanup cannot remove a serving candidate.
  if [[ "$bridge_active" == false || ! -e "$STATE_DIR/backend-bridge.json" ]]; then
    docker rm -f "$candidate_container" >/dev/null 2>&1 || true
    rm -f "$STATE_DIR/backend-bridge.nginx.conf"
  else
    echo "전환 상태가 확인되지 않아 서비스 중일 수 있는 후보 컨테이너를 유지합니다." >&2
  fi
  rm -rf "$work_dir"
}
trap cleanup EXIT

compose() {
  docker compose --project-name "$COMPOSE_PROJECT" \
    --file "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
}

# 이 파이프라인이 만든 후보·롤백 이미지 태그만 정리한다.
# 공용 호스트의 다른 빌드 캐시와 이미지, DB/Redis 볼륨은 정리하지 않는다.
prune_deployment_artifacts() {
  local stale_image

  for reference in \
    "msg-dev-${COMPONENT}:candidate-*" \
    "msg-dev-${COMPONENT}:rollback-*"; do
    while IFS= read -r stale_image; do
      [[ -n "$stale_image" ]] || continue
      docker image rm "$stale_image" >/dev/null 2>&1 || true
    done < <(docker image ls --filter "reference=${reference}" \
      --format '{{.Repository}}:{{.Tag}}')
  done

}

bridge() {
  python3 "$BRIDGE_HELPER" "$@" --domain "$PUBLIC_DOMAIN"
}

# 로그인하지 않은 API 점검은 정상적으로 401을 돌려줄 수 있다. 연결 성공은
# 2xx~4xx(단, 경로가 없는 404 제외), 서버 장애는 5xx로 구분한다.
assert_api_reachable() {
  local status
  status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' "$@")"
  if ((10#$status < 200 || 10#$status >= 500 || 10#$status == 404)); then
    echo "API smoke test 실패(HTTP ${status})" >&2
    return 1
  fi
}

rollback_service() {
  if [[ "$switched" == true && "$rollback_available" == true ]]; then
    if [[ "$COMPONENT" == backend ]]; then
      bridge_active=true
      if [[ -f "$STATE_DIR/backend-bridge.json" ]]; then
        # A failed finish may already have routed some requests to the primary.
        bridge hold --container "$candidate_container" || return 1
      else
        local frontend
        frontend="$(compose ps -q frontend)" || return 1
        bridge begin --container "$candidate_container" --frontend "$frontend" || return 1
      fi
    fi
    echo "검증 실패: 이전 ${COMPONENT} 이미지로 되돌립니다." >&2
    docker image tag "$rollback_image" "$stable_image" || return 1
    compose up -d --no-deps --force-recreate "$COMPONENT" || return 1
    if [[ "$COMPONENT" == backend ]]; then
      local primary
      primary="$(compose ps -q backend)" || return 1
      bridge ready --container "$primary" || return 1
    fi
  fi
  if [[ "$bridge_active" == true ]]; then
    local primary
    primary="$(compose ps -q backend)" || return 1
    bridge finish --container "$primary" || return 1
    bridge_active=false
  fi
}

on_error() {
  local exit_code=$?
  trap - ERR INT TERM HUP
  rollback_service || echo "롤백 검증 실패: 후보 컨테이너와 전환 기록을 유지합니다." >&2
  exit "$exit_code"
}
trap on_error ERR
trap 'exit 130' INT
trap 'exit 143' TERM HUP

echo "[준비] 이 구성요소의 오래된 배포 이미지 태그 정리"
prune_deployment_artifacts

echo "[1/7] 정확한 ${REPOSITORY}@${COMMIT_SHA} 가져오기"
git -C "$work_dir" init --quiet
git -C "$work_dir" remote add origin "https://github.com/${REPOSITORY}.git"
git -C "$work_dir" fetch --quiet --depth=1 origin "$COMMIT_SHA"
test "$(git -C "$work_dir" rev-parse FETCH_HEAD)" = "$COMMIT_SHA"
# 작업 디렉터리 자체는 mktemp의 0700 권한으로 다른 사용자가 접근하지 못한다.
# 다만 checkout까지 전역 umask 077을 적용하면 Git의 0644 파일이 0600으로
# 생성되어, Docker 이미지 안의 비루트 프로세스가 COPY된 파일을 읽지 못한다.
# checkout 한 번만 정상 umask를 사용해 저장소에 기록된 실행 권한을 보존한다.
(umask 022 && git -C "$work_dir" checkout --quiet --detach FETCH_HEAD)
test -f "$work_dir/Dockerfile" || {
  echo "저장소 루트에 Dockerfile이 없습니다." >&2
  exit 1
}

echo "[2/7] 후보 이미지 빌드"
docker build --pull --tag "$candidate_image" "$work_dir"
image_user="$(docker image inspect --format '{{.Config.User}}' "$candidate_image")"
case "$image_user" in
  "" | 0 | root | 0:0 | root:root)
    echo "후보 이미지가 root 사용자로 실행됩니다." >&2
    exit 1
    ;;
esac

echo "[3/7] 후보 컨테이너 사전 검사"
if [[ "$COMPONENT" == backend ]]; then
  docker run --rm --network "$DOCKER_NETWORK" --env-file "$ENV_FILE" \
    "$candidate_image" python manage.py check --deploy --fail-level WARNING

  docker run -d --name "$candidate_container" --network "$DOCKER_NETWORK" \
    --memory 768m --cpus 1 --pids-limit 128 \
    --env-file "$ENV_FILE" -p 127.0.0.1::8000 "$candidate_image" >/dev/null
  candidate_port="$(docker port "$candidate_container" 8000/tcp | awk -F: 'NR == 1 {print $NF}')"
  for _ in {1..30}; do
    if curl --fail --silent --show-error --output /dev/null \
      -H "Host: $PUBLIC_DOMAIN" -H 'X-Forwarded-Proto: https' \
      "http://127.0.0.1:${candidate_port}/admin/login/"; then
      break
    fi
    sleep 2
  done
  curl --fail --silent --show-error --output /dev/null \
    -H "Host: $PUBLIC_DOMAIN" -H 'X-Forwarded-Proto: https' \
    "http://127.0.0.1:${candidate_port}/admin/login/"
else
  docker run -d --name "$candidate_container" --network "$DOCKER_NETWORK" \
    -e PORT=80 -e BACKEND_URL=http://backend:8000 \
    -p 127.0.0.1::80 "$candidate_image" >/dev/null
  candidate_port="$(docker port "$candidate_container" 80/tcp | awk -F: 'NR == 1 {print $NF}')"
  for _ in {1..30}; do
    if curl --fail --silent --show-error --output /dev/null \
      -H "Host: $PUBLIC_DOMAIN" -H 'X-Forwarded-Proto: https' \
      "http://127.0.0.1:${candidate_port}/"; then
      break
    fi
    sleep 2
  done
  assert_api_reachable \
    -H "Host: $PUBLIC_DOMAIN" -H 'X-Forwarded-Proto: https' \
    "http://127.0.0.1:${candidate_port}/api/v1/auth/me"
fi
if [[ "$COMPONENT" != backend ]]; then
  docker rm -f "$candidate_container" >/dev/null
fi

if [[ "$COMPONENT" == backend ]]; then
  echo "[4/7] PostgreSQL 백업 후 migration"
  backup_file="${BACKUP_DIR}/pre-backend-${COMMIT_SHA:0:12}-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
  # 이 변수들은 호스트가 아니라 PostgreSQL 컨테이너 안에서 확장되어야 한다.
  # shellcheck disable=SC2016
  compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip -9 >"$backup_file"
  test -s "$backup_file"
  docker run --rm --network "$DOCKER_NETWORK" --env-file "$ENV_FILE" \
    "$candidate_image" python manage.py migrate --plan
  docker run --rm --network "$DOCKER_NETWORK" --env-file "$ENV_FILE" \
    "$candidate_image" python manage.py migrate --noinput
else
  echo "[4/7] 프론트엔드는 DB migration 없음"
fi

if [[ "$COMPONENT" == backend ]]; then
  echo "[전환 준비] DB·Redis와 인증 API 검증 후 후보로 요청 전달"
  frontend_container="$(compose ps -q frontend)"
  # begin이 일부만 진행돼도 EXIT에서 서비스 중인 후보를 제거하지 않는다.
  bridge_active=true
  bridge begin --container "$candidate_container" --frontend "$frontend_container"
fi

echo "[5/7] 현재 이미지 보관 후 후보 이미지로 교체"
current_container="$(compose ps -q "$COMPONENT")"
if [[ -n "$current_container" ]]; then
  current_image_id="$(docker inspect --format '{{.Image}}' "$current_container")"
  docker image tag "$current_image_id" "$rollback_image"
  rollback_available=true
fi
docker image tag "$candidate_image" "$stable_image"
switched=true
compose up -d --no-deps --force-recreate "$COMPONENT"
if [[ "$COMPONENT" == backend ]]; then
  primary_container="$(compose ps -q backend)"
  bridge ready --container "$primary_container"
fi

echo "[6/7] 교체된 서비스와 외부 HTTPS 검사"
for _ in {1..30}; do
  if curl --fail --silent --show-error --output /dev/null \
    -H "Host: $PUBLIC_DOMAIN" -H 'X-Forwarded-Proto: https' \
    http://127.0.0.1:8080/; then
    break
  fi
  sleep 2
done
for _ in {1..30}; do
  if assert_api_reachable \
    -H "Host: $PUBLIC_DOMAIN" -H 'X-Forwarded-Proto: https' \
    http://127.0.0.1:8080/api/v1/auth/me; then
    break
  fi
  sleep 2
done
assert_api_reachable \
  -H "Host: $PUBLIC_DOMAIN" -H 'X-Forwarded-Proto: https' \
  http://127.0.0.1:8080/api/v1/auth/me
curl --fail --silent --show-error --output /dev/null "https://${PUBLIC_DOMAIN}/"
assert_api_reachable "https://${PUBLIC_DOMAIN}/api/v1/auth/me"
if [[ "$COMPONENT" == backend ]]; then
  # finish reloads Nginx with the new Docker DNS address and drains old workers
  # before the candidate may be deleted, including after rollback.
  bridge finish --container "$primary_container"
  bridge_active=false
  curl --fail --silent --show-error --output /dev/null "https://${PUBLIC_DOMAIN}/healthz"
  assert_api_reachable "https://${PUBLIC_DOMAIN}/api/v1/auth/me"
fi

echo "[7/7] 성공한 SHA 기록"
printf '%s\n' "$COMMIT_SHA" >"${STATE_DIR}/${COMPONENT}.sha"
printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"${STATE_DIR}/${COMPONENT}.deployed-at"
switched=false
docker image rm "$candidate_image" >/dev/null 2>&1 || true
prune_deployment_artifacts
echo "배포 성공: ${COMPONENT} ${COMMIT_SHA}"
