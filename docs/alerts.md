# One-way alerting

A unit may sit on a segment you cannot reach, so it has to tell you when it
needs attention without anyone logging in, and it must never open a path back
in. Everything here is outbound from the unit and read-only for the unit.

## What is reported

| Condition | Level | Where it shows |
|---|---|---|
| PPS gone, no fix, chrony unsynchronised, gpsd or chronyd down | critical | banner, `mother_ticker_health_level`=2, syslog severity 2, webhook |
| GPS lost and serving stratum 2, SoC warm, few satellites, offset drift | warning | banner, level 1, syslog severity 4, webhook |
| Security updates pending, or a reboot required to finish an update | warning | host panel, banner, `mother_ticker_security_updates_pending`, `mother_ticker_reboot_required`, webhook |
| Other updates pending | info | host panel, `mother_ticker_updates_pending` |
| Update check never ran or its lists are stale | info | system screen, `mother_ticker_updates_checked`, `mother_ticker_updates_checked_timestamp_seconds` |

The daily check (`mother-ticker-updates.timer`, `mother-ticker check-updates`)
installs nothing. It runs `apt-get update` where the network allows, then
`apt-get -s dist-upgrade`, counts the `Inst` lines and those from a
`-security` suite, and writes `/var/lib/mother-ticker/updates.json`. On the
isolated unit `apt-get update` fails and the count comes from the lists the
unit has, which is stated on the system screen. Run it by hand with
`sudo mother-ticker check-updates` (`--no-refresh` to skip the fetch).

## Main LAN: Grafana alert rules on the scraped metrics

The exporter serves `/metrics` on 9101 to the Prometheus scraper. Two rules
cover most of it. Grafana's contact points then deliver by email, Slack,
Discord, Matrix, ntfy, PagerDuty or a webhook, so email lives in Grafana, not
on the unit.

```yaml
groups:
  - name: mother-ticker
    rules:
      - alert: MotherTickerCritical
        expr: mother_ticker_health_level >= 2
        for: 2m
        labels: {severity: critical}
        annotations:
          summary: "{{ $labels.instance }} time service critical"
      - alert: MotherTickerWarning
        expr: mother_ticker_health_level == 1
        for: 15m
        labels: {severity: warning}
      - alert: MotherTickerSecurityUpdates
        expr: mother_ticker_security_updates_pending > 0
        for: 1h
        labels: {severity: warning}
        annotations:
          summary: "{{ $value }} security updates pending on {{ $labels.instance }}"
      - alert: MotherTickerRebootRequired
        expr: mother_ticker_reboot_required == 1
        for: 1h
      - alert: MotherTickerUpdateCheckStale
        expr: time() - mother_ticker_updates_checked_timestamp_seconds > 3 * 86400
        for: 1h
      - alert: MotherTickerScrapeDown
        expr: up{job="mother-ticker"} == 0
        for: 5m
        labels: {severity: critical}
```

`ScrapeDown` is the one that fires when the unit itself is gone, which none
of the unit's own signals can say.

## Main LAN: the webhook

For push without waiting on a scrape interval, the exporter can POST directly.
In the unit's host_vars (or `install.conf`):

```yaml
mother_ticker_alert_webhook_url: "https://n8n.lab.example/webhook/mother-ticker"
mother_ticker_alert_format: json          # or ntfy
mother_ticker_alert_min_level: warning    # or critical
mother_ticker_alert_token_file: /etc/mother-ticker/webhook.token   # optional bearer token
```

The token file is yours to place (`install -m 0640 -o root -g motherticker`);
it is read at send time and never copied into `config.toml`. A message goes
out when the health level crosses the floor in either direction and when a
new security update appears, never on every tick. Sends are counted in
`mother_ticker_alerts_sent` and `mother_ticker_alerts_failed`.

**JSON shape** (`format: json`), one document per event:

```json
{"kind":"health","level":"critical","title":"critical: time service",
 "message":"PPS not pulsing (last edge 12s ago); GNSS no fix",
 "host":"ntp-main","site":"main-lan","version":"0.1.0","time":"2026-09-21T12:00:00+00:00",
 "problems":[{"level":"critical","subsystem":"pps","message":"PPS not pulsing (last edge 12s ago)"}],
 "updates":{"pending":0,"security":0,"security_packages":[],"reboot_required":false}}
```

**n8n**: a Webhook node (POST) receiving that document, then whatever you
route to: an Email node, a Matrix or Discord node, a Grafana annotation.
`kind`, `level` and `host` are the fields to branch on.

**ntfy** (`format: ntfy`): point `webhook_url` at the ntfy server's base URL
(`https://ntfy.lab.example`) and set `ntfy_topic`; the payload uses ntfy's
JSON publish format with priority 5 for critical, 4 for warning, 3 for
recovery, and a bearer token if the topic is protected. Phones subscribed to
the topic get a push.

## Malware net: through the relay only

The isolated unit has no webhook and no scraper. Every value above is in the
RFC 5424 message it already sends to the segment's one-way relay, in the JSON
body (`health_level`, `security_updates_pending`, `reboot_required`,
`updates_checked_timestamp_seconds`, ...), and the syslog severity follows the
health level. Alert on those at the SIEM the relay feeds, the same way you
alert on anything else from that segment. Nothing new leaves the segment.

## Where Patch Gremlin fits

[Patch Gremlin](https://github.com/ChiefGyk3D/Patch-Gremlin) is the other
model: it installs updates with `unattended-upgrades` and reports what it
installed to Discord, Teams, Slack or Matrix. That is a good fit for a
main-LAN unit in `dev` mode, where the root is writable and outbound chat
webhooks are reachable. It is the wrong tool for an appliance: on a read-only
overlay every install vanishes at reboot, and on the malware net its
notifications cannot get out. Mother Ticker therefore only *reports* pending
updates and leaves installing them to the maintenance procedure in
`docs/offline-updates.md`, which is deliberate on a device other hosts trust
for time.
