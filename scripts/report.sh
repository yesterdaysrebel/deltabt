#!/usr/bin/env bash
# The daily report, on demand, from a laptop. EVERY LIVE STACK BY DEFAULT.
#
#   scripts/report.sh                 # yesterday, every running stack
#   scripts/report.sh today           # the UTC day in progress
#   scripts/report.sh 2026-08-19      # a specific UTC day
#   scripts/report.sh status          # the short experiment status
#   scripts/report.sh dashboard       # forward the live dashboard to a browser
#
#   DELTABT_STACK=v4 scripts/report.sh today     # narrow to one stack
#
# This is the SAME scripts/daily_report.py the schedule runs, so what appears
# here is what lands in the run summary at 01:30 UTC -- not a second
# implementation that can drift from it.
#
# STACKS ARE DISCOVERED, NOT LISTED. They come from the Stack tag on running
# instances, so a stack added in Terraform appears here the moment it boots and
# a decommissioned one disappears when it goes. A hardcoded list is how the
# monitor matrix ended up asking about v1 and v2 for a day after they were
# destroyed, failing every run on a missing instance id.
#
# NOTHING ELSE IS PINNED EITHER. The SSM document comes from the name
# convention, so a host replacement -- which happens on every user-data change
# and happened twice during the August capacity outage -- does not leave this
# pointing at an instance that no longer exists. The expected hashes are read
# from each RUNNING experiment, so the drift check compares a bot against its
# own registration rather than against a constant somebody forgot to update
# after the last restart.
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
ENVIRONMENT="${DELTABT_ENV:-paper}"
ARG="${1:-yesterday}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# "<stack> <instance-id>" per line, sorted so the report order is stable.
discover() {
  aws ec2 describe-instances --region "$REGION" \
    --filters "Name=instance-state-name,Values=running" \
              "Name=tag:Name,Values=deltabt-${ENVIRONMENT}-*" \
              "Name=tag-key,Values=Stack" \
    --query 'Reservations[].Instances[].[Tags[?Key==`Stack`]|[0].Value,InstanceId]' \
    --output text | sort
}

PAIRS="$(discover)"
if [[ -n "${DELTABT_STACK:-}" ]]; then
  PAIRS="$(awk -v s="$DELTABT_STACK" '$1 == s' <<<"$PAIRS")"
  if [[ -z "$PAIRS" ]]; then
    echo "no running instance for stack '${DELTABT_STACK}' in ${REGION}." >&2
    echo "running stacks: $(discover | awk '{print $1}' | paste -sd' ' -)" >&2
    exit 1
  fi
fi
if [[ -z "$PAIRS" ]]; then
  echo "no running deltabt-${ENVIRONMENT}-* instances tagged Stack in ${REGION}." >&2
  exit 1
fi
COUNT=$(wc -l <<<"$PAIRS")

# Wait for one SSM invocation instead of sleeping a fixed interval.
await() {
  local id="$1" iid="$2" st
  for _ in $(seq 1 40); do
    st=$(aws ssm get-command-invocation --region "$REGION" \
          --command-id "$id" --instance-id "$iid" \
          --query 'Status' --output text 2>/dev/null || echo Pending)
    [[ "$st" == "Pending" || "$st" == "InProgress" ]] || break
    sleep 2
  done
  aws ssm get-command-invocation --region "$REGION" --command-id "$id" \
    --instance-id "$iid" --query 'StandardOutputContent' --output text
}

run_remote() {
  local iid="$1" cmd="$2" id
  id=$(aws ssm send-command --region "$REGION" --instance-ids "$iid" \
        --document-name AWS-RunShellScript \
        --parameters "commands=[\"$cmd\"]" \
        --query 'Command.CommandId' --output text)
  await "$id" "$iid"
}

# ---- dashboard: one host, or it is ambiguous ------------------------------
if [[ "$ARG" == "dashboard" ]]; then
  if [[ "$COUNT" -gt 1 ]]; then
    # Both would want localhost:8000 and the second would fail on a bound
    # port. Naming which one is the only unambiguous answer.
    echo "more than one stack is running; say which to forward:" >&2
    awk '{printf "  DELTABT_STACK=%s scripts/report.sh dashboard\n", $1}' <<<"$PAIRS" >&2
    exit 1
  fi
  IID=$(awk '{print $2}' <<<"$PAIRS")
  # start-session needs a LOCAL binary the AWS CLI does not ship. Without it
  # the CLI fails with a message about an unknown plugin, which reads like a
  # permissions problem and is not one. Say what is actually missing.
  if ! command -v session-manager-plugin >/dev/null 2>&1; then
    echo "session-manager-plugin is not installed; port forwarding needs it." >&2
    echo "  https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html" >&2
    echo "The report and status modes do NOT need it and work as-is." >&2
    exit 1
  fi
  # The API listens on 127.0.0.1 ONLY -- no inbound security-group rule, no
  # public port -- which is why this needs a tunnel rather than a URL. Session
  # Manager carries it over the agent's existing channel, so nothing is opened
  # to the internet to make it work.
  echo "forwarding ${IID}:8000 -> http://localhost:8000  (ctrl-c to stop)"
  exec aws ssm start-session --region "$REGION" --target "$IID" \
    --document-name AWS-StartPortForwardingSession \
    --parameters '{"portNumber":["8000"],"localPortNumber":["8000"]}'
fi

case "$ARG" in
  status)    DAY="" ;;
  yesterday) DAY="$(date -u -d yesterday +%F)" ;;
  today)     DAY="$(date -u +%F)" ;;
  *)         DAY="$ARG" ;;
esac

# THE EXIT CODE IS THE WORST OF THE STACKS, NOT THE LAST ONE. `set -e` would
# abort the loop on the first stack whose report found a problem, and the
# remaining stacks would go unreported -- which is precisely backwards: they
# are separate experiments and one being unhealthy says nothing about another.
# monitor.yml sets fail-fast: false for the same reason.
rc=0
while read -r STACK IID; do
  [[ -n "$STACK" ]] || continue
  if [[ "$COUNT" -gt 1 ]]; then
    echo
    echo "==================================================================="
    echo "  stack ${STACK}  ·  ${IID}"
    echo "==================================================================="
  fi

  if [[ "$ARG" == "status" ]]; then
    run_remote "$IID" \
      'docker exec deltabot python -m app forward-test status 2>&1 | head -60' \
      || rc=1
    continue
  fi

  DOC="deltabt-${ENVIRONMENT}-${STACK}-monitor"
  LOG_GROUP="/deltabt/${ENVIRONMENT}/${STACK}/bot"

  # Read the hashes off the RUNNING experiment so the drift check is against
  # what the bot registered. Parsed HERE rather than in the remote command:
  # quoting an awk program inside a JSON document inside a shell string is
  # three levels of escaping and the failure mode is silently reporting none.
  EXPECT=$(run_remote "$IID" \
    'docker exec deltabot python -m app forward-test status 2>&1 | grep -E \"strategy_hash|risk_hash\"' \
    2>/dev/null || true)
  SH=$(awk '/strategy_hash/ {print $2; exit}' <<<"$EXPECT")
  RH=$(awk '/risk_hash/     {print $2; exit}' <<<"$EXPECT")

  ARGS=(--instance-id "$IID" --document "$DOC" --region "$REGION"
        --environment "$ENVIRONMENT" --stack "$STACK"
        --log-group "$LOG_GROUP" --day "$DAY")
  # `if`, not `[[ ... ]] && ...`. This runs `set -e`, and a trailing && whose
  # test is FALSE returns non-zero -- so on the one path that matters, an
  # experiment that is not running and therefore reports no hashes, the
  # one-liner form would abort instead of producing an unpinned report.
  if [[ -n "$SH" ]]; then ARGS+=(--expect-strategy-hash "$SH"); fi
  if [[ -n "$RH" ]]; then ARGS+=(--expect-risk-hash "$RH"); fi

  echo "# instance ${IID} · stack ${STACK} · day ${DAY} (UTC)" >&2
  if [[ -n "$SH" ]]; then
    echo "# expecting strategy ${SH} risk ${RH}" >&2
  else
    echo "# no RUNNING experiment: reporting without a drift check" >&2
  fi
  python3 "$ROOT/scripts/daily_report.py" "${ARGS[@]}" || rc=1
done <<<"$PAIRS"

if [[ "$rc" -ne 0 && "$COUNT" -gt 1 ]]; then
  echo >&2
  echo "# at least one stack reported a problem -- see the sections above" >&2
fi
exit "$rc"
