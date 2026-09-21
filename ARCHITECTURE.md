# Architecture

How the pieces of a Mother Ticker unit fit together, and why each is where it is.

## The time chain

```
  GNSS antenna
       |
  u-blox M8 (on the Uputronics HAT)
       |----- UART (ttyAMA0): NMEA sentences, UBX commands ----> gpsd ----> SHM 0 ----.
       |                                                          |                    |
       '----- timepulse (GPIO18) ---> kernel pps-gpio ---> /dev/pps0 ----------------> chronyd
                                                                  |                    |
  RV-3028-C7 RTC (I2C 0x52) ---> /dev/rtc0 --- udev: hwclock --hctosys at boot ---->   |
                                     ^                                                 |
                                     '------------- rtcsync every 11 min --------------'
                                                                                       |
                                                              UDP 123 (and NTS 4460) to clients
```

**Two refclocks, one job each.** The NMEA sentence from gpsd (`refclock SHM 0
... noselect`) tells chrony *which* second it is; its timing is only good to
a few milliseconds, so `noselect` keeps it out of the clock. The PPS edge
(`refclock PPS /dev/pps0 lock NMEA prefer`) tells chrony *exactly when* the
second starts, to well under a microsecond; `lock NMEA` pairs each pulse with
the sentence that names it. Together they make a stratum-1 server. Either one
alone does not.

**Why kernel PPS and not gpsd's PPS.** gpsd can also forward PPS through SHM,
but the kernel `pps-gpio` driver timestamps the edge in interrupt context
with no user-space hop, and chrony reads it directly. gpsd is also handed
`/dev/pps0` so that `cgps` and `gpsmon` show pulses, which is useful when
debugging, but chrony does not depend on gpsd for the pulse.

**Why `-n` on gpsd and no socket activation.** Debian starts gpsd on demand
when a client connects. chrony's SHM source is not a client of the socket, so
without `-n` and a real `gpsd.service` the NMEA source stays empty forever and
the PPS source has nothing to lock to. This is the most common cause of a
"PPS refclock never selected" report.

**The RTC's two moments.** At boot, before any network or GPS, a udev rule
copies the RTC into the system clock as soon as `/dev/rtc0` appears, so
chrony starts within a second of true time and `makestep 1 3` can finish the
job with the first three GPS updates. From then on chrony's `rtcsync` has the
kernel write the disciplined clock back to the RTC every 11 minutes, so the
next boot starts equally close. Debian's stock `hwclock-set` exits early under
systemd, and `fake-hwclock` would overwrite the RTC with a file at shutdown;
the role installs the rule and purges the package.

**Constellation policy.** At every boot, after gpsd is up,
`mother-ticker-gnss-config` uses `ubxtool` through gpsd to enable GPS and
Galileo, disable GLONASS and BeiDou, set the stationary dynamic model and save
to the receiver. GLONASS's timing is worse than GPS or Galileo and the M8
tracks at most three systems concurrently, so this is a deliberate choice, not
an omission. Doing it at boot rather than once means a receiver with a dead
backup cell still gets the policy.

**When GPS is lost.** PPS stops, chrony marks the source unreachable and
coasts on its frequency estimate. On the main LAN two public servers are
configured at ordinary priority, so after a while chrony selects one and the
unit serves stratum 2, still correct to milliseconds, and the TUI shows a
warning. On the malware net there is nothing else, so `local stratum 10
orphan` keeps the unit answering from its free-running clock, and the stratum
tells clients how much to trust it. Both are visible in the metrics.

## The site flag

`mother_ticker_site` is a host variable. In the role it selects the defaults
for metrics mode and the overlay, and templates read it directly for chrony's
orphan mode and the apt timers. In the Python code it is carried in
`config.toml` and only decides which metrics writer runs. Nothing else
branches on it, which is what keeps the two units one codebase.

## The application

```
 src/mother_ticker/
   collectors/   gpsd (stdlib socket), chrony (chronyc -c), pps (sysfs),
                 system (/proc, thermal, vcgencmd), network (ip -j), services (systemctl)
                 -> frozen dataclasses in model.py; parsers are pure and fixture-tested
   health/       evaluate(): Snapshot -> ok | warning | critical with reasons
                 check(): the escalation ladder behind the timer
   metrics/      model: Snapshot -> [Metric]; prometheus: text exposition + HTTP;
                 syslog: RFC 5424 + JSON body, TCP or UDP
   tui/          Textual app: collector thread -> Snapshot -> every open screen
   cli.py        tui | status | metrics | exporter | healthcheck, --version
```

**One evaluation.** `health.evaluate` is the only place that decides what is
wrong. The dashboard banner, `mother_ticker_health_level`, the syslog severity
and the health-check ladder all consume its result, so they cannot disagree.

**TUI data flow.** The app runs one collector thread. Every second it gathers
a `Snapshot` (network less often), evaluates it, and posts a `SnapshotUpdated`
message to every screen on the stack. Screens are passive: they render what
they are handed. The chrony detail and journal screens run their own short
worker for the raw text they show. Privileged actions (restart, reboot,
overlay toggle) go through `sudo -n` and a sudoers file that names exactly
those commands for the TUI user; the code never builds a command from user
input.

**Why Textual.** The dashboard must be the same thing on the unit's console
(TERM=linux, 16 colours, keyboard only) and over SSH. Textual renders to
whatever the terminal offers, so the stylesheet uses the ANSI palette and the
warning state is expressed as colour plus blinking inverse, which survives a
16-colour console.

**Memory on a 1 GB Pi.** The TUI (Textual plus the collector) sits around
60 to 80 MB; the exporter is a stdlib HTTP server capped at 96 MB by its unit;
gpsd and chronyd are a few MB each. No swap is configured.

## Metrics paths

```
 main-lan:     collectors -> evaluate -> [Metric] -> Prometheus text -> HTTP :9101 <- scraper
                                                      (nftables: scraper IP only)

 malware-net:  collectors -> evaluate -> [Metric] -> flat JSON -> RFC 5424 -> TCP/UDP -> relay -> SIEM
                                                      (no listener; nothing inbound; one message per interval)
```

The isolated unit never opens a metrics port and never talks to the
production monitoring stack. It writes to the same relay the segment already
uses for logs, in the format that relay expects, and that is the only
outbound path the exporter has.

## The health ladder

Every 30 s (`mother-ticker-healthcheck.timer`, root):

1. Evaluate. Exit status is the level, so `systemctl status` and the journal
   show it without parsing.
2. Count consecutive critical results per subsystem in `/run/mother-ticker/health.json`.
3. At `restart_after_failures` (4, so two minutes) restart the unit a restart
   can fix: gpsd when it is unreachable, chronyd when it does not answer or
   stays unsynchronised while PPS is healthy. A receiver with no fix is an
   antenna or sky problem, and the ladder says so in the journal instead of
   restarting gpsd. Restarts back off (every 8 ticks after the first).
4. At `reboot_after_failures` (40, twenty minutes) with the host up for at
   least 30 minutes, reboot. The uptime floor bounds a reboot loop to one
   attempt per half hour.

Underneath that, `Restart=always` on gpsd and chronyd handles crashes, and
the BCM2711 hardware watchdog (`RuntimeWatchdogSec=15`) reboots a unit whose
kernel or PID 1 has stopped.

## Provisioning

One Ansible role, imported task files in a fixed order, tags per file. The
role refuses to run on an overlay root (changes would vanish), asserts the
things it depends on, and ends with a verification pass that names what to
look at when something is missing. The read-only overlay is the very last
step because it freezes everything before it.

The application is installed from the checkout the playbook runs from
(`src/`, `pyproject.toml`) into `/opt/mother-ticker/venv`, so what you
deployed is what you have in git. A wheelhouse variable makes the pip step
work with no index for the isolated unit.

## Security model

- Time clients trust UDP 123 answers from this host. chrony serves only the
  listed subnets, rate-limits, and its command port is bound to loopback;
  reconfiguration needs root on the box. chronyd runs as `_chrony` and the
  role asserts it.
- The host accepts nothing inbound except NTP from the client subnets, SSH
  from management, and (main LAN) the metrics port from the scraper. Default
  drop, logged at a low rate.
- SSH is key-only, no root, no forwarding, a fixed user list, OpenSSH's
  per-source penalties where available, and fail2ban on top.
- The TUI user is unprivileged with a locked password and a sudoers entry
  for exactly the actions the menu offers. Its shell is the TUI. The admin
  user is a normal account.
- Physical access to the unit is root: the console TUI can restart services
  and reboot without a password because the SD card is right there anyway.
- Nothing in the repository is a secret. Real subnets live in a gitignored
  inventory. CI runs gitleaks.
