#!/usr/bin/env bash
# Line-ending renormalization is not a change to what a reviewer reads.
#
#   renorm-only.sh <worktree-dir>
#       Print the tracked paths git reports as modified ONLY because of
#       line-ending normalization: same mode, working-tree bytes identical to
#       the committed blob. One path per line.
#   renorm-only.sh --filter <worktree-dir>
#       Read `git status --porcelain=v1` lines on stdin and print them back
#       WITHOUT the ` M` entries for exactly those paths. The one place
#       freeze-target.sh and verify-target.sh both excuse them, so the two
#       cannot drift apart on what counts.
#
# Why this exists: a repo that committed CRLF files and later added
# `.gitattributes` `eol=lf` shows those files as ` M` on EVERY fresh checkout
# (git would renormalize them on the next add), although the bytes on disk are
# exactly the committed ones. freeze-target.sh refused such a repo outright and
# left the worktree behind -- measured 2026-09-22 on an SDK repository, 18
# files under one subdirectory (reported by a peer session).
#
# The test is BYTE identity against HEAD, not `git diff --ignore-cr-at-eol`:
# a reviewer reads the working tree, so an EXCUSED file must hold the commit's
# bytes. Ignoring CR would also excuse a real edit that happens to be
# line-ending shaped. Scope, stated so nobody reads more into it: this bounds
# only what is excused. The certification itself stays `git status`-based, and
# a tree git calls clean can still differ from the blobs byte for byte (a
# round-trip smudge/clean filter, `ident`, the caller's global core.autocrlf,
# skip-worktree) -- pre-existing, not changed here. A mode change is never excused (the raw
# diff's old and new modes must match), only a ` M` entry is ever excused
# (never staged, untracked or ignored ones), and a path git has to escape
# (backslash, control characters) is skipped -- it stays dirty, which fails
# safe.
set -euo pipefail

usage() { echo "usage: renorm-only.sh [--filter] <worktree-dir>" >&2; exit 2; }
filter=""
if [ "${1:-}" = "--filter" ]; then filter=1; shift; fi
[ $# -eq 1 ] || usage
dir=$1

list() {
  # --raw: ":<old mode> <new mode> <old sha> <new sha> <status>\t<path>".
  # HEAD against the WORKING TREE (index included), tracked paths only.
  git -C "$dir" -c core.quotePath=false diff HEAD --raw --no-renames --no-ext-diff 2>/dev/null |
  while IFS=$'\t' read -r meta path; do
    read -r omode nmode _ _ status <<< "$meta"
    [ "${omode#:}" = "$nmode" ] || continue   # mode changed: never excused
    [ "$status" = "M" ] || continue           # only modifications
    case "$path" in \"*) continue ;; esac     # escaped by git: stays dirty
    f="$dir/$path"
    [ -f "$f" ] && [ ! -L "$f" ] || continue
    # an if, not `cmp && printf`: a non-identical LAST file would otherwise
    # become the loop's status, and under pipefail + set -e abort the caller
    if cmp -s <(git -C "$dir" cat-file blob "HEAD:$path") "$f"; then
      printf '%s\n' "$path"
    fi
  done
}

renorm=$(list)
if [ -z "$filter" ]; then
  [ -z "$renorm" ] || printf '%s\n' "$renorm"
  exit 0
fi

while IFS= read -r line; do
  [ -n "$line" ] || continue
  code=${line:0:2}; path=${line:3}
  # porcelain v1 wraps a path containing a space in quotes; unwrap that plain
  # case only -- anything with an escape inside stays as it is, and so dirty
  case "$path" in
    \"*\") inner=${path:1:${#path}-2}
           case "$inner" in *\\*) ;; *) path=$inner ;; esac ;;
  esac
  if [ "$code" = " M" ] && [ -n "$renorm" ] && printf '%s\n' "$renorm" | grep -qxF -- "$path"; then
    continue
  fi
  printf '%s\n' "$line"
done
