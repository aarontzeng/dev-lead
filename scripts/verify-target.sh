#!/usr/bin/env bash
# Assert a review target is still exactly what you froze: right commit, clean
# tree. Run it BEFORE launching a reviewer and AGAIN after it returns.
#
# This replaces the hand-written bracket that bit twice in this repo's own
# history: written without `git -C` it silently checks the LEAD's cwd instead
# of the target, and with the expected SHA captured on the adjacent line it
# compares HEAD against itself — a tautology that can only pass. Capture the
# SHA when you FREEZE (freeze-target.sh prints it); pass it here.
#
# Usage: verify-target.sh <dir> <expected-sha>
set -euo pipefail

die() { echo "verify-target: $*" >&2; exit 1; }

[ $# -eq 2 ] || die "usage: verify-target.sh <dir> <expected-sha>"
dir=$1; expected=$2

[ -d "$dir" ] || die "target dir does not exist: $dir"
git -C "$dir" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repo: $dir"

actual=$(git -C "$dir" rev-parse HEAD)
if [ "$actual" != "$expected" ]; then
  die "HEAD moved: $dir is at $actual, expected $expected -- the review target was NOT frozen; discard this run's conclusions"
fi

dirty=$(git -C "$dir" status --porcelain=v1)
if [ -n "$dirty" ]; then
  echo "verify-target: target is dirty -- a reviewer reads the WORKING TREE, not your commit:" >&2
  echo "$dirty" >&2
  die "refusing to certify $dir as frozen"
fi

echo "verify-target: OK ($dir frozen at $expected)"
