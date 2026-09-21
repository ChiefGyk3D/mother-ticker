# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Snapshot to a flat list of metrics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mother_ticker.collectors.model import Snapshot
from mother_ticker.health.evaluate import HealthReport

MetricType = Literal["gauge", "counter"]


@dataclass(frozen=True)
class Metric:
    name: str
    kind: MetricType
    help: str
    value: float
    labels: tuple[tuple[str, str], ...] = ()


def _b(value: bool) -> float:
    return 1.0 if value else 0.0


def snapshot_to_metrics(snapshot: Snapshot, health: HealthReport) -> list[Metric]:
    """Every metric carries the `mother_ticker_` prefix and a `site` label."""
    site = (("site", snapshot.site),)
    tracking = snapshot.chrony.tracking
    gnss = snapshot.gnss
    pps = snapshot.pps
    system = snapshot.system
    out: list[Metric] = [
        Metric(
            "mother_ticker_info", "gauge", "Build info", 1.0, (*site, ("version", snapshot.version))
        ),
        Metric(
            "mother_ticker_health_level",
            "gauge",
            "0 ok, 1 warning, 2 critical",
            float(health.level),
            site,
        ),
        Metric(
            "mother_ticker_gpsd_reachable",
            "gauge",
            "1 when gpsd answers on its socket",
            _b(gnss.reachable),
            site,
        ),
        Metric(
            "mother_ticker_gnss_fix_mode",
            "gauge",
            "gpsd TPV mode: 0 none, 1 no fix, 2 2D, 3 3D",
            float(gnss.fix_mode),
            site,
        ),
        Metric(
            "mother_ticker_gnss_satellites_used",
            "gauge",
            "Satellites in the solution",
            float(gnss.satellites_used),
            site,
        ),
        Metric(
            "mother_ticker_gnss_satellites_seen",
            "gauge",
            "Satellites tracked",
            float(gnss.satellites_seen),
            site,
        ),
        Metric(
            "mother_ticker_pps_present",
            "gauge",
            "1 when the kernel PPS device exists",
            _b(pps.present),
            site,
        ),
        Metric(
            "mother_ticker_pps_pulsing",
            "gauge",
            "1 when the last PPS edge is fresh",
            _b(pps.pulsing),
            site,
        ),
        Metric(
            "mother_ticker_chrony_reachable",
            "gauge",
            "1 when chronyc can talk to chronyd",
            _b(tracking.reachable),
            site,
        ),
        Metric(
            "mother_ticker_chrony_synchronised",
            "gauge",
            "1 when chrony reports a synchronised leap status",
            _b(tracking.synchronised),
            site,
        ),
        Metric(
            "mother_ticker_chrony_stratum",
            "gauge",
            "Stratum chrony is serving",
            float(tracking.stratum),
            site,
        ),
        Metric(
            "mother_ticker_chrony_system_offset_seconds",
            "gauge",
            "System clock offset from the reference, positive when fast",
            tracking.system_time_offset_s,
            site,
        ),
        Metric(
            "mother_ticker_chrony_rms_offset_seconds",
            "gauge",
            "Long-term RMS offset",
            tracking.rms_offset_s,
            site,
        ),
        Metric(
            "mother_ticker_chrony_frequency_ppm",
            "gauge",
            "Clock frequency error estimate",
            tracking.frequency_ppm,
            site,
        ),
        Metric(
            "mother_ticker_chrony_skew_ppm",
            "gauge",
            "Frequency estimate error bound",
            tracking.skew_ppm,
            site,
        ),
        Metric(
            "mother_ticker_chrony_root_dispersion_seconds",
            "gauge",
            "Root dispersion",
            tracking.root_dispersion_s,
            site,
        ),
        Metric(
            "mother_ticker_chrony_update_interval_seconds",
            "gauge",
            "Seconds between clock updates",
            tracking.update_interval_s,
            site,
        ),
        Metric("mother_ticker_uptime_seconds", "gauge", "Host uptime", system.uptime_s, site),
        Metric(
            "mother_ticker_memory_used_percent", "gauge", "Memory in use", system.mem_used_pct, site
        ),
        Metric(
            "mother_ticker_disk_used_percent",
            "gauge",
            "Root filesystem in use",
            system.disk_used_pct,
            site,
        ),
        Metric(
            "mother_ticker_overlay_root",
            "gauge",
            "1 when the root filesystem is the read-only overlay",
            _b(system.overlay_root),
            site,
        ),
    ]
    upd = snapshot.updates
    out.extend(
        [
            Metric(
                "mother_ticker_updates_pending",
                "gauge",
                "Packages a dist-upgrade would install",
                float(upd.pending),
                site,
            ),
            Metric(
                "mother_ticker_security_updates_pending",
                "gauge",
                "Of those, from a security suite",
                float(upd.security),
                site,
            ),
            Metric(
                "mother_ticker_reboot_required",
                "gauge",
                "1 when /run/reboot-required exists",
                _b(upd.reboot_required),
                site,
            ),
            Metric(
                "mother_ticker_updates_checked",
                "gauge",
                "1 when the daily update check has run since boot",
                _b(upd.checked),
                site,
            ),
        ]
    )
    if upd.checked_at is not None:
        out.append(
            Metric(
                "mother_ticker_updates_checked_timestamp_seconds",
                "gauge",
                "Unix time of the last update check",
                upd.checked_at,
                site,
            )
        )
    if pps.age_s is not None:
        out.append(
            Metric(
                "mother_ticker_pps_last_edge_age_seconds",
                "gauge",
                "Seconds since the last PPS edge",
                pps.age_s,
                site,
            )
        )
    if system.temperature_c is not None:
        out.append(
            Metric(
                "mother_ticker_soc_temperature_celsius",
                "gauge",
                "SoC temperature",
                system.temperature_c,
                site,
            )
        )
    if system.throttled_flags is not None:
        out.append(
            Metric(
                "mother_ticker_throttled_flags",
                "gauge",
                "Raw vcgencmd get_throttled bits",
                float(system.throttled_flags),
                site,
            )
        )
    for src in snapshot.chrony.sources:
        labels: tuple[tuple[str, str], ...] = (
            *site,
            ("source", src.name),
            ("mode", src.mode_label),
            ("state", src.state_label),
        )
        out.append(
            Metric(
                "mother_ticker_chrony_source_offset_seconds",
                "gauge",
                "Per-source measured offset",
                src.measured_offset_s,
                labels,
            )
        )
        out.append(
            Metric(
                "mother_ticker_chrony_source_reach",
                "gauge",
                "Per-source reach register, decimal",
                float(src.reach),
                labels,
            )
        )
    for svc in snapshot.services:
        labels = (*site, ("unit", svc.name))
        out.append(
            Metric(
                "mother_ticker_service_active",
                "gauge",
                "1 when the unit is active",
                _b(svc.active_state == "active"),
                labels,
            )
        )
        out.append(
            Metric(
                "mother_ticker_service_restarts",
                "counter",
                "systemd NRestarts for the unit",
                float(svc.restarts),
                labels,
            )
        )
    return out


def metrics_to_flat_dict(metrics: list[Metric]) -> dict[str, float | str]:
    """The JSON body for syslog: metrics keyed by name, labelled ones suffixed by label values."""
    out: dict[str, float | str] = {}
    for m in metrics:
        extra = [v for k, v in m.labels if k not in ("site",)]
        if m.name == "mother_ticker_info":
            out["version"] = dict(m.labels)["version"]
            continue
        key = m.name.removeprefix("mother_ticker_")
        if extra:
            key = key + "." + ".".join(extra)
        out[key] = m.value
    return out
