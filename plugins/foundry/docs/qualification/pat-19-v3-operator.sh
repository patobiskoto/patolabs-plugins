#!/usr/bin/env bash
# PAT-19 protocol v3 operator loop: the model is unloaded and reloaded (pinned command) before EACH launch,
# and every launch plays at most one task (exploration.one_task_per_launch). The launcher loads no model.
#   pat-19-v3-operator.sh screen  <candidate>                         <checkout> <runs-dir> <work-root> <repo>
#   pat-19-v3-operator.sh compare <candidate> <screening-campaign-id> <checkout> <runs-dir> <work-root> <repo>
# <checkout>  the tooling checkout (config: <checkout>/plugins/foundry/docs/qualification/pat-19-campaign-v3.json)
# <runs-dir>  holds envelope.json (read) and state/ (results and ledger); the operator log is written there
# <work-root> disposable area outside every checkout; <repo> the full clone the corpus tasks are built from
set -euo pipefail

usage() { echo "usage: $0 screen <candidate> | compare <candidate> <screening-campaign-id>  <checkout> <runs-dir> <work-root> <repo>" >&2; exit 64; }
[ $# -ge 1 ] || usage
MODE=$1; shift
case "$MODE" in
  screen)  [ $# -eq 5 ] || usage; CAND=$1; SCREENING_ID=""; shift 1 ;;
  compare) [ $# -eq 6 ] || usage; CAND=$1; SCREENING_ID=$2; shift 2 ;;
  *) usage ;;
esac
CHECKOUT=$1; RUNS=$2; WORK=$3; REPO=$4

QUAL="$CHECKOUT/plugins/foundry/docs/qualification"
CFG="$QUAL/pat-19-campaign-v3.json"
ENVELOPE_FILE="$RUNS/envelope.json"
STATE="$RUNS/state"
for f in "$CFG" "$ENVELOPE_FILE" "$QUAL/pat-19-corpus-snapshot-v1.json" "$QUAL/pat-19-corpus-manifest-v1.json"; do
  [ -f "$f" ] || { echo "missing file: $f" >&2; exit 66; }
done
mkdir -p "$STATE"
LOG="$RUNS/operator-$MODE-$CAND-$(date -u +%Y%m%dT%H%M%SZ).log"
log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG"; }

# The pinned load command, from the frozen config; refuse an empty one.
LOAD_CMD=$(python3 -c 'import json,shlex,sys
c = json.load(open(sys.argv[1]))["candidates"].get(sys.argv[2], {}).get("load_command") or []
print(shlex.join(c))' "$CFG" "$CAND")
[ -n "$LOAD_CMD" ] || { echo "no pinned load_command for candidate $CAND in $CFG" >&2; exit 65; }
log "operator start mode=$MODE candidate=$CAND config=$CFG load_command='$LOAD_CMD -y'"

finish() { log "final: lms unload --all"; lms unload --all >>"$LOG" 2>&1 || log "final unload failed"; }
trap finish EXIT

LAUNCHER=(python3 -m foundry.local_first_runner)
case "$MODE" in
  screen)  LAUNCHER+=(screen-exploration) ;;
  compare) LAUNCHER+=(compare-exploration --paths A,L,E --screening-campaign "$SCREENING_ID") ;;
esac
LAUNCHER+=(--campaign "$CFG" --candidate "$CAND" --envelope "$ENVELOPE_FILE" --state-dir "$STATE"
           --work-root "$WORK" --repo "$REPO" --snapshot "$QUAL/pat-19-corpus-snapshot-v1.json"
           --manifest "$QUAL/pat-19-corpus-manifest-v1.json")

n=0
while :; do
  n=$((n + 1))
  log "launch $n: lms unload --all"
  lms unload --all >>"$LOG" 2>&1
  log "launch $n: $LOAD_CMD -y"
  # shellcheck disable=SC2086  # the pinned command is a shell-quoted argv by construction (shlex.join)
  eval "$LOAD_CMD -y" >>"$LOG" 2>&1 < /dev/null
  PS_JSON=$(lms ps --json)
  log "launch $n: loaded $(printf '%s' "$PS_JSON" | python3 -c 'import json,sys
for m in json.load(sys.stdin):
    q = m.get("quantization") or {}
    print("identifier=%s modelKey=%s quantization=%s context=%s" % (m.get("identifier"), m.get("modelKey"), q.get("name"), m.get("contextLength")))')"
  set +e
  OUT=$(cd "$CHECKOUT/plugins/foundry/tooling" && PYTHONPATH=. "${LAUNCHER[@]}" < /dev/null 2>>"$LOG")
  CODE=$?
  set -e
  printf '%s\n' "$OUT" >>"$LOG"
  WR=$(printf '%s\n' "$OUT" | grep -E '^pat19-v3: work_remains=(yes|no)$' | tail -1 || true)
  log "launch $n: launcher exit code $CODE ${WR:-no work_remains line}"
  if [ "$CODE" -ne 0 ]; then
    log "STOP: launcher exited $CODE (2 = refused/preflight, 3 = cap reached, 4 = real Foundry registry changed by a cloud arm: investigate first): re-run this script to resume"
    exit "$CODE"
  fi
  case "$WR" in
    *work_remains=yes) ;;
    *work_remains=no) log "done: no work remains"; break ;;
    *) log "STOP: exit 0 without a work_remains line"; exit 70 ;;
  esac
done
