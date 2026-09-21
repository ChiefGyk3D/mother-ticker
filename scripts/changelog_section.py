#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Print the CHANGELOG.md section for one version, for use as release notes.

    scripts/changelog_section.py 0.1.0            print the section body
    scripts/changelog_section.py --check 0.1.0    exit 1 if the section is missing or empty

Keep a Changelog format: a section starts with `## [X.Y.Z] - YYYY-MM-DD` and
ends at the next `## ` heading.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def section_for(text: str, version: str) -> str | None:
    heading = re.compile(rf"^## \[{re.escape(version)}\](?: - \d{{4}}-\d{{2}}-\d{{2}})?\s*$", re.M)
    match = heading.search(text)
    if not match:
        return None
    rest = text[match.end() :]
    nxt = re.search(r"^## ", rest, re.M)
    body = rest[: nxt.start()] if nxt else rest
    return body.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("version")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--path", default="CHANGELOG.md")
    args = parser.parse_args(argv)
    body = section_for(Path(args.path).read_text(encoding="utf-8"), args.version)
    if body is None:
        print(f"CHANGELOG.md has no section for {args.version}", file=sys.stderr)
        return 1
    if not body:
        print(f"CHANGELOG.md section for {args.version} is empty", file=sys.stderr)
        return 1
    if not args.check:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
