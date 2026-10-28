#!/bin/sh
# Publish the public Eyewitness repo (brief section 11).
# NOT run in Phase 1: the run overrides say commit, never push.
# Requires the LICENSE decision (eyewitness/LICENSE) before first run.
set -eu
TMP=$(mktemp -d)
git clone --branch feature/eyewitness ssh://192.168.1.251:2222/creativeheadz/wegweiser.git "$TMP/repo"
cd "$TMP/repo"
git filter-repo --force \
  --path app/eyewitness/ \
  --path app/templates/eyewitness/ \
  --path app/static/js/eyewitness/ \
  --path app/static/css/eyewitness/ \
  --path-glob 'migrations/versions/eyewitness_*' \
  --path tests/eyewitness/ \
  --path eyewitness/ \
  --path-rename eyewitness/README.md:README.md \
  --path-rename eyewitness/LICENSE:LICENSE
git push https://github.com/creativeheadz/wegweiser-eyewitness.git HEAD:main
curl -sf -o /dev/null https://github.com/creativeheadz/wegweiser-eyewitness
echo "published"
