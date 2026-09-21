# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Messages the collector thread posts to whichever screens are open."""

from __future__ import annotations

from textual.message import Message

from mother_ticker.collectors.model import Snapshot
from mother_ticker.health.evaluate import HealthReport


class SnapshotUpdated(Message):
    """A fresh Snapshot and its health report."""

    def __init__(self, snapshot: Snapshot, report: HealthReport) -> None:
        super().__init__()
        self.snapshot = snapshot
        self.report = report
