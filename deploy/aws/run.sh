#!/usr/bin/env bash
# Start the bot in the foreground. systemd owns the restart policy.
#
# Everything environment-specific comes from /opt/deltabt/env, written by
# user-data from Terraform outputs. Nothing here is account-specific, so this
# file is reviewable in the repository and identical on every host.
set -euo pipefail

# shellcheck disable=SC1091
source /opt/deltabt/env

log() { echo "[run] $*"; }

TAG="$(aws ssm get-parameter --region "$AWS_REGION" --name "$SSM_IMAGE_TAG_PARAM" \
        --query 'Parameter.Value' --output text)"

if [[ -z "$TAG" || "$TAG" == "none" ]]; then
  log "no image tag set in $SSM_IMAGE_TAG_PARAM -- nothing to run yet."
  log "this is the expected state after 'terraform apply' and before the"
  log "first deploy workflow run."
  # 90 is matched by RestartPreventExitStatus in the unit. A plain exit 0
  # would be RESTARTED -- Restart=always means always -- giving a 15-second
  # crash loop on a host that is simply waiting for its first deploy.
  exit 90
fi

# The database password is fetched at START, never stored on disk and never
# baked into the image. RDS rotates it; this picks up the current value on
# every restart.
SECRET="$(aws secretsmanager get-secret-value --region "$AWS_REGION" \
           --secret-id "$DB_SECRET_ARN" --query SecretString --output text)"
DB_USER="$(printf '%s' "$SECRET" | python3 -c 'import json,sys;print(json.load(sys.stdin)["username"])')"
DB_PASS="$(printf '%s' "$SECRET" | python3 -c 'import json,sys;print(json.load(sys.stdin)["password"])')"
unset SECRET

# The password may contain characters that are not URL-safe.
DB_PASS_ENC="$(printf '%s' "$DB_PASS" | python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.stdin.read(), safe=""))')"
unset DB_PASS

DATABASE_URL="postgresql://${DB_USER}:${DB_PASS_ENC}@${DB_HOST}:${DB_PORT}/${DB_NAME}?sslmode=require"

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "${ECR_REPOSITORY_URL%%/*}"

log "pulling ${ECR_REPOSITORY_URL}:${TAG}"
docker pull "${ECR_REPOSITORY_URL}:${TAG}"

docker rm -f deltabot >/dev/null 2>&1 || true

# The DSN is written to a root-only file on TMPFS, never to disk and never to
# `-e`, so it appears neither in `docker inspect` nor in the process table.
# /run is tmpfs, so a reboot clears it; it is rewritten on every start anyway.
install -d -m 0700 /run/deltabt
umask 077
printf 'DATABASE_URL=%s\n' "$DATABASE_URL" > /run/deltabt/env
unset DATABASE_URL DB_PASS_ENC

# THE DEFAULTS BELOW ARE THE STRICT CONFIGURATION, DELIBERATELY.
#
# A host whose /opt/deltabt/env predates these variables falls back to
# max_open=1, a 10% drawdown halt and a 3-loss streak limit -- not to the
# relaxed values. That combination will not match the risk hash of an
# experiment started with the relaxed limits, so the bot refuses to bind and
# says so, instead of quietly running a different configuration than the one
# the experiment claims to be measuring.
#
# --rm plus systemd Restart=always: one owner of the lifecycle, not two.
# Docker's own restart policy is deliberately NOT used, because then a
# `systemctl stop` would be fought by dockerd.
exec docker run --rm --name deltabot \
  --env-file /run/deltabt/env \
  -e "DELTABOT_SYMBOLS=$DELTABOT_SYMBOLS" \
  -e "DELTABOT_VARIANT=${DELTABOT_VARIANT:-V1}" \
  -e "DELTABOT_MAX_OPEN=${DELTABOT_MAX_OPEN:-1}" \
  -e "DELTABOT_MAX_DRAWDOWN=${DELTABOT_MAX_DRAWDOWN:-0.10}" \
  -e "DELTABOT_MAX_CONSEC_LOSSES=${DELTABOT_MAX_CONSEC_LOSSES:-3}" \
  -e "DELTABOT_LOG_LEVEL=$DELTABOT_LOG_LEVEL" \
  -e DELTABOT_API_PORT=8000 \
  -e TZ=UTC \
  -e PYTHONUNBUFFERED=1 \
  -p 127.0.0.1:8000:8000 \
  --log-driver awslogs \
  --log-opt "awslogs-region=$AWS_REGION" \
  --log-opt "awslogs-group=$LOG_GROUP" \
  --log-opt "awslogs-stream=bot/$TAG" \
  --security-opt no-new-privileges:true \
  --cap-drop ALL \
  --read-only \
  --tmpfs /tmp:size=512m \
  --user 10001:10001 \
  --memory 1600m --cpus 2.0 \
  --stop-timeout 45 \
  "${ECR_REPOSITORY_URL}:${TAG}"
