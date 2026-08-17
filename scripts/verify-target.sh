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
# The clean-tree check ignores the operator's personal global gitignore (see
# below): whether a target is frozen must not depend on whose machine it is.
#
# Optional trailing arguments declare scaffolding a leg cannot place anywhere
# else — opencode's `opencode.json` must sit at the project root to bind at
# all, so that leg cannot keep its config outside the target. Declaring a path
# permits that ONE path, only while it is UNTRACKED ("??"), and requires it to
# still be there. A tracked file that was modified, deleted or renamed fails
# even when its path is declared: that is a mutation of the reviewed tree, not
# scaffolding.
#
# It permits an ENTRY, not its CONTENT. `?? opencode.json` reads the same
# whether or not the reviewer rewrote the file, and some legs allow-list
# commands that can write (`git diff --output=<path>`). If the scaffolding
# matters, hold a sha256 of it in the lead and check that too — this script
# cannot and does not.
#
# Usage: verify-target.sh <dir> <expected-sha> [expected-path ...]
set -euo pipefail

die() { echo "verify-target: $*" >&2; exit 1; }

[ $# -ge 2 ] || die "usage: verify-target.sh <dir> <expected-sha> [expected-path ...]"
dir=$1; expected=$2; shift 2

[ -d "$dir" ] || die "target dir does not exist: $dir"
git -C "$dir" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repo: $dir"

actual=$(git -C "$dir" rev-parse HEAD)
if [ "$actual" != "$expected" ]; then
  die "HEAD moved: $dir is at $actual, expected $expected -- the review target was NOT frozen; discard this run's conclusions"
fi

# --untracked-files=all: the default collapses an untracked directory to a
# single "?? scaffold/" entry, so a declaration of "scaffold/" would permit an
# entire subtree — adding scaffold/anything leaves that one entry unchanged.
# Measured: declaring "scaffold/" certified a target after an undeclared second
# file appeared inside it. Listing every file makes one declaration permit one
# file, which is what the flag name promises.
# -c core.excludesFile=/dev/null: a LEAD's personal global gitignore must not
# decide what counts as a clean review target. Measured — a procedure that
# dropped `REVIEW-*.md` scaffolding into frozen targets certified fine on the
# machine that wrote it, because that machine's ~/.gitignore matched the name,
# and would have failed for everyone else. The project's own .gitignore still
# applies; only the operator's personal file is neutralised.
dirty=$(git -C "$dir" -c core.excludesFile=/dev/null status --porcelain=v1 --untracked-files=all)

if [ $# -eq 0 ]; then
  if [ -n "$dirty" ]; then
    echo "verify-target: target is dirty -- a reviewer reads the WORKING TREE, not your commit:" >&2
    echo "$dirty" >&2
    die "refusing to certify $dir as frozen"
  fi
  echo "verify-target: OK ($dir frozen at $expected)"
  exit 0
fi

# Declared-scaffolding mode. Porcelain v1 is "XY <path>": status code in the
# first two columns, path from column 4.
#
# A declaration may only excuse an UNTRACKED entry ("??"). Scaffolding is a
# file the leg adds; a tracked path that is modified, deleted or renamed is a
# mutation of the reviewed tree, and no declaration may hide it — that is the
# one thing this script exists to catch. Measured: an earlier version compared
# paths only, so declaring `f0.txt` certified a target whose tracked f0.txt had
# been rewritten, and the test covering it asserted success while its own
# comment said the mutation must not be hidden.
unexpected=""
seen=""
while IFS= read -r line; do
  [ -n "$line" ] || continue
  code=${line:0:2}
  path=${line:3}
  match=""
  if [ "$code" = "??" ]; then
    for want in "$@"; do
      if [ "$path" = "$want" ]; then match=1; seen="$seen $want"; break; fi
    done
  fi
  [ -n "$match" ] || unexpected="$unexpected$line"$'\n'
done <<< "$dirty"

if [ -n "$unexpected" ]; then
  echo "verify-target: target has changes beyond the declared scaffolding:" >&2
  printf '%s' "$unexpected" >&2
  die "refusing to certify $dir as frozen"
fi

missing=""
for want in "$@"; do
  case " $seen " in *" $want "*) ;; *) missing="$missing $want" ;; esac
done
if [ -n "$missing" ]; then
  die "declared scaffolding is absent from $dir --$missing; it was removed or never created, so this run did not have the setup you certified"
fi

echo "verify-target: OK ($dir frozen at $expected; declared scaffolding present: $*)"
echo "verify-target: NOTE -- declared paths were permitted as UNTRACKED ENTRIES, not verified by CONTENT" >&2
