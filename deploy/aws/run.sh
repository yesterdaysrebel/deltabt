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

# THE LIST BELOW IS HAND-MAINTAINED, AND THAT IS HOW A GATE WENT MISSING.
#
# The chain from Terraform to the risk engine has THREE links, not two:
# a variable, user_data writing it into /opt/deltabt/env, and this file
# forwarding it into `docker run`. On 2026-08-26 max_daily_loss_pct was added
# to the first two and not the third, so the value reached the host, sat in
# /opt/deltabt/env looking correct, and never entered the container. The gate
# read 1.0 -- disabled -- while every artifact above it said 0.02.
#
# `source /opt/deltabt/env` does NOT put a variable in the container's
# environment. Only an explicit -e does. tests/live/test_env_forwarding.py
# now fails if user_data writes a DELTABOT_* that this list does not carry.
#
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
# CPU LIMIT SIZED FROM THE HOST, NOT ASSUMED.
#
# This was hardcoded to `--cpus 2.0`, sized for t4g.small's 2 vCPU. On
# 2026-08-19 AWS ran out of the whole t4g family in ap-south-1a and the
# replacement host was an m6g.medium, which has 1 vCPU. Docker refuses
# outright -- "Range of CPUs is from 0.01 to 1.00, as there are only 1 CPUs
# available" -- so the service crash-looped and the bot never started, turning
# a capacity outage into a second, self-inflicted one.
#
# Capping at 2 keeps the previous ceiling on larger hosts. The container
# measures under 5% CPU steady, so neither bound is close to binding; the
# point is that a hardcoded limit must not decide whether the bot can run at
# all on whatever shape capacity happens to allow.
# An `if` and not `[ ... ] && ...`: this script runs `set -euo pipefail`, and a
# trailing && whose test is false is exactly the shape that quietly aborts a
# boot script on some shells. Not worth being clever about on a host nobody is
# watching at 3am.
CPU_LIMIT=$(nproc 2>/dev/null || echo 1)
if [ "$CPU_LIMIT" -gt 2 ]; then
  CPU_LIMIT=2
fi
log "container cpu limit: $CPU_LIMIT (host has $(nproc 2>/dev/null || echo '?'))"

exec docker run --rm --name deltabot \
  --env-file /run/deltabt/env \
  -e "DELTABOT_SYMBOLS=$DELTABOT_SYMBOLS" \
  -e "DELTABOT_VARIANT=${DELTABOT_VARIANT:-V1}" \
  -e "DELTABOT_MAX_OPEN=${DELTABOT_MAX_OPEN:-1}" \
  -e "DELTABOT_MAX_DRAWDOWN=${DELTABOT_MAX_DRAWDOWN:-0.10}" \
  -e "DELTABOT_MAX_DAILY_LOSS=${DELTABOT_MAX_DAILY_LOSS:-0.02}" \
  -e "DELTABOT_MAX_CONSEC_LOSSES=${DELTABOT_MAX_CONSEC_LOSSES:-3}" \
  -e "DELTABOT_MAX_HOLD=${DELTABOT_MAX_HOLD:-0}" \
  -e "DELTABOT_WPR_BAND_EXIT=${DELTABOT_WPR_BAND_EXIT:-0}" \
  -e "DELTABOT_WPR_EXIT_LONG=${DELTABOT_WPR_EXIT_LONG:--80}" \
  -e "DELTABOT_WPR_EXIT_SHORT=${DELTABOT_WPR_EXIT_SHORT:--20}" \
  -e "DELTABOT_MIN_RR=${DELTABOT_MIN_RR:-2.0}" \
  -e "DELTABOT_COOLDOWN_AFTER_TRADE=${DELTABOT_COOLDOWN_AFTER_TRADE:-900}" \
  -e "DELTABOT_COOLDOWN_AFTER_LOSS=${DELTABOT_COOLDOWN_AFTER_LOSS:-3600}" \
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
  --memory 1600m --cpus "$CPU_LIMIT" \
  --stop-timeout 45 \
  "${ECR_REPOSITORY_URL}:${TAG}"
