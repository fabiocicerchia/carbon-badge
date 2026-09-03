# shellcheck shell=sh
# Devtron — refresh the CI carbon badge on a schedule.
#
# Not a standalone script: it is the body of a Devtron task, and Devtron
# supplies the interpreter — hence the shellcheck directive instead of a
# shebang.
#
# Run it as a task of type "Container image" with
# ghcr.io/fabiocicerchia/carbon-badge:0.2.1 — the image's entrypoint is the CLI
# itself, so the task needs no script at all: put the arguments in the task's
# Command/Args and the token in an Input Variable.
#
#   Command: (leave empty — the entrypoint is carbon-badge)
#   Args:    OWNER/REPO --grid-intensity 56
#
# This script is the Shell-task equivalent, for a node that has Docker but no
# per-task image. Two places it fits: a Devtron Job with a cron trigger, or a
# Post-Deployment task if you would rather refresh after each release.
set -eu

TARGET_REPO="${TARGET_REPO:?owner/repo (GitHub) or group/project (GitLab)}"
PROVIDER="${PROVIDER:-github}"
GRID_INTENSITY="${GRID_INTENSITY:-480}" # world average; 56 = eu-north-1
BADGE_OUT="${BADGE_OUT:-/tmp/badge.json}"
CARBON_BADGE_IMAGE="${CARBON_BADGE_IMAGE:-ghcr.io/fabiocicerchia/carbon-badge:0.2.1}"

docker run --rm -e GITHUB_TOKEN -e GITLAB_TOKEN "$CARBON_BADGE_IMAGE" \
  "$TARGET_REPO" \
  --provider "$PROVIDER" \
  --grid-intensity "$GRID_INTENSITY" \
  > "$BADGE_OUT"

cat "$BADGE_OUT"

# The badge is only useful once shields.io can fetch it. Copy it to whatever
# public object store you already run, e.g.:
#   aws s3 cp "$BADGE_OUT" s3://my-public-badges/badge.json
