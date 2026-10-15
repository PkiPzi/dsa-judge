#!/usr/bin/env bash
# Install a built wheel into a clean venv and use it like a user would: version, doctor, the
# bundled sample problems, a function-mode solve and a design-problem (ops) build + solve.
# Usage: scripts/smoke_wheel.sh dist/dsajudge-*.whl
set -euo pipefail

wheel="$(realpath "$1")"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"  # not the repo: nothing may be picked up from a source checkout

uv venv --quiet venv
uv pip install --quiet --python venv/bin/python "$wheel"
export PATH="$work/venv/bin:$PATH"
export DSA_HOME="$work/bank" XDG_CACHE_HOME="$work/cache"

dsa --version
dsa doctor >/dev/null

# function mode: the bundled problem seeds the bank; samples + manual-less submit
dsa open max-subarray --lang python --no-editor
cat >"$DSA_HOME/work/max-subarray/part1/sol.py" <<'PY'
class Solution:
    def maxSubarraySum(self, nums):
        best, cur = nums[0], 0
        for x in nums:
            cur = max(x, cur + x)
            best = max(best, cur)
        return best
PY
(cd "$DSA_HOME/work/max-subarray/part1" && dsa submit --lang python)

# ops mode: build the hidden tests from three refs, then solve with a python MinStack
dsa build min-stack
dsa open min-stack --lang python --no-editor
cat >"$DSA_HOME/work/min-stack/part1/sol.py" <<'PY'
class MinStack:
    def __init__(self):
        self.st, self.mn = [], []

    def push(self, val):
        self.st.append(val)
        self.mn.append(val if not self.mn else min(self.mn[-1], val))

    def pop(self):
        self.st.pop()
        self.mn.pop()

    def top(self):
        return self.st[-1]

    def getMin(self):
        return self.mn[-1]
PY
(cd "$DSA_HOME/work/min-stack/part1" && dsa submit --lang python)

dsa stats >/dev/null
dsa completions zsh >/dev/null
echo "wheel smoke test passed"
