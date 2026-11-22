#!/usr/bin/env bash
# Offline end-to-end check on case 6: run, approve, kill during send, restart, verify exactly-once.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-$(command -v python3 || command -v python)}"
export PYTHONPATH=. PDR_OFFLINE=1 PYTHONWARNINGS=ignore
export PDR_DATA_DIR="$(mktemp -d)"
OUT="$PDR_DATA_DIR/out"
trap 'rm -rf "$PDR_DATA_DIR"' EXIT
CASE=tests/cases/case06_wrong_base_over_cap
KEY='CLG|GPA20260612|1'
pdr() { "$PY" -m pdr.cli "$@"; }
fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }
step() { echo "== $*"; }

step doctor;  pdr doctor
step init;    pdr init
step run;     pdr run --in "$CASE" --out "$OUT"
grep -q '"disputable"' "$OUT/verdicts.jsonl" || fail "case 6 is not disputable"
step "run again (memoised)"; pdr run --in "$CASE" --out "$OUT" | tee "$PDR_DATA_DIR/run2.log"
grep -q 'saved' "$PDR_DATA_DIR/run2.log" && fail "second run recomputed a stage"
[ "$(wc -l < "$OUT/verdicts.jsonl")" -eq 1 ] || fail "verdicts.jsonl is not one line per deduction"
step status;  pdr status
step actions; ACTION=$(pdr actions | awk 'NR==1{print $1}'); [ -n "$ACTION" ] || fail "no drafted action"
step "send before approval"; pdr send | grep -q 'nothing to send' || fail "unapproved action was sent"
step approve; pdr approve "$ACTION" --role kam --name "Smoke KAM"
step "send, killed after intent"
set +e; PDR_CRASH_AT=send.after_intent pdr send; rc=$?; set -e
[ "$rc" -eq 137 ] || fail "expected exit 137 from the killed send, got $rc"
step "send again (restart)"; pdr send; pdr send
n=$(ls "$PDR_DATA_DIR/outbox" | wc -l)
[ "$n" -eq 1 ] || fail "expected exactly one outbox file, found $n"
pdr actions | grep -q "$ACTION.* sent " || fail "action is not in status sent"
step outbox;    pdr outbox
step report;    pdr report --out "$OUT"
step ledger;    pdr ledger --by deduction
step economics; pdr economics
step trail;     pdr trail --verify
step recheck;   pdr recheck "$KEY"
echo "SMOKE OK"
