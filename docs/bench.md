# Bench checklist

What to run, in what order, on a real Pi, and what to write down. Two
passes: one on a bare Pi 4 before the GPS board arrives, one once it is
fitted. CI never sees a Pi; every claim in the README marked *verify on
hardware* stays open until somebody does this and files a
[bench result](../.github/ISSUE_TEMPLATE/bench-result.yml). The first
completed second pass is what moves the project from alpha to beta.

Commands assume `ssh admin@unit` unless they say otherwise. Redact your
real subnets in anything you paste; the RFC 5737 placeholders from the
README are fine to leave in.

## What to record

- OS image name and date, Pi model and revision, board revision.
- `mother-ticker --version`.
- Which deploy path you used, and the exact `install.conf` or host_vars
  (placeholders in place of your subnets).
- The output of each check below that did not match what this page says.
- Anything you had to change to make it work: a GPIO number, an overlay name,
  a protocol version, a package name. That is the useful part.

## Pass 1: a bare Pi, no board

Everything except the time chain. Set `mother_ticker_hardware_present:
false` (Ansible) or `HARDWARE_PRESENT=no` (`install.conf`) so the role
skips the PPS, RTC and UART checks and the health ladder cannot reboot a
unit that can never get a fix. Deploy per the README, then:

### Deploy and verify

```sh
sudo mother-ticker-install                 # or ansible-playbook from the controller
systemctl --failed                          # nothing
systemctl is-active chrony gpsd nftables mother-ticker-console \
    mother-ticker-exporter mother-ticker-healthcheck.timer mother-ticker-updates.timer
/opt/mother-ticker/venv/bin/mother-ticker status
```

Expected: the verify step ends with the "hardware_present is false" note
and no failed assertion. `status` reports **CRITICAL** with `PPS device pps0
missing`, `GNSS no fix` or `gpsd unreachable`, and `chrony stratum 0`; that
is the truth about a unit with no receiver. Record the whole `status` block.

Re-run the same deploy a second time. Expected: `changed=0` from Ansible, or
the installer finishing with nothing changed. A task that changes on every
run is a bug; name it.

### The console

- tty1 shows the dashboard on the DSI panel within a minute of boot, red
  banner, big clock ticking, the version in the corner. Record whether the
  font size (`FONTSIZE="8x16"`) gives the intended 100 by 30 cells, and
  whether the touchscreen's touch input does anything unwanted (it should be
  ignored).
- Plug in a keyboard. Any key opens the menu; Esc returns; F1 lists the keys.
- **Ctrl+A**: the admin password prompt appears; the right password gives a
  shell as the admin user with `sudo` working; `exit` brings the dashboard
  back; a wrong password returns to the dashboard.
- **Ctrl+Q**: the TUI quits and systemd brings it back within a few seconds.
- Alt+F2 gives a login prompt on tty2; Alt+F1 is the TUI again.
- Blinking: `mother-ticker tui --demo warning` and `--demo critical` from the
  admin shell on tty2 show the yellow and red banners blinking on the panel.
  Say whether the blink rate is bearable from across a room.

### SSH, fail2ban and the firewall (from a second machine)

```sh
ssh motherticker@unit                       # lands on the TUI; Ctrl+A works here too
ssh motherticker@unit status                # one-shot text, exit code is the health level
ssh motherticker@unit 'ls /'                # refused: only status and metrics run non-interactively
ssh -o PubkeyAuthentication=no admin@unit   # "Permission denied (publickey)", no password prompt
```

From a host **outside** `mother_ticker_mgmt_allow`: `ssh admin@unit` must
time out, not refuse. From inside it, four wrong keys or passwords in a row
must get you banned: `sudo fail2ban-client status sshd` on the unit lists
your address, and `sudo nft list ruleset` shows it in the fail2ban set.
`sudo fail2ban-client set sshd unbanip <addr>` lets you back in.

```sh
sudo nft list ruleset                       # on the unit: policy drop on input and forward
sudo journalctl -k | grep 'mother-ticker drop'   # a few lines from the probe above
```

### Metrics

Main-LAN site: from an address in `mother_ticker_metrics_allow`,
`curl -s http://unit:9101/metrics | grep mother_ticker_health_level` returns
`2`. From any other address the connection times out. Point Prometheus at it
per `grafana/README.md`; the dashboard's stat panels go red and the
`MotherTickerCritical` rule fires after two minutes. That is the alerting
path tested end to end without a receiver.

Malware-net site: on the relay host, `nc -l -p 514` (TCP) or `nc -u -l -p
514` (UDP) shows one RFC 5424 line every 15 seconds with
`"health_level":2` in the body and severity 2 in the priority. Record which
transport and framing your relay wanted.

### Updates

```sh
sudo mother-ticker check-updates            # main LAN: "lists refreshed"; isolated: "not refreshed"
cat /var/lib/mother-ticker/updates.json
```

The system screen shows the counts; the host panel says `updates available`
only if there are any. If a webhook is configured, `sudo systemctl restart
mother-ticker-exporter` after a fake critical state (below) sends one
message and `mother_ticker_alerts_sent` goes to 1.

### The overlay and maintenance mode (appliance mode)

```sh
mother-ticker-maint status                  # running: read-only overlay
sudo touch /etc/bench-marker && sudo reboot
ls /etc/bench-marker                        # gone
sudo mother-ticker-maint on                 # reboots read-write
sudo touch /etc/bench-marker && sudo mother-ticker-maint off
ls /etc/bench-marker                        # still there: the write happened on the real root
```

The TUI's maintenance menu does the same two steps; try it from the panel
once.

### The receiver, pretended

The fake receiver exercises the gpsd collector, the GNSS screen and the
health ladder's reaction to a fix appearing and vanishing, with real sockets.
It produces no PPS and writes no shared memory, so chrony stays critical,
which is right.

```sh
sudo systemctl stop gpsd.socket gpsd.service
/opt/mother-ticker/venv/bin/mother-ticker fake-gpsd --satellites 8   # keep it running
```

In another session: the dashboard's GNSS panel shows `3D fix` and `8/14`
within a few seconds; the GNSS screen lists fourteen satellites with the
first eight marked used; `mother_ticker_gnss_fix_mode` is 3 on `/metrics`.
Ctrl+C the fake and restart it with `--scenario nofix`: the panel drops to
`no fix` and the health banner text changes. `sudo systemctl start gpsd`
afterwards.

### Dev mode

If you also deploy a unit with `MODE=dev`: the root is writable, the apt
timers run, the journal persists across a reboot, and the TUI menu offers
*Drop to a shell*. Record whether anything from the appliance list above
behaves differently.

## Pass 2: the board is fitted

Set `mother_ticker_hardware_present: true` (or drop `HARDWARE_PRESENT`) and
re-run the deploy; only the checks and the GNSS unit change. Then, in
order, because each step depends on the one before:

### Boot configuration

```sh
grep -A12 'Uputronics' /boot/firmware/config.txt
dmesg | grep -iE 'pps|rtc|ttyAMA'
ls -l /dev/pps0 /dev/rtc0 /dev/ttyAMA0
sudo i2cdetect -y 1                         # the RV-3028 answers at 0x52 (shows as UU once the driver has it)
```

If `/dev/pps0` is missing, the PPS GPIO number is the first suspect: the
README carries `18` as *verify on hardware*. Change `mother_ticker_pps_gpio`,
re-run `-t boot`, reboot, and put the working number in your report.

### The RTC

```sh
sudo hwclock -r                             # a plausible time, not 1970
timedatectl                                 # "RTC time" agrees with "Universal time" to the second
dpkg -l fake-hwclock                        # purged
```

Pull the power for a minute and boot with the network cable out. The clock
must come up within a second or two of real time from the RTC alone.

### The receiver

```sh
sudo cat /dev/ttyAMA0                       # $GNRMC, $GNGGA lines once a second; Ctrl+C
journalctl -u mother-ticker-gnss-config     # "applied: enable [GPS GALILEO] disable [GLONASS BEIDOU] stationary=1"
ubxtool -P 18 -p CFG-GNSS | grep -E 'GPS|GAL|GLO|BDS'   # enabled flags match
cgps -s                                     # a fix within 2 minutes under open sky, 15 on a cold start
```

If `mother-ticker-gnss-config` failed, the protocol version is the usual
reason: `ubxtool -P 18 -p MON-VER` prints the firmware; set
`mother_ticker_gnss_protver` to match and re-run `-t gpsd`. Report both
numbers.

### PPS

```sh
sudo ppstest /dev/pps0                      # one line per second once there is a fix
cat /sys/class/pps/pps0/assert; sleep 2; cat /sys/class/pps/pps0/assert   # sequence grew by 2
```

### chrony

```sh
chronyc sources -v                          # over 10 minutes
chronyc tracking
```

Expected progression: `#? PPS` and `#? NMEA` first; then `#x NMEA` (false
ticker is correct for a noselect source) and `#* PPS` selected; stratum 1 in
`tracking`, `System time` in the tens of nanoseconds, `Update interval` 16 s.
If PPS never gets selected, measure the NMEA offset per
[RUNBOOK.md](../RUNBOOK.md#measuring-the-nmea-offset), set it in host_vars,
re-run `-t chrony`, and report the value; both units usually want the same
one but each is measured.

Record the `chronyc sourcestats` NMEA line and the `tracking` block after an
hour of PPS lock. Those two are the numbers the README will quote.

### From a client

On another machine, `chronyc -n ntpdata unit` (or `ntpdate -q unit`,
`sntp unit`) shows stratum 1, reference `PPS`, and a root dispersion under a
millisecond. If NTS is enabled, `chronyc -N authdata` on a client configured
with `nts` shows `NTS` in the mode column; `docs/nts.md` has the client line.

### The whole thing, cold

Pull the power. Boot with the antenna connected and no keyboard. Within five
minutes and with nobody touching it, the banner is green and `chronyc
sources` shows `#* PPS`. Then pull the antenna: the banner goes yellow
(main LAN, upstream servers) or red (isolated, orphan stratum 10) within
the health thresholds, and green again a few minutes after the antenna is
back. Report the times.

### Thermal, in the case

After an hour in the SmartiPi case with the panel on, the system screen's
temperature and `vcgencmd get_throttled` (must be `0x0`). Record both; the
thresholds in `config.toml` were set without a measurement.

## When both passes are done

File the bench result. If nothing needed changing beyond the values above,
the maintainer sets `__status__` in `src/mother_ticker/version.py` to
`beta`, records what was verified in the CHANGELOG, and cuts a release per
`CONTRIBUTING.md`.
