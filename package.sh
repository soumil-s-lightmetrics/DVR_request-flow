#!/usr/bin/env bash
set -e
set -o pipefail

CURDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" > /dev/null 2>&1 && pwd)"
pushd $CURDIR &> /dev/null

function _exit() {
  popd &> /dev/null && exit $1
}

IMAGE_NAME=chat-knowledge-base

# Read version from VERSION file if not set via environment variable
if [ -z "$IMAGE_VERSION" ]; then
  if [ -f "VERSION" ]; then
    BASE_VERSION=$(cat VERSION | tr -d '[:space:]')
    if ! echo "$BASE_VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$'; then
      echo "Error: Invalid version format in VERSION file: ${BASE_VERSION}"
      echo "Version must be in format: major.minor.patch (e.g., 1.2.4) with an optional prerelease suffix (e.g., 1.2.4-prerelease)"
      _exit 1
    fi
    IMAGE_VERSION="$BASE_VERSION$(git status --porcelain | grep -q . && echo '-unstable' || true)"
  else
    # Fall back to deriving the version from git
    IMAGE_VERSION=$(git describe --tags --always | sed 's|^v\([0-9]\)|\1|g')$(git status --porcelain | grep -q . && echo '-unstable' || true)
  fi
fi

echo Building $IMAGE_NAME:$IMAGE_VERSION

hostPort=5000

# Remove existing image and build it anew
docker images | grep '^'$IMAGE_NAME' *'$IMAGE_VERSION' ' &> /dev/null && echo Deleting existing docker image && docker image rm $IMAGE_NAME:$IMAGE_VERSION
docker build --platform linux/amd64 --build-arg IMAGE_VERSION=$IMAGE_VERSION -t $IMAGE_NAME:latest -t $IMAGE_NAME:$IMAGE_VERSION . || (echo 'Docker build failed' && _exit 1)

echo 'Docker build successful. Starting container...'

docker rm -f $IMAGE_NAME || true

# Determine host networking based on OS
# macOS/Windows: host.docker.internal works natively
# Linux: needs --add-host flag (Docker 20.10+)
EXTRA_HOST_FLAG=""
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
  EXTRA_HOST_FLAG="--add-host=host.docker.internal:host-gateway"
fi

# Start local container
# Note: LLM_POSTGRES_HOST is overridden to host.docker.internal to allow container
# to connect to PostgreSQL running on host machine
set -x
container=$(docker run --platform linux/amd64 -d --name $IMAGE_NAME -e IMAGE_VERSION=$IMAGE_VERSION \
  -e LOG_TO_STDOUT \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN \
  -e LLM_POSTGRES_HOST=host.docker.internal \
  --env-file ./.env \
  -p $hostPort:5000 \
  $EXTRA_HOST_FLAG \
  $IMAGE_NAME:$IMAGE_VERSION | cut -c-12)
set +x

function _deletecontainer() {
  docker rm -f $container &> /dev/null && echo Deleted container || (echo Failed to delete container && _exit 1)
}

# Check if container started successfully
if [ $(docker inspect -f '{{.State.Running}}' $container) == "false" ]; then
  printf '\033[0;33m'
  echo Container failed to start. Logs:
  docker logs -f $container
  printf '\033[0;0m'
  _deletecontainer
  _exit 1
else
  echo "Container $container is running in detached mode"
fi

# Check if container application is running
printf 'Health checking application\n'
# Wait for the container node process to start or fail
echo 'Giving time to the server to start...'
echo

function health_check() {
  curl http://localhost:$hostPort/health-check --silent -f -o /dev/null 2>&1
}
timeout_s=60
start_time=$(date +%s)
wait_till=$(($start_time + $timeout_s))

printf '\033[0;34m'
docker logs -f $container &
docker_logs_pid=$!

while ! health_check && test $(date +%s) -le "$wait_till"; do
  # Fail fast if container has crashed
  if [ "$(docker inspect -f '{{.State.Running}}' $container 2>/dev/null)" != "true" ]; then
    echo ""
    printf '\033[0;33m'
    echo "Container crashed during startup. Logs:"
    docker logs $container
    printf '\033[0;0m'
    kill $docker_logs_pid 2>/dev/null
    _deletecontainer
    _exit 1
  fi
  sleep 0.5
done

elapsed_time=$(($(date +%s) - $start_time))

if health_check; then
  printf '\033[0;32m'
  echo "Health check passed after ${elapsed_time}s"
else
  printf '\033[0;33m'
  echo "Health check failed after ${elapsed_time}s timeout"
  echo "Testing endpoint manually:"
  curl -v http://localhost:$hostPort/health-check 2>&1 | head -15
fi
printf '\033[0;0m'
kill $docker_logs_pid

# Stop and delete container
# NON_INTERACTIVE can be set via environment variable to skip prompts
if [ "${NON_INTERACTIVE}" = "true" ]; then
  should_delete_container='y'
else
  should_delete_container='y'
  printf '\033[0;0m'
  read -p $'\e[33mTest(s) successful. Delete container? [Y/n/f/s/r/^]:\e[0m ' should_delete_container1
  if ! test -z $should_delete_container1; then
    should_delete_container=$should_delete_container1
  fi
fi

if test "${should_delete_container,,}" == "y"; then
  _deletecontainer
  exit 0
elif test "${should_delete_container,,}" == "f"; then
  printf '\033[0;34m'
  docker logs -f $container -n 0
elif test "${should_delete_container,,}" == "s"; then
  printf '\033[0;34m'
  docker exec -it $container /bin/sh
elif test "${should_delete_container,,}" == "r"; then
  printf '\033[0;31m'
  echo Redoing package.sh. Warning: pulled environment variables are not cleared.
  printf '\033[0;34m'
  exec ./package.sh
elif test "${should_delete_container,,}" == "^"; then
  printf '\033[0;34m'
  /usr/bin/env ecr_push_image.sh
fi
