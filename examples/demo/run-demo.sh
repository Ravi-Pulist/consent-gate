#!/usr/bin/env bash
# Reproduce the exhibit behind examples/demo/index.html.
#
# Runs from anywhere: the script resolves the repo root from its own location.
# Pass --dry-run to print the commands without executing them.
#
#     ./examples/demo/run-demo.sh
#     ./examples/demo/run-demo.sh --dry-run
#
# Exit codes come from the exhibit itself:
#   0  exclusion leaked on nothing
#   1  exclusion leaked (a failure, not a caveat)
#   2  the exhibit could not run, including the case where the masking
#      baseline leaked nothing and the corpus therefore proves nothing

set -uo pipefail

DRY_RUN=0
PYTHON="${PYTHON:-python}"
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        *) echo "unknown argument: $arg" >&2; exit 64 ;;
    esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
export PYTHONPATH=src

step() {
    echo
    echo "\$ PYTHONPATH=src $PYTHON $*"
    if [ "$DRY_RUN" -eq 1 ]; then return 0; fi
    "$PYTHON" "$@"
}

echo "repo root: $ROOT"
echo "corpus:    deterministic, seed 20260804, synthetic throughout"

# 1. Regenerate the corpus. Byte-reproducible from the seed, so this
#    overwrites corpus/data with identical bytes.
step corpus/generate.py || { echo "corpus generation failed" >&2; exit 2; }

# 2. Run both configurations over the same probes and diff them.
#    Writes exhibits/exhibit.md and exhibits/exhibit.json.
step -m consent_gate.cli exhibit --corpus corpus/data \
    --out exhibits/exhibit.md --json exhibits/exhibit.json
EXHIBIT=$?

# 3. The test suite, including the 9 sabotage cases.
step -m pytest -q || { echo "tests failed" >&2; exit 2; }

# 4. Re-derive every figure printed in the demo.
step examples/demo/verify.py || { echo "demo figures do not match" >&2; exit 1; }

echo
if [ "$DRY_RUN" -eq 1 ]; then
    echo "dry run: nothing was executed."
elif [ "$EXHIBIT" -eq 0 ]; then
    echo "exhibit exit code: 0 (exclusion leaked nothing, as expected)"
else
    echo "exhibit exit code: $EXHIBIT"
    exit "$EXHIBIT"
fi

echo
echo "open examples/demo/index.html to read the results."
