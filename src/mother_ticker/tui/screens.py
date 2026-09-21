# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Every screen of the TUI. Two levels: the menu, and one detail screen under it."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Label, Log, OptionList, Static
from textual.widgets.option_list import Option

from mother_ticker.collectors.model import Snapshot
from mother_ticker.health.evaluate import HealthReport, Level
from mother_ticker.tui import art
from mother_ticker.tui.format import (
    chrony_summary,
    gnss_summary,
    offset_text,
    pps_summary,
    system_lines,
)
from mother_ticker.tui.messages import SnapshotUpdated
from mother_ticker.tui.widgets import BigClock, IconPanel
from mother_ticker.version import __version__

if TYPE_CHECKING:
    from mother_ticker.tui.app import MotherTickerApp


class _Base(Screen[None]):
    """Common: a title bar, Esc to go back, and access to the typed app."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "back", "Back"),
        Binding("q", "back", "Back", show=False),
    ]

    title_text = ""

    @property
    def mt_app(self) -> MotherTickerApp:
        return self.app  # type: ignore[return-value]

    def action_back(self) -> None:
        self.app.pop_screen()

    def _title(self) -> Static:
        return Static(self.title_text, classes="title")

    def _hint(self, text: str = "Esc: back") -> Static:
        return Static(text, classes="hint")


class DashboardScreen(Screen[None]):
    """The idle state: big clock, four panels, a banner that cannot be missed."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+q", "app.quit", "Quit", show=False, priority=True)
    ]

    def compose(self) -> ComposeResult:
        yield Static("STARTING", id="banner")
        yield BigClock(ascii_only=self.mt_app.config.tui.ascii_only, id="clock")
        yield Static("", id="clock-sub")
        with Grid(id="panels"):
            yield IconPanel("GNSS", art.SATELLITE, id="panel-gnss")
            yield IconPanel("CHRONY", art.CLOCK, id="panel-chrony")
            yield IconPanel("PPS", art.PULSE, id="panel-pps")
            yield IconPanel("NETWORK", art.NETWORK, id="panel-net")
        with Horizontal(id="dash-footer"):
            yield Static("MOTHER TICKER    press any key for the menu", id="footer-left")
            yield Static("", classes="version", id="footer-right")

    @property
    def mt_app(self) -> MotherTickerApp:
        return self.app  # type: ignore[return-value]

    def on_mount(self) -> None:
        self._tick_clock()
        self.set_interval(0.5, self._flash)
        self.set_interval(0.25, self._tick_clock)
        app = self.app
        snapshot = getattr(app, "snapshot", None)
        report = getattr(app, "report", None)
        if snapshot is not None and report is not None:
            self._show_snapshot(snapshot, report)

    def on_key(self, event: events.Key) -> None:
        # Any key drops into the menu. Modifier-only and quit chords are left alone.
        if event.key in ("ctrl+q", "ctrl+c") or event.key.startswith(("shift", "ctrl", "alt")):
            return
        event.stop()
        self.app.push_screen("menu")

    def _tick_clock(self) -> None:
        now = datetime.now().astimezone()
        utc = datetime.now(tz=UTC)
        self.query_one("#clock", BigClock).set_time(utc.strftime("%H:%M:%S"))
        self.query_one("#clock-sub", Static).update(
            f"{utc.strftime('%Y-%m-%d')} UTC    local {now.strftime('%H:%M:%S %Z')}"
        )

    def _flash(self) -> None:
        banner = self.query_one("#banner", Static)
        if banner.has_class("critical") or banner.has_class("warning"):
            banner.toggle_class("flash")
        else:
            banner.remove_class("flash")

    def on_snapshot_updated(self, message: SnapshotUpdated) -> None:
        self._show_snapshot(message.snapshot, message.report)

    def _show_snapshot(self, snap: Snapshot, report: HealthReport) -> None:
        banner = self.query_one("#banner", Static)
        banner.remove_class("warning", "critical")
        if report.level is Level.CRITICAL:
            banner.add_class("critical")
            banner.update("!!  " + report.headline + "  !!")
        elif report.level is Level.WARNING:
            banner.add_class("warning")
            banner.update("WARNING: " + report.headline)
        else:
            banner.update(report.headline)

        def panel(pid: str, subsystem: str, text: str) -> None:
            widget = self.query_one(pid, IconPanel)
            level = report.by_subsystem(subsystem)
            widget.set_level(
                "critical"
                if level is Level.CRITICAL
                else "warning"
                if level is Level.WARNING
                else ""
            )
            widget.set_text(text)

        t = snap.chrony.tracking
        panel("#panel-gnss", "gnss", gnss_summary(snap.gnss))
        panel(
            "#panel-chrony",
            "chrony",
            chrony_summary(t) + (f", rms {offset_text(t.rms_offset_s)}" if t.reachable else ""),
        )
        panel("#panel-pps", "pps", pps_summary(snap.pps))
        addrs = snap.network.primary_addresses()
        panel("#panel-net", "network", "\n".join(addrs[:3]) if addrs else "no address")
        self.query_one("#footer-right", Static).update(
            f"{snap.system.hostname}    site {snap.site}    v{snap.version}"
        )


class MenuScreen(_Base):
    title_text = "Mother Ticker menu"

    def compose(self) -> ComposeResult:
        yield self._title()
        options = [
            Option("GNSS detail: satellites and signal", id="gnss"),
            Option("chrony detail: tracking and sources", id="chrony"),
            Option("Services: restart, view logs", id="services"),
            Option("Network: interfaces and addresses", id="network"),
            Option("System: uptime, temperature, memory, disk", id="system"),
            Option("Maintenance: reboot, read-only overlay", id="maint"),
            Option("About Mother Ticker", id="about"),
        ]
        if self.mt_app.config.tui.allow_shell:
            options.append(Option("Drop to a shell (exits the TUI)", id="shell"))
        options.append(Option("Back to the dashboard", id="back"))
        yield OptionList(*options, id="menu")
        yield self._hint("Up/Down or Tab: move    Enter: select    Esc: dashboard")

    def on_mount(self) -> None:
        self.query_one("#menu", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        choice = event.option.id
        if choice == "back":
            self.action_back()
        elif choice == "shell":
            self.app.push_screen(
                ConfirmScreen("Leave the TUI and open a shell as this user?"),
                callback=self._shell_confirmed,
            )
        elif choice is not None:
            self.app.push_screen(choice)

    def _shell_confirmed(self, yes: bool | None) -> None:
        if yes:
            self.mt_app.exit_to_shell = True
            self.app.exit()


class GnssScreen(_Base):
    title_text = "GNSS detail"

    def compose(self) -> ComposeResult:
        yield self._title()
        yield Static("", classes="status-line", id="gnss-status")
        table: DataTable[str] = DataTable(id="sats", cursor_type="row", zebra_stripes=True)
        yield table
        yield self._hint()

    def on_mount(self) -> None:
        table = self.query_one("#sats", DataTable)
        table.add_columns("PRN", "System", "Elev", "Az", "SNR dB", "Used", "Health")
        if self.mt_app.snapshot is not None:
            self._show_snapshot(self.mt_app.snapshot)

    def on_snapshot_updated(self, message: SnapshotUpdated) -> None:
        self._show_snapshot(message.snapshot)

    def _show_snapshot(self, snap: Snapshot) -> None:
        g = snap.gnss
        pos = ""
        if g.latitude is not None and g.longitude is not None:
            pos = f"    {g.latitude:.5f}, {g.longitude:.5f}"
            if g.altitude_m is not None:
                pos += f", {g.altitude_m:.0f} m"
        self.query_one("#gnss-status", Static).update(
            f"{gnss_summary(g)}{pos}\n"
            f"receiver time {g.time_utc or 'unknown'}    device {g.device or '?'}"
        )
        table = self.query_one("#sats", DataTable)
        table.clear()
        for s in g.satellites:
            table.add_row(
                str(s.prn),
                s.constellation,
                f"{s.elevation:.0f}" if s.elevation is not None else "-",
                f"{s.azimuth:.0f}" if s.azimuth is not None else "-",
                f"{s.snr:.0f}" if s.snr is not None else "-",
                "yes" if s.used else "",
                "ok" if s.health == 1 else ("bad" if s.health == 2 else "?"),
            )


class ChronyScreen(_Base):
    title_text = "chrony detail"

    def compose(self) -> ComposeResult:
        yield self._title()
        with VerticalScroll():
            yield Static("chronyc tracking", classes="title")
            yield Static("loading", classes="pre", id="tracking", markup=False)
            yield Static("chronyc sources -v", classes="title")
            yield Static("loading", classes="pre", id="sources", markup=False)
        yield self._hint("Esc: back    refreshes every 2 s")

    def on_mount(self) -> None:
        self._load()
        self.set_interval(2.0, self._load)

    @work(thread=True, exclusive=True, group="chrony-detail")
    def _load(self) -> None:
        status = self.mt_app.collect_chrony_text()
        tracking = status.tracking_text or (status.tracking.error or "no output")
        sources = status.sources_text or "no output"
        self.app.call_from_thread(self._show, tracking, sources)

    def _show(self, tracking: str, sources: str) -> None:
        self.query_one("#tracking", Static).update(tracking.rstrip())
        self.query_one("#sources", Static).update(sources.rstrip())


class ServicesScreen(_Base):
    title_text = "Services"

    def compose(self) -> ComposeResult:
        yield self._title()
        yield Static("", classes="status-line", id="svc-status")
        options: list[Option] = []
        for unit in self.mt_app.config.tui.services:
            options.append(Option(f"Restart {unit}", id=f"restart:{unit}"))
            options.append(Option(f"View recent log for {unit}", id=f"log:{unit}"))
        options.append(Option("Back", id="back"))
        yield OptionList(*options, id="svc-menu")
        yield self._hint("Enter: select    Esc: back    restarts ask for confirmation")

    def on_mount(self) -> None:
        self.query_one("#svc-menu", OptionList).focus()
        if self.mt_app.snapshot is not None:
            self._show_snapshot(self.mt_app.snapshot)

    def on_snapshot_updated(self, message: SnapshotUpdated) -> None:
        self._show_snapshot(message.snapshot)

    def _show_snapshot(self, snap: Snapshot) -> None:
        lines = [
            f"{s.name:<10} {s.active_state}/{s.sub_state:<10} restarts {s.restarts:<3} "
            f"since {s.since}"
            for s in snap.services
        ]
        self.query_one("#svc-status", Static).update("\n".join(lines) or "no services configured")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        choice = event.option.id or ""
        if choice == "back":
            self.action_back()
            return
        action, _, unit = choice.partition(":")
        if action == "log":
            self.app.push_screen(JournalScreen(unit))
        elif action == "restart":
            self.app.push_screen(
                ConfirmScreen(f"Restart {unit}? Clients will see a brief gap in service."),
                callback=lambda yes: self._restart(unit, yes),
            )

    def _restart(self, unit: str, yes: bool | None) -> None:
        if not yes:
            return
        ok, message = self.mt_app.restart_service(unit)
        self.app.notify(message, severity="information" if ok else "error", timeout=6)


class JournalScreen(_Base):
    def __init__(self, unit: str) -> None:
        super().__init__()
        self.unit = unit
        self.title_text = f"journalctl -u {unit} (last 60 lines)"

    def compose(self) -> ComposeResult:
        yield self._title()
        yield Log(id="journal", auto_scroll=True)
        yield self._hint("Esc: back    r: reload")

    BINDINGS: ClassVar[list[BindingType]] = [*_Base.BINDINGS, Binding("r", "reload", "Reload")]

    def on_mount(self) -> None:
        self.action_reload()

    def action_reload(self) -> None:
        self._load()

    @work(thread=True, exclusive=True, group="journal")
    def _load(self) -> None:
        text = self.mt_app.journal_tail(self.unit)
        self.app.call_from_thread(self._show, text)

    def _show(self, text: str) -> None:
        log = self.query_one("#journal", Log)
        log.clear()
        log.write_lines(text.splitlines() or ["(empty)"])


class NetworkScreen(_Base):
    title_text = "Network"

    def compose(self) -> ComposeResult:
        yield self._title()
        table: DataTable[str] = DataTable(id="ifaces", cursor_type="row", zebra_stripes=True)
        yield table
        yield self._hint()

    def on_mount(self) -> None:
        self.query_one("#ifaces", DataTable).add_columns(
            "Interface", "State", "MAC", "MTU", "Address", "Scope"
        )
        if self.mt_app.snapshot is not None:
            self._show_snapshot(self.mt_app.snapshot)

    def on_snapshot_updated(self, message: SnapshotUpdated) -> None:
        self._show_snapshot(message.snapshot)

    def _show_snapshot(self, snap: Snapshot) -> None:
        table = self.query_one("#ifaces", DataTable)
        table.clear()
        for iface in snap.network.interfaces:
            if not iface.addresses:
                table.add_row(
                    iface.name, iface.state, iface.mac or "", str(iface.mtu or ""), "", ""
                )
            for a in iface.addresses:
                table.add_row(
                    iface.name,
                    iface.state,
                    iface.mac or "",
                    str(iface.mtu or ""),
                    f"{a.address}/{a.prefix}",
                    a.scope,
                )


class SystemScreen(_Base):
    title_text = "System"

    def compose(self) -> ComposeResult:
        yield self._title()
        yield Static("loading", classes="pre", id="sysinfo", markup=False)
        yield self._hint()

    def on_mount(self) -> None:
        if self.mt_app.snapshot is not None:
            self._show_snapshot(self.mt_app.snapshot)

    def on_snapshot_updated(self, message: SnapshotUpdated) -> None:
        self._show_snapshot(message.snapshot)

    def _show_snapshot(self, snap: Snapshot) -> None:
        self.query_one("#sysinfo", Static).update("\n".join(system_lines(snap.system)))


class MaintenanceScreen(_Base):
    title_text = "Maintenance"

    def compose(self) -> ComposeResult:
        yield self._title()
        yield Static("", classes="status-line", id="maint-status")
        yield OptionList(
            Option("Reboot this unit", id="reboot"),
            Option(
                "Maintenance mode ON: make the root filesystem writable (reboots)", id="maint-on"
            ),
            Option(
                "Maintenance mode OFF: re-enable the read-only overlay (reboots)", id="maint-off"
            ),
            Option("Back", id="back"),
            id="maint-menu",
        )
        yield self._hint("Enter: select    Esc: back    every action asks for confirmation")

    def on_mount(self) -> None:
        self.query_one("#maint-menu", OptionList).focus()
        if self.mt_app.snapshot is not None:
            self._show_snapshot(self.mt_app.snapshot)

    def on_snapshot_updated(self, message: SnapshotUpdated) -> None:
        self._show_snapshot(message.snapshot)

    def _show_snapshot(self, snap: Snapshot) -> None:
        state = (
            "read-only overlay (normal for malware-net)"
            if snap.system.overlay_root
            else "read-write"
        )
        self.query_one("#maint-status", Static).update(f"Root filesystem is currently: {state}")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        choice = event.option.id
        if choice == "back":
            self.action_back()
        elif choice == "reboot":
            self.app.push_screen(
                ConfirmScreen(
                    "Reboot now? Time service stops until the unit is back and GPS has re-locked."
                ),
                callback=self._reboot,
            )
        elif choice in ("maint-on", "maint-off"):
            enable = choice == "maint-on"
            what = (
                "disable the overlay and reboot" if enable else "re-enable the overlay and reboot"
            )
            self.app.push_screen(
                ConfirmScreen(f"Maintenance mode {'ON' if enable else 'OFF'}: {what}. Continue?"),
                callback=lambda yes: self._maint(enable, yes),
            )

    def _reboot(self, yes: bool | None) -> None:
        if yes:
            ok, message = self.mt_app.reboot_host()
            self.app.notify(message, severity="information" if ok else "error", timeout=8)

    def _maint(self, enable: bool, yes: bool | None) -> None:
        if yes:
            ok, message = self.mt_app.maintenance_mode(enable)
            self.app.notify(message, severity="information" if ok else "error", timeout=8)


class AboutScreen(_Base):
    title_text = "About"

    def compose(self) -> ComposeResult:
        yield self._title()
        yield Static(art.TITLE, id="about-title", markup=False)
        yield Static("Go sync with that NTP Mother Ticker.", id="about-tagline", markup=False)
        with Horizontal(id="about-body"):
            yield Static(art.LOGO, id="about-logo", markup=False)
            yield Static(self._text(), id="about-text", markup=False)
        yield self._hint()

    def _text(self) -> str:
        cfg = self.mt_app.config
        return "\n".join(
            [
                f"version {__version__}    site {cfg.site}",
                "",
                "GPS-disciplined stratum-1 NTP appliance",
                "chrony + gpsd + kernel PPS + RV-3028 RTC",
                "",
                "by ChiefGyk3D",
                "Renegade Penguin LLC",
                "AGPL-3.0-or-later",
                "",
                "github.com/ChiefGyk3D/mother-ticker",
                "support.chiefgyk3d.com",
            ]
        )


class ConfirmScreen(ModalScreen[bool]):
    """Yes/No modal. Esc or n answers no; y answers yes."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "answer_no", "No"),
        Binding("n", "answer_no", "No", show=False),
        Binding("y", "answer_yes", "Yes", show=False),
    ]

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self.question)
            with Horizontal(id="confirm-buttons"):
                yield Button("No (Esc)", id="no", variant="primary")
                yield Button("Yes (y)", id="yes", variant="error")

    def on_mount(self) -> None:
        self.query_one("#no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_answer_no(self) -> None:
        self.dismiss(False)

    def action_answer_yes(self) -> None:
        self.dismiss(True)
