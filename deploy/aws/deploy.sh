#!/usr/bin/env bash
# Roll the bot onto a new image tag, verify it, and roll back if it fails.
#
# Invoked by SSM Run Command from the deploy workflow -- never over SSH.
# Usage: deploy.sh <image-tag>
set -euo pipefail

# shellcheck disable=SC1091
source /opt/deltabt/env

TAG="${1:?usage: deploy.sh <image-tag>}"
log() { echo "[deploy] $*"; }

param() { aws ssm get-parameter --region "$AWS_REGION" --name "$1" --query 'Parameter.Value' --output text; }
set_param() { aws ssm put-parameter --region "$AWS_REGION" --name "$1" --value "$2" --type String --overwrite >/dev/null; }

PREVIOUS="$(param "$SSM_IMAGE_TAG_PARAM")"
log "current=$PREVIOUS requested=$TAG"

# Fail before touching anything if the image is not actually in ECR. A deploy
# that half-succeeds is worse than one that never starts.
aws ecr describe-images --region "$AWS_REGION" \
  --repository-name "$ECR_REPOSITORY_NAME" --image-ids "imageTag=$TAG" >/dev/null

start_and_verify() {
  local tag="$1" deadline
  set_param "$SSM_IMAGE_TAG_PARAM" "$tag"
  systemctl restart deltabt.service

  # Warm-up backfills ~7 days across four symbols before the bot is ready, so
  # the window is generous. 900s is roughly 3x the observed warm-up.
  deadline=$(( $(date +%s) + 900 ))
  while (( $(date +%s) < deadline )); do
    if curl -fsS --max-time 5 http://127.0.0.1:8000/readyz >/dev/null 2>&1; then
      log "readyz passed on $tag"

      # /healthz is reported but is NOT the rollback gate. It is a statement
      # about MARKET DATA -- a candle gap in the seconds after a restart makes
      # it red for reasons that have nothing to do with the image. Rolling
      # back a good build because the feed hiccuped would be wrong.
      if curl -fsS --max-time 5 http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
        log "healthz green"
      else
        log "WARNING: healthz not green yet -- check the feed, not the image"
      fi
      return 0
    fi
    if ! systemctl is-active --quiet deltabt.service; then
      log "service died while starting $tag"
      return 1
    fi
    sleep 10
  done
  log "timed out waiting for readyz on $tag"
  return 1
}

if start_and_verify "$TAG"; then
  set_param "$SSM_IMAGE_TAG_PREVIOUS_PARAM" "$PREVIOUS"
  log "deployed $TAG (previous $PREVIOUS retained for rollback)"
  exit 0
fi

log "DEPLOY FAILED -- rolling back to $PREVIOUS"
journalctl -u deltabt.service -n 100 --no-pager || true

if [[ -z "$PREVIOUS" || "$PREVIOUS" == "none" ]]; then
  log "no previous tag exists; leaving the service stopped rather than looping"
  systemctl stop deltabt.service || true
  exit 1
fi

if start_and_verify "$PREVIOUS"; then
  log "rolled back to $PREVIOUS"
else
  log "ROLLBACK ALSO FAILED -- the problem is not the image. Investigate the"
  log "database and the network before deploying anything else."
fi
exit 1
