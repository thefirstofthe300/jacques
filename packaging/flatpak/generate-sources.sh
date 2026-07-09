#!/usr/bin/env bash
# Generate offline pip sources for the Flatpak build.
#
# Prerequisites:
#   pip install flatpak-pip-generator
#
# Run from the repo root:
#   bash packaging/flatpak/generate-sources.sh
#
# The script writes packaging/flatpak/python-modules.json, which is referenced
# by the python-deps module in the Flatpak manifest.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REQUIREMENTS="$SCRIPT_DIR/requirements.txt"
OUTPUT="$SCRIPT_DIR/python-modules.json"

if ! command -v flatpak-pip-generator &>/dev/null; then
    echo "ERROR: flatpak-pip-generator not found." >&2
    echo "Install it with: pip install flatpak-pip-generator" >&2
    exit 1
fi

echo "Generating Flatpak pip sources from $REQUIREMENTS..."
flatpak-pip-generator \
    --python-version 3.12 \
    --requirements-file "$REQUIREMENTS" \
    --output "$OUTPUT"

echo "Written: $OUTPUT"
echo "Commit python-modules.json alongside the manifest before building."
