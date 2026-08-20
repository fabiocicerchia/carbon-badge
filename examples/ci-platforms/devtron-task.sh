#!/bin/sh
# Devtron — refresh the CI carbon badge on a schedule.
#
# Two places this fits, both running this same script as an "Execute custom
# script" / Shell task:
#   - a Devtron Job with a cron trigger, if you want the badge refreshed
#     nightly regardless of deploys (what the GitHub Actions version does);
#   - a Post-Deployment task, if you would rather it refresh after each
#     release.
#
# Declare TARGET_REPO and the token as Input Variables on the task; the token
# is a PAT with actions:read (GitHub) or read_api (GitLab).
set -eu

TARGET_REPO="${TARGET_REPO:?owner/repo (GitHub) or group/project (GitLab)}"
PROVIDER="${PROVIDER:-github}"
GRID_INTENSITY="${GRID_INTENSITY:-480}" # world average; 56 = eu-north-1
BADGE_OUT="${BADGE_OUT:-/tmp/badge.json}"

pip install --no-cache-dir --quiet "git+https://github.com/fabiocicerchia/carbon-badge@v0.2.1"

carbon-badge "$TARGET_REPO" \
  --provider "$PROVIDER" \
  --grid-intensity "$GRID_INTENSITY" \
  > "$BADGE_OUT"

cat "$BADGE_OUT"

# The badge is only useful once shields.io can fetch it. Copy it to whatever
# public object store you already run, e.g.:
#   aws s3 cp "$BADGE_OUT" s3://my-public-badges/badge.json
