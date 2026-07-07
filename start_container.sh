#!/bin/bash
set -e
set -o pipefail

program=$1
if test -z "$program"; then
  program='main.py'
else
  shift
fi

echo 'Starting container'

# /etc/docker_application_image_version.txt is created during docker build
export IMAGE_VERSION=$(cat /etc/docker_application_image_version.txt)

function check_env_param() {
  env_param_name=$1
  env_param_value=$(eval 'echo -n $'$env_param_name)
  if test -z "$env_param_value"; then
    echo "Invalid $1 in env" 1>&2
    exit 1
  fi
}
check_env_param 'PARAMETER_STORE_REGION'
check_env_param 'PARAMETER_STORE_BASE_PATH'

echo 'Pulling environment from parameter store'

# Separate base paths for
# - COMMON: '.../common/' path suffix (same values across different application versions)
# - VERSIONED: '.../<IMAGE_VERSION>/' path suffix (to be used to specify different env values across application versions)
# VERSIONED env values always override COMMON if specified under both paths
function pull_env() {
  sub_path=$1
  aws --output text --region $PARAMETER_STORE_REGION ssm get-parameters-by-path \
    --path "$PARAMETER_STORE_BASE_PATH""$sub_path"'/' --with-decryption |
    sort |
    awk '{ sub("^.*/", "", $5); print "test -z \"$"$5"\" && export '\''"$5"="$7"'\'' || true" }' ||
    true
}

# Finish all env download from paramater store before exporting to env
# to avoid the scenario where different AWS credentials from parameter store
# are exported before finishing the env download from parameter store
#versioned_env=$(pull_env $IMAGE_VERSION)
common_env=$(pull_env common)

# pull_env variables are only exported if they are not already set
# Hence, eval versioned_env first, then common_env so that versioned_env
# variables take precedence over common_env variables
#eval "$versioned_env"
eval "$common_env"

echo 'Loaded environment'

# Start Filebeat if 'MONITORING_ES_HOST' is set in the environment
if [ -n "${MONITORING_ES_HOST}" ]; then
  # Substitute the variables in /etc/filebeat/filebeat.yml.template and put the result in /etc/filebeat/filebeat.yml
  echo 'Creating filebeat.yml from template'
  
  envsubst \
    '\$MONITORING_ES_USERNAME \
     \$MONITORING_ES_PASSWORD \
     \$MONITORING_ES_HOST \
     \$MONITORING_ES_PROTOCOL \
     \$BEATS_NAME \
     \$IMAGE_VERSION \
     \$FILEBEAT_LOGS_PATH \
     \$INDEX_NAME_PREFIX' \
    < /etc/filebeat/filebeat.yml.template \
    > /etc/filebeat/filebeat.yml

  echo 'Starting Filebeat in the background'
  # Start Filebeat in the background
  filebeat -path.home /usr/share/filebeat \
           -path.config /etc/filebeat \
           -path.data /var/lib/filebeat \
           -path.logs /var/log/filebeat \
           -d "publish" &> /dev/null &
fi

echo "Starting $program"
python "$program" $@
