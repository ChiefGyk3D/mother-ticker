# Updating the isolated unit

The malware-net unit has no general internet. Two things follow: it is built
somewhere that does, and it is updated either during the firewall's
intermittent WAN windows or from a bundle carried in over the management
network. This page is the procedure for both, and the list of what to
pre-stage.

## Before deployment: what to pre-stage

Build the unit on a network with internet, with its malware-net host_vars, so
that everything below is already on the card when it moves:

- All apt packages the role installs (`mother_ticker_packages`,
  `mother_ticker_packages_optional`, fail2ban).
- The Python venv at `/opt/mother-ticker/venv` with Textual and the
  application. PyPI is never needed on the malware net.
- The GNSS receiver's constellation policy, saved to the receiver at first
  boot (and reapplied at every boot from the local script anyway).
- Your SSH key in the admin user's `authorized_keys`, and the sshd drop-in.
- If you use NTS: the certificate and key (see `docs/nts.md`).
- Time itself: let the unit get a GPS fix once before moving it, so the RTC
  holds correct time through the move.

Nothing on the unit phones home. apt timers are masked; there is no
unattended-upgrades; the exporter only sends to the relay.

## Maintenance mode

The root filesystem is a read-only overlay. Any update must start by
disabling it and end by re-enabling it, each with a reboot:

```sh
ssh admin@ntp-malware
sudo mother-ticker-maint status      # running: read-only overlay
sudo mother-ticker-maint on          # reboots read-write in 5 s
# ... reconnect after the reboot, do the work ...
sudo mother-ticker-maint off         # reboots read-only in 5 s
```

The TUI's maintenance menu does the same two things with a confirmation.

## Option A: update during a WAN window

When the firewall opens the WAN for this segment:

```sh
sudo mother-ticker-maint on              # reboot
sudo apt-get update && sudo apt-get dist-upgrade
sudo mother-ticker-maint off             # reboot
```

To update the application as well, on the unit itself (after pulling or
copying a newer checkout to `/opt/mother-ticker/repo`):

```sh
sudo mother-ticker-install
```

or from your controller on the management network during the same window:

```sh
cd ansible
../.venv/bin/ansible-playbook site.yml -l ntp-malware
```

Either way the role's last step re-enables the overlay and reboots, so you do
not need the manual `off` in that case. The role refuses to run while the overlay is
active; `on` first.

## Option B: update from a bundle (no WAN needed)

Build the bundle on the main-LAN unit, which runs the same OS and
architecture and has internet:

```sh
ssh admin@ntp-main
sudo mother-ticker-stage-bundle ~/bundle
```

(The role installs `scripts/stage-offline-bundle.sh` as
`/usr/local/sbin/mother-ticker-stage-bundle` on every unit. It refreshes apt
lists, downloads every pending `.deb`, copies the lists, and builds a
wheelhouse for the venv. Output is one tarball and its SHA-256.)

Carry it over the management network:

```sh
scp ~/bundle/mother-ticker-offline-*.tar* admin@ntp-malware:/tmp/
```

Apply on the isolated unit:

```sh
ssh admin@ntp-malware
cd /tmp && sha256sum -c mother-ticker-offline-*.tar.sha256
sudo mother-ticker-maint on                                   # reboot
cd /tmp && mkdir bundle && tar -C bundle -xf mother-ticker-offline-*.tar
sudo cp -a bundle/var/lib/apt/lists/. /var/lib/apt/lists/
sudo cp bundle/debs/*.deb /var/cache/apt/archives/
sudo apt-get --no-download dist-upgrade
sudo /opt/mother-ticker/venv/bin/pip install --no-index --find-links /tmp/bundle/wheelhouse --upgrade /opt/mother-ticker/repo
sudo mother-ticker-maint off                                  # reboot
```

To update the application source itself this way, copy the repository's
`src/` and `pyproject.toml` to `/opt/mother-ticker/repo` before the pip line,
or run the role with `mother_ticker_offline: true` and
`mother_ticker_wheelhouse: /path/to/bundle/wheelhouse` from the controller.

## What not to do

- Do not add the malware net to your Prometheus scrape config or point the
  unit at your production monitoring. The relay is the path.
- Do not leave maintenance mode on. `mother-ticker-maint status` on the TUI's
  system screen shows `read-write` in that case, and the
  `mother_ticker_overlay_root` metric is 0.
- Do not skip the hash check on a bundle that crossed a network boundary.
