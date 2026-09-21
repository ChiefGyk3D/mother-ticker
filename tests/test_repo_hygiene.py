# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Properties of the repository itself, not the program.

- No em dashes anywhere (a project rule; they creep in from editors and models).
- Every Python and shell source file carries the SPDX header.
- The version literal agrees between version.py, the README badge and CHANGELOG.
"""

from __future__ import annotations

import re
import runpy
import subprocess
from pathlib import Path

import pytest
import yaml

from mother_ticker.version import __version__

ROOT = Path(__file__).resolve().parent.parent
EM_DASH = chr(0x2014)  # spelled out so this file passes its own check
SPDX = "SPDX-License-Identifier: AGPL-3.0-or-later"


def _tracked_files() -> list[Path]:
    """Files git knows about, or everything under the tree when git is unavailable."""
    try:
        out = subprocess.run(
            [
                "git",
                "-C",
                str(ROOT),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return [ROOT / p for p in out.split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        return [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]


TEXT_SUFFIXES = {
    ".py",
    ".sh",
    ".md",
    ".yml",
    ".yaml",
    ".toml",
    ".j2",
    ".tcss",
    ".txt",
    ".cfg",
    ".rules",
    ".service",
    ".timer",
    "",
}


class TestNoEmDashes:
    def test_no_em_dash_in_any_text_file(self) -> None:
        offenders: list[str] = []
        for path in _tracked_files():
            if path.suffix not in TEXT_SUFFIXES or not path.is_file():
                continue
            if path.name == "LICENSE":
                continue  # the FSF's text is verbatim and not ours to edit
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if EM_DASH in line:
                    offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
        assert not offenders, "em dashes are banned in this repository:\n" + "\n".join(offenders)


class TestSpdxHeaders:
    @pytest.mark.parametrize(
        "pattern", ["src/**/*.py", "tests/**/*.py", "scripts/*.py", "scripts/*.sh"]
    )
    def test_headers_present(self, pattern: str) -> None:
        missing = []
        for path in ROOT.glob(pattern):
            head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:5])
            if SPDX not in head:
                missing.append(str(path.relative_to(ROOT)))
        assert not missing, "missing SPDX header:\n" + "\n".join(missing)


class TestVersionAgreement:
    def test_readme_badge(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        match = re.search(r"version-(\d+\.\d+\.\d+)-blue", readme)
        assert match, "README.md has no version badge"
        assert match.group(1) == __version__

    def test_changelog_has_section(self) -> None:
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        assert re.search(rf"^## \[{re.escape(__version__)}\]", changelog, re.M), (
            f"CHANGELOG.md has no section for {__version__}"
        )
        assert re.search(r"^## \[Unreleased\]", changelog, re.M)

    def test_changelog_tool_finds_section(self) -> None:
        result = subprocess.run(
            [
                "python3",
                str(ROOT / "scripts/changelog_section.py"),
                "--check",
                __version__,
                "--path",
                str(ROOT / "CHANGELOG.md"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


class TestScreenshots:
    """README screenshots come from scripts/render_screenshots.py; both directions must agree."""

    def test_docs_reference_existing_images(self) -> None:
        missing = []
        for doc in [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]:
            for ref in re.findall(
                r"\]\(((?:docs/images|media)/[^)\s]+)\)", doc.read_text(encoding="utf-8")
            ):
                if not (ROOT / ref).exists():
                    missing.append(f"{doc.name}: {ref}")
        assert not missing, "referenced image missing:\n" + "\n".join(missing)

    def test_every_generated_screenshot_is_shown(self) -> None:
        shots = runpy.run_path(str(ROOT / "scripts/render_screenshots.py"))["SHOTS"]
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        unshown = [name for name in shots if f"docs/images/{name}.png" not in readme]
        assert not unshown, f"screenshots generated but not in README: {unshown}"
        for name in shots:
            for ext in ("svg", "png"):
                assert (ROOT / "docs/images" / f"{name}.{ext}").exists(), (
                    f"docs/images/{name}.{ext} missing; run make screenshots"
                )


class TestWorkflows:
    """Every action is pinned to a full commit SHA with the version in a comment."""

    def test_actions_are_sha_pinned(self) -> None:
        offenders = []
        for wf in (ROOT / ".github/workflows").glob("*.yml"):
            for lineno, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), 1):
                m = re.search(r"uses:\s*([^\s@]+)@(\S+)", line)
                if not m:
                    continue
                ref = m.group(2)
                if not re.fullmatch(r"[0-9a-f]{40}", ref) or "#" not in line:
                    offenders.append(f"{wf.name}:{lineno}: {line.strip()}")
        assert not offenders, (
            "actions must be pinned to a 40-char SHA with a version comment:\n"
            + "\n".join(offenders)
        )

    def test_every_ci_job_feeds_all_green(self) -> None:
        wf = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
        jobs = wf["jobs"]
        assert "all-green" in jobs, "ci.yml needs an all-green job for branch protection"
        needs = set(jobs["all-green"]["needs"])
        others = set(jobs) - {"all-green"}
        assert needs == others, (
            f"all-green must need every job; missing {others - needs}, extra {needs - others}"
        )
