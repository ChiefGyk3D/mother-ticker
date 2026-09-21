# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Runtime configuration for the TUI, exporter and health check.

One TOML file, rendered by the Ansible role from the site variables, read by
every entry point. The site flag decides how metrics leave the box; nothing
else in the Python code branches on it, so both units run one codebase.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

DEFAULT_CONFIG_PATH = Path("/etc/mother-ticker/config.toml")
ENV_CONFIG_PATH = "MOTHER_TICKER_CONFIG"

Site = Literal["main-lan", "malware-net"]
MetricsMode = Literal["prometheus", "syslog", "none"]
Transport = Literal["tcp", "udp"]
Framing = Literal["newline", "octet-counted"]


class ConfigError(ValueError):
    """Raised when the configuration file is missing a required value or has a bad one."""


@dataclass(frozen=True)
class GpsdConfig:
    host: str = "127.0.0.1"
    port: int = 2947
    timeout_s: float = 5.0


@dataclass(frozen=True)
class PpsConfig:
    device: str = "pps0"
    stale_after_s: float = 3.0


@dataclass(frozen=True)
class PrometheusConfig:
    bind: str = "0.0.0.0"  # noqa: S104  # nosec B104  nftables restricts the port to the scraper
    port: int = 9101
    refresh_s: float = 5.0


@dataclass(frozen=True)
class SyslogConfig:
    host: str = "127.0.0.1"
    port: int = 514
    transport: Transport = "tcp"
    framing: Framing = "newline"
    facility: int = 16  # local0
    app_name: str = "mother-ticker"
    interval_s: float = 15.0


@dataclass(frozen=True)
class MetricsConfig:
    mode: MetricsMode = "none"
    prometheus: PrometheusConfig = field(default_factory=PrometheusConfig)
    syslog: SyslogConfig = field(default_factory=SyslogConfig)


@dataclass(frozen=True)
class Thresholds:
    """Health thresholds. Offsets are seconds, temperatures are degrees Celsius."""

    offset_warn_s: float = 0.001
    offset_crit_s: float = 0.1
    temp_warn_c: float = 70.0
    temp_crit_c: float = 80.0
    disk_warn_pct: float = 85.0
    mem_warn_pct: float = 90.0
    min_satellites: int = 4


@dataclass(frozen=True)
class HealthConfig:
    """Escalation ladder for the health-check timer."""

    thresholds: Thresholds = field(default_factory=Thresholds)
    restart_after_failures: int = 4
    reboot_after_failures: int = 40
    min_uptime_before_reboot_s: int = 1800
    reboot_enabled: bool = True
    state_path: Path = Path("/run/mother-ticker/health.json")


@dataclass(frozen=True)
class TuiConfig:
    refresh_s: float = 1.0
    network_refresh_s: float = 30.0
    allow_shell: bool = True
    services: tuple[str, ...] = ("chrony", "gpsd")


@dataclass(frozen=True)
class Config:
    site: Site = "main-lan"
    hostname_override: str | None = None
    gpsd: GpsdConfig = field(default_factory=GpsdConfig)
    pps: PpsConfig = field(default_factory=PpsConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    health: HealthConfig = field(default_factory=HealthConfig)
    tui: TuiConfig = field(default_factory=TuiConfig)


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    return value


def _choice(value: Any, allowed: tuple[str, ...], where: str) -> str:
    if value not in allowed:
        raise ConfigError(f"{where} must be one of {', '.join(allowed)}; got {value!r}")
    return str(value)


def config_from_dict(data: dict[str, Any]) -> Config:
    """Build a Config from parsed TOML. Pure, so tests can feed dicts directly."""
    site = _choice(data.get("site", "main-lan"), ("main-lan", "malware-net"), "site")

    gpsd_d = _section(data, "gpsd")
    pps_d = _section(data, "pps")
    metrics_d = _section(data, "metrics")
    prom_d = _section(metrics_d, "prometheus")
    syslog_d = _section(metrics_d, "syslog")
    health_d = _section(data, "health")
    thr_d = _section(health_d, "thresholds")
    tui_d = _section(data, "tui")

    mode = _choice(metrics_d.get("mode", "none"), ("prometheus", "syslog", "none"), "metrics.mode")
    transport = _choice(
        syslog_d.get("transport", "tcp"), ("tcp", "udp"), "metrics.syslog.transport"
    )
    framing = _choice(
        syslog_d.get("framing", "newline"), ("newline", "octet-counted"), "metrics.syslog.framing"
    )

    thresholds = Thresholds(
        offset_warn_s=float(thr_d.get("offset_warn_s", Thresholds.offset_warn_s)),
        offset_crit_s=float(thr_d.get("offset_crit_s", Thresholds.offset_crit_s)),
        temp_warn_c=float(thr_d.get("temp_warn_c", Thresholds.temp_warn_c)),
        temp_crit_c=float(thr_d.get("temp_crit_c", Thresholds.temp_crit_c)),
        disk_warn_pct=float(thr_d.get("disk_warn_pct", Thresholds.disk_warn_pct)),
        mem_warn_pct=float(thr_d.get("mem_warn_pct", Thresholds.mem_warn_pct)),
        min_satellites=int(thr_d.get("min_satellites", Thresholds.min_satellites)),
    )

    return Config(
        site=site,  # type: ignore[arg-type]
        hostname_override=data.get("hostname"),
        gpsd=GpsdConfig(
            host=str(gpsd_d.get("host", GpsdConfig.host)),
            port=int(gpsd_d.get("port", GpsdConfig.port)),
            timeout_s=float(gpsd_d.get("timeout_s", GpsdConfig.timeout_s)),
        ),
        pps=PpsConfig(
            device=str(pps_d.get("device", PpsConfig.device)),
            stale_after_s=float(pps_d.get("stale_after_s", PpsConfig.stale_after_s)),
        ),
        metrics=MetricsConfig(
            mode=mode,  # type: ignore[arg-type]
            prometheus=PrometheusConfig(
                bind=str(prom_d.get("bind", PrometheusConfig.bind)),
                port=int(prom_d.get("port", PrometheusConfig.port)),
                refresh_s=float(prom_d.get("refresh_s", PrometheusConfig.refresh_s)),
            ),
            syslog=SyslogConfig(
                host=str(syslog_d.get("host", SyslogConfig.host)),
                port=int(syslog_d.get("port", SyslogConfig.port)),
                transport=transport,  # type: ignore[arg-type]
                framing=framing,  # type: ignore[arg-type]
                facility=int(syslog_d.get("facility", SyslogConfig.facility)),
                app_name=str(syslog_d.get("app_name", SyslogConfig.app_name)),
                interval_s=float(syslog_d.get("interval_s", SyslogConfig.interval_s)),
            ),
        ),
        health=HealthConfig(
            thresholds=thresholds,
            restart_after_failures=int(
                health_d.get("restart_after_failures", HealthConfig.restart_after_failures)
            ),
            reboot_after_failures=int(
                health_d.get("reboot_after_failures", HealthConfig.reboot_after_failures)
            ),
            min_uptime_before_reboot_s=int(
                health_d.get("min_uptime_before_reboot_s", HealthConfig.min_uptime_before_reboot_s)
            ),
            reboot_enabled=bool(health_d.get("reboot_enabled", HealthConfig.reboot_enabled)),
            state_path=Path(str(health_d.get("state_path", HealthConfig.state_path))),
        ),
        tui=TuiConfig(
            refresh_s=float(tui_d.get("refresh_s", TuiConfig.refresh_s)),
            network_refresh_s=float(tui_d.get("network_refresh_s", TuiConfig.network_refresh_s)),
            allow_shell=bool(tui_d.get("allow_shell", TuiConfig.allow_shell)),
            services=tuple(str(s) for s in tui_d.get("services", list(TuiConfig.services))),
        ),
    )


def load_config(path: Path | None = None) -> Config:
    """Read the config file. A missing file yields defaults so the TUI runs on a dev box."""
    chosen = path or Path(os.environ.get(ENV_CONFIG_PATH, str(DEFAULT_CONFIG_PATH)))
    if not chosen.exists():
        return Config()
    try:
        with chosen.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{chosen}: {exc}") from exc
    return config_from_dict(data)
