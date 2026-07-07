#!/usr/bin/env bash
set -e
set -o pipefail

CURDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" > /dev/null 2>&1 && pwd)"
pushd $CURDIR &> /dev/null

IMAGE_NAME=openai-vectorstore-sync
IMAGE_VERSION=$(git describe --tags --always | sed 's|^v\([0-9]\)|\1|g')$(git status --porcelain | grep -q . && echo '-unstable' || true)
echo Building $IMAGE_NAME:$IMAGE_VERSION

hostPort=5000

function _exit() {
  popd &> /dev/null && exit $1
}

# Remove existing image and build it anew
docker images | grep '^'$IMAGE_NAME' *'$IMAGE_VERSION' ' &> /dev/null && echo Deleting existing docker image && docker image rm $IMAGE_NAME:$IMAGE_VERSION
docker build --platform linux/amd64 --build-arg IMAGE_VERSION=$IMAGE_VERSION -t $IMAGE_NAME:latest -t $IMAGE_NAME:$IMAGE_VERSION . || (echo 'Docker build failed' && _exit 1)

echo 'Docker build successful. Starting container...'

docker rm -f $IMAGE_NAME || true
# Start local container
set -x
container=$(docker run --platform linux/amd64 -d --name $IMAGE_NAME -e IMAGE_VERSION=$IMAGE_VERSION \
  -e LOG_TO_STDOUT \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN \
  --env-file ./.env \
  --network host $IMAGE_NAME:$IMAGE_VERSION | cut -c-12)
set +x

function _deletecontainer() {
  docker rm -f $container &> /dev/null && echo Deleted container || (echo Failed to delete container && _exit 1)
}
function _exit() {
  popd &> /dev/null && exit $1
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

printf '\033[0;34m'
docker logs -f $container &
docker_logs_pid=$!

printf '\033[0;0m'
kill $docker_logs_pid

# Stop and delete container
should_delete_container='y'
printf '\033[0;0m'
read -p $'\e[33mTest(s) successful. Delete container? [Y/n/f/s/r/^]:\e[0m ' should_delete_container1
if ! test -z $should_delete_container1; then
  should_delete_container=$should_delete_container1
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
