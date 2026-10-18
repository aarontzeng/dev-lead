#!/usr/bin/env bash
# Create a frozen, detached review checkout at an exact SHA: a git worktree by
# default, a self-contained clone with --clone.
#
# Why this is a script and not a snippet in a skill: the freeze is the one
# place where a copy-paste error destroys work rather than merely misleading
# (a `git checkout` on the wrong tree, a worktree created from a moving
# branch name, a "frozen" directory something else is still writing to).
# Both measured bugs in this repo's own shell fragments were in this class.
#
# Usage:  freeze-target.sh <repo-dir> <committish> <dest-dir> [--clone]
# Prints: the resolved 40-char SHA on stdout (capture it as REVIEW_HEAD)
#
# --clone: for a reader that can see only the frozen directory. A worktree's
# .git is a one-line pointer file into the main repo's .git/worktrees/, outside
# that directory; agy 1.4.2 runs git sandboxed inside --add-dir, so every git
# call in the review fails with "not a git repository: <main>/.git/worktrees/<n>"
# (measured 2026-10-18, same probe on a clone worked). The clone's .git is a real
# directory inside the target: `git clone --local --no-hardlinks` copies the
# objects (no alternates pointing back out, and no inode shared with the repo,
# so a later chmod -R a-w on the target cannot reach it). Teardown is rm -rf
# (chmod -R u+w first if the target was made read-only), not `worktree remove`.
set -euo pipefail

here=$(cd -- "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")")" && pwd)
. "$here/lib.sh"
# resolved up front, before anything is created, so it cannot fail past cleanup
[ -x "$here/renorm-only.sh" ] || die "cannot find renorm-only.sh next to ${BASH_SOURCE[0]}"

usage="usage: freeze-target.sh <repo-dir> <committish> <dest-dir> [--clone]"
mode=worktree
case $# in
  3) ;;
  4) [ "$4" = "--clone" ] || die "unknown option '$4'; $usage"
     mode=clone ;;
  *) die "$usage" ;;
esac
repo=$1; committish=$2; dest=$3

require_git_repo "$repo" "repo dir"

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

# A refusal from here on must not leave the checkout behind it: the caller got
# a non-zero exit and no SHA, so nothing will ever clean it up, and the next
# freeze to the same path dies on "already exists". Measured 2026-09-22 (a peer
# session, on an SDK repository). Every command after a SUCCESSFUL creation
# that can fail is guarded with `|| refuse` -- a bare failure under `set -e`
# exits WITHOUT cleaning up (found by two review legs, 2026-09-22). Only after
# the creation succeeded is the checkout provably ours (clone mode: the mkdir above;
# worktree mode: a successful add): a failed creation may be failing BECAUSE something
# else holds that path (a concurrent freeze, a stale registration), so it dies without
# touching it.
refuse() {
  if [ "$mode" = clone ]; then
    rm -rf -- "$dest" >/dev/null 2>&1 || true
    die "$* (the clone was removed again)"
  fi
  git -C "$repo" worktree remove --force --force "$dest" >/dev/null 2>&1 || true
  die "$* (the worktree was removed again)"
}

if [ "$mode" = clone ]; then
  # `mkdir` (the leaf, no -p) is atomic: it succeeds only for a path this run creates, which is
  # what makes the rm -rf in refuse() safe. The -e guard above is the early, friendly refusal;
  # on its own it is check-then-act (a concurrent freeze to the same path passes it too).
  mkdir -p -- "$(dirname -- "$dest")" && mkdir -- "$dest" \
    || die "cannot create $dest (nothing was removed: the path may belong to someone else)"
  # --template= and core.hooksPath off: the user's init.templateDir / global hooks must not run
  # in, or write into, a tree whose point is to be exactly the commit.
  git clone --quiet --template= --local --no-hardlinks --no-checkout -- "$repo" "$dest" >/dev/null \
    || refuse "git clone failed"
  # A local clone copies every object of the source, reachable from a ref or not, so a
  # commit no ref reaches (a detached HEAD's) is in it too. It also copies the source's
  # alternates link, which would point the clone's object reads back OUT of the target:
  # not self-contained, so refused.
  [ ! -e "$dest/.git/objects/info/alternates" ] \
    || refuse "$repo borrows its objects from another repository (objects/info/alternates), so the clone would read outside itself; run 'git repack -a -d' there first"
  git -C "$dest" -c core.hooksPath=/dev/null checkout --quiet --detach "$sha" \
    || refuse "cannot check out $sha in the clone"
else
  # --detach: no branch, so nothing can advance this worktree under the reviewer.
  git -C "$repo" worktree add --detach "$dest" "$sha" >/dev/null \
    || die "git worktree add failed (nothing was removed: the path may belong to someone else)"
fi

# Verify what we created rather than assuming it: cheap, and the whole point.
actual=$(git -C "$dest" rev-parse HEAD 2>/dev/null) \
  || refuse "cannot read HEAD of the new $mode"
[ "$actual" = "$sha" ] || refuse "created $mode is at $actual, expected $sha"

# Line-ending renormalization is not a change to what a reviewer reads: those
# files hold the commit's exact bytes (renorm-only.sh checks that, byte for
# byte, mode included). Everything else still refuses.
# core.quotePath=false: renorm-only.sh lists raw paths, so a non-ASCII name
# must arrive raw here too or it can never match (and stays dirty).
status=$(git -C "$dest" -c core.quotePath=false status --porcelain=v1 2>/dev/null) \
  || refuse "git status failed in the new $mode"
dirty=$(printf '%s\n' "$status" | "$here/renorm-only.sh" --filter "$dest") \
  || refuse "could not check the $mode for line-ending-only changes"
[ -z "$dirty" ] \
  || refuse "freshly created $mode is not clean -- refusing to call it frozen:
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
