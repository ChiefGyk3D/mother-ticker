# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Every template the role writes, rendered for every shape of unit, without Ansible.

The role's defaults are the only source of values; a scenario overrides the
few a host_vars file would. Rendering uses StrictUndefined, so a template
that names a variable the defaults do not define fails here instead of on a
Pi. What comes out is then checked the way the consumer would read it:
config.toml through the application's own loader, install.conf through the
installer's own parser, the rest line by line for the properties the
SECURITY document promises.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jinja2
import pytest
import yaml

from mother_ticker.collectors import services
from mother_ticker.config import config_from_dict

ROOT = Path(__file__).resolve().parent.parent
ROLE = ROOT / "ansible" / "roles" / "mother_ticker"
TEMPLATES = ROLE / "templates"
INSTALLER = ROOT / "scripts" / "install.sh"


# The role sets these facts in tasks, not defaults; the harness supplies them.
TASK_FACTS: dict[str, Any] = {
    "inventory_hostname": "ntp-test",
    "mother_ticker_admin_user": "admin",
    "mother_ticker_ssh_penalties": True,
}


def _ansible_bool(value: Any) -> bool:
    """Ansible's `bool` filter: strings such as yes/no/on/off count."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    return str(value).strip().lower() in ("yes", "on", "1", "true", "y", "t")


def _search(value: Any, pattern: str) -> bool:
    return re.search(pattern, str(value)) is not None


def _environment() -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,  # noqa: S701  # nosec B701  these are config files, not HTML
    )
    env.filters["bool"] = _ansible_bool
    env.tests["search"] = _search
    return env


ENV = _environment()


def _finalize(value: Any) -> Any:
    """What Ansible's classic templar does with a rendered scalar: True/False become bool."""
    if value == "True":
        return True
    if value == "False":
        return False
    return value


def effective_vars(overrides: dict[str, Any]) -> dict[str, Any]:
    """Defaults, then overrides, then the templated defaults evaluated against the result.

    A default such as `"{{ mother_ticker_mode == 'appliance' }}"` is resolved lazily by
    Ansible, so an override of the variable it reads changes it. Evaluate in passes until
    nothing templated remains.
    """
    data: dict[str, Any] = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
    data.update(TASK_FACTS)
    data.update(overrides)

    def render_value(value: Any) -> Any:
        if isinstance(value, str) and "{{" in value:
            return _finalize(ENV.from_string(value).render(**data))
        if isinstance(value, list):
            return [render_value(v) for v in value]
        return value

    for _ in range(5):
        changed = False
        for key, value in list(data.items()):
            new = render_value(value)
            if new != value:
                data[key] = new
                changed = True
        if not changed:
            break
    leftover = [k for k, v in data.items() if isinstance(v, str) and "{{" in v]
    assert not leftover, f"defaults still templated after evaluation: {leftover}"
    return data


def render(name: str, variables: dict[str, Any]) -> str:
    return ENV.get_template(name).render(**variables)


@dataclass(frozen=True)
class Scenario:
    name: str
    overrides: dict[str, Any] = field(default_factory=dict)

    @property
    def vars(self) -> dict[str, Any]:
        return effective_vars(self.overrides)


SCENARIOS = [
    Scenario(
        "main-lan appliance",
        {
            "mother_ticker_site": "main-lan",
            "mother_ticker_upstream_ntp": ["time.cloudflare.com", "time.nist.gov"],
            "mother_ticker_metrics_allow": ["192.0.2.10/32"],
        },
    ),
    Scenario(
        "main-lan dev",
        {
            "mother_ticker_site": "main-lan",
            "mother_ticker_mode": "dev",
            "mother_ticker_upstream_ntp": ["time.cloudflare.com"],
        },
    ),
    Scenario(
        "malware-net appliance",
        {
            "mother_ticker_site": "malware-net",
            "mother_ticker_ntp_allow": ["198.51.100.0/24", "2001:db8:1::/64"],
            "mother_ticker_mgmt_allow": ["198.51.100.250/32"],
            "mother_ticker_orphan_stratum": 10,
            "mother_ticker_syslog_transport": "udp",
        },
    ),
    Scenario(
        "malware-net dev",
        {"mother_ticker_site": "malware-net", "mother_ticker_mode": "dev"},
    ),
    Scenario(
        "no hat",
        {"mother_ticker_site": "main-lan", "mother_ticker_hardware_present": False},
    ),
    Scenario(
        "nts",
        {
            "mother_ticker_site": "main-lan",
            "mother_ticker_nts_enabled": True,
            "mother_ticker_upstream_ntp": ["time.cloudflare.com"],
        },
    ),
]

TEMPLATE_NAMES = sorted(p.name for p in TEMPLATES.glob("*.j2"))
EM_DASH = chr(0x2014)


@pytest.fixture(params=SCENARIOS, ids=[s.name for s in SCENARIOS])
def scenario(request: pytest.FixtureRequest) -> Scenario:
    scen: Scenario = request.param
    return scen


class TestEveryTemplate:
    """Properties that hold for every template in every scenario."""

    @pytest.mark.parametrize("name", TEMPLATE_NAMES)
    def test_renders_with_strict_undefined(self, scenario: Scenario, name: str) -> None:
        text = render(name, scenario.vars)
        assert text.strip(), f"{name} rendered empty"
        assert EM_DASH not in text

    @pytest.mark.parametrize("name", TEMPLATE_NAMES)
    def test_every_template_is_written_by_a_task(self, name: str) -> None:
        """A template nothing installs is dead data; a task naming a missing one dies on the Pi."""
        tasks = "\n".join(p.read_text() for p in (ROLE / "tasks").glob("*.yml"))
        assert name in tasks or name.removesuffix(".j2") in tasks, f"no task writes {name}"


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]


class TestChrony:
    def test_pps_is_the_preferred_reference_and_nmea_only_numbers_it(
        self, scenario: Scenario
    ) -> None:
        v = scenario.vars
        lines = _lines(render("chrony.conf.j2", v))
        shm = [ln for ln in lines if ln.startswith("refclock SHM 0")]
        pps = [ln for ln in lines if ln.startswith("refclock PPS /dev/pps0")]
        assert len(shm) == 1 and "noselect" in shm[0] and "refid NMEA" in shm[0]
        assert len(pps) == 1 and "lock NMEA" in pps[0] and pps[0].endswith("prefer")
        assert f"offset {v['mother_ticker_nmea_offset']}" in shm[0]
        assert "rtcsync" in lines
        assert "makestep 1 3" in lines

    def test_command_port_stays_on_loopback(self, scenario: Scenario) -> None:
        lines = _lines(render("chrony.conf.j2", scenario.vars))
        assert "bindcmdaddress 127.0.0.1" in lines
        assert "bindcmdaddress ::1" in lines
        assert not any(ln.startswith("cmdport") for ln in lines), (
            "cmdport 0 would also block the unprivileged TUI"
        )
        assert not any(
            ln.startswith("cmdallow") and ln.split()[1] not in ("127.0.0.1", "::1") for ln in lines
        )

    def test_allow_lines_are_exactly_the_client_networks(self, scenario: Scenario) -> None:
        v = scenario.vars
        allows = [
            ln.split()[1] for ln in _lines(render("chrony.conf.j2", v)) if ln.startswith("allow ")
        ]
        assert allows == v["mother_ticker_ntp_allow"]

    def test_upstream_and_orphan_follow_the_site(self, scenario: Scenario) -> None:
        v = scenario.vars
        lines = _lines(render("chrony.conf.j2", v))
        servers = [ln.split()[1] for ln in lines if ln.startswith("server ")]
        assert servers == v["mother_ticker_upstream_ntp"]
        for ln in lines:
            if ln.startswith("server "):
                assert ln.endswith("iburst")
        local = [ln for ln in lines if ln.startswith("local ")]
        if v["mother_ticker_orphan_stratum"]:
            assert local == [
                f"local stratum {v['mother_ticker_orphan_stratum']} orphan distance 0.1"
            ]
        else:
            assert local == []

    def test_nts_lines_only_when_enabled(self, scenario: Scenario) -> None:
        v = scenario.vars
        text = render("chrony.conf.j2", v)
        present = "ntsservercert" in text
        assert present == bool(v["mother_ticker_nts_enabled"])
        if present:
            assert f"ntsport {v['mother_ticker_nts_port']}" in text


class TestNftables:
    def test_default_deny_in_and_forward(self, scenario: Scenario) -> None:
        text = render("nftables.conf.j2", scenario.vars)
        assert text.startswith("#!/usr/sbin/nft -f")
        assert "flush ruleset" in text
        assert "type filter hook input priority filter; policy drop;" in text
        assert "type filter hook forward priority filter; policy drop;" in text
        assert text.count("policy drop;") == 2
        assert text.rstrip().endswith("}")

    def test_only_named_networks_reach_a_port(self, scenario: Scenario) -> None:
        """Every accept that names a port names a source set; the sets are the role's lists."""
        v = scenario.vars
        accepts = [
            ln.strip() for ln in render("nftables.conf.j2", v).splitlines() if " dport " in ln
        ]
        assert accepts, "no service is reachable at all"
        for ln in accepts:
            assert ln.endswith("accept")
            m = re.match(r"(ip6?) saddr \{ (.+?) \} (udp|tcp) dport (\d+) accept", ln)
            assert m, f"accept without a source set: {ln}"
            family, sources, _proto, port = m.groups()
            listed = {
                "123": v["mother_ticker_ntp_allow"],
                str(v["mother_ticker_nts_port"]): v["mother_ticker_ntp_allow"],
                "22": v["mother_ticker_mgmt_allow"],
                str(v["mother_ticker_metrics_port"]): v["mother_ticker_metrics_allow"],
            }[port]
            for src in sources.split(", "):
                assert src in listed, f"{src} is not in the role's list for port {port}"
                assert (":" in src) == (family == "ip6")

    def test_metrics_port_open_only_for_prometheus_scrapers(self, scenario: Scenario) -> None:
        v = scenario.vars
        text = render("nftables.conf.j2", v)
        port = f"dport {v['mother_ticker_metrics_port']} accept"
        expect_open = v["mother_ticker_metrics_mode"] == "prometheus" and bool(
            v["mother_ticker_metrics_allow"]
        )
        assert (port in text) == expect_open

    def test_nts_port_follows_the_switch(self, scenario: Scenario) -> None:
        v = scenario.vars
        text = render("nftables.conf.j2", v)
        assert (f"tcp dport {v['mother_ticker_nts_port']} accept" in text) == bool(
            v["mother_ticker_nts_enabled"]
        )

    def test_drops_are_logged_and_rate_limited(self, scenario: Scenario) -> None:
        text = render("nftables.conf.j2", scenario.vars)
        assert 'limit rate 5/minute log prefix "mother-ticker drop: " counter' in text


class TestSshd:
    def test_keys_only_no_root_named_users(self, scenario: Scenario) -> None:
        v = scenario.vars
        lines = _lines(render("sshd.conf.j2", v))
        assert "PasswordAuthentication no" in lines
        assert "KbdInteractiveAuthentication no" in lines
        assert "PermitRootLogin no" in lines
        assert "PubkeyAuthentication yes" in lines
        allow = next(ln for ln in lines if ln.startswith("AllowUsers "))
        assert allow.split()[1:] == [v["mother_ticker_admin_user"], v["mother_ticker_user"]]
        assert "X11Forwarding no" in lines
        assert "AllowTcpForwarding no" in lines

    def test_penalties_follow_the_fact(self) -> None:
        with_ = render("sshd.conf.j2", effective_vars({"mother_ticker_ssh_penalties": True}))
        without = render("sshd.conf.j2", effective_vars({"mother_ticker_ssh_penalties": False}))
        assert "PerSourcePenalties" in with_
        assert "PerSourcePenalties" not in without


class TestSudoers:
    """The sudoers file and the collector must agree on the exact privileged commands."""

    def _allowed(self, text: str) -> set[str]:
        out: set[str] = set()
        for ln in _lines(text):
            _, _, cmds = ln.partition("NOPASSWD:")
            out.update(c.strip() for c in cmds.split(","))
        return out

    def test_every_action_the_tui_takes_is_listed(self, scenario: Scenario) -> None:
        v = scenario.vars
        allowed = self._allowed(render("sudoers.j2", v))
        restart = " ".join(services.ALLOWED_ACTIONS["restart"])
        for unit in ("chrony", "gpsd"):
            assert f"/usr/bin/{restart} {unit}" in allowed
        assert "/usr/bin/systemctl reboot" in allowed
        for action in ("on", "off", "status"):
            assert f"/usr/local/sbin/mother-ticker-maint {action}" in allowed

    def test_nothing_else_is_listed(self, scenario: Scenario) -> None:
        text = render("sudoers.j2", scenario.vars)
        for ln in _lines(text):
            assert ln.startswith(f"{scenario.vars['mother_ticker_user']} ALL=(root) NOPASSWD: ")
        allowed = self._allowed(text)
        assert len(allowed) == 8, sorted(allowed)
        for cmd in allowed:
            assert not cmd.endswith("*") and " ALL" not in cmd


class TestConfigToml:
    def test_loads_through_the_application(self, scenario: Scenario) -> None:
        v = scenario.vars
        data = tomllib.loads(render("config.toml.j2", v))
        cfg = config_from_dict(data)
        assert cfg.site == v["mother_ticker_site"]
        assert cfg.metrics.mode == v["mother_ticker_metrics_mode"]
        assert cfg.metrics.mode == ("prometheus" if cfg.site == "main-lan" else "syslog")
        assert cfg.metrics.prometheus.port == v["mother_ticker_metrics_port"]
        assert cfg.metrics.syslog.transport == v["mother_ticker_syslog_transport"]
        assert cfg.tui.admin_user == "admin"
        assert cfg.tui.allow_shell == (v["mother_ticker_mode"] == "dev")
        assert cfg.health.reboot_enabled == bool(v["mother_ticker_hardware_present"])
        assert cfg.alerts.webhook_url == ""
        assert cfg.gpsd.host == "127.0.0.1"

    def test_no_secret_lands_in_the_file(self) -> None:
        text = render(
            "config.toml.j2",
            effective_vars(
                {
                    "mother_ticker_alert_webhook_url": "https://ntfy.example/",
                    "mother_ticker_alert_token_file": "/etc/mother-ticker/webhook.token",
                }
            ),
        )
        assert 'token_file = "/etc/mother-ticker/webhook.token"' in text
        assert "token =" not in text and "Bearer" not in text


class TestInstallConf:
    """What the role records must come back out of the installer unchanged."""

    def test_round_trips_through_the_installer(self, scenario: Scenario, tmp_path: Path) -> None:
        v = scenario.vars
        conf = tmp_path / "install.conf"
        conf.write_text(render("install.conf.j2", v))
        result = subprocess.run(
            [str(INSTALLER), "--config", str(conf), "--inventory-only", str(tmp_path / "inv")],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "ignoring unknown key" not in result.stderr
        inv = yaml.safe_load((tmp_path / "inv" / "hosts.yml").read_text())
        hosts = inv["mother_ticker"]["hosts"]
        assert list(hosts) == [v["mother_ticker_hostname"]]
        hv = next(iter(hosts.values()))
        for key, value in hv.items():
            if key.startswith("ansible_"):
                continue
            assert key in v, f"installer emits {key}, which the role does not define"
            assert value == v[key], f"{key}: role had {v[key]!r}, installer produced {value!r}"
        for key in (
            "mother_ticker_site",
            "mother_ticker_mode",
            "mother_ticker_ntp_allow",
            "mother_ticker_upstream_ntp",
            "mother_ticker_hardware_present",
            "mother_ticker_overlay",
            "mother_ticker_nts_enabled",
        ):
            assert key in hv or v[key] == effective_vars({})[key], f"{key} was not carried"


class TestBootAndDevices:
    def test_config_txt_names_the_hat(self, scenario: Scenario) -> None:
        v = scenario.vars
        lines = _lines(render("config.txt.j2", v))
        assert "enable_uart=1" in lines
        assert "dtoverlay=disable-bt" in lines
        assert f"dtoverlay=pps-gpio,gpiopin={v['mother_ticker_pps_gpio']}" in lines
        assert "dtparam=i2c_arm=on" in lines
        assert f"dtoverlay={v['mother_ticker_rtc_overlay']}" in lines
        assert "dtparam=watchdog=on" in lines

    def test_gpsd_reads_the_uart_and_pps_from_boot(self, scenario: Scenario) -> None:
        lines = _lines(render("gpsd.default.j2", scenario.vars))
        assert 'DEVICES="/dev/ttyAMA0 /dev/pps0"' in lines
        assert 'GPSD_OPTIONS="-n"' in lines
        assert 'USBAUTO="false"' in lines

    def test_gnss_policy_prefers_gps_and_galileo(self, scenario: Scenario) -> None:
        lines = _lines(render("gnss.default.j2", scenario.vars))
        assert 'UBX_ENABLE="GPS GALILEO"' in lines
        assert 'UBX_DISABLE="GLONASS BEIDOU"' in lines
        assert 'UBX_STATIONARY="1"' in lines


def _unit(text: str) -> dict[str, list[str]]:
    """A systemd unit as {key: [values]}; keys repeat, so configparser will not do."""
    out: dict[str, list[str]] = {}
    for ln in _lines(text):
        if ln.startswith("["):
            continue
        key, _, value = ln.partition("=")
        out.setdefault(key, []).append(value)
    return out


class TestSystemdUnits:
    def test_long_running_services_are_unprivileged(self, scenario: Scenario) -> None:
        v = scenario.vars
        for name in ("mother-ticker-console.service.j2", "mother-ticker-exporter.service.j2"):
            unit = _unit(render(name, v))
            assert unit["User"] == [v["mother_ticker_user"]]
            assert unit["Restart"] == ["always"]
            assert (
                unit["Environment"].count("MOTHER_TICKER_CONFIG=/etc/mother-ticker/config.toml")
                == 1
            )

    def test_exporter_is_sandboxed(self, scenario: Scenario) -> None:
        unit = _unit(render("mother-ticker-exporter.service.j2", scenario.vars))
        assert unit["NoNewPrivileges"] == ["yes"]
        assert unit["ProtectSystem"] == ["strict"]
        assert unit["ProtectHome"] == ["yes"]
        assert unit["ExecStart"] == ["/opt/mother-ticker/venv/bin/mother-ticker exporter"]

    def test_console_runs_the_login_wrapper_on_tty1(self, scenario: Scenario) -> None:
        unit = _unit(render("mother-ticker-console.service.j2", scenario.vars))
        assert unit["ExecStart"] == ["/usr/local/bin/mother-ticker-login"]
        assert unit["TTYPath"] == ["/dev/tty1"]
        assert unit["Conflicts"] == ["getty@tty1.service"]
        assert unit["NoNewPrivileges"] == ["no"], "su - for the admin login needs setuid"

    def test_timers_and_oneshots(self, scenario: Scenario) -> None:
        v = scenario.vars
        hc = _unit(render("mother-ticker-healthcheck.service.j2", v))
        assert hc["Type"] == ["oneshot"]
        assert hc["SuccessExitStatus"] == ["1 2"], "health levels are results, not failures"
        assert hc["ExecStart"] == ["/opt/mother-ticker/venv/bin/mother-ticker healthcheck"]
        hct = _unit(render("mother-ticker-healthcheck.timer.j2", v))
        assert hct["OnUnitActiveSec"] == ["30s"]
        upd = _unit(render("mother-ticker-updates.service.j2", v))
        assert upd["ExecStart"] == ["/opt/mother-ticker/venv/bin/mother-ticker check-updates"]
        assert "User" not in upd, "apt-get -s needs root to lock nothing but read everything"
        updt = _unit(render("mother-ticker-updates.timer.j2", v))
        assert updt["OnCalendar"] == [v["mother_ticker_updates_oncalendar"]]
        assert updt["Persistent"] == ["true"]


class TestFail2ban:
    def test_sshd_jail_uses_nftables_and_the_journal(self, scenario: Scenario) -> None:
        v = scenario.vars
        lines = _lines(render("jail.local.j2", v))
        assert "backend = systemd" in lines
        assert "banaction = nftables-multiport" in lines
        assert f"maxretry = {v['mother_ticker_fail2ban_maxretry']}" in lines
        assert "enabled = true" in lines
        assert "ignoreip = 127.0.0.1/8 ::1" in lines
