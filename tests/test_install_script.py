# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""The on-box installer's inventory generation, run without root and without Ansible."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "install.sh"
EXAMPLE = ROOT / "install.conf.example"


def run(*args: str, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *args, "--inventory-only", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )


def load(out: Path) -> dict[str, object]:
    data = yaml.safe_load((out / "hosts.yml").read_text(encoding="utf-8"))
    hosts = data["mother_ticker"]["hosts"]
    assert len(hosts) == 1
    host_vars: dict[str, object] = next(iter(hosts.values()))
    return host_vars


class TestExampleConfig:
    def test_example_renders_main_lan(self, tmp_path: Path) -> None:
        result = run("--config", str(EXAMPLE), out=tmp_path)
        assert result.returncode == 0, result.stderr
        hv = load(tmp_path)
        assert hv["ansible_connection"] == "local"
        assert hv["mother_ticker_site"] == "main-lan"
        assert hv["mother_ticker_mode"] == "appliance"
        assert hv["mother_ticker_admin_user"] == "admin"
        assert hv["mother_ticker_ntp_allow"] == ["192.0.2.0/24"]
        assert hv["mother_ticker_upstream_ntp"] == ["time.cloudflare.com", "time.nist.gov"]
        assert "mother_ticker_overlay" not in hv  # auto: the role decides from the site

    def test_flags_override_file(self, tmp_path: Path) -> None:
        result = run(
            "--config",
            str(EXAMPLE),
            "--site",
            "malware-net",
            "--ntp-allow",
            "198.51.100.0/24 198.51.100.128/25",
            "--upstream",
            "",
            "--relay-transport",
            "udp",
            "--orphan-stratum",
            "10",
            "--overlay",
            "yes",
            "--nts",
            "yes",
            out=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        hv = load(tmp_path)
        assert hv["mother_ticker_site"] == "malware-net"
        assert hv["mother_ticker_ntp_allow"] == ["198.51.100.0/24", "198.51.100.128/25"]
        assert hv["mother_ticker_upstream_ntp"] == []
        assert hv["mother_ticker_syslog_transport"] == "udp"
        assert hv["mother_ticker_orphan_stratum"] == 10
        assert hv["mother_ticker_overlay"] is True
        assert hv["mother_ticker_nts_enabled"] is True

    def test_no_hat_flag(self, tmp_path: Path) -> None:
        result = run("--config", str(EXAMPLE), "--hardware-present", "no", out=tmp_path)
        assert result.returncode == 0, result.stderr
        assert load(tmp_path)["mother_ticker_hardware_present"] is False

    def test_dev_mode_flag(self, tmp_path: Path) -> None:
        result = run("--config", str(EXAMPLE), "--mode", "dev", out=tmp_path)
        assert result.returncode == 0, result.stderr
        assert load(tmp_path)["mother_ticker_mode"] == "dev"

    def test_malware_net_without_explicit_upstream_gets_none(self, tmp_path: Path) -> None:
        """A minimal malware-net config must not inherit public servers from the default."""
        conf = tmp_path / "c.conf"
        conf.write_text("SITE=malware-net\nADMIN_USER=admin\nNTP_ALLOW=198.51.100.0/24\n")
        result = run("--config", str(conf), out=tmp_path / "inv")
        assert result.returncode == 0, result.stderr
        assert load(tmp_path / "inv")["mother_ticker_upstream_ntp"] == []


class TestValidation:
    @pytest.mark.parametrize(
        ("flag", "value", "message"),
        [
            ("--site", "moon-base", "SITE must be"),
            ("--overlay", "maybe", "OVERLAY must be"),
            ("--relay-transport", "pigeon", "RELAY_TRANSPORT must be"),
            ("--relay-framing", "smoke", "RELAY_FRAMING must be"),
        ],
    )
    def test_bad_values_fail_early(
        self, tmp_path: Path, flag: str, value: str, message: str
    ) -> None:
        result = run("--config", str(EXAMPLE), flag, value, out=tmp_path)
        assert result.returncode == 1
        assert message in result.stderr

    def test_admin_user_required(self, tmp_path: Path) -> None:
        conf = tmp_path / "c.conf"
        conf.write_text("SITE=main-lan\n")
        result = run("--config", str(conf), out=tmp_path / "inv")
        assert result.returncode == 1
        assert "ADMIN_USER is required" in result.stderr

    def test_config_lines_are_not_executed(self, tmp_path: Path) -> None:
        """The config is parsed, never sourced: a command in it must not run."""
        marker = tmp_path / "pwned"
        conf = tmp_path / "c.conf"
        conf.write_text(
            f"SITE=main-lan\nADMIN_USER=admin\nEVIL=$(touch {marker})\n`touch {marker}`\n"
        )
        result = run("--config", str(conf), out=tmp_path / "inv")
        assert result.returncode == 0, result.stderr
        assert not marker.exists()
        assert "ignoring unknown key EVIL" in result.stderr
