#!/usr/bin/env bash
# Start the LIVE bot in the foreground. systemd owns the restart policy.
#
# WHY THIS IS A SEPARATE FILE FROM run.sh
#
#   ec2.tf embeds the boot script into user_data via base64gzip, and carries
#   `user_data_replace_on_change = true`. So ANY edit to run.sh replaces every
#   paper host -- including hosts mid-experiment, through a feed gap, which for
#   a bot that simulates stops from ticks means stop evaluations that did not
#   happen. Adding a live branch to run.sh would have cost that on every future
#   edit. A separate file leaves those bytes untouched forever.
#
# IT DELIBERATELY DOES NOT SOURCE run.sh. Two scripts that drift are better
# than one that replaces a running experiment whenever the live path changes.
#
# WHAT IT DOES DIFFERENTLY
#
#   * pulls the LIVE image tag, which is a different ECR repository
#   * fetches the exchange credentials from Secrets Manager at boot
#   * forwards DELTA_ENV so the process knows which venue it is on
#   * mounts /run/deltabt so the kill switch (live/guards.py) is reachable
#     from the host by anyone with SSM access, in one command
set -euo pipefail

log() { echo "[run_live] $*"; }

# shellcheck disable=SC1091
set -a; . /opt/deltabt/env; set +a

TAG="$(aws ssm get-parameter --region "$AWS_REGION" --name "$SSM_IMAGE_TAG_PARAM" \
        --query Parameter.Value --output text)"
if [[ -z "$TAG" || "$TAG" == "none" ]]; then
  log "no image tag set; refusing to start"
  # 90 is matched by RestartPreventExitStatus in the unit. A plain exit 0 would
  # be RESTARTED, giving a crash loop on a host awaiting its first deploy.
  exit 90
fi

# THE VENUE IS DERIVED THE SAME WAY, AND FOR THE SAME REASON.
#
# var.live_venue was declared, validated, and WIRED TO NOTHING. Terraform
# accepted `live_venue = "mainnet"` (the name before it became "prod"),
# planned it, applied it -- and the host
# still ran testnet, because the only thing that ever set DELTA_ENV was the
# `${DELTA_ENV:-testnet}` fallback below. Prod credentials pointed at the
# testnet URL fail authentication, so it breaks loudly rather than trading the
# wrong book; but "loudly" is not the same as "truthfully", and the operator
# would be debugging a configuration that says one thing and does another.
#
# NO DEFAULT HERE, DELIBERATELY. A read failure must not quietly become
# testnet: that is the same lie in a different place. Terraform creates this
# parameter alongside the host, so its absence means the stack is misconfigured
# and the right answer is to refuse -- 90, the same not-a-crash-loop exit the
# missing image tag and the missing credential both use.
VENUE_PARAM="${SSM_IMAGE_TAG_PARAM%/*}/delta_env"
DELTA_ENV="$(aws ssm get-parameter --region "$AWS_REGION" \
              --name "$VENUE_PARAM" --query Parameter.Value --output text 2>/dev/null || true)"
case "$DELTA_ENV" in
  testnet|prod) ;;
  *)
    log "venue at $VENUE_PARAM is ${DELTA_ENV:-unset}, not testnet or prod; refusing to start"
    exit 90
    ;;
esac
export DELTA_ENV

# THE CREDENTIAL'S NAME IS DERIVED, NOT PASSED. Adding a variable to the
# user_data template would change the rendered bytes for PAPER stacks too, and
# that replaces their instances. SSM_IMAGE_TAG_PARAM is already in the
# environment and already per-stack, so its prefix names this stack's space.
#
# A NAME, NOT AN ARN: the ARN's random suffix exists only once the secret does,
# so requiring it would force the credential to be created after the
# infrastructure. `--secret-id` takes either.
SECRET_PARAM="${SSM_IMAGE_TAG_PARAM%/*}/delta_secret_id"
DELTA_SECRET_ID="$(aws ssm get-parameter --region "$AWS_REGION" \
                     --name "$SECRET_PARAM" --query Parameter.Value --output text 2>/dev/null || true)"
if [[ -z "$DELTA_SECRET_ID" || "$DELTA_SECRET_ID" == "none" ]]; then
  log "no exchange credential configured at $SECRET_PARAM; refusing to start"
  exit 90
fi

# Never on disk, never in the image, never in `docker inspect`. Same treatment
# as the database password, for the same reason.
#
# A MISSING SECRET IS AN EXPECTED STATE, NOT A CRASH. The secret is created by
# an operator, not by Terraform, so between an apply and that happening this
# lookup legitimately fails. Without the `|| true` the bare command substitution
# under `set -euo pipefail` exits non-zero, and Restart=always turns a host
# waiting for its credential into a restart loop -- which is the one failure
# mode exit 90 exists to avoid.
DELTA_SECRET="$(aws secretsmanager get-secret-value --region "$AWS_REGION" \
                 --secret-id "$DELTA_SECRET_ID" --query SecretString --output text 2>/dev/null || true)"
if [[ -z "$DELTA_SECRET" ]]; then
  log "secret $DELTA_SECRET_ID holds no value yet (or cannot be read); refusing to start"
  log "create it with: aws secretsmanager create-secret --name $DELTA_SECRET_ID \\"
  log "  --secret-string '{\"api_key\":\"...\",\"api_secret\":\"...\"}'"
  exit 90
fi
DELTA_API_KEY="$(printf '%s' "$DELTA_SECRET" | python3 -c 'import json,sys;print(json.load(sys.stdin)["api_key"])')"
DELTA_API_SECRET="$(printf '%s' "$DELTA_SECRET" | python3 -c 'import json,sys;print(json.load(sys.stdin)["api_secret"])')"
unset DELTA_SECRET

SECRET="$(aws secretsmanager get-secret-value --region "$AWS_REGION" \
           --secret-id "$DB_SECRET_ARN" --query SecretString --output text)"
DB_USER="$(printf '%s' "$SECRET" | python3 -c 'import json,sys;print(json.load(sys.stdin)["username"])')"
DB_PASS="$(printf '%s' "$SECRET" | python3 -c 'import json,sys;print(json.load(sys.stdin)["password"])')"
unset SECRET
DB_PASS_ENC="$(printf '%s' "$DB_PASS" | python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.stdin.read(), safe=""))')"
unset DB_PASS
DATABASE_URL="postgresql://${DB_USER}:${DB_PASS_ENC}@${DB_HOST}:${DB_PORT}/${DB_NAME}?sslmode=require"

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "${ECR_REPOSITORY_URL%%/*}"
log "pulling ${ECR_REPOSITORY_URL}:${TAG}"
docker pull "${ECR_REPOSITORY_URL}:${TAG}"
docker rm -f deltabot >/dev/null 2>&1 || true

# Root-only file on tmpfs: neither `docker inspect` nor the process table sees
# these. /run is tmpfs, so a reboot clears it and this rewrites it.
install -d -m 0700 /run/deltabt
umask 077
{
  printf 'DATABASE_URL=%s\n' "$DATABASE_URL"
  printf 'DELTA_API_KEY=%s\n' "$DELTA_API_KEY"
  printf 'DELTA_API_SECRET=%s\n' "$DELTA_API_SECRET"
} > /run/deltabt/env
unset DATABASE_URL DB_PASS_ENC DELTA_API_KEY DELTA_API_SECRET

CPU_LIMIT=$(nproc 2>/dev/null || echo 1)
if [ "$CPU_LIMIT" -gt 2 ]; then CPU_LIMIT=2; fi
log "venue=$DELTA_ENV cpu=$CPU_LIMIT"

# /run/deltabt is BIND-MOUNTED so the kill switch works. live/guards.py checks
# for /run/deltabt/HALT before every order, and a path that existed only inside
# the container could not be reached to stop the bot -- which is the one thing
# a kill switch has to be able to do.
exec docker run --rm --name deltabot \
  --env-file /run/deltabt/env \
  -v /run/deltabt:/run/deltabt:ro \
  -e "DELTA_ENV=$DELTA_ENV" \
  -e "DELTABOT_SYMBOLS=$DELTABOT_SYMBOLS" \
  -e "DELTABOT_VARIANT=${DELTABOT_VARIANT:-V1}" \
  -e "DELTABOT_MAX_OPEN=${DELTABOT_MAX_OPEN:-1}" \
  -e "DELTABOT_MAX_DRAWDOWN=${DELTABOT_MAX_DRAWDOWN:-0.10}" \
  -e "DELTABOT_MAX_DAILY_LOSS=${DELTABOT_MAX_DAILY_LOSS:-0.03}" \
  -e "DELTABOT_MAX_CONSEC_LOSSES=${DELTABOT_MAX_CONSEC_LOSSES:-4}" \
  -e "DELTABOT_MAX_HOLD=${DELTABOT_MAX_HOLD:-0}" \
  -e "DELTABOT_MIN_RR=${DELTABOT_MIN_RR:-2.0}" \
  -e "DELTABOT_COOLDOWN_AFTER_TRADE=${DELTABOT_COOLDOWN_AFTER_TRADE:-900}" \
  -e "DELTABOT_COOLDOWN_AFTER_LOSS=${DELTABOT_COOLDOWN_AFTER_LOSS:-3600}" \
  -e TZ=UTC -e PYTHONUNBUFFERED=1 \
  -p 8000:8000 \
  --cpus "$CPU_LIMIT" \
  --log-driver awslogs \
  --log-opt "awslogs-region=$AWS_REGION" \
  --log-opt "awslogs-group=$LOG_GROUP" \
  --log-opt "awslogs-stream=bot" \
  "${ECR_REPOSITORY_URL}:${TAG}"
