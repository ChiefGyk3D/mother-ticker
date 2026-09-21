# Security

## Reporting a vulnerability

Use GitHub's private vulnerability reporting on this repository
(**Security** tab, **Report a vulnerability**). Include the version
(`mother-ticker --version`), the site mode, what you observed and how to
reproduce it. You will get an acknowledgement within a week and a fix or a
stated decision within 90 days, sooner for anything that affects served time
or remote access.

Please do not open a public issue for something exploitable.

## Scope

Covered:

- The Ansible role and everything it installs or configures: chrony, gpsd,
  nftables, sshd, fail2ban, the systemd units, sudoers, the udev rule, the
  helper scripts.
- The Python code: TUI, exporter, health check, CLI.
- The documentation, where it describes a security property.

Not covered here (report upstream): chrony, gpsd, Raspberry Pi OS, the
firmware, Textual, or the relay and SIEM the malware-net unit sends to.

## Threat model in one paragraph

A Mother Ticker unit is a device other hosts trust for time. The threats
that matter are: an attacker on an allowed subnet trying to change what time
it serves, an attacker anywhere trying to reach a service on it, and the
isolated unit becoming a channel out of the malware net. The countermeasures
are in `ARCHITECTURE.md` under *Security model*; in short, chrony cannot be
reconfigured over the network, nothing listens except NTP, SSH from
management and (main LAN) the metrics port from the scraper, the isolated
unit only ever sends to the relay the segment already uses, and the TUI user
can do exactly what its menu shows.

## Hardening checklist the role applies

- chrony: `allow` limited to listed subnets, command port bound to loopback,
  `cmdallow` loopback only, rate limiting, runs as `_chrony` (asserted).
- nftables: default drop inbound, explicit allows, forward dropped, drops
  logged at a bounded rate.
- sshd: key-only, no root, no forwarding, `AllowUsers`, 3 auth tries, 20 s
  grace, `PerSourcePenalties` on OpenSSH 9.8+, fail2ban with the nftables
  action.
- Removed or masked: avahi, Bluetooth, ModemManager, triggerhappy, swap,
  `fake-hwclock`, serial gettys, Wi-Fi (device tree), apt timers on the
  isolated unit.
- Watchdog: hardware watchdog via systemd; health-check ladder with a
  boot-loop guard.
- Exporter unit: `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`,
  memory cap, unprivileged user.
- Read-only overlay root on the isolated unit.

## Supported versions

The latest release on `main`. Security fixes are released as a patch version
and noted in `CHANGELOG.md` under *Security*.
