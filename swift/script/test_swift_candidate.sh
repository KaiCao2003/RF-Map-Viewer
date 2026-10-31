#!/usr/bin/env bash
set -euo pipefail

EVIDENCE_DIR="${RFMAPPING_SWIFT_TEST_EVIDENCE_DIR:?Set RFMAPPING_SWIFT_TEST_EVIDENCE_DIR}"
mkdir -p "$EVIDENCE_DIR"

NSUnbufferedIO=YES swift test -c release > >(tee "$EVIDENCE_DIR/swift-test.log") 2>&1 &
TEST_PID=$!
START_SECONDS=$SECONDS
SAMPLED=0
TIMED_OUT=0

stop_tests() {
  for process_id in $(pgrep -x xctest || true); do
    kill -TERM "$process_id" 2>/dev/null || true
  done
  kill -TERM "$TEST_PID" 2>/dev/null || true
}

trap 'stop_tests; exit 130' INT
trap 'stop_tests; exit 143' TERM

while kill -0 "$TEST_PID" 2>/dev/null; do
  ELAPSED=$((SECONDS - START_SECONDS))
  if (( ELAPSED >= 180 && SAMPLED == 0 )); then
    SAMPLED=1
    echo "Swift tests still running after ${ELAPSED}s; collecting XCTest process samples."
    ps -axo pid,ppid,state,etime,command > "$EVIDENCE_DIR/processes.txt"
    for process_id in $(pgrep -x xctest || true); do
      /usr/bin/sample "$process_id" 3 -file "$EVIDENCE_DIR/xctest-${process_id}.sample.txt" || true
    done
  fi
  if (( ELAPSED >= 480 )); then
    TIMED_OUT=1
    echo "Swift test step exceeded 480 seconds; stopping it with diagnostics preserved."
    stop_tests
    break
  fi
  sleep 2
done

TEST_STATUS=0
wait "$TEST_PID" || TEST_STATUS=$?
if (( TIMED_OUT )); then exit 124; fi
exit "$TEST_STATUS"
