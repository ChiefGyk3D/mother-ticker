# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Collectors are the only code that touches the running system.

Each one returns a frozen dataclass from `model.py`. Parsers are pure functions
over text or JSON so the test suite can run them against recorded fixtures
with no GPS, no chrony and no Raspberry Pi.
"""
