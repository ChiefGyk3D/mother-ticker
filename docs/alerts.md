# One-way alerting

A unit may sit on a segment you cannot reach, so it has to tell you when it
needs attention without anyone logging in, and it must never open a path back
in. Everything here is outbound from the unit and read-only for the unit.

## What is reported

| Condition | Level | Where it shows |
|---|---|---|
| PPS gone, no fix, chrony unsynchronised, gpsd or chronyd down | critical | banner, `mother_ticker_health_level`=2, syslog severity 2, webhook |
| GPS lost and serving stratum 2, SoC warm, few satellites, offset drift | warning | banner, level 1, syslog severity 4, webhook |
| Security updates pending, or a reboot required to finish an update | alert only | `mother_ticker_security_updates_pending`, `mother_ticker_reboot_required`, webhook; on screen just "updates available" or "reboot required" |
| Other updates pending | alert only | `mother_ticker_updates_pending`; on screen "updates available" |
| Update check never ran or its lists are stale | alert only | `mother_ticker_updates_checked`, `mother_ticker_updates_checked_timestamp_seconds`; counts on the system screen |

Pending updates never touch the banner. The dashboard is for the time
service; updates are a routine chore and belong in the alerting paths below,
with at most two words on the host panel.

The daily check (`mother-ticker-updates.timer`, `mother-ticker check-updates`)
installs nothing. It runs `apt-get update` where the network allows, then
`apt-get -s dist-upgrade`, counts the `Inst` lines and those from a
`-security` suite, and writes `/var/lib/mother-ticker/updates.json`. On the
isolated unit `apt-get update` fails and the count comes from the lists the
unit has, which is stated on the system screen. Run it by hand with
`sudo mother-ticker check-updates` (`--no-refresh` to skip the fetch).

## Main LAN: Grafana alert rules on the scraped metrics (start here)

The exporter serves `/metrics` on 9101 to the Prometheus scraper. This is
the path that needs nothing new on the unit. The rules ship as a file,
`grafana/rules/mother-ticker.rules.yml`, in Prometheus rule format: load it
into Prometheus, a ruler, or Grafana 11.4+ with *Import to Grafana-managed
rules*. `grafana/README.md` has the loading steps and the scrape config;
`grafana/dashboards/mother-ticker.json` is the matching dashboard. Grafana's
contact points then deliver by email, Slack, Discord, Matrix, ntfy, PagerDuty
or a webhook, so email lives in Grafana, not on the unit.

| Rule | Fires when | Severity | Then |
|---|---|---|---|
| `MotherTickerScrapeDown` | `up` is 0 for 5 min | critical | Power, network, the scraper's address in `mother_ticker_metrics_allow`, the exporter service |
| `MotherTickerCritical` | health level 2 for 2 min | critical | The banner is red; `RUNBOOK.md` top to bottom |
| `MotherTickerWarning` | health level 1 for 15 min | warning | Fallback source, few satellites, drift or a warm SoC |
| `MotherTickerPpsSilent` | PPS device present, no edge for 3 min | critical | Receiver, antenna, timepulse pin; `RUNBOOK.md` PPS not pulsing |
| `MotherTickerNoFix` | gpsd answers, fix below 3D for 10 min | warning | Sky view, antenna, cold start; `RUNBOOK.md` Lost GPS fix |
| `MotherTickerNotStratum1` | synchronised but not stratum 1 for 15 min | warning | Serving from an upstream server or the orphan stratum |
| `MotherTickerOffset` | more than 1 ms from the reference for 10 min | warning | Which source is selected; NMEA offset |
| `MotherTickerHot` | SoC above 75 C for 10 min | warning | Airflow, case, room |
| `MotherTickerServiceRestarting` | 3 or more restarts of a unit in an hour | warning | The ladder or a crash loop; `journalctl -u` the unit |
| `MotherTickerSecurityUpdates` | security updates pending for 1 h | warning | `docs/offline-updates.md`; the system screen lists them |
| `MotherTickerRebootRequired` | `/run/reboot-required` for 1 h | warning | Reboot at a quiet moment |
| `MotherTickerUpdateCheckStale` | no update check for 3 days | warning | `mother-ticker-updates.timer` |
| `MotherTickerAlertsFailing` | a webhook send failed in the last hour | warning | URL, token file, the receiver |

`MotherTickerScrapeDown` is the one that fires when the unit itself is gone,
which none of the unit's own signals can say. The test suite checks that
every metric a rule names is one the exporter serves, and that this table
and the file name the same rules.

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

**ntfy** (`format: ntfy`): point `webhook_url` at the ntfy server's base URL
(`https://ntfy.lab.example`) and set `ntfy_topic`; the payload uses ntfy's
JSON publish format with priority 5 for critical, 4 for warning, 3 for
recovery, and a bearer token if the topic is protected. Phones subscribed to
the topic get a push. This is the shortest route from a unit to a pocket.

**JSON shape** (`format: json`), one document per event, for n8n, a Grafana
webhook contact point, Home Assistant or anything else that takes JSON:

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
