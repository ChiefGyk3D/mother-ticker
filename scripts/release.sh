#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
#
# Cut a release: bump the version, roll CHANGELOG's Unreleased section into a
# dated heading, commit, and create an annotated tag. Pushing the tag runs
# .github/workflows/release.yml, which verifies everything agrees and creates
# the GitHub release with the CHANGELOG section as its notes.
#
#   scripts/release.sh 0.2.0
set -euo pipefail

usage() { echo "usage: $0 X.Y.Z" >&2; exit 64; }
[[ $# -eq 1 ]] || usage
version=$1
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "not a SemVer version: $version" >&2; exit 64; }

root=$(git rev-parse --show-toplevel)
cd "$root"

if [[ -n $(git status --porcelain) ]]; then
    echo "working tree is not clean; commit or stash first" >&2
    exit 1
fi
branch=$(git rev-parse --abbrev-ref HEAD)
if [[ $branch != "main" ]]; then
    echo "releases are cut from main (currently on $branch)" >&2
    exit 1
fi
if git rev-parse -q --verify "refs/tags/v$version" >/dev/null; then
    echo "tag v$version already exists" >&2
    exit 1
fi
if ! grep -q '^## \[Unreleased\]' CHANGELOG.md; then
    echo "CHANGELOG.md has no [Unreleased] section" >&2
    exit 1
fi

# Four cases. A section for the version may already exist (the first release,
# written by hand) and Unreleased may or may not hold entries.
section_exists=0; grep -q "^## \[$version\]" CHANGELOG.md && section_exists=1
unreleased_has_entries=0; python3 scripts/changelog_section.py --check Unreleased >/dev/null 2>&1 && unreleased_has_entries=1
roll=0
if [[ $section_exists -eq 1 && $unreleased_has_entries -eq 1 ]]; then
    echo "CHANGELOG.md has both a [$version] section and Unreleased entries; fold them into one by hand, or pick the next version" >&2
    exit 1
elif [[ $section_exists -eq 0 && $unreleased_has_entries -eq 0 ]]; then
    echo "CHANGELOG.md's [Unreleased] section is empty and there is no [$version] section; nothing to release" >&2
    exit 1
elif [[ $section_exists -eq 0 ]]; then
    roll=1
fi

today=$(date -u +%Y-%m-%d)

# version.py is the single source of truth.
sed -i -E "s/^__version__ = \"[^\"]+\"/__version__ = \"$version\"/" src/mother_ticker/version.py
grep -q "__version__ = \"$version\"" src/mother_ticker/version.py

# README badge.
sed -i -E "s/(version-)[0-9]+\.[0-9]+\.[0-9]+(-blue)/\1$version\2/" README.md

# Roll Unreleased into a dated section and open a fresh Unreleased above it,
# unless the section was already written by hand.
if [[ $roll -eq 1 ]]; then
    python3 - "$version" "$today" <<'PY'
import re, sys
version, today = sys.argv[1:3]
path = "CHANGELOG.md"
text = open(path, encoding="utf-8").read()
new = f"## [Unreleased]\n\n## [{version}] - {today}"
text, n = re.subn(r"^## \[Unreleased\]\s*$", new, text, count=1, flags=re.M)
assert n == 1, "no Unreleased heading"
open(path, "w", encoding="utf-8").write(text)
PY
fi

python3 scripts/changelog_section.py --check "$version"

git add src/mother_ticker/version.py README.md CHANGELOG.md
if ! git diff --cached --quiet; then
    git commit -m "Release $version"
else
    echo "version, badge and CHANGELOG already say $version; tagging HEAD as is"
fi
git tag -a "v$version" -m "Mother Ticker $version"

echo
echo "Tagged v$version. Review with: git show v$version"
echo "Publish with:   git push origin main v$version"
