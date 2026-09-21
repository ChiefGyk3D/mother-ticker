# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Drive the TUI headless with Textual's pilot. Collectors are faked; nothing touches the host."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import replace
from typing import Any

from textual.pilot import Pilot
from textual.widgets import DataTable, Log, OptionList, Static

from mother_ticker.collectors.model import (
    ChronyStatus,
    NetworkStatus,
    PpsStatus,
    Snapshot,
)
from mother_ticker.config import Config, TuiConfig
from mother_ticker.tui import screens
from mother_ticker.tui.app import MotherTickerApp
from mother_ticker.tui.format import bytes_text, duration_text, offset_text
from tests.conftest import make_snapshot

CONFIG = Config(tui=TuiConfig(refresh_s=0.05, network_refresh_s=1.0))


class FakeApp(MotherTickerApp):
    """Every privileged action is recorded instead of executed."""

    def __init__(self, snapshot: Snapshot, config: Config = CONFIG) -> None:
        super().__init__(
            config,
            collector=lambda _cfg, _net: snapshot,
            network_collector=NetworkStatus,
        )
        self.calls: list[tuple[str, ...]] = []

    def collect_chrony_text(self) -> ChronyStatus:
        base = make_snapshot().chrony
        return ChronyStatus(
            tracking=base.tracking,
            sources=base.sources,
            tracking_text="Reference ID    : 50505300 (PPS)\nStratum         : 1\n",
            sources_text=(
                "MS Name/IP address         Stratum Poll Reach LastRx Last sample\n"
                "#* PPS 0 4 377 12 -21ns\n"
            ),
        )

    def journal_tail(self, unit: str) -> str:
        self.calls.append(("journal", unit))
        return f"2026-09-21T12:00:00+0000 host {unit}[1]: started\n"

    def restart_service(self, unit: str) -> tuple[bool, str]:
        self.calls.append(("restart", unit))
        return True, f"{unit} restarted"

    def reboot_host(self) -> tuple[bool, str]:
        self.calls.append(("reboot",))
        return True, "reboot requested"

    def maintenance_mode(self, enable: bool) -> tuple[bool, str]:
        self.calls.append(("maint", "on" if enable else "off"))
        return True, "ok"


def run(app: FakeApp, scenario: Callable[[Pilot[None]], Coroutine[Any, Any, None]]) -> None:
    async def _go() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.2)
            await scenario(pilot)

    asyncio.run(_go())


def _banner(app: FakeApp) -> Static:
    return app.screen.query_one("#banner", Static)


class TestDashboard:
    def test_healthy_banner_and_panels(self) -> None:
        app = FakeApp(make_snapshot())

        async def scenario(pilot: Pilot[None]) -> None:
            assert isinstance(app.screen, screens.DashboardScreen)
            banner = _banner(app)
            assert not banner.has_class("critical")
            assert "NOMINAL" in str(banner.render())
            footer = app.screen.query_one("#footer-right", Static)
            assert "v0.1.0" in str(footer.render())
            assert "site main-lan" in str(footer.render())
            gnss = app.screen.query_one("#panel-gnss", Static)
            assert "3D fix" in str(gnss.render())

        run(app, scenario)

    def test_critical_banner_flashes(self) -> None:
        sick = make_snapshot(pps=PpsStatus(present=True, device="pps0", age_s=12.0, pulsing=False))
        app = FakeApp(sick)

        async def scenario(pilot: Pilot[None]) -> None:
            banner = _banner(app)
            assert banner.has_class("critical")
            assert "PPS not pulsing" in str(banner.render())
            assert app.screen.query_one("#panel-pps", Static).has_class("critical")
            states = set()
            for _ in range(6):
                states.add(banner.has_class("flash"))
                await pilot.pause(0.55)
            assert states == {True, False}

        run(app, scenario)

    def test_any_key_opens_menu_and_escape_returns(self) -> None:
        app = FakeApp(make_snapshot())

        async def scenario(pilot: Pilot[None]) -> None:
            await pilot.press("space")
            await pilot.pause(0.1)
            assert isinstance(app.screen, screens.MenuScreen)
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert isinstance(app.screen, screens.DashboardScreen)

        run(app, scenario)


class TestMenuAndDetails:
    def _open(self, pilot: Pilot[None], app: FakeApp, option_id: str) -> Coroutine[Any, Any, None]:
        async def _inner() -> None:
            await pilot.press("space")
            await pilot.pause(0.1)
            menu = app.screen.query_one("#menu", OptionList)
            index = next(
                i for i in range(menu.option_count) if menu.get_option_at_index(i).id == option_id
            )
            menu.highlighted = index
            await pilot.press("enter")
            await pilot.pause(0.3)

        return _inner()

    def test_gnss_detail_lists_every_satellite(self) -> None:
        app = FakeApp(make_snapshot())

        async def scenario(pilot: Pilot[None]) -> None:
            await self._open(pilot, app, "gnss")
            assert isinstance(app.screen, screens.GnssScreen)
            table = app.screen.query_one("#sats", DataTable)
            assert table.row_count == 8
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert isinstance(app.screen, screens.MenuScreen)

        run(app, scenario)

    def test_chrony_detail_shows_raw_output(self) -> None:
        app = FakeApp(make_snapshot())

        async def scenario(pilot: Pilot[None]) -> None:
            await self._open(pilot, app, "chrony")
            await pilot.pause(0.3)
            assert isinstance(app.screen, screens.ChronyScreen)
            assert "Reference ID" in str(app.screen.query_one("#tracking", Static).render())
            assert "PPS" in str(app.screen.query_one("#sources", Static).render())

        run(app, scenario)

    def test_services_restart_requires_confirmation(self) -> None:
        app = FakeApp(make_snapshot())

        async def scenario(pilot: Pilot[None]) -> None:
            await self._open(pilot, app, "services")
            assert isinstance(app.screen, screens.ServicesScreen)
            menu = app.screen.query_one("#svc-menu", OptionList)
            menu.highlighted = 0  # Restart chrony
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert isinstance(app.screen, screens.ConfirmScreen)
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert ("restart", "chrony") not in app.calls
            await pilot.press("enter")
            await pilot.pause(0.1)
            await pilot.press("y")
            await pilot.pause(0.1)
            assert ("restart", "chrony") in app.calls

        run(app, scenario)

    def test_services_log_view(self) -> None:
        app = FakeApp(make_snapshot())

        async def scenario(pilot: Pilot[None]) -> None:
            await self._open(pilot, app, "services")
            menu = app.screen.query_one("#svc-menu", OptionList)
            menu.highlighted = 3  # View log for gpsd
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert isinstance(app.screen, screens.JournalScreen)
            assert ("journal", "gpsd") in app.calls
            log = app.screen.query_one("#journal", Log)
            assert log.line_count >= 1

        run(app, scenario)

    def test_network_and_system_screens(self) -> None:
        app = FakeApp(make_snapshot())

        async def scenario(pilot: Pilot[None]) -> None:
            await self._open(pilot, app, "system")
            assert isinstance(app.screen, screens.SystemScreen)
            text = str(app.screen.query_one("#sysinfo", Static).render())
            assert "Uptime" in text and "1d 00h 00m" in text
            await pilot.press("escape")
            await pilot.pause(0.1)
            menu = app.screen.query_one("#menu", OptionList)
            menu.highlighted = 3  # network
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert isinstance(app.screen, screens.NetworkScreen)

        run(app, scenario)

    def test_reboot_confirmation_no_by_default(self) -> None:
        app = FakeApp(make_snapshot())

        async def scenario(pilot: Pilot[None]) -> None:
            await self._open(pilot, app, "maint")
            assert isinstance(app.screen, screens.MaintenanceScreen)
            await pilot.press("enter")  # Reboot highlighted first
            await pilot.pause(0.1)
            assert isinstance(app.screen, screens.ConfirmScreen)
            await pilot.press("enter")  # focused button is No
            await pilot.pause(0.1)
            assert ("reboot",) not in app.calls
            await pilot.press("enter")
            await pilot.pause(0.1)
            await pilot.press("y")
            await pilot.pause(0.1)
            assert ("reboot",) in app.calls

        run(app, scenario)

    def test_drop_to_shell_sets_exit_flag(self) -> None:
        app = FakeApp(make_snapshot())

        async def scenario(pilot: Pilot[None]) -> None:
            await self._open(pilot, app, "shell")
            assert isinstance(app.screen, screens.ConfirmScreen)
            await pilot.press("y")
            await pilot.pause(0.2)

        run(app, scenario)
        assert app.exit_to_shell

    def test_shell_hidden_when_disallowed(self) -> None:
        cfg = replace(CONFIG, tui=replace(CONFIG.tui, allow_shell=False))
        app = FakeApp(make_snapshot(), cfg)

        async def scenario(pilot: Pilot[None]) -> None:
            await pilot.press("space")
            await pilot.pause(0.1)
            menu = app.screen.query_one("#menu", OptionList)
            ids = {menu.get_option_at_index(i).id for i in range(menu.option_count)}
            assert "shell" not in ids

        run(app, scenario)


class TestFormatters:
    def test_offset_scales(self) -> None:
        assert offset_text(-2.1e-8) == "-21 ns"
        assert offset_text(1.5e-5) == "+15.0 us"
        assert offset_text(-0.0025) == "-2.500 ms"
        assert offset_text(3.0) == "+3.000 s"

    def test_duration(self) -> None:
        assert duration_text(59) == "0m 59s"
        assert duration_text(3661) == "1h 01m 01s"
        assert duration_text(90000) == "1d 01h 00m"

    def test_bytes(self) -> None:
        assert bytes_text(512) == "512.0 B"
        assert bytes_text(1536) == "1.5 KiB"
        assert bytes_text(3 * 1024**3) == "3.0 GiB"
