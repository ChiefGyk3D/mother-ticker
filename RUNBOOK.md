# Runbook

Symptom first. Each entry says what it looks like, how to confirm, and how to
recover. Commands assume `ssh admin@unit` and `sudo`; the TUI shows most of
the same information under its menu.

## Normal

- Banner: green, `ALL SYSTEMS NOMINAL`.
- GNSS: `3D fix`, 6 to 14 satellites used.
- chrony: `stratum 1 via PPS`, offset in the tens of nanoseconds, `leap Normal`.
- PPS: `pulsing, last edge 0.x s ago`.
- `chronyc sources -v`: `#* PPS` selected, `#x NMEA` (false ticker is expected
  for a `noselect` source), any upstream servers `^-` or `^+`.
- `chronyc tracking`: `Root dispersion` in microseconds, `Update interval` 16 s.

## Lost GPS fix

**Looks like:** banner red, `GNSS no fix` (or yellow `GNSS 2D fix only`). The
PPS panel may still show pulsing for a while; some receivers keep the
timepulse going briefly on a lost fix and then stop. chrony holds for
minutes, then the main-LAN unit drops to stratum 2 on a public server
(yellow banner), the malware-net unit to stratum 10 orphan (red).

**Confirm:**

```sh
cgps -s                          # satellites seen, SNR per satellite; q to quit
gpspipe -w -n 10 | grep TPV      # "mode":1 is no fix
journalctl -u gpsd -n 50
```

**Causes and fixes, most common first:**

1. Antenna: cable loose, SMA not tight, antenna moved indoors, antenna power
   (the HAT feeds the active antenna; a short on the cable can trip it).
   Reseat, then wait. A cold start with a good sky is under 2 minutes; an
   M8 with a dead backup cell and no almanac can take 15.
2. Sky view: fewer than 4 satellites above 10 degrees elevation means no 3D
   fix. Move the antenna. The GNSS detail screen lists elevation per satellite.
3. Constellation policy: `journalctl -u mother-ticker-gnss-config`. If the
   boot-time policy failed, the receiver keeps its previous configuration; if
   it succeeded and the sky is poor, temporarily allow GLONASS in host_vars
   and re-run `-t gpsd`.
4. gpsd not reading the UART: `ls -l /dev/ttyAMA0`, `stty -F /dev/ttyAMA0`,
   `sudo cat /dev/ttyAMA0` should show `$GNRMC` lines. If nothing, Bluetooth
   still owns the PL011: check `dtoverlay=disable-bt` is in
   `/boot/firmware/config.txt`, `hciuart` is masked, and reboot.

**Recovery:** the health ladder does not restart gpsd for a no-fix state on
purpose. Once satellites return, the fix returns on its own and chrony
re-selects PPS within a few polls.

## PPS not pulsing

**Looks like:** banner red, `PPS not pulsing (last edge N s ago)` or `PPS
device pps0 missing`. GNSS may still show a 3D fix.

**Confirm:**

```sh
ls /sys/class/pps/                 # pps0 must exist
cat /sys/class/pps/pps0/assert     # timestamp#sequence; run twice, sequence must grow
sudo ppstest /dev/pps0             # one line per second
dmesg | grep -i pps
```

**Causes and fixes:**

- `pps0` missing: the overlay did not load. Check the MOTHER TICKER block in
  `/boot/firmware/config.txt` and reboot. If it is there and `dmesg` shows no
  `pps-gpio`, the GPIO number is wrong for the board (see README, *verify on
  hardware*); change `mother_ticker_pps_gpio` and re-run `-t boot`.
- `pps0` present, no edges, GNSS has a fix: the receiver's timepulse output is
  off. `ubxtool -P 18 -p CFG-TP5` shows the timepulse configuration; the
  default is 1 Hz while a fix is held. `ubxtool -P 18 -e PPS` re-enables it.
- `pps0` present, no edges, no fix: normal. The M8 stops the timepulse without
  a fix. Fix the fix.

**Recovery:** automatic once edges return. chrony needs a few consecutive
pulses that agree with NMEA before it selects PPS again; watch `chronyc
sources` for `#* PPS`.

## chrony not synchronised or on the wrong source

**Looks like:** red `chrony not synchronised`, or yellow `stratum 2, not on
PPS`, or a large offset.

**Confirm:**

```sh
chronyc tracking
chronyc sources -v
chronyc sourcestats
journalctl -u chrony -n 50
```

**Read it:**

- `Leap status: Not synchronised` with `#? PPS` and `#? NMEA`: chrony sees
  nothing from either refclock. Usually gpsd is not feeding SHM: `gpspipe -w
  -n 5` should show data; `ipcs -m` should list a shared memory segment with
  key `0x4e545030` (NTP0). If gpsd is running but SHM is empty, gpsd started
  before the UART was ready; `systemctl restart gpsd`.
- `#x NMEA` and `#? PPS`: NMEA arrives but PPS does not. See the PPS section.
- `#* NMEA` selected: someone removed `noselect`. Clients get millisecond
  time. Restore chrony.conf (re-run `-t chrony`).
- `#? PPS` with a fix and pulses: the NMEA offset is off by more than half a
  second so pulses cannot be paired with sentences. See "Measuring the NMEA
  offset" below.
- `^* time.cloudflare.com` on the main LAN: GPS is lost; the unit is serving
  stratum 2 correctly. Fix GPS.
- Large `Root dispersion` growing over time: chrony is coasting; no source has
  been reachable. Same as above.

**Recovery:** `sudo systemctl restart chrony` after fixing the cause. The
health ladder restarts chronyd itself after two minutes of "not answering",
or "not synchronised with a healthy PPS".

## Measuring the NMEA offset

`refclock SHM 0 ... offset X` compensates for the delay between the PPS edge
and the arrival of the NMEA sentence describing it. With the wrong value
chrony cannot pair them (`lock NMEA` fails) and PPS is never selected.

1. With a fix and pulses, run `chronyc sourcestats` a few times over ten
   minutes. The NMEA row's `Offset` is the number you want, in seconds; it is
   typically 0.05 to 0.5 and stable to a few milliseconds.
2. Set `mother_ticker_nmea_offset` to that value in the unit's host_vars.
3. Re-run `-t chrony`. Within a few polls `#* PPS` appears.

Both units use the same board and firmware, so the value is usually the same
for both, but measure each.

## gpsd or chronyd down

**Looks like:** red `gpsd is failed` or `chrony is inactive`, or `gpsd
unreachable` / `chronyd not answering`.

`Restart=always` on both units restarts a crashed process in 5 s; the health
ladder restarts a stuck one after two minutes. If a service keeps failing,
`journalctl -u gpsd -b` or `journalctl -u chrony -b` says why; a typo in
`chrony.conf` shows up here (the role validates the file before installing
it, so this usually means a manual edit).

## Thermal

**Looks like:** yellow `SoC 72 C`, red at 80, `throttled now` on the system
screen. A closed SmartiPi case in a warm rack can reach this.

Check `vcgencmd measure_temp` and `vcgencmd get_throttled`. Bit 3 set means
the soft limit is active; bit 0 is under-voltage, which is a power supply
problem rather than heat. Improve airflow or fit a heatsink; the unit keeps
serving time while throttled, just slower.

## Exporter and relay

- Main LAN, scrape failing: `curl -s http://127.0.0.1:9101/metrics | head` on
  the unit. If that works, nftables: is the scraper in
  `mother_ticker_metrics_allow`? `sudo nft list ruleset`.
- Malware net, nothing arriving at the SIEM: `journalctl -u
  mother-ticker-exporter -n 30`; a refused TCP connection is logged each
  interval and counted in `mother_ticker_relay_send_failures`. Check the relay
  address, port, transport and framing in host_vars against what the relay
  runs. `nc -zv relay 514` from the unit tests reachability.
- The exporter refreshes at least every 5 s and serves a cached rendering, so
  a scrape storm does not load gpsd or chronyc.

## Read-only overlay (malware-net)

- "My change vanished after a reboot": the root is an overlay. Every write
  goes to RAM. `mother-ticker-maint status` shows it. Use maintenance mode:
  `sudo mother-ticker-maint on` (reboots read-write), do the work, `sudo
  mother-ticker-maint off` (reboots read-only). The TUI's maintenance menu
  does the same.
- "Ansible refuses to run": same reason; the role refuses an overlay root so
  its changes are not lost. Maintenance mode first.
- The journal is volatile on this unit. Anything you need after a reboot
  should be at the SIEM already, via the relay.
- The drift file does not persist. PPS re-learns drift within a few minutes
  of each boot; the RTC keeps the boot-time error under a second.

## Health-check reboots

The journal line `reboot host (critical for 40 consecutive checks: ...)` is
the ladder giving up. The unit will not do it again within 30 minutes of
booting. If it keeps happening, the cause is hardware (antenna, overlay,
card) and the journal lines before the reboot name it. Set
`mother_ticker_health_reboot_enabled: false` to stop the ladder rebooting
while you investigate; the restarts still run.

## Getting a shell when the TUI is in the way

- On the unit: Alt+F2 gives a login on tty2 for the admin user. Alt+F1 is the TUI.
- Over SSH: `ssh admin@unit` is a normal shell. `ssh motherticker@unit` is
  the TUI; choose "Drop to a shell" from its menu for an unprivileged shell,
  or `ssh motherticker@unit status` for one-shot text.
- Ctrl+Q quits the TUI. systemd restarts it on tty1 after 2 s.

## After a power cut

Boot takes about a minute. The RTC sets the clock within a second; chrony
steps to GPS in its first three updates; PPS is selected a few minutes after
the first fix. The health check waits three minutes after boot before its
first tick so that a normal start never counts as a failure.
