#!/usr/bin/env bash
# Run the test suite one file per process (process isolation).
#
# Why: several tests exercise the full real-data universe pipeline and hold large
# pandas frames. In a single `pytest` process on a memory-constrained machine the
# cumulative footprint can OOM (SIGKILL / exit 137). Running each test file in its
# own process releases memory between files, so the suite passes reliably anywhere.
#
# On a machine with ample RAM, plain `pytest` works fine; this is the safe default
# for constrained environments (e.g. CI runners, sandboxes).
#
# Usage:  bash scripts/run_tests.sh
set -u
cd "$(dirname "$0")/.."

pass=0; fail=0; failed=""
for f in tests/test_*.py; do
  out=$(python3 -m pytest -q -p no:cacheprovider "$f" 2>&1 | tail -1)
  printf '%-34s %s\n' "$(basename "$f")" "$out"
  if echo "$out" | grep -q "passed" && ! echo "$out" | grep -qE "failed|error"; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1)); failed="$failed $(basename "$f")"
  fi
done
echo "----"
echo "files passed=$pass failed=$fail$failed"
[ "$fail" -eq 0 ]
