# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Drive the TUI headless with Textual's pilot. Collectors are faked; nothing touches the host."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import replace
from typing import Any

import pytest
from textual.pilot import Pilot
from textual.widgets import DataTable, Log, OptionList, Static

from mother_ticker.collectors.model import (
    ChronyStatus,
    NetworkStatus,
    PpsStatus,
    Snapshot,
)
from mother_ticker.config import Config, Thresholds, TuiConfig
from mother_ticker.health.evaluate import Level, evaluate
from mother_ticker.tui import screens
from mother_ticker.tui.app import MotherTickerApp
from mother_ticker.tui.art import LOGO, render_big
from mother_ticker.tui.demo import DemoApp, demo_snapshot
from mother_ticker.tui.format import bytes_text, duration_text, offset_text
from mother_ticker.tui.widgets import LIT, BigClock, IconPanel
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


def _banner(app: MotherTickerApp) -> Static:
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
            gnss = app.screen.query_one("#panel-gnss", IconPanel)
            assert "3D fix" in str(gnss.query_one(".panel-text", Static).render())
            assert "|##|=" in str(gnss.query_one(".panel-icon", Static).render())
            clock = app.screen.query_one("#clock", BigClock).render()
            plain = str(clock)
            assert plain.count("\n") == 4
            assert LIT not in plain and "#" not in plain  # lit cells are coloured spaces
            spans = getattr(clock, "spans", [])
            assert spans and all(span.style.background is not None for span in spans)

        run(app, scenario)

    def test_critical_banner_flashes(self) -> None:
        sick = make_snapshot(pps=PpsStatus(present=True, device="pps0", age_s=12.0, pulsing=False))
        app = FakeApp(sick)

        async def scenario(pilot: Pilot[None]) -> None:
            banner = _banner(app)
            assert banner.has_class("critical")
            assert "PPS not pulsing" in str(banner.render())
            assert app.screen.query_one("#panel-pps", IconPanel).has_class("critical")
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


class TestAboutAndArt:
    def test_about_screen_names_the_author(self) -> None:
        app = FakeApp(make_snapshot())

        async def scenario(pilot: Pilot[None]) -> None:
            await pilot.press("space")
            await pilot.pause(0.1)
            menu = app.screen.query_one("#menu", OptionList)
            menu.highlighted = next(
                i for i in range(menu.option_count) if menu.get_option_at_index(i).id == "about"
            )
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert isinstance(app.screen, screens.AboutScreen)
            text = str(app.screen.query_one("#about-text", Static).render())
            assert "ChiefGyk3D" in text
            assert "Renegade Penguin LLC" in text
            assert "0.1.0" in text
            logo = str(app.screen.query_one("#about-logo", Static).render())
            assert "/ A   \\" in logo  # the anarchy A inside the swirl

        run(app, scenario)

    def test_logo_is_plain_ascii_and_fits(self) -> None:
        lines = LOGO.splitlines()
        assert all(ord(c) < 128 for line in lines for c in line)
        assert max(len(line) for line in lines) <= 48
        assert len(lines) <= 17

    def test_render_big(self) -> None:
        out = render_big("10:5", "#")
        rows = out.split("\n")
        assert len(rows) == 5
        assert len({len(r) for r in rows}) == 1  # equal widths, or centring tears the digits
        assert rows[4].startswith("##### #####")  # 1 has a full base, 0 has a full base
        assert rows[1].split()[2] == "#"  # the colon's upper dot
        ascii_out = render_big("8", "#")
        assert ascii_out.split("\n")[0] == "#####"

    def test_ascii_only_clock(self) -> None:
        cfg = replace(CONFIG, tui=replace(CONFIG.tui, ascii_only=True))
        app = FakeApp(make_snapshot(), cfg)

        async def scenario(pilot: Pilot[None]) -> None:
            clock = str(app.screen.query_one("#clock", BigClock).render())
            assert "#" in clock and LIT not in clock

        run(app, scenario)


class TestDemo:
    @pytest.mark.parametrize("state", ["nominal", "warning", "critical"])
    def test_demo_snapshot_states(self, state: str) -> None:
        snap = demo_snapshot(state)  # type: ignore[arg-type]
        level = evaluate(snap, Thresholds()).level
        assert (
            level
            == {"nominal": Level.OK, "warning": Level.WARNING, "critical": Level.CRITICAL}[state]
        )

    def test_demo_app_runs_and_actions_are_harmless(self) -> None:
        app = DemoApp(CONFIG, "nominal")

        async def scenario(pilot: Pilot[None]) -> None:
            assert isinstance(app.screen, screens.DashboardScreen)
            assert "NOMINAL" in str(_banner(app).render())
            ok, msg = app.restart_service("gpsd")
            assert ok and msg.startswith("demo:")
            assert "PPS" in app.collect_chrony_text().sources_text
            assert "chrony" in app.journal_tail("chrony")

        async def go() -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause(0.4)
                await scenario(pilot)

        asyncio.run(go())


class TestKeysAndAdmin:
    def test_footer_lists_the_key_combos(self) -> None:
        app = FakeApp(make_snapshot())

        async def scenario(pilot: Pilot[None]) -> None:
            left = str(app.screen.query_one("#footer-left", Static).render())
            for combo in ("Ctrl+A", "F1", "Ctrl+Q", "any key"):
                assert combo in left

        run(app, scenario)

    def test_f1_opens_the_key_table_from_anywhere(self) -> None:
        app = FakeApp(make_snapshot())

        async def scenario(pilot: Pilot[None]) -> None:
            await pilot.press("f1")
            await pilot.pause(0.1)
            assert isinstance(app.screen, screens.HelpScreen)
            text = str(app.screen.query_one("#keys", Static).render())
            assert "Ctrl+A" in text and "Alt+F2" in text
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert isinstance(app.screen, screens.DashboardScreen)

        run(app, scenario)

    def test_ctrl_a_runs_su_as_the_admin(self) -> None:
        cfg = replace(CONFIG, tui=replace(CONFIG.tui, admin_user="admin"))
        app = FakeApp(make_snapshot(), cfg)
        calls: list[str] = []

        def fake_su(admin: str) -> int:
            calls.append(admin)
            return 0

        app.run_admin_shell = fake_su  # type: ignore[method-assign]

        async def scenario(pilot: Pilot[None]) -> None:
            await pilot.press("ctrl+a")
            await pilot.pause(0.1)
            assert calls == ["admin"]
            await pilot.press("space")
            await pilot.pause(0.1)
            menu = app.screen.query_one("#menu", OptionList)
            ids = [menu.get_option_at_index(i).id for i in range(menu.option_count)]
            assert "admin" in ids and "help" in ids

        run(app, scenario)

    def test_admin_login_absent_without_admin_user(self) -> None:
        app = FakeApp(make_snapshot())

        async def scenario(pilot: Pilot[None]) -> None:
            await pilot.press("space")
            await pilot.pause(0.1)
            menu = app.screen.query_one("#menu", OptionList)
            ids = {menu.get_option_at_index(i).id for i in range(menu.option_count)}
            assert "admin" not in ids

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
