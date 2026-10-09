#!/usr/bin/env bash
# PAT-19 protocol c1 operator script (PAT-118). DRAFT of phase 1: not frozen, not run. The protocol compresses a long test output with ONE
# call to the local OpenAI-compatible endpoint (no tool) before it is sent to the cloud; see pat-19-protocol-c1.md.
# The launcher loads no model: this script loads each candidate (pinned command) ONCE and the launcher plays its tasks (the calls are
# independent single-shot requests, no tool loop: the per-task reload of v3 to v5 is not required here).
#   pat-19-c1-operator.sh screen  <checkout> <runs-dir> <work-root> <repo>              the 5 candidates x 12 tasks, local, no cloud
#   pat-19-c1-operator.sh compare <candidate> <checkout> <runs-dir> <work-root> <repo>  the 12 tasks x arms F and S (<= 36 cloud executions)
#   pat-19-c1-operator.sh pilot   <candidate> <checkout> <runs-dir> <work-root> <repo>  the one-task pilot (PR 27): that candidate's call, then F and S
# <checkout>  the tooling checkout (config: <checkout>/plugins/foundry/docs/qualification/pat-19-campaign-c1[-pilot].json)
# <runs-dir>  holds envelope.json (read) and state/ (results and ledger); the operator log is written there. The pilot and the campaign
#             NEVER share a runs-dir, nor a campaign id (checked below and by the launcher).
# <work-root> disposable area outside every checkout; <repo> the full clone the corpus tasks are built from
# Preconditions the operator establishes (not this script): LM Studio's server is running on the loopback address of the config
# (compression.local_call.endpoint), the machine is dedicated (the launcher's preflight refuses otherwise), the maintainer's Foundry
# state is backed up off the repository.
# Refusals before any model is loaded: 64 usage, 65 pinned precondition (campaign id, state directory, a bundle copy in the temp
# directory, Claude Code, the local endpoint), 66 file.
set -euo pipefail

usage() { echo "usage: $0 screen <checkout> <runs-dir> <work-root> <repo> | compare|pilot <candidate> <checkout> <runs-dir> <work-root> <repo>" >&2; exit 64; }
[ $# -ge 1 ] || usage
MODE=$1; shift
case "$MODE" in
  screen) [ $# -eq 4 ] || usage; CAND="" ;;
  compare|pilot) [ $# -eq 5 ] || usage; CAND=$1; shift 1 ;;
  *) usage ;;
esac
CHECKOUT=$1; RUNS=$2; WORK=$3; REPO=$4

QUAL="$CHECKOUT/plugins/foundry/docs/qualification"
if [ "$MODE" = pilot ]; then CFG="$QUAL/pat-19-campaign-c1-pilot.json"; else CFG="$QUAL/pat-19-campaign-c1.json"; fi
ENVELOPE_FILE="$RUNS/envelope.json"
STATE="$RUNS/state"
for f in "$CFG" "$ENVELOPE_FILE" "$QUAL/pat-19-corpus-snapshot-v1.json" "$QUAL/pat-19-corpus-manifest-v1.json"; do
  [ -f "$f" ] || { echo "missing file: $f" >&2; exit 66; }
done

# The pilot and the campaign are never mixed: the campaign id of the envelope and the files already in the state directory must
# belong to the mode asked for.
CAMPAIGN_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["campaign_id"])' "$ENVELOPE_FILE")
if [ "$MODE" = pilot ]; then
  case "$CAMPAIGN_ID" in *pilot*) ;; *) echo "pilot: the campaign id '$CAMPAIGN_ID' of $ENVELOPE_FILE must contain 'pilot'" >&2; exit 65 ;; esac
else
  case "$CAMPAIGN_ID" in *pilot*) echo "$MODE: the campaign id '$CAMPAIGN_ID' of $ENVELOPE_FILE must not contain 'pilot'" >&2; exit 65 ;; esac
fi
for f in "$STATE"/results-*.jsonl "$STATE"/ledger-*.jsonl; do
  [ -e "$f" ] || continue
  case "$(basename "$f")" in
    *pilot*) [ "$MODE" = pilot ] || { echo "$MODE: $f is a pilot file: use another runs-dir" >&2; exit 65; } ;;
    *) [ "$MODE" != pilot ] || { echo "pilot: $f is not a pilot file: use another runs-dir" >&2; exit 65; } ;;
  esac
done

# The candidates the mode plays, from the config (never a name typed here): all five in the order of protocol v1, or the one named.
CANDIDATES=$(python3 -c 'import json,sys
c = json.load(open(sys.argv[1]))
names = c["rules"]["compression_screening"]["candidates"]
want = sys.argv[2]
if want and want not in names:
    sys.exit("unknown candidate " + want)
print(" ".join([want] if want else names))' "$CFG" "$CAND") || { echo "candidate '$CAND' is not in $CFG" >&2; exit 65; }

# The shared per-user temp directory must hold no copy of the bundle left by an earlier run: refuse, before any model, a top-level
# directory that contains plugins/foundry.
TMP_ROOT=${TMPDIR:-/tmp}
for d in "$TMP_ROOT"/*/; do
  if [ -d "${d}plugins/foundry" ]; then
    echo "leftover bundle copy in the shared temp directory: ${d} (contains plugins/foundry): remove it first" >&2
    exit 65
  fi
done

SEEN=""
if [ "$MODE" != screen ]; then
  # Claude Code is pinned by the config (binary_version): refused here too, before a model is loaded for nothing.
  PIN=$(python3 -c 'import json,sys
print(json.load(open(sys.argv[1]))["drivers"]["cloud_diagnoser"]["binary_version"]["version"])' "$CFG")
  SEEN=$(claude --version 2>/dev/null | head -1 | awk '{print $1}' || true)
  [ "$SEEN" = "$PIN" ] || { echo "Claude Code on PATH is '${SEEN:-unreadable}', the config pins $PIN" >&2; exit 65; }
fi

mkdir -p "$STATE"
LOG="$RUNS/operator-$MODE-${CAND:-all}-$(date -u +%Y%m%dT%H%M%SZ).log"
log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG"; }

# The pinned load command of a candidate, from the config; refuse an empty one.
load_command() {
  python3 -c 'import json,shlex,sys
c = json.load(open(sys.argv[1]))["candidates"].get(sys.argv[2], {}).get("load_command") or []
print(shlex.join(c))' "$CFG" "$1"
}

LAUNCHER=(python3 -m foundry.local_first_runner)
COMMON=(--campaign "$CFG" --envelope "$ENVELOPE_FILE" --state-dir "$STATE" --work-root "$WORK" --repo "$REPO"
        --snapshot "$QUAL/pat-19-corpus-snapshot-v1.json" --manifest "$QUAL/pat-19-corpus-manifest-v1.json")

# One launch of the launcher; a non-zero exit stops the script (re-running it resumes: a decided call is never replayed).
launch() {
  local code out
  set +e
  out=$(cd "$CHECKOUT/plugins/foundry/tooling" && PYTHONPATH=. "${LAUNCHER[@]}" "$@" < /dev/null 2>>"$LOG")
  code=$?
  set -e
  printf '%s\n' "$out" >>"$LOG"
  log "launcher '$1' exit code $code"
  if [ "$code" -ne 0 ]; then
    log "STOP: launcher exited $code (2 = refused/preflight or a tool error recorded as tool_error; 3 = cap reached, also premium_tokens_unmeasurable after a cloud execution with unknown tokens; 4 = real Foundry registry changed by a cloud arm: investigate first; 130 = interrupted by Ctrl-C, 143 = SIGTERM, 129 = SIGHUP, 1 = unexpected launcher error: all four recorded as interrupted)"
    log "STOP: before any relaunch, produce the report and read ledger.unknown_spent_work and compression_comparison.paired_rule.campaign_level_reasons (operator notice, 'Après un arrêt'); re-running this script resumes"
    exit "$code"
  fi
}

finish() { log "final: lms unload --all"; lms unload --all >>"$LOG" 2>&1 || log "final unload failed"; }
trap finish EXIT
log "operator start mode=$MODE candidates='$CANDIDATES' campaign_id=$CAMPAIGN_ID config=$CFG claude_code=${SEEN:-not needed}"

if [ "$MODE" != compare ]; then
  # The local endpoint must answer (a read-only GET of the model list on the loopback address of the config: no inference).
  python3 - "$CFG" <<'PY' || { echo "the local endpoint of the config does not answer: start LM Studio's server first" >&2; exit 65; }
import json, sys, urllib.parse, urllib.request
call = json.load(open(sys.argv[1]))["compression"]["local_call"]
url = urllib.parse.urlsplit(call["endpoint"])
if url.hostname not in ("127.0.0.1", "::1"):
    sys.exit("the endpoint is not a loopback address")
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
opener.open(f"http://{url.netloc}/v1/models", timeout=10).read()
PY
  for NAME in $CANDIDATES; do
    LOAD_CMD=$(load_command "$NAME")
    [ -n "$LOAD_CMD" ] || { echo "no pinned load_command for candidate $NAME in $CFG" >&2; exit 65; }
    log "candidate $NAME: lms unload --all"
    lms unload --all >>"$LOG" 2>&1
    log "candidate $NAME: $LOAD_CMD -y"
    # shellcheck disable=SC2086  # the pinned command is a shell-quoted argv by construction (shlex.join)
    eval "$LOAD_CMD -y" >>"$LOG" 2>&1 < /dev/null
    PS_JSON=$(lms ps --json)
    log "candidate $NAME: loaded $(printf '%s' "$PS_JSON" | python3 -c 'import json,sys
for m in json.load(sys.stdin):
    q = m.get("quantization") or {}
    print("identifier=%s modelKey=%s quantization=%s context=%s" % (m.get("identifier"), m.get("modelKey"), q.get("name"), m.get("contextLength")))')"
    launch screen-compression "${COMMON[@]}" --candidate "$NAME"
  done
  log "screening done: lms unload --all (the comparison reads the summaries from the screening records and loads no model)"
  lms unload --all >>"$LOG" 2>&1 || log "unload failed"
fi

if [ "$MODE" != screen ]; then
  launch compare-compression "${COMMON[@]}" --candidate "$CAND" --paths F,S
fi
log "done"
