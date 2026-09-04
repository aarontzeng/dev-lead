#!/usr/bin/env bash
# Cut a release: bump plugin.json, tag it with the notes, print the push.
#
# Why this is a script and not three commands in a doc: every step below failed
# in a real session on 2026-09-04, and each failure was invisible at the moment
# it happened.
#
#   * Two clones both bumped to 0.3.40. `git describe` is reachability-based, so
#     each saw only its own tags and both linted green. scripts/lint.py now asks
#     origin, and this script asks again at the one moment it matters.
#   * A tag was pushed before its commit reached origin/master. A rebase then
#     moved the commit, leaving two published tags pointing at nothing on any
#     branch -- and deleting a published tag is not undoable.
#   * Release notes were hand-edited afterwards, so the tag annotation and the
#     published page now disagree permanently. ci.yml builds the Release with
#     --notes-from-tag: the annotation IS the notes, so this script refuses a
#     thin one.
#
# It does NOT bump the version. This repo's lint rule 2 requires every commit
# past a release to already declare a higher version, so the bump belongs in the
# content commit that needed it -- a bump added here would red every push in
# between. What this does is prove the declared version is releasable, and tag it.
#
# Usage:  scripts/release.sh <notes-file>
# Prints: the exact push command. It does NOT push -- publishing stays a human
#         decision, and an --atomic push is what keeps master and the tag from
#         ever going out separately.
set -euo pipefail

die() { echo "release: $*" >&2; exit 1; }

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

[ $# -eq 1 ] || die "usage: release.sh <notes-file>"
notes=$1
[ -f "$notes" ] || die "notes file does not exist: $notes"

# The annotation IS the release page (ci.yml --notes-from-tag). A one-line
# annotation ships a one-line release; that is how v0.3.39 and v0.3.40 ended up
# hand-edited after the fact.
[ -s "$notes" ] || die "notes file is empty: $notes"
[ "$(grep -c . "$notes")" -ge 3 ] \
  || die "notes file has fewer than 3 non-blank lines -- this text becomes the
       release page verbatim, so write it here rather than editing the page later"

manifest=.claude-plugin/plugin.json
[ -f "$manifest" ] || die "no $manifest"

# A dirty tree means the tag would not describe what you think it does.
[ -z "$(git status --porcelain)" ] || die "working tree is dirty -- commit or stash first"

git fetch --quiet --tags origin || die "cannot fetch origin"

branch=$(git rev-parse --abbrev-ref HEAD)
[ "$branch" = master ] || die "on '$branch', not master"

# THE ordering rule. Bump on top of a base that is already published, so the tag
# can never end up on a commit a later rebase moves. Everything content-side must
# be pushed BEFORE the release commit exists.
git merge-base --is-ancestor HEAD origin/master \
  || die "HEAD is not on origin/master -- push your content commits first, then
       cut the release. A tag on an unpushed commit is one rebase from dangling."
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/master)" ]; then
  die "HEAD is behind origin/master -- run: git pull --ff-only"
fi

declared=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$manifest" | head -1)
echo "$declared" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$' \
  || die "$manifest declares '$declared', which is not X.Y.Z"

highest=$(git ls-remote --tags --refs origin 'v*' \
          | sed 's#.*refs/tags/v##' \
          | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' \
          | sort -t. -k1,1n -k2,2n -k3,3n \
          | tail -1) || true
[ -n "${highest:-}" ] || die "origin has no v<semver> tag -- refusing to guess the first one"

# Numerically, not lexically: 0.3.9 < 0.3.10 and a string compare disagrees.
newest=$(printf '%s\n%s\n' "$highest" "$declared" \
         | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)
if [ "$declared" = "$highest" ] || [ "$newest" != "$declared" ]; then
  IFS=. read -r maj min pat <<<"$highest"
  die "$manifest declares '$declared' and origin has already published v$highest.
       Bump the manifest in a content commit first -- the next free version is
       $maj.$min.$((pat + 1)). This is the collision that cost two published
       tags on 2026-09-04; scripts/lint.py now catches it before the commit."
fi

version=$declared
tag="v$version"
git rev-parse -q --verify "refs/tags/$tag" >/dev/null \
  && die "$tag already exists locally"
[ -z "$(git ls-remote --tags origin "$tag")" ] \
  || die "$tag is already published on origin"

git tag -a "$tag" -F "$notes"

# Verify what we made rather than assuming it: ci.yml hard-fails on a
# lightweight tag, and that failure is only visible after the push.
[ "$(git cat-file -t "$tag")" = tag ] || die "$tag is not an annotated tag object"

echo "release: $tag is ready on $(git rev-parse --short HEAD)"
echo
echo "  git push --atomic origin master $tag"
echo
echo "--atomic: master and the tag go together or neither goes. That is what"
echo "stops a pushed tag from outliving a commit that gets rebased away."
