#!/usr/bin/env bash
# PAT-19 protocol v5 operator loop (PAT-126, DRAFT until the freeze): the model is unloaded and reloaded (pinned
# command) before EACH launch, and every launch plays at most one task (exploration.one_task_per_launch). The launcher
# loads no model. The v5 candidate is fixed by the protocol (no screening) and the arms are A and L (no Haiku arm).
#   pat-19-v5-operator.sh compare <candidate> <checkout> <runs-dir> <work-root> <repo>   the 12-task campaign
#   pat-19-v5-operator.sh pilot   <candidate> <checkout> <runs-dir> <work-root> <repo>   the one-task pilot (PR 27)
# <checkout>  the tooling checkout (config: <checkout>/plugins/foundry/docs/qualification/pat-19-campaign-v5[-pilot].json)
# <runs-dir>  holds envelope.json (read) and state/ (results and ledger); the operator log is written there.
#             The pilot and the campaign NEVER share a runs-dir, nor a campaign id (checked below and by the launcher).
# <work-root> disposable area outside every checkout; <repo> the full clone the corpus tasks are built from
# Refusals before any model is loaded: 64 usage, 65 pinned precondition (campaign id, state directory, Claude Code), 66 file.
set -euo pipefail

usage() { echo "usage: $0 compare|pilot <candidate> <checkout> <runs-dir> <work-root> <repo>" >&2; exit 64; }
[ $# -ge 1 ] || usage
MODE=$1; shift
case "$MODE" in
  compare|pilot) [ $# -eq 5 ] || usage; CAND=$1; shift 1 ;;
  *) usage ;;
esac
CHECKOUT=$1; RUNS=$2; WORK=$3; REPO=$4

QUAL="$CHECKOUT/plugins/foundry/docs/qualification"
if [ "$MODE" = pilot ]; then CFG="$QUAL/pat-19-campaign-v5-pilot.json"; else CFG="$QUAL/pat-19-campaign-v5.json"; fi
ENVELOPE_FILE="$RUNS/envelope.json"
STATE="$RUNS/state"
for f in "$CFG" "$ENVELOPE_FILE" "$QUAL/pat-19-corpus-snapshot-v1.json" "$QUAL/pat-19-corpus-manifest-v1.json"; do
  [ -f "$f" ] || { echo "missing file: $f" >&2; exit 66; }
done

# The pilot and the campaign are never mixed: the campaign id of the envelope and the files already in the state
# directory must belong to the mode asked for.
CAMPAIGN_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["campaign_id"])' "$ENVELOPE_FILE")
if [ "$MODE" = pilot ]; then
  case "$CAMPAIGN_ID" in *pilot*) ;; *) echo "pilot: the campaign id '$CAMPAIGN_ID' of $ENVELOPE_FILE must contain 'pilot'" >&2; exit 65 ;; esac
else
  case "$CAMPAIGN_ID" in *pilot*) echo "compare: the campaign id '$CAMPAIGN_ID' of $ENVELOPE_FILE must not contain 'pilot'" >&2; exit 65 ;; esac
fi
for f in "$STATE"/results-*.jsonl "$STATE"/ledger-*.jsonl; do
  [ -e "$f" ] || continue
  case "$(basename "$f")" in
    *pilot*) [ "$MODE" = pilot ] || { echo "compare: $f is a pilot file: use another runs-dir" >&2; exit 65; } ;;
    *) [ "$MODE" = compare ] || { echo "pilot: $f is not a pilot file: use another runs-dir" >&2; exit 65; } ;;
  esac
done

# Claude Code is pinned by the config (binary_version): refused here too, before a model is loaded for nothing.
PIN=$(python3 -c 'import json,sys
print(json.load(open(sys.argv[1]))["drivers"]["cloud_reviewer"]["binary_version"]["version"])' "$CFG")
SEEN=$(claude --version 2>/dev/null | head -1 | awk '{print $1}' || true)
[ "$SEEN" = "$PIN" ] || { echo "Claude Code on PATH is '${SEEN:-unreadable}', the config pins $PIN" >&2; exit 65; }

mkdir -p "$STATE"
LOG="$RUNS/operator-$MODE-$CAND-$(date -u +%Y%m%dT%H%M%SZ).log"
log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG"; }

# The pinned load command, from the config; refuse an empty one.
LOAD_CMD=$(python3 -c 'import json,shlex,sys
c = json.load(open(sys.argv[1]))["candidates"].get(sys.argv[2], {}).get("load_command") or []
print(shlex.join(c))' "$CFG" "$CAND")
[ -n "$LOAD_CMD" ] || { echo "no pinned load_command for candidate $CAND in $CFG" >&2; exit 65; }
log "operator start mode=$MODE candidate=$CAND campaign_id=$CAMPAIGN_ID config=$CFG claude_code=$SEEN load_command='$LOAD_CMD -y'"

finish() { log "final: lms unload --all"; lms unload --all >>"$LOG" 2>&1 || log "final unload failed"; }
trap finish EXIT

LAUNCHER=(python3 -m foundry.local_first_runner)
LAUNCHER+=(compare-exploration --paths A,L)  # v5: arms A and L, the candidate is fixed by the config
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
