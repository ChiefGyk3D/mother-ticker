# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""`mother-ticker` command line.

mother-ticker tui            the dashboard and menu (default when no subcommand)
mother-ticker status         one-shot text status, useful over a bad link
mother-ticker exporter       run the metrics writer chosen by the site config
mother-ticker healthcheck    one tick of the escalation ladder (systemd timer)
mother-ticker metrics        print the Prometheus rendering once and exit
mother-ticker --version
"""

from __future__ import annotations

import argparse
import logging
import signal
import socket
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from mother_ticker.config import Config, ConfigError, load_config
from mother_ticker.version import __status__, __version__

if TYPE_CHECKING:
    from mother_ticker.tui.app import MotherTickerApp

EXIT_SHELL = 10  # the login wrapper execs a shell when the TUI exits with this


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _config(args: argparse.Namespace) -> Config:
    try:
        return load_config(Path(args.config) if args.config else None)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def cmd_tui(args: argparse.Namespace) -> int:
    config = _config(args)
    if args.demo:
        from mother_ticker.tui.demo import DemoApp

        app: MotherTickerApp = DemoApp(config, args.demo)
    else:
        from mother_ticker.tui.app import MotherTickerApp

        app = MotherTickerApp(config)
    app.run()
    return EXIT_SHELL if app.exit_to_shell else 0


def cmd_status(args: argparse.Namespace) -> int:
    from mother_ticker.collectors.gather import gather
    from mother_ticker.health.evaluate import evaluate

    config = _config(args)
    snap = gather(config)
    report = evaluate(snap, config.health.thresholds)
    t = snap.chrony.tracking
    lines = [
        f"Mother Ticker {__version__}{' ' + __status__ if __status__ else ''}"
        f"  site={snap.site}  host={snap.system.hostname}",
        f"health : {report.level.name}  {report.headline}",
        f"gnss   : {snap.gnss.fix_label}  used {snap.gnss.satellites_used}"
        f"/{snap.gnss.satellites_seen}",
        f"pps    : {'pulsing' if snap.pps.pulsing else 'NOT pulsing'}"
        + (f"  age {snap.pps.age_s:.1f}s" if snap.pps.age_s is not None else ""),
        f"chrony : stratum {t.stratum}  ref {t.ref_name or '-'}"
        f"  offset {t.system_time_offset_s * 1e6:+.1f} us  leap {t.leap_status or '-'}",
        f"system : up {snap.system.uptime_s / 3600:.1f} h  "
        + (f"{snap.system.temperature_c:.0f} C  " if snap.system.temperature_c is not None else "")
        + f"mem {snap.system.mem_used_pct:.0f}%  disk {snap.system.disk_used_pct:.0f}%",
        "net    : " + (", ".join(snap.network.primary_addresses()) or "no global addresses"),
    ]
    print("\n".join(lines))
    return int(report.level)


def cmd_metrics(args: argparse.Namespace) -> int:
    from mother_ticker.collectors.gather import gather
    from mother_ticker.health.evaluate import evaluate
    from mother_ticker.metrics.model import snapshot_to_metrics
    from mother_ticker.metrics.prometheus import render

    config = _config(args)
    snap = gather(config)
    print(render(snapshot_to_metrics(snap, evaluate(snap, config.health.thresholds))), end="")
    return 0


def cmd_exporter(args: argparse.Namespace) -> int:
    from mother_ticker.collectors.gather import gather
    from mother_ticker.health.evaluate import evaluate
    from mother_ticker.metrics import prometheus, syslog
    from mother_ticker.metrics.model import Metric, snapshot_to_metrics

    config = _config(args)
    log = logging.getLogger("exporter")
    stop = threading.Event()

    def _stop(*_: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    if config.metrics.mode == "none":
        log.info("metrics.mode is none; nothing to export for site %s", config.site)
        return 0

    if config.metrics.mode == "prometheus":

        def refresh() -> list[Metric]:
            snap = gather(config)
            return snapshot_to_metrics(snap, evaluate(snap, config.health.thresholds))

        prometheus.serve_forever(
            config.metrics.prometheus.bind,
            config.metrics.prometheus.port,
            prometheus.MetricsCache(),
            refresh=refresh,
            interval_s=config.metrics.prometheus.refresh_s,
            stop=stop,
        )
        return 0

    sc = config.metrics.syslog
    sender = syslog.SyslogSender(sc.host, sc.port, sc.transport, sc.framing)
    hostname = config.hostname_override or socket.gethostname()

    def build() -> str:
        snap = gather(config)
        report = evaluate(snap, config.health.thresholds)
        metrics = snapshot_to_metrics(snap, report)
        metrics.append(
            Metric(
                "mother_ticker_relay_send_failures",
                "counter",
                "syslog sends that failed",
                float(sender.failed),
            )
        )
        return syslog.build_message(
            metrics,
            report.level,
            site=config.site,
            version=__version__,
            hostname=hostname,
            facility=sc.facility,
            app_name=sc.app_name,
        )

    syslog.run_forever(sender, build, sc.interval_s, stop)
    return 0


def cmd_healthcheck(args: argparse.Namespace) -> int:
    from mother_ticker.collectors.gather import gather
    from mother_ticker.health.check import run_check

    config = _config(args)
    return run_check(gather(config), config.health, dry_run=args.dry_run)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mother-ticker",
        description=(
            "GPS-disciplined stratum-1 NTP appliance: status TUI, metrics exporter, health check."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"mother-ticker {__version__}" + (f" ({__status__})" if __status__ else ""),
    )
    parser.add_argument(
        "-c", "--config", help="config file (default /etc/mother-ticker/config.toml)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging to stderr")
    sub = parser.add_subparsers(dest="command")
    tui = sub.add_parser("tui", help="dashboard and menu (default)")
    tui.add_argument(
        "--demo",
        nargs="?",
        const="nominal",
        choices=["nominal", "warning", "critical"],
        help="run with fabricated data (no gpsd, chronyd or hardware); optional state",
    )
    tui.set_defaults(func=cmd_tui)
    sub.add_parser(
        "status", help="one-shot text status; exit code is the health level"
    ).set_defaults(func=cmd_status)
    sub.add_parser("metrics", help="print Prometheus metrics once").set_defaults(func=cmd_metrics)
    sub.add_parser("exporter", help="run the metrics writer for this site").set_defaults(
        func=cmd_exporter
    )
    hc = sub.add_parser("healthcheck", help="one tick of the escalation ladder")
    hc.add_argument("--dry-run", action="store_true", help="decide but do not restart or reboot")
    hc.set_defaults(func=cmd_healthcheck)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    func = getattr(args, "func", cmd_tui)
    if not hasattr(args, "dry_run"):
        args.dry_run = False
    if not hasattr(args, "demo"):
        args.demo = None
    result: int = func(args)
    return result


def run() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    run()
