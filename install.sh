#!/usr/bin/env bash
set -euo pipefail
# One-line installer for carbon-badge
# Usage: curl -fsSL https://raw.githubusercontent.com/fabiocicerchia/carbon-badge/main/install.sh | bash

if command -v pipx &>/dev/null; then
  pipx install git+https://github.com/fabiocicerchia/carbon-badge
else
  pip install --user git+https://github.com/fabiocicerchia/carbon-badge
fi
echo "carbon-badge installed. Run: carbon-badge --help"
