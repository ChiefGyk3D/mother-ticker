#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
#
# Build an offline update bundle FOR the malware-net unit ON the main-LAN unit
# (same OS, same architecture, full internet). The bundle carries fresh apt
# lists, every pending .deb, and a wheelhouse for the Python venv, so the
# isolated unit can update without a WAN window.
#
#   sudo scripts/stage-offline-bundle.sh [output-dir]
#
# Apply on the isolated unit (as root, in maintenance mode):
#   tar -C / -xf mother-ticker-offline-<date>.tar --no-same-owner ./var/lib/apt/lists
#   cp bundle/debs/*.deb /var/cache/apt/archives/
#   apt-get --no-download upgrade
#   /opt/mother-ticker/venv/bin/pip install --no-index --find-links bundle/wheelhouse --upgrade /opt/mother-ticker/src
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "run as root (apt needs it)" >&2; exit 1; }
out=${1:-./offline-bundle}
stamp=$(date -u +%Y%m%d)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

echo "==> refreshing apt lists"
apt-get update -q

echo "==> downloading every upgradable package"
mkdir -p "$work/debs"
apt-get -q -y --download-only -o Dir::Cache::archives="$work/debs" dist-upgrade
rm -rf "$work/debs/partial" "$work/debs/lock"

echo "==> copying apt lists"
mkdir -p "$work/var/lib/apt"
cp -a /var/lib/apt/lists "$work/var/lib/apt/"
rm -rf "$work/var/lib/apt/lists/partial" "$work/var/lib/apt/lists/lock"

echo "==> building the Python wheelhouse"
mkdir -p "$work/wheelhouse"
src=/opt/mother-ticker/src
if [[ -f $src/pyproject.toml ]]; then
    /opt/mother-ticker/venv/bin/pip download --quiet --dest "$work/wheelhouse" "$src"
else
    echo "    /opt/mother-ticker/src not found; skipping wheelhouse" >&2
fi

mkdir -p "$out"
bundle="$out/mother-ticker-offline-$stamp.tar"
tar -C "$work" -cf "$bundle" .
sha256sum "$bundle" > "$bundle.sha256"
echo
echo "bundle: $bundle"
echo "sha256: $(cut -d' ' -f1 "$bundle.sha256")"
echo "debs:   $(find "$work/debs" -name '*.deb' | wc -l)"
echo "Copy both files to the isolated unit over the management network and verify the hash there."
