# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""CLI surface: version, help, config loading, and the one-shot commands with collectors stubbed."""

from __future__ import annotations

from pathlib import Path

import pytest

import mother_ticker.collectors.gather as gather_mod
from mother_ticker import cli
from mother_ticker.config import ConfigError, config_from_dict, load_config
from mother_ticker.version import __status__, __version__
from tests.conftest import make_snapshot


class TestVersion:
    def test_version_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc:
            cli.main(["--version"])
        assert exc.value.code == 0
        expected = f"mother-ticker {__version__}" + (f" ({__status__})" if __status__ else "")
        assert capsys.readouterr().out.strip() == expected

    def test_semver_shape(self) -> None:
        major, minor, patch = __version__.split(".")
        assert all(part.isdigit() for part in (major, minor, patch))


class TestConfig:
    def test_defaults_when_file_missing(self, tmp_path: Path) -> None:
        cfg = load_config(tmp_path / "missing.toml")
        assert cfg.site == "main-lan"
        assert cfg.metrics.mode == "none"

    def test_site_and_syslog(self, tmp_path: Path) -> None:
        p = tmp_path / "c.toml"
        p.write_text(
            'site = "malware-net"\n'
            '[metrics]\nmode = "syslog"\n'
            '[metrics.syslog]\nhost = "198.51.100.5"\nport = 6514\ntransport = "udp"\n'
            'framing = "octet-counted"\n'
            "[health]\nreboot_enabled = false\n"
        )
        cfg = load_config(p)
        assert cfg.site == "malware-net"
        assert cfg.metrics.syslog.host == "198.51.100.5"
        assert cfg.metrics.syslog.transport == "udp"
        assert cfg.metrics.syslog.framing == "octet-counted"
        assert cfg.health.reboot_enabled is False

    def test_bad_site_rejected(self) -> None:
        with pytest.raises(ConfigError, match="site must be one of"):
            config_from_dict({"site": "moon-base"})

    def test_bad_transport_rejected(self) -> None:
        with pytest.raises(ConfigError, match="transport"):
            config_from_dict({"metrics": {"syslog": {"transport": "carrier-pigeon"}}})

    def test_bad_toml_reports_path(self, tmp_path: Path) -> None:
        p = tmp_path / "c.toml"
        p.write_text("site = \n")
        with pytest.raises(ConfigError, match=str(p)):
            load_config(p)

    def test_env_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        p = tmp_path / "c.toml"
        p.write_text('site = "malware-net"\n')
        monkeypatch.setenv(
            cli.ENV_CONFIG_PATH if hasattr(cli, "ENV_CONFIG_PATH") else "MOTHER_TICKER_CONFIG",
            str(p),
        )
        assert load_config().site == "malware-net"


class TestOneShotCommands:
    def test_status_and_metrics(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        monkeypatch.setattr(gather_mod, "gather", lambda *_a, **_k: make_snapshot())
        assert cli.main(["-c", str(tmp_path / "none.toml"), "status"]) == 0
        out = capsys.readouterr().out
        assert "health : OK" in out
        assert "stratum 1" in out
        assert cli.main(["-c", str(tmp_path / "none.toml"), "metrics"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("# HELP mother_ticker_")

    def test_healthcheck_dry_run(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(gather_mod, "gather", lambda *_a, **_k: make_snapshot())
        assert cli.main(["-c", str(tmp_path / "none.toml"), "healthcheck", "--dry-run"]) == 0

    def test_exporter_mode_none_exits(self, tmp_path: Path) -> None:
        assert cli.main(["-c", str(tmp_path / "none.toml"), "exporter"]) == 0
