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

[ -e "$dest" ] && die "destination already exists: $dest (refusing to touch it)"

# --detach: no branch, so nothing can advance this worktree under the reviewer.
git -C "$repo" worktree add --detach "$dest" "$sha" >/dev/null \
  || die "git worktree add failed"

# Verify what we created rather than assuming it: cheap, and the whole point.
actual=$(git -C "$dest" rev-parse HEAD)
[ "$actual" = "$sha" ] || die "created worktree is at $actual, expected $sha"
[ -z "$(git -C "$dest" status --porcelain=v1)" ] \
  || die "freshly created worktree is not clean -- refusing to call it frozen"

echo "$sha"
