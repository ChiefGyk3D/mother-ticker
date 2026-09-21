# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""ASCII art and the block-digit font.

Plain ASCII on purpose: it renders on the Linux console with the stock
console font, over any SSH client, and in an SVG screenshot, identically.
"""

from __future__ import annotations

LOGO = r"""
       .-~~~~~-.
      /         \
     |  \__ __/  |
     |  (o) (o)  |
     |   \___/   |
    /|           |\
   / |   .-.     | \
  |  |  (-A-)    |  |
  |  |   '-'     |  |     ______
  | _|___________|__|____/_____/=======---
  |[_|___________|__|___/  |_|
  |  |           |  |   \_\
   \  \         /  /
    \  '-------'  /
 .--'             '--.
(_____)           (_____)
""".strip("\n")

TITLE = r"""
 __  __  ___ _____ _  _ ___ ___   _____ ___ ___ _  _____ ___
|  \/  |/ _ \_   _| || | __| _ \ |_   _|_ _/ __| |/ / __| _ \
| |\/| | (_) || | | __ | _||   /   | |  | | (__| ' <| _||   /
|_|  |_|\___/ |_| |_||_|___|_|_\   |_| |___\___|_|\_\___|_|_\
""".strip("\n")

SATELLITE = r"""
 __   ____   __
|##|=|    |=|##|
|##|=| () |=|##|
 ~~   \__/   ~~
        \/
""".strip("\n")

CLOCK = r"""
   .-""-.
  /  |   \
 |   *--> |
  \      /
   '-..-'
""".strip("\n")

PULSE = r"""
   _   _   _
  | | | | | |
__| |_| |_| |__

  1 pulse / s
""".strip("\n")

NETWORK = r"""
   [====]
      |
   .--+--.
   |     |
 [==]   [==]
""".strip("\n")

# 5 x 5 block digits. Each glyph is five rows of five cells; "X" is a lit cell.
_FONT: dict[str, tuple[str, ...]] = {
    "0": ("XXXXX", "X   X", "X   X", "X   X", "XXXXX"),
    "1": ("  X  ", " XX  ", "  X  ", "  X  ", "XXXXX"),
    "2": ("XXXXX", "    X", "XXXXX", "X    ", "XXXXX"),
    "3": ("XXXXX", "    X", "XXXXX", "    X", "XXXXX"),
    "4": ("X   X", "X   X", "XXXXX", "    X", "    X"),
    "5": ("XXXXX", "X    ", "XXXXX", "    X", "XXXXX"),
    "6": ("XXXXX", "X    ", "XXXXX", "X   X", "XXXXX"),
    "7": ("XXXXX", "    X", "    X", "    X", "    X"),
    "8": ("XXXXX", "X   X", "XXXXX", "X   X", "XXXXX"),
    "9": ("XXXXX", "X   X", "XXXXX", "    X", "XXXXX"),
    ":": ("     ", "  X  ", "     ", "  X  ", "     "),
    "-": ("     ", "     ", "XXXXX", "     ", "     "),
    " ": ("     ", "     ", "     ", "     ", "     "),
}
_GLYPH_WIDTH: dict[str, int] = {":": 3, " ": 2}


def render_big(text: str, glyph: str = "█") -> str:
    """Render digits, colons and dashes as five-row block characters.

    Every row has the same length on purpose: a centred widget shifts each
    row by its own length, so ragged rows would tear the digits apart.
    """
    rows = [""] * 5
    for ch in text:
        pattern = _FONT.get(ch, _FONT[" "])
        width = _GLYPH_WIDTH.get(ch, 5)
        for i in range(5):
            cell = pattern[i]
            if width < 5:
                trim = (5 - width) // 2
                cell = cell[trim : trim + width]
            rows[i] += cell.replace("X", glyph) + " "
    return "\n".join(r[:-1] for r in rows)
