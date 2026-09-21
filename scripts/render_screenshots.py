#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Render the TUI headless with demo data and save screenshots for the docs.

    scripts/render_screenshots.py [OUT_DIR]      default docs/images

Textual exports an SVG; Chromium (through Playwright) rasterises it to the
PNG the README shows. The SVG is kept beside it. PNG rather than SVG because
GitHub shows README images with external fonts blocked and the fallback
monospace differs per viewer, while a PNG is the same pixels everywhere.
Size is 100x30 cells, the 7 inch panel with the default console font.

Needs `pip install playwright` and `playwright install chromium`; without
them the SVGs are written and the PNG step is skipped with a warning.
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
from pathlib import Path

from rich.console import Console
from textual.widgets import OptionList

from mother_ticker.config import Config, TuiConfig
from mother_ticker.tui.demo import DemoApp, DemoState

SIZE = (100, 30)

# Rich's default template wraps the terminal in a macOS-style window with a
# title bar and fetches Fira Code from a CDN. This one is the terminal alone,
# on the app's own background, in whatever monospace the viewer has.
SVG_TEMPLATE = """\
<svg class="rich-terminal" viewBox="0 0 {terminal_width} {terminal_height}" xmlns="http://www.w3.org/2000/svg">
    <style>
    .{unique_id}-matrix {{
        font-family: "DejaVu Sans Mono", Menlo, Consolas, "Liberation Mono", monospace;
        font-size: {char_height}px;
        line-height: {line_height}px;
        font-variant-east-asian: full-width;
    }}
    {styles}
    </style>
    <defs>
    <clipPath id="{unique_id}-clip-terminal">
      <rect x="0" y="0" width="{terminal_width}" height="{terminal_height}" />
    </clipPath>
    {lines}
    </defs>
    <rect fill="#0b0e14" x="0" y="0" width="{terminal_width}" height="{terminal_height}" />
    <g clip-path="url(#{unique_id}-clip-terminal)">
    {backgrounds}
    <g class="{unique_id}-matrix">
    {matrix}
    </g>
    </g>
</svg>
"""


def export_svg(app: DemoApp) -> str:
    """Textual's export_screenshot with our template instead of Rich's default."""
    width, height = app.size
    console = Console(
        width=width,
        height=height,
        file=io.StringIO(),
        force_terminal=True,
        color_system="truecolor",
        record=True,
        legacy_windows=False,
        safe_box=False,
    )
    console.print(
        app.screen._compositor.render_update(full=True, screen_stack=app._background_screens)
    )
    return console.export_svg(title="", code_format=SVG_TEMPLATE)


CONFIG = Config(tui=TuiConfig(refresh_s=0.2, network_refresh_s=60.0))

# name -> (demo state, menu option id or None for the dashboard)
SHOTS: dict[str, tuple[DemoState, str | None]] = {
    "dashboard": ("nominal", None),
    "dashboard-warning": ("warning", None),
    "dashboard-critical": ("critical", None),
    "menu": ("nominal", "menu"),
    "gnss": ("nominal", "gnss"),
    "chrony": ("nominal", "chrony"),
    "services": ("nominal", "services"),
    "system": ("nominal", "system"),
    "about": ("nominal", "about"),
}


async def render_one(name: str, state: DemoState, target: str | None, out_dir: Path) -> Path:
    app = DemoApp(CONFIG, state)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause(0.6)
        if target is not None:
            await pilot.press("space")
            await pilot.pause(0.2)
            if target != "menu":
                menu = app.screen.query_one("#menu", OptionList)
                index = next(
                    i for i in range(menu.option_count) if menu.get_option_at_index(i).id == target
                )
                menu.highlighted = index
                await pilot.press("enter")
                await pilot.pause(0.6)
        if target is None:
            # Capture the banner's steady state, not whichever half of the blink the timer is on.
            app.screen.query_one("#banner").remove_class("flash")
            await pilot.pause(0.1)
        path = out_dir / f"{name}.svg"
        path.write_text(export_svg(app), encoding="utf-8")
        return path


def rasterise(svgs: list[Path]) -> bool:
    """SVG to PNG with Chromium, shown as <img> exactly as GitHub does. False if unavailable."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415  optional dependency
    except ImportError:
        print("playwright not installed; PNGs not regenerated", file=sys.stderr)
        return False
    executable = os.environ.get("MOTHER_TICKER_CHROMIUM")
    try:
        with sync_playwright() as pw:
            browser = (
                pw.chromium.launch(executable_path=executable)
                if executable
                else pw.chromium.launch()
            )
            page = browser.new_page(device_scale_factor=2)
            for svg in svgs:
                html = svg.with_suffix(".html")
                html.write_text(
                    '<html><body style="margin:0;background:#fff">'
                    f'<img id="shot" src="file://{svg.resolve()}"></body></html>',
                    encoding="utf-8",
                )
                page.goto(f"file://{html.resolve()}")
                page.wait_for_timeout(400)
                page.locator("#shot").screenshot(path=str(svg.with_suffix(".png")), timeout=15000)
                html.unlink()
                print(svg.with_suffix(".png"))
            browser.close()
    except Exception as exc:
        print(f"chromium rasterisation failed: {exc}", file=sys.stderr)
        return False
    return True


def main(argv: list[str]) -> int:
    out_dir = Path(argv[1]) if len(argv) > 1 else Path("docs/images")
    out_dir.mkdir(parents=True, exist_ok=True)
    svgs: list[Path] = []
    for name, (state, target) in SHOTS.items():
        path = asyncio.run(render_one(name, state, target, out_dir))
        print(path)
        svgs.append(path)
    return 0 if rasterise(svgs) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
