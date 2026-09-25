#!/usr/bin/env bash
# Sourced by the helpers next to it; not a program. Added 0.6.49 to replace
# five copies of die() and three of the git-repo check.
#
#   here=$(cd -- "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")")" && pwd)
#   . "$here/lib.sh"
#
# The resolver goes through symlinks on purpose: the library is a sibling of
# the REAL file, not of a link to it (freeze-target.sh had this bug first).

# die: the message on stderr, prefixed with the calling script's name, exit 1.
# DIE_PREFIX overrides the prefix for a script whose name is not its voice.
die() { echo "${DIE_PREFIX:-$(basename -- "$0" .sh)}: $*" >&2; exit 1; }

# require_git_repo <dir> [what]: the directory exists and is inside a git
# repository, or die naming it as <what> (default "dir").
require_git_repo() {
  [ -d "$1" ] || die "${2:-dir} does not exist: $1"
  git -C "$1" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repo: $1"
}
