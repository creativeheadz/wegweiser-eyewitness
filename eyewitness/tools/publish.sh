#!/bin/sh
# Publish the public Eyewitness repo (brief section 11).
#
# The source is the private Wegweiser repository, given in the environment so
# its address never appears in this public file:
#   EYEWITNESS_SOURCE_REPO=<clone URL of the private repo> eyewitness/tools/publish.sh
# Optional: EYEWITNESS_PUBLISH_DENYLIST=<file of strings that must not go public>
# Needs git-filter-repo on PATH. The push is a plain (non-force) push, so it
# only ever fast-forwards: a rewritten feature/eyewitness history is refused.
set -eu
: "${EYEWITNESS_SOURCE_REPO:?set EYEWITNESS_SOURCE_REPO to the private repo clone URL}"
PUBLIC_REPO="${EYEWITNESS_PUBLIC_REPO:-https://github.com/creativeheadz/wegweiser-eyewitness.git}"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
git clone --quiet --branch feature/eyewitness "$EYEWITNESS_SOURCE_REPO" "$TMP/repo"
cd "$TMP/repo"
git filter-repo --force --quiet \
  --path app/eyewitness/ \
  --path app/templates/eyewitness/ \
  --path app/static/js/eyewitness/ \
  --path app/static/css/eyewitness/ \
  --path-glob 'migrations/versions/eyewitness_*' \
  --path tests/eyewitness/ \
  --path eyewitness/ \
  --path-rename eyewitness/README.md:README.md \
  --path-rename eyewitness/LICENSE:LICENSE
# Refuse to publish internal details. EYEWITNESS_PUBLISH_DENYLIST names
# a file outside the repo, one fixed string per line; the commits about
# to go public are searched for each, and any match stops the push.
if [ -n "${EYEWITNESS_PUBLISH_DENYLIST:-}" ]; then
  git fetch --quiet "$PUBLIC_REPO" main
  if git log -p FETCH_HEAD..HEAD | grep -q -F -f "$EYEWITNESS_PUBLISH_DENYLIST"; then
    echo "refusing to publish: new commits hold text from the denylist" >&2
    exit 1
  fi
fi
git push "$PUBLIC_REPO" HEAD:main
curl -sf -o /dev/null https://github.com/creativeheadz/wegweiser-eyewitness
echo "published $(git rev-parse --short HEAD)"
