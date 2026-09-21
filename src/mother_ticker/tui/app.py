# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""The application: owns the collector thread and the screen registry.

Every system call the screens need goes through a method here so a test can
swap in fakes without touching Textual internals.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from textual.app import App
from textual.binding import Binding, BindingType
from textual.screen import Screen
from textual.worker import get_current_worker

from mother_ticker.collectors import services as svc
from mother_ticker.collectors.chrony import collect_chrony
from mother_ticker.collectors.gather import gather
from mother_ticker.collectors.model import ChronyStatus, NetworkStatus, Snapshot
from mother_ticker.collectors.network import collect_network
from mother_ticker.config import Config
from mother_ticker.health.evaluate import HealthReport, evaluate
from mother_ticker.tui import screens
from mother_ticker.tui.messages import SnapshotUpdated

Collector = Callable[[Config, NetworkStatus | None], Snapshot]


class MotherTickerApp(App[None]):
    TITLE = "Mother Ticker"
    # Absolute so a subclass in another module (the test fake) resolves the same file.
    CSS_PATH = Path(__file__).with_name("styles.tcss")
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+q", "quit", "Quit", show=False, priority=True)
    ]
    SCREENS: ClassVar[dict[str, Callable[[], Screen[Any]]]] = {
        "menu": screens.MenuScreen,
        "gnss": screens.GnssScreen,
        "chrony": screens.ChronyScreen,
        "services": screens.ServicesScreen,
        "network": screens.NetworkScreen,
        "system": screens.SystemScreen,
        "maint": screens.MaintenanceScreen,
    }

    def __init__(
        self,
        config: Config,
        collector: Collector | None = None,
        network_collector: Callable[[], NetworkStatus] | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.collector: Collector = collector or gather
        self.network_collector = network_collector or collect_network
        self.snapshot: Snapshot | None = None
        self.report: HealthReport | None = None
        self.exit_to_shell = False

    def on_mount(self) -> None:
        self.push_screen(screens.DashboardScreen())
        self.run_worker(self._collect_loop, thread=True, exclusive=True, name="collector")

    # Collector thread ------------------------------------------------------

    def _collect_loop(self) -> None:
        worker = get_current_worker()
        network: NetworkStatus | None = None
        network_at = 0.0
        while not worker.is_cancelled:
            started = time.monotonic()
            if network is None or started - network_at >= self.config.tui.network_refresh_s:
                network = self.network_collector()
                network_at = started
            snapshot = self.collector(self.config, network)
            report = evaluate(snapshot, self.config.health.thresholds)
            self.call_from_thread(self._publish, snapshot, report)
            remaining = self.config.tui.refresh_s - (time.monotonic() - started)
            while remaining > 0 and not worker.is_cancelled:
                step = min(0.2, remaining)
                time.sleep(step)
                remaining -= step

    def _publish(self, snapshot: Snapshot, report: HealthReport) -> None:
        self.snapshot = snapshot
        self.report = report
        for screen in self.screen_stack:
            screen.post_message(SnapshotUpdated(snapshot, report))

    # System actions the screens call ----------------------------------------

    def collect_chrony_text(self) -> ChronyStatus:
        return collect_chrony(with_text=True)

    def journal_tail(self, unit: str) -> str:
        return svc.journal_tail(unit)

    def restart_service(self, unit: str) -> tuple[bool, str]:
        return svc.restart_service(unit, self.config.tui.services)

    def reboot_host(self) -> tuple[bool, str]:
        return svc.reboot_host()

    def maintenance_mode(self, enable: bool) -> tuple[bool, str]:
        return svc.maintenance_mode(enable)
