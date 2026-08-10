#!/usr/bin/env bash
# Push-detection tripwire around a write delegate.
#
# `save` before dispatch, `check` at handoff. A successful `git push` updates
# a remote-tracking ref, so a delta here means something pushed during the run
# — a stop-and-report incident, never a shrug.
#
# Honest scope: this is a tripwire for the common case, not an alibi. A
# review-system push (refs/for/*) creates no remote-tracking ref and will not
# show up. That is why no-push stays machine-denied in the delegates'
# permission configs rather than being enforced by this check.
#
# Usage: snapshot-refs.sh save  <dir> <outfile>
#        snapshot-refs.sh check <dir> <outfile>
set -euo pipefail

die() { echo "snapshot-refs: $*" >&2; exit 1; }

[ $# -eq 3 ] || die "usage: snapshot-refs.sh save|check <dir> <outfile>"
mode=$1; dir=$2; file=$3

[ -d "$dir" ] || die "dir does not exist: $dir"
git -C "$dir" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repo: $dir"

case "$mode" in
  save)
    # The parent dir must exist NOW -- the measured failure was a snapshot
    # written to $RUN_DIR before $RUN_DIR was created, which fails at the
    # exact moment the baseline stops existing to compare against.
    parent=$(dirname "$file")
    [ -d "$parent" ] || die "output dir does not exist yet: $parent (create RUN_DIR first)"
    git -C "$dir" for-each-ref refs/remotes > "$file"
    echo "snapshot-refs: baseline saved to $file"
    ;;
  check)
    [ -f "$file" ] || die "baseline not found: $file (was 'save' run before dispatch?)"
    if ! git -C "$dir" for-each-ref refs/remotes | diff -u "$file" - > /dev/null; then
      echo "snapshot-refs: REMOTE REFS CHANGED during the run -- something pushed:" >&2
      git -C "$dir" for-each-ref refs/remotes | diff -u "$file" - >&2 || true
      die "stop and report; do not merge"
    fi
    echo "snapshot-refs: OK (no remote-tracking refs changed)"
    ;;
  *) die "unknown mode '$mode' (expected save|check)" ;;
esac
