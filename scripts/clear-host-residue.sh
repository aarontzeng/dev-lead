#!/usr/bin/env bash
# Remove the memory plugin's `.remember/` directory a review leg's host left in a
# frozen target, so verify-target.sh can certify the tree afterwards.
#
# Why: a cursor-agent leg loads the lead's Claude Code plugins' hooks, and the
# `remember` plugin's SessionStart / PostToolUse / SessionEnd hooks create
# `<cwd>/.remember/` (logs, tmp, a `.gitignore` of `*`). A plain `git status`
# shows nothing, but verify-target.sh lists ignored entries on purpose, so it
# reports `!! .remember/` as a change to the reviewed tree. The plugin has no
# switch to turn this off (measured 2026-10-27: its paths come from the hook's
# cwd, no environment input names the directory). Whether a host does it varies
# by machine.
#
# It removes ONLY a directory it can identify as that plugin's own: a real
# directory (not a link) at the target's top level, untracked, holding the
# plugin's `.install-marker` and a `.gitignore` of exactly `*`. Anything else
# named `.remember` is left alone and the script exits 1, so verify-target.sh
# still reports it. Run it AFTER the leg has returned and BEFORE the closing
# verify-target.sh; never while a leg is running. It does not touch any other
# path, so a leg that wrote anywhere else still fails the bracket.
# Known limits: anything a leg wrote inside the identified `.remember/` goes with it (the
# helper cannot tell which leg made the directory); and it adds no check of its own to
# verify-target.sh, so that script's own limits (e.g. a tracked file marked
# `skip-worktree`, which `git status` omits) apply unchanged. A symlinked <dir> is refused;
# a symlink among its ancestors is not detected.
#
# Usage: clear-host-residue.sh <dir>
# Exit:  0 nothing to remove, or removed; 1 a `.remember` it will not remove.
set -euo pipefail

here=$(cd -- "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")")" && pwd)
. "$here/lib.sh"

[ $# -eq 1 ] || die "usage: clear-host-residue.sh <dir>"
dir=$1
require_git_repo "$dir" "target dir"
[ ! -L "${dir%/}" ] || die "$dir is a symlink; name the real directory"

residue=$dir/.remember
if [ ! -e "$residue" ] && [ ! -L "$residue" ]; then
  echo "clear-host-residue: nothing to remove in $dir"
  exit 0
fi

[ -d "$residue" ] && [ ! -L "$residue" ] || die "$residue is not a real directory; left in place"
[ -f "$residue/.install-marker" ] && [ ! -L "$residue/.install-marker" ] \
  && grep -q "remember was first bootstrapped" "$residue/.install-marker" \
  || die "$residue has no remember plugin marker; left in place"
# the plugin writes `*` and a newline; compare the bytes, not a $(...) that strips every trailing newline
[ -f "$residue/.gitignore" ] && [ ! -L "$residue/.gitignore" ] \
  && { printf '*\n' | cmp -s - "$residue/.gitignore" || printf '*' | cmp -s - "$residue/.gitignore"; } \
  || die "$residue/.gitignore is not the plugin's own; left in place"
# a failed listing must not read as "nothing tracked": capture it on its own so set -e sees the status
tracked=$(git -C "$dir" ls-files -- .remember) || die "cannot list tracked files in $dir; $residue left in place"
[ -z "$tracked" ] || die "$residue holds tracked files; left in place"
# rm -rf unlinks the children before the directory, so a target whose root is not writable would be emptied and then fail
[ -w "$dir" ] || die "$dir is not writable; $residue left in place"

rm -rf -- "$residue"
echo "clear-host-residue: removed the remember plugin's $residue"
