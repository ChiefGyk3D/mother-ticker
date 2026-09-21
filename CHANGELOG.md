# Changelog

All notable changes to this project are documented here. The format is
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-09-21

First cut, **alpha**: tested in CI against recorded gpsd and chrony output and
rendered headless; not yet run on the target hardware. The lifecycle label
lives in `src/mother_ticker/version.py` beside the version.

### Added

- Daily pending-updates check (`mother-ticker check-updates`, a systemd
  timer) that installs nothing: `mother_ticker_updates_*` metrics for Grafana
  rules, the same values in the syslog body for the malware-net relay, counts
  on the system screen, and only the words "updates available" or "reboot
  required" on the dashboard. The banner never changes for updates.
- Optional one-way webhook alerts from the exporter (JSON for n8n and
  friends, or ntfy's publish shape) on health transitions and new security
  updates, with a bearer token read from a file. `docs/alerts.md` covers
  Grafana rules, n8n, ntfy, email and where Patch Gremlin fits.
- Admin login from the TUI: Ctrl+A or a menu item hands the terminal to
  `su - <admin>` for a password-gated shell, with a keyboard or over SSH, in
  both modes. F1 shows every key; the dashboard footer lists the main ones.

- Two ways to provision: `mother-ticker-install` from a shell on the unit
  (config file, no controller) and `ansible-playbook` from a controller. Both
  run the same role.
- Ansible role that turns Raspberry Pi OS Lite (64-bit) into the appliance:
  device tree overlays for the Uputronics GPS/RTC HAT (PPS, RTC, UART release,
  hardware watchdog), gpsd with a boot-time GNSS constellation policy (GPS and
  Galileo on, GLONASS and BeiDou off, stationary model), chrony disciplined by
  kernel PPS locked to NMEA, RTC sync at boot and every 11 minutes, nftables
  default-deny, key-only sshd with per-source penalties and fail2ban, unneeded
  services removed, systemd hardware watchdog, and the read-only overlay on the
  isolated unit.
- Site flag (`main-lan` or `malware-net`) selecting chrony sources and orphan
  mode, allowed subnets, the metrics path, apt timers and the overlay.
- Textual TUI with a passive dashboard (a big clock drawn in coloured cells,
  ASCII icons for GNSS, chrony, PPS and network, version) and a flashing
  warning banner, an About screen with the project logo in ASCII, a demo mode
  (`tui --demo`) that needs no hardware, and screenshots rendered from it, and a two-level menu: GNSS
  satellites, chrony tracking and sources, service restarts and journals with
  confirmation, network, system, maintenance, drop to shell. Runs on tty1 and
  over SSH.
- Metrics exporter: Prometheus text on the main LAN, RFC 5424 syslog with a
  JSON body over TCP or UDP toward the relay on the malware net.
- Health-check timer with an escalation ladder (restart gpsd or chronyd after
  sustained faults a restart can fix, reboot after prolonged criticality with
  a boot-loop guard).
- `mother-ticker` CLI: `tui`, `status`, `metrics`, `exporter`, `healthcheck`,
  `--version`.
- CI: ruff, mypy strict, pytest on Python 3.11 to 3.13, bandit, shellcheck,
  yamllint, ansible-lint, gitleaks, and an all-green gate. Tag-triggered
  release workflow.
- Documentation: README, ARCHITECTURE, RUNBOOK, SECURITY, CONTRIBUTING, NTS
  guide, offline-update guide. Issue forms for bug reports and bench results,
  Dependabot for action pins and Python dependencies, and the support and
  socials sections shared with the maintainer's other projects.

### Notes on the design as released

- The health ladder may reboot in both modes; a dev box that wedges itself
  reboots too.
- A `mode` flag (`appliance`, the default, or `dev`) now decides the
  read-only overlay, apt timer masking, journal storage, the TUI's shell
  escape and whether the health ladder may reboot; `site` decides only
  networks, metrics path and chrony sources. Appliance is the default on
  both sites, so a main-LAN unit gets the overlay too unless it is set to
  `dev`.
- The role writes `/etc/mother-ticker/install.conf` from the values it was
  deployed with (never overwriting one that exists), so a unit deployed from
  a controller can later run `mother-ticker-install` on its own.
- The About screen's belly emblem is the swirl with the anarchy A.
