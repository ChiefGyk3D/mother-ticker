#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Generate grafana/dashboards/mother-ticker.json.

The dashboard is data about the metrics the exporter serves, so it is
generated from a list here rather than hand-edited in Grafana and pasted
back: every panel names its metric once, the test suite checks each name
against what the exporter actually emits, and `--check` fails when the
committed file and this script disagree.

    python3 scripts/gen_grafana_dashboard.py            # write the file
    python3 scripts/gen_grafana_dashboard.py --check    # exit 1 if stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "grafana" / "dashboards" / "mother-ticker.json"

UID = "mother-ticker"
TITLE = "Mother Ticker"
DATASOURCE = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}
SELECTOR = '{site=~"$site", instance=~"$instance"}'

GREEN, AMBER, RED, GREY = "green", "orange", "red", "text"


def _thresholds(*steps: tuple[float | None, str]) -> dict[str, Any]:
    return {
        "mode": "absolute",
        "steps": [{"color": color, "value": value} for value, color in steps],
    }


def _mappings(values: dict[str, tuple[str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "value",
            "options": {
                k: {"text": text, "color": color, "index": i}
                for i, (k, (text, color)) in enumerate(values.items())
            },
        }
    ]


def _target(expr: str, legend: str = "", ref: str = "A") -> dict[str, Any]:
    return {"datasource": DATASOURCE, "expr": expr, "legendFormat": legend, "refId": ref}


def _panel(kind: str, title: str, targets: list[dict[str, Any]], **field: Any) -> dict[str, Any]:
    options = field.pop("options", {})
    description = field.pop("description", "")
    return {
        "type": kind,
        "title": title,
        "description": description,
        "datasource": DATASOURCE,
        "targets": targets,
        "fieldConfig": {"defaults": field, "overrides": []},
        "options": options,
        "gridPos": {},
    }


def _stat(
    title: str,
    metric: str,
    *,
    description: str = "",
    unit: str = "",
    thresholds: dict[str, Any] | None = None,
    mappings: list[dict[str, Any]] | None = None,
    decimals: int | None = None,
) -> dict[str, Any]:
    field: dict[str, Any] = {
        "unit": unit,
        "thresholds": thresholds or _thresholds((None, GREEN)),
        "mappings": mappings or [],
        "color": {"mode": "thresholds"},
    }
    if decimals is not None:
        field["decimals"] = decimals
    return _panel(
        "stat",
        title,
        [_target(f"{metric}{SELECTOR}", "{{instance}}")],
        description=description,
        options={
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": "background",
            "graphMode": "none",
            "textMode": "value",
            "orientation": "auto",
        },
        **field,
    )


def _series(
    title: str,
    targets: list[tuple[str, str]],
    *,
    description: str = "",
    unit: str = "",
    decimals: int | None = None,
    log_base: int = 1,
) -> dict[str, Any]:
    field: dict[str, Any] = {
        "unit": unit,
        "color": {"mode": "palette-classic"},
        "custom": {
            "drawStyle": "line",
            "lineWidth": 1,
            "fillOpacity": 8,
            "showPoints": "never",
            "spanNulls": False,
            "scaleDistribution": {"type": "log", "log": log_base}
            if log_base > 1
            else {"type": "linear"},
        },
    }
    if decimals is not None:
        field["decimals"] = decimals
    return _panel(
        "timeseries",
        title,
        [_target(expr, legend, ref=chr(ord("A") + i)) for i, (expr, legend) in enumerate(targets)],
        description=description,
        options={
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "none"},
        },
        **field,
    )


def panels() -> list[dict[str, Any]]:
    """The dashboard, top to bottom. Every expression names a `mother_ticker_` metric."""
    health = _thresholds((None, GREEN), (1, AMBER), (2, RED))
    return [
        _stat(
            "Health",
            "mother_ticker_health_level",
            description="The same evaluation that drives the banner on the unit.",
            thresholds=health,
            mappings=_mappings(
                {"0": ("OK", GREEN), "1": ("WARNING", AMBER), "2": ("CRITICAL", RED)}
            ),
        ),
        _stat(
            "Stratum",
            "mother_ticker_chrony_stratum",
            description="1 means PPS is the selected source. 0 means chrony is not synchronised.",
            thresholds=_thresholds((None, RED), (1, GREEN), (2, AMBER)),
        ),
        _stat(
            "GNSS fix",
            "mother_ticker_gnss_fix_mode",
            thresholds=_thresholds((None, RED), (2, AMBER), (3, GREEN)),
            mappings=_mappings(
                {
                    "0": ("no data", RED),
                    "1": ("no fix", RED),
                    "2": ("2D", AMBER),
                    "3": ("3D", GREEN),
                }
            ),
        ),
        _stat(
            "Satellites used",
            "mother_ticker_gnss_satellites_used",
            thresholds=_thresholds((None, RED), (4, AMBER), (6, GREEN)),
        ),
        _stat(
            "PPS",
            "mother_ticker_pps_pulsing",
            description="1 when the kernel saw a PPS edge within the last few seconds.",
            thresholds=_thresholds((None, RED), (1, GREEN)),
            mappings=_mappings({"0": ("silent", RED), "1": ("pulsing", GREEN)}),
        ),
        _stat(
            "Synchronised",
            "mother_ticker_chrony_synchronised",
            thresholds=_thresholds((None, RED), (1, GREEN)),
            mappings=_mappings({"0": ("no", RED), "1": ("yes", GREEN)}),
        ),
        _series(
            "System offset from reference",
            [(f"mother_ticker_chrony_system_offset_seconds{SELECTOR}", "{{instance}}")],
            description=(
                "Positive when the system clock is fast. Tens of nanoseconds on PPS; "
                "milliseconds means NMEA or an upstream server is being used."
            ),
            unit="s",
        ),
        _series(
            "Per-source offset",
            [
                (
                    f"mother_ticker_chrony_source_offset_seconds{SELECTOR}",
                    "{{instance}} {{source}} ({{state}})",
                )
            ],
            description=(
                "Every source chrony measures, including the noselect NMEA source. "
                "The NMEA offset is the number RUNBOOK.md asks you to measure."
            ),
            unit="s",
        ),
        _series(
            "Frequency and skew",
            [
                (f"mother_ticker_chrony_frequency_ppm{SELECTOR}", "{{instance}} frequency"),
                (f"mother_ticker_chrony_skew_ppm{SELECTOR}", "{{instance}} skew"),
            ],
            unit="ppm",
        ),
        _series(
            "Satellites",
            [
                (f"mother_ticker_gnss_satellites_used{SELECTOR}", "{{instance}} used"),
                (f"mother_ticker_gnss_satellites_seen{SELECTOR}", "{{instance}} seen"),
            ],
            unit="short",
            decimals=0,
        ),
        _series(
            "PPS edge age",
            [(f"mother_ticker_pps_last_edge_age_seconds{SELECTOR}", "{{instance}}")],
            description=(
                "Seconds since the last PPS assert at scrape time. Flat near zero is "
                "healthy; a ramp is a receiver that stopped pulsing."
            ),
            unit="s",
        ),
        _series(
            "SoC temperature",
            [(f"mother_ticker_soc_temperature_celsius{SELECTOR}", "{{instance}}")],
            unit="celsius",
        ),
        _series(
            "Root dispersion and update interval",
            [
                (
                    f"mother_ticker_chrony_root_dispersion_seconds{SELECTOR}",
                    "{{instance}} dispersion",
                ),
                (
                    f"mother_ticker_chrony_update_interval_seconds{SELECTOR}",
                    "{{instance}} interval",
                ),
            ],
            unit="s",
            log_base=10,
        ),
        _stat(
            "Updates pending",
            "mother_ticker_updates_pending",
            description="What a dist-upgrade would install. Nothing installs on its own.",
            thresholds=_thresholds((None, GREEN), (1, GREY)),
            decimals=0,
        ),
        _stat(
            "Security updates",
            "mother_ticker_security_updates_pending",
            thresholds=_thresholds((None, GREEN), (1, AMBER)),
            decimals=0,
        ),
        _stat(
            "Reboot required",
            "mother_ticker_reboot_required",
            thresholds=_thresholds((None, GREEN), (1, AMBER)),
            mappings=_mappings({"0": ("no", GREEN), "1": ("yes", AMBER)}),
        ),
        _stat(
            "Update check age",
            "time() - mother_ticker_updates_checked_timestamp_seconds",
            description="Time since the daily check last ran.",
            unit="s",
            thresholds=_thresholds((None, GREEN), (2 * 86400, AMBER), (3 * 86400, RED)),
        ),
        _series(
            "Service restarts",
            [(f"mother_ticker_service_restarts{SELECTOR}", "{{instance}} {{unit}}")],
            description="systemd NRestarts. A climbing line is the health ladder or a crash loop.",
            unit="short",
            decimals=0,
        ),
        _series(
            "Memory and disk",
            [
                (f"mother_ticker_memory_used_percent{SELECTOR}", "{{instance}} memory"),
                (f"mother_ticker_disk_used_percent{SELECTOR}", "{{instance}} disk"),
            ],
            unit="percent",
        ),
        _series(
            "Throttled flags",
            [(f"mother_ticker_throttled_flags{SELECTOR}", "{{instance}}")],
            description=(
                "Raw vcgencmd get_throttled bits. Anything but 0 means under-voltage "
                "or thermal throttling has happened."
            ),
            unit="short",
            decimals=0,
        ),
    ]


# Layout: (panel index, width, height). 24 columns per row.
LAYOUT: tuple[tuple[int, int], ...] = (
    (4, 4),
    (4, 4),
    (4, 4),
    (4, 4),
    (4, 4),
    (4, 4),
    (12, 8),
    (12, 8),
    (8, 7),
    (8, 7),
    (8, 7),
    (8, 7),
    (8, 7),
    (2, 7),
    (2, 7),
    (2, 7),
    (2, 7),
    (8, 7),
    (8, 7),
    (8, 7),
)


def _place(items: list[dict[str, Any]]) -> None:
    if len(items) != len(LAYOUT):
        raise ValueError(f"{len(items)} panels, {len(LAYOUT)} layout entries")
    x = y = row_h = 0
    for panel, (w, h) in zip(items, LAYOUT, strict=True):
        if x + w > 24:
            x, y, row_h = 0, y + row_h, 0
        panel["gridPos"] = {"x": x, "y": y, "w": w, "h": h}
        x += w
        row_h = max(row_h, h)
    for i, panel in enumerate(items, start=1):
        panel["id"] = i


def dashboard() -> dict[str, Any]:
    items = panels()
    _place(items)
    return {
        "__inputs": [
            {
                "name": "DS_PROMETHEUS",
                "label": "Prometheus",
                "description": "",
                "type": "datasource",
                "pluginId": "prometheus",
                "pluginName": "Prometheus",
            }
        ],
        "uid": UID,
        "title": TITLE,
        "description": (
            "GPS-disciplined stratum-1 NTP appliance. Generated by "
            "scripts/gen_grafana_dashboard.py; do not edit in Grafana and paste back."
        ),
        "tags": ["mother-ticker", "ntp", "chrony"],
        "timezone": "utc",
        "editable": True,
        "graphTooltip": 1,
        "refresh": "30s",
        "time": {"from": "now-6h", "to": "now"},
        "schemaVersion": 39,
        "version": 1,
        "templating": {
            "list": [
                {
                    "name": "DS_PROMETHEUS",
                    "label": "Prometheus",
                    "type": "datasource",
                    "query": "prometheus",
                    "current": {},
                    "hide": 0,
                    "refresh": 1,
                },
                {
                    "name": "site",
                    "label": "Site",
                    "type": "query",
                    "datasource": DATASOURCE,
                    "query": "label_values(mother_ticker_info, site)",
                    "definition": "label_values(mother_ticker_info, site)",
                    "includeAll": True,
                    "multi": True,
                    "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
                    "refresh": 2,
                    "sort": 1,
                },
                {
                    "name": "instance",
                    "label": "Unit",
                    "type": "query",
                    "datasource": DATASOURCE,
                    "query": 'label_values(mother_ticker_info{site=~"$site"}, instance)',
                    "definition": 'label_values(mother_ticker_info{site=~"$site"}, instance)',
                    "includeAll": True,
                    "multi": True,
                    "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
                    "refresh": 2,
                    "sort": 1,
                },
            ]
        },
        "annotations": {
            "list": [
                {
                    "name": "Health changes",
                    "datasource": DATASOURCE,
                    "enable": True,
                    "iconColor": "red",
                    "expr": "changes(mother_ticker_health_level[1m]) > 0",
                    "titleFormat": "{{instance}} health changed",
                    "step": "1m",
                }
            ]
        },
        "panels": items,
    }


def render() -> str:
    return json.dumps(dashboard(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the file is stale")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current != text:
            print(f"{args.output} is stale; run {Path(sys.argv[0]).name}", file=sys.stderr)
            return 1
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
