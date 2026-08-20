#!/usr/bin/env bash
# The daily report, on demand, from a laptop.
#
#   scripts/report.sh                 # yesterday (what the schedule reports)
#   scripts/report.sh today           # the UTC day in progress
#   scripts/report.sh 2026-08-19      # a specific UTC day
#   scripts/report.sh status          # the short experiment status instead
#   scripts/report.sh dashboard       # forward the live dashboard to a browser
#
# This is the SAME scripts/daily_report.py the schedule runs, so what appears
# here is what lands in the run summary at 01:30 UTC -- not a second
# implementation that can drift from it.
#
# NOTHING HERE IS PINNED. The instance is found by its Stack tag and the SSM
# document by name convention, so a host replacement -- which happens on every
# user-data change and happened twice during the August capacity outage --
# does not leave this script pointing at an instance that no longer exists.
# The hashes are read from the RUNNING experiment rather than hardcoded, so the
# drift check compares the bot against its own registration instead of against
# a constant somebody forgot to update.
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
STACK="${DELTABT_STACK:-v3}"
ENVIRONMENT="${DELTABT_ENV:-paper}"
ARG="${1:-yesterday}"

here() { cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd; }
ROOT="$(here)"

instance() {
  aws ec2 describe-instances --region "$REGION" \
    --filters "Name=tag:Stack,Values=${STACK}" \
              "Name=instance-state-name,Values=running" \
    --query 'Reservations[].Instances[0].InstanceId' --output text
}

IID="$(instance)"
if [[ -z "$IID" || "$IID" == "None" ]]; then
  echo "no running instance tagged Stack=${STACK} in ${REGION}." >&2
  echo "check: aws ec2 describe-instances --region ${REGION}" >&2
  exit 1
fi

case "$ARG" in
  dashboard)
    # The API listens on 127.0.0.1 ONLY -- there is no inbound security-group
    # rule and no public port, which is why this needs a tunnel rather than a
    # URL. Session Manager forwards it over the agent's existing channel, so
    # nothing is opened to the internet to make this work.
    # start-session is the one mode that needs a LOCAL binary the AWS CLI does
    # not ship: without session-manager-plugin the CLI fails with a message
    # about an unknown plugin, which reads like a permissions problem and is
    # not one. Say what is actually missing.
    if ! command -v session-manager-plugin >/dev/null 2>&1; then
      echo "session-manager-plugin is not installed; port forwarding needs it." >&2
      echo "  https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html" >&2
      echo "The report and status modes do NOT need it and work as-is." >&2
      exit 1
    fi
    echo "forwarding ${IID}:8000 -> http://localhost:8000  (ctrl-c to stop)"
    exec aws ssm start-session --region "$REGION" --target "$IID" \
      --document-name AWS-StartPortForwardingSession \
      --parameters '{"portNumber":["8000"],"localPortNumber":["8000"]}'
    ;;
  status)
    CMD='docker exec deltabot python -m app forward-test status 2>&1 | head -60'
    ;;
  *)
    CMD=""
    ;;
esac

if [[ -n "${CMD:-}" ]]; then
  ID=$(aws ssm send-command --region "$REGION" --instance-ids "$IID" \
        --document-name AWS-RunShellScript \
        --parameters "commands=[\"$CMD\"]" \
        --query 'Command.CommandId' --output text)
  # No fixed sleep: poll until the invocation leaves Pending/InProgress.
  for _ in $(seq 1 40); do
    ST=$(aws ssm get-command-invocation --region "$REGION" \
          --command-id "$ID" --instance-id "$IID" \
          --query 'Status' --output text 2>/dev/null || echo Pending)
    [[ "$ST" == "Pending" || "$ST" == "InProgress" ]] || break
    sleep 2
  done
  aws ssm get-command-invocation --region "$REGION" --command-id "$ID" \
    --instance-id "$IID" --query 'StandardOutputContent' --output text
  exit 0
fi

case "$ARG" in
  yesterday) DAY="$(date -u -d yesterday +%F)" ;;
  today)     DAY="$(date -u +%F)" ;;
  *)         DAY="$ARG" ;;
esac

DOC="deltabt-${ENVIRONMENT}-${STACK}-monitor"
LOG_GROUP="/deltabt/${ENVIRONMENT}/${STACK}/bot"

# Read the hashes off the RUNNING experiment so the drift check is against what
# the bot registered, not against a constant in this file. The parsing is done
# HERE rather than in the remote command: quoting an awk program inside a JSON
# document inside a shell string is three levels of escaping and exactly the
# kind of thing that breaks silently and reports no hashes at all.
HID=$(aws ssm send-command --region "$REGION" --instance-ids "$IID" \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["docker exec deltabot python -m app forward-test status 2>&1 | grep -E \"strategy_hash|risk_hash\""]' \
  --query 'Command.CommandId' --output text)
for _ in $(seq 1 30); do
  ST=$(aws ssm get-command-invocation --region "$REGION" --command-id "$HID" \
        --instance-id "$IID" --query 'Status' --output text 2>/dev/null || echo Pending)
  [[ "$ST" == "Pending" || "$ST" == "InProgress" ]] || break
  sleep 2
done
EXPECT=$(aws ssm get-command-invocation --region "$REGION" --command-id "$HID" \
  --instance-id "$IID" --query 'StandardOutputContent' --output text 2>/dev/null || true)
SH=$(awk '/strategy_hash/ {print $2; exit}' <<<"$EXPECT")
RH=$(awk '/risk_hash/     {print $2; exit}' <<<"$EXPECT")

ARGS=(--instance-id "$IID" --document "$DOC" --region "$REGION"
      --environment "$ENVIRONMENT" --stack "$STACK"
      --log-group "$LOG_GROUP" --day "$DAY")
# `if`, not `[[ ... ]] && ...`. This script runs `set -e`, and a trailing &&
# whose test is FALSE returns non-zero -- so on the one path that matters here,
# an experiment that is not running and therefore reports no hashes, the
# one-liner form would exit silently instead of producing an unpinned report.
if [[ -n "$SH" ]]; then ARGS+=(--expect-strategy-hash "$SH"); fi
if [[ -n "$RH" ]]; then ARGS+=(--expect-risk-hash "$RH"); fi

echo "# instance ${IID} · stack ${STACK} · day ${DAY} (UTC)" >&2
if [[ -n "$SH" ]]; then
  echo "# expecting strategy ${SH} risk ${RH}" >&2
else
  echo "# no RUNNING experiment: reporting without a drift check" >&2
fi
exec python3 "$ROOT/scripts/daily_report.py" "${ARGS[@]}"
