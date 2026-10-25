#!/usr/bin/env bash
# Install the opencode Go usage plugin (opencode-plugin/go-usage) for opencode 2.
#
# Usage: install-opencode-go-usage.sh [--force] [--dest <dir>]
#
# Copies the plugin's files to ~/.config/opencode/plugins/go-usage (--dest
# overrides, for tests). A copy, not a link: the suite lives under a
# versioned path that changes on every update, so a link would dangle.
# Re-run after an update to refresh the copy.
#
# The copy is built in a fresh private directory next to the destination. An
# existing install is renamed into a second fresh private directory first, the
# stage is renamed into place, and only then is the old one deleted; if any
# step fails the old one is renamed back. No predictable temporary name exists
# for anyone to plant a link at. Known limit: another process creating the
# destination between the final check and the rename is not excluded (it would
# need a lock; a second installer run at the same moment is the only
# realistic case).
#
# What it replaces: its own earlier install (a real directory holding a
# regular, non-link marker file). A link, or a directory that is not its own,
# is refused, exit 1, unless --force; with --force a link is removed (never
# its target) and a foreign directory is kept, renamed into <dest>.old.XXXXXX/old
# (printed), never deleted. The retired opencode 1 link ~/.config/opencode/plugin/
# go-usage.ts is removed (only while it is a symlink) when installing to the
# default destination.
set -euo pipefail

here=$(cd -- "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")")" && pwd)
. "$here/lib.sh"

SRC="$here/../opencode-plugin/go-usage"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
DEST="$CONF/plugins/go-usage"
DEFAULT_DEST=1
MARK=".installed-by-dev-lead"
FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --dest)  [ -n "${2:-}" ] || die "--dest needs a directory"; DEST=$2; DEFAULT_DEST=0; shift 2 ;;
    *)       die "unknown option: $1 (usage: install-opencode-go-usage.sh [--force] [--dest <dir>])" ;;
  esac
done

# a trailing slash would make a link at the destination look like its target
while [ "${#DEST}" -gt 1 ] && [ "${DEST%/}" != "$DEST" ]; do DEST=${DEST%/}; done
case "$DEST" in /|.|..|*/.|*/..) die "refusing --dest $DEST" ;; esac

for f in index.ts cache.ts tui.ts; do [ -f "$SRC/$f" ] || die "plugin source missing: $SRC/$f"; done

# 0 = nothing there, 1 = our own install, 2 = a link, 3 = anything else
state=0
if [ -L "$DEST" ]; then
  state=2
elif [ -e "$DEST" ]; then
  [ -d "$DEST" ] || die "$DEST exists and is not a directory"
  if [ -f "$DEST/$MARK" ] && [ ! -L "$DEST/$MARK" ]; then state=1; else state=3; fi
fi
if [ "$state" -ge 2 ] && [ "$FORCE" != 1 ]; then
  die "$DEST is $([ "$state" = 2 ] && echo "a link" || echo "a directory this script did not make"); rerun with --force to replace it"
fi

parent=$(dirname -- "$DEST")
mkdir -p -- "$parent"
stage=$(mktemp -d -- "$DEST.stage.XXXXXX")
hold=""
restore() {                       # put the old install back if the swap did not finish
  if [ -n "$hold" ] && [ -e "$hold/old" ] && [ ! -e "$DEST" ] && [ ! -L "$DEST" ]; then
    mv -- "$hold/old" "$DEST" && rmdir -- "$hold" || true
  fi
}
trap 'rm -rf -- "$stage"; restore' EXIT
cp -- "$SRC/index.ts" "$SRC/cache.ts" "$SRC/tui.ts" "$stage/"
: > "$stage/$MARK"
for f in index.ts cache.ts tui.ts "$MARK"; do [ -f "$stage/$f" ] || die "staging incomplete: $f"; done

if [ "$state" != 0 ]; then
  hold=$(mktemp -d -- "$DEST.old.XXXXXX")
  mv -- "$DEST" "$hold/old"
fi
if [ -e "$DEST" ] || [ -L "$DEST" ]; then die "$DEST appeared during the install; nothing was replaced"; fi
mv -- "$stage" "$DEST"
trap - EXIT
case "$state" in
  3) echo "moved aside: $hold/old" ;;
  1|2) rm -rf -- "$hold" ;;
esac
echo "installed: $DEST (index.ts cache.ts tui.ts)"

if [ "$DEFAULT_DEST" = 1 ]; then
  old="$CONF/plugin/go-usage.ts"
  if [ -L "$old" ]; then
    rm -f -- "$old"
    echo "removed retired opencode 1 plugin link: $old"
  fi
fi
v=$(opencode --version 2>&1 | head -n 1 || true)
echo "needs opencode 2: ${v:-opencode not found}"
