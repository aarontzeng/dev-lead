#!/usr/bin/env bash
# Create a frozen, detached review worktree at an exact SHA.
#
# Why this is a script and not a snippet in a skill: the freeze is the one
# place where a copy-paste error destroys work rather than merely misleading
# (a `git checkout` on the wrong tree, a worktree created from a moving
# branch name, a "frozen" directory something else is still writing to).
# Both measured bugs in this repo's own shell fragments were in this class.
#
# Usage:  freeze-target.sh <repo-dir> <committish> <dest-dir>
# Prints: the resolved 40-char SHA on stdout (capture it as REVIEW_HEAD)
set -euo pipefail

die() { echo "freeze-target: $*" >&2; exit 1; }

[ $# -eq 3 ] || die "usage: freeze-target.sh <repo-dir> <committish> <dest-dir>"
repo=$1; committish=$2; dest=$3

[ -d "$repo" ] || die "repo dir does not exist: $repo"
git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repo: $repo"

# Resolve BEFORE creating anything: a branch name resolves now and may move
# later, which is exactly what a frozen target must not depend on.
sha=$(git -C "$repo" rev-parse --verify "${committish}^{commit}" 2>/dev/null) \
  || die "cannot resolve '$committish' to a commit in $repo"

# Absolutise the destination BEFORE the guard below touches it. `git -C "$repo"
# worktree add` resolves a relative path against $repo, while every other line
# here resolves it against the caller's cwd -- so a relative dest planted the
# worktree INSIDE the target repository and left it dirty ("?? frozen/"), while
# the guard below checked an unrelated path in the cwd and passed. Reproduced
# 2026-09-13: cwd empty, repo carrying an orphan detached worktree, and git's
# own "fatal:" surfacing with no freeze-target: prefix.
case "$dest" in
  /*) ;;
  *)  dest=$PWD/$dest ;;
esac

[ -e "$dest" ] && die "destination already exists: $dest (refusing to touch it)"

# A refusal from here on must not leave the worktree registered behind it: the
# caller got a non-zero exit and no SHA, so nothing will ever clean it up, and
# the next freeze to the same path dies on "already exists". Measured
# 2026-09-22 (SITL-bench, QCS9075-QLI2.0-SDK). Every command after a
# SUCCESSFUL add that can fail is guarded with `|| refuse` -- a bare failure
# under `set -e` exits WITHOUT cleaning up (found by two review legs,
# 2026-09-22). Only after the add succeeded is the worktree provably ours: a
# failed add may be failing BECAUSE something else holds that path (a
# concurrent freeze, a stale registration), so it dies without touching it.
refuse() {
  git -C "$repo" worktree remove --force --force "$dest" >/dev/null 2>&1 || true
  die "$* (the worktree was removed again)"
}

# --detach: no branch, so nothing can advance this worktree under the reviewer.
git -C "$repo" worktree add --detach "$dest" "$sha" >/dev/null \
  || die "git worktree add failed (nothing was removed: the path may belong to someone else)"

# Verify what we created rather than assuming it: cheap, and the whole point.
actual=$(git -C "$dest" rev-parse HEAD 2>/dev/null) \
  || refuse "cannot read HEAD of the new worktree"
[ "$actual" = "$sha" ] || refuse "created worktree is at $actual, expected $sha"

# Line-ending renormalization is not a change to what a reviewer reads: those
# files hold the commit's exact bytes (renorm-only.sh checks that, byte for
# byte, mode included). Everything else still refuses.
here=$(dirname -- "${BASH_SOURCE[0]}")
# core.quotePath=false: renorm-only.sh lists raw paths, so a non-ASCII name
# must arrive raw here too or it can never match (and stays dirty).
status=$(git -C "$dest" -c core.quotePath=false status --porcelain=v1 2>/dev/null) \
  || refuse "git status failed in the new worktree"
dirty=$(printf '%s\n' "$status" | "$here/renorm-only.sh" --filter "$dest") \
  || refuse "could not check the worktree for line-ending-only changes"
[ -z "$dirty" ] \
  || refuse "freshly created worktree is not clean -- refusing to call it frozen:
$dirty"
# From here on only output remains. SIGPIPE ignored so a closed stdout or
# stderr is an EPIPE error the lines below handle, not a silent kill that
# skips cleanup (review, 2026-09-22).
trap '' PIPE

# Informational only, so best-effort: a failure writing it must not exit past
# the cleanup guarantee above.
if [ -n "$status" ]; then
  { echo "freeze-target: NOTE -- files shown as modified only by line-ending renormalization (bytes identical to the commit); verify-target.sh excuses exactly these:"
    "$here/renorm-only.sh" "$dest" | sed 's/^/  /'
  } >&2 || true
fi

# The SHA is the result: a caller that cannot receive it has no frozen target,
# so failing to write it cleans up too.
printf '%s\n' "$sha" || refuse "could not write the SHA to stdout"
