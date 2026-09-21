# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Widgets shared by the screens: the big clock and the icon panel.

The clock does not use Textual's Digits on purpose. Digits draws with rounded
box-drawing glyphs that the Linux console font may not carry. The big digits
here are lit cells: spaces with a background colour. That needs no glyph at
all, so it is identical on the console, over SSH and in an SVG screenshot.
`ascii_only` swaps the cells for `#` characters for a terminal that cannot
show background colours.
"""

from __future__ import annotations

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from mother_ticker.tui.art import render_big

LIT = "█"
CLOCK_COLOUR = "bright_cyan"


class BigClock(Static):
    """HH:MM:SS in 5-row digits made of coloured cells, centred by CSS."""

    def __init__(self, ascii_only: bool = False, **kwargs: object) -> None:
        super().__init__("", markup=False, **kwargs)  # type: ignore[arg-type]
        self.ascii_only = ascii_only

    def set_time(self, text: str) -> None:
        if self.ascii_only:
            self.update(render_big(text, "#"))
            return
        lit = Style(bgcolor=CLOCK_COLOUR)
        rendered = Text(no_wrap=True)
        for i, row in enumerate(render_big(text, LIT).split("\n")):
            if i:
                rendered.append("\n")
            for ch in row:
                rendered.append(" ", lit if ch == LIT else None)
        self.update(rendered)


class IconPanel(Horizontal):
    """A dashboard panel: fixed-width ASCII icon on the left, status text on the right."""

    def __init__(self, title: str, icon: str, *, id: str) -> None:
        super().__init__(id=id, classes="panel")
        self.title_text = title
        self.icon = icon

    def compose(self) -> ComposeResult:
        yield Static(self.icon, classes="panel-icon", markup=False)
        yield Static(f"{self.title_text}\nwaiting for data", classes="panel-text", markup=False)

    def set_text(self, body: str) -> None:
        self.query_one(".panel-text", Static).update(f"{self.title_text}\n{body}")

    def set_level(self, level_class: str) -> None:
        self.remove_class("warning", "critical")
        if level_class:
            self.add_class(level_class)
