#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
#
# Mother Ticker on-box installer: provision THIS Raspberry Pi from a shell,
# no controller, no inventory to learn. It reads a small KEY=VALUE config,
# writes a one-host local inventory, installs ansible-core from apt if it is
# missing, and runs the same role a controller would. One implementation,
# two ways to drive it.
#
#   sudo scripts/install.sh --config install.conf            first run from a checkout
#   sudo mother-ticker-install                                re-run later (installed by the role)
#   sudo mother-ticker-install --site malware-net --tags chrony,firewall,app
#
# Flags override the config file. The effective config is saved to
# /etc/mother-ticker/install.conf so a bare re-run repeats the last install.
set -euo pipefail

CONF_DEFAULT=/etc/mother-ticker/install.conf
INV_DIR=/var/lib/mother-ticker/inventory
REPO_FALLBACK=/opt/mother-ticker/repo

usage() {
    cat <<'USAGE'
usage: mother-ticker-install [options]

  --config FILE            KEY=VALUE config (default /etc/mother-ticker/install.conf)
  --site main-lan|malware-net
  --mode appliance|dev     appliance (default): overlay, no apt timers, no TUI shell escape, may reboot
  --hostname NAME          --admin-user USER
  --ntp-allow "CIDR ..."   --mgmt-allow "CIDR ..."   --metrics-allow "CIDR ..."
  --metrics-port N         --upstream "host ..."
  --relay-host H --relay-port P --relay-transport tcp|udp --relay-framing newline|octet-counted
  --orphan-stratum N       --overlay auto|yes|no     --nts yes|no
  --nmea-offset SECONDS    --offline yes|no          --wheelhouse DIR
  --alert-webhook-url URL  --alert-format json|ntfy  --alert-ntfy-topic T  --alert-token-file F
  --alert-min-level warning|critical
  --extra-vars-file FILE   YAML with any mother_ticker_* variable
  --repo DIR               checkout to run from (default: this script's checkout, else /opt/mother-ticker/repo)
  --tags TAGS              run only these role tags (comma separated)
  --check                  Ansible check mode: report, change nothing
  --inventory-only DIR     write the inventory to DIR and exit (no Ansible)
  -h, --help
USAGE
}

die() { echo "mother-ticker-install: $*" >&2; exit 1; }

# Defaults (the role's defaults win for anything left empty).
SITE=main-lan MODE=appliance OVERLAY=auto NTS=no OFFLINE=no UPSTREAM_SET=0
ADMIN_USER='' HOSTNAME_SET='' NTP_ALLOW='' MGMT_ALLOW='' METRICS_ALLOW='' METRICS_PORT=''
UPSTREAM_NTP='' RELAY_HOST='' RELAY_PORT='' RELAY_TRANSPORT='' RELAY_FRAMING='' ORPHAN_STRATUM=''
NMEA_OFFSET='' WHEELHOUSE='' EXTRA_VARS_FILE='' REPO_DIR=''
ALERT_WEBHOOK_URL='' ALERT_FORMAT='' ALERT_NTFY_TOPIC='' ALERT_TOKEN_FILE='' ALERT_MIN_LEVEL=''

load_conf() {
    # Only plain KEY=VALUE lines with a known key are honoured; nothing is executed.
    local file=$1 line key val
    [[ -r $file ]] || die "cannot read config $file"
    while IFS= read -r line || [[ -n $line ]]; do
        line=${line%%#*}
        line=${line//$'\r'/}
        [[ $line =~ ^[[:space:]]*([A-Z][A-Z0-9_]*)[[:space:]]*=[[:space:]]*(.*)$ ]] || continue
        key=${BASH_REMATCH[1]}
        val=${BASH_REMATCH[2]}
        val=${val%"${val##*[![:space:]]}"}
        val=${val#\"}; val=${val%\"}
        val=${val#\'}; val=${val%\'}
        case $key in
            SITE) SITE=$val ;;
            MODE) MODE=$val ;;
            ADMIN_USER) ADMIN_USER=$val ;;
            HOSTNAME) HOSTNAME_SET=$val ;;
            NTP_ALLOW) NTP_ALLOW=$val ;;
            MGMT_ALLOW) MGMT_ALLOW=$val ;;
            METRICS_ALLOW) METRICS_ALLOW=$val ;;
            METRICS_PORT) METRICS_PORT=$val ;;
            UPSTREAM_NTP) UPSTREAM_NTP=$val; UPSTREAM_SET=1 ;;
            RELAY_HOST) RELAY_HOST=$val ;;
            RELAY_PORT) RELAY_PORT=$val ;;
            RELAY_TRANSPORT) RELAY_TRANSPORT=$val ;;
            RELAY_FRAMING) RELAY_FRAMING=$val ;;
            ORPHAN_STRATUM) ORPHAN_STRATUM=$val ;;
            OVERLAY) OVERLAY=$val ;;
            NMEA_OFFSET) NMEA_OFFSET=$val ;;
            NTS) NTS=$val ;;
            OFFLINE) OFFLINE=$val ;;
            WHEELHOUSE) WHEELHOUSE=$val ;;
            EXTRA_VARS_FILE) EXTRA_VARS_FILE=$val ;;
            REPO_DIR) REPO_DIR=$val ;;
            ALERT_WEBHOOK_URL) ALERT_WEBHOOK_URL=$val ;;
            ALERT_FORMAT) ALERT_FORMAT=$val ;;
            ALERT_NTFY_TOPIC) ALERT_NTFY_TOPIC=$val ;;
            ALERT_TOKEN_FILE) ALERT_TOKEN_FILE=$val ;;
            ALERT_MIN_LEVEL) ALERT_MIN_LEVEL=$val ;;
            *) echo "mother-ticker-install: ignoring unknown key $key in $file" >&2 ;;
        esac
    done < "$file"
}

conf_file=
tags='' check=0 inventory_only=''
# First pass: find --config so flags can override it.
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    if [[ ${args[$i]} == --config ]]; then conf_file=${args[$((i + 1))]:-}; fi
done
if [[ -z $conf_file && -r $CONF_DEFAULT ]]; then conf_file=$CONF_DEFAULT; fi
[[ -n $conf_file ]] && load_conf "$conf_file"

while [[ $# -gt 0 ]]; do
    case $1 in
        --config) shift ;;
        --site) SITE=$2; shift ;;
        --mode) MODE=$2; shift ;;
        --hostname) HOSTNAME_SET=$2; shift ;;
        --admin-user) ADMIN_USER=$2; shift ;;
        --ntp-allow) NTP_ALLOW=$2; shift ;;
        --mgmt-allow) MGMT_ALLOW=$2; shift ;;
        --metrics-allow) METRICS_ALLOW=$2; shift ;;
        --metrics-port) METRICS_PORT=$2; shift ;;
        --upstream) UPSTREAM_NTP=$2; UPSTREAM_SET=1; shift ;;
        --relay-host) RELAY_HOST=$2; shift ;;
        --relay-port) RELAY_PORT=$2; shift ;;
        --relay-transport) RELAY_TRANSPORT=$2; shift ;;
        --relay-framing) RELAY_FRAMING=$2; shift ;;
        --orphan-stratum) ORPHAN_STRATUM=$2; shift ;;
        --overlay) OVERLAY=$2; shift ;;
        --nts) NTS=$2; shift ;;
        --nmea-offset) NMEA_OFFSET=$2; shift ;;
        --offline) OFFLINE=$2; shift ;;
        --wheelhouse) WHEELHOUSE=$2; shift ;;
        --extra-vars-file) EXTRA_VARS_FILE=$2; shift ;;
        --repo) REPO_DIR=$2; shift ;;
        --alert-webhook-url) ALERT_WEBHOOK_URL=$2; shift ;;
        --alert-format) ALERT_FORMAT=$2; shift ;;
        --alert-ntfy-topic) ALERT_NTFY_TOPIC=$2; shift ;;
        --alert-token-file) ALERT_TOKEN_FILE=$2; shift ;;
        --alert-min-level) ALERT_MIN_LEVEL=$2; shift ;;
        --tags) tags=$2; shift ;;
        --check) check=1 ;;
        --inventory-only) inventory_only=$2; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown option $1" ;;
    esac
    shift
done

# Validate the few things that would otherwise fail late and confusingly.
case $SITE in main-lan|malware-net) ;; *) die "SITE must be main-lan or malware-net (got '$SITE')" ;; esac
case $MODE in appliance|dev) ;; *) die "MODE must be appliance or dev (got '$MODE')" ;; esac
case $OVERLAY in auto|yes|no) ;; *) die "OVERLAY must be auto, yes or no" ;; esac
case $NTS in yes|no) ;; *) die "NTS must be yes or no" ;; esac
case $OFFLINE in yes|no) ;; *) die "OFFLINE must be yes or no" ;; esac
[[ -n $ADMIN_USER ]] || die "ADMIN_USER is required (the account Raspberry Pi Imager created)"
if [[ -n $RELAY_TRANSPORT ]]; then case $RELAY_TRANSPORT in tcp|udp) ;; *) die "RELAY_TRANSPORT must be tcp or udp" ;; esac; fi
if [[ -n $RELAY_FRAMING ]]; then case $RELAY_FRAMING in newline|octet-counted) ;; *) die "RELAY_FRAMING must be newline or octet-counted" ;; esac; fi
if [[ -n $ALERT_FORMAT ]]; then case $ALERT_FORMAT in json|ntfy) ;; *) die "ALERT_FORMAT must be json or ntfy" ;; esac; fi
if [[ -n $ALERT_MIN_LEVEL ]]; then case $ALERT_MIN_LEVEL in warning|critical) ;; *) die "ALERT_MIN_LEVEL must be warning or critical" ;; esac; fi
if [[ -n $EXTRA_VARS_FILE && ! -r $EXTRA_VARS_FILE ]]; then die "EXTRA_VARS_FILE $EXTRA_VARS_FILE is not readable"; fi
if [[ $SITE == malware-net && $UPSTREAM_SET -eq 0 ]]; then UPSTREAM_NTP=; UPSTREAM_SET=1; fi

hostname_value=${HOSTNAME_SET:-$(hostname)}

yaml_list() {
    # "a b c" -> ["a", "b", "c"]; empty -> []
    local out="[" first=1 item
    for item in $1; do
        if [[ $first -eq 0 ]]; then out+=", "; fi
        out+="\"$item\""; first=0
    done
    printf '%s]' "$out"
}

write_inventory() {
    local dir=$1
    mkdir -p "$dir"
    {
        echo "---"
        echo "# Written by mother-ticker-install. Edit /etc/mother-ticker/install.conf instead."
        echo "mother_ticker:"
        echo "  hosts:"
        echo "    $hostname_value:"
        echo "      ansible_connection: local"
        echo "      ansible_python_interpreter: /usr/bin/python3"
        echo "      mother_ticker_site: $SITE"
        echo "      mother_ticker_mode: $MODE"
        echo "      mother_ticker_hostname: $hostname_value"
        echo "      mother_ticker_admin_user: $ADMIN_USER"
        [[ -n $NTP_ALLOW ]] && echo "      mother_ticker_ntp_allow: $(yaml_list "$NTP_ALLOW")"
        [[ -n $MGMT_ALLOW ]] && echo "      mother_ticker_mgmt_allow: $(yaml_list "$MGMT_ALLOW")"
        [[ -n $METRICS_ALLOW ]] && echo "      mother_ticker_metrics_allow: $(yaml_list "$METRICS_ALLOW")"
        [[ -n $METRICS_PORT ]] && echo "      mother_ticker_metrics_port: $METRICS_PORT"
        [[ $UPSTREAM_SET -eq 1 ]] && echo "      mother_ticker_upstream_ntp: $(yaml_list "$UPSTREAM_NTP")"
        [[ -n $RELAY_HOST ]] && echo "      mother_ticker_syslog_host: $RELAY_HOST"
        [[ -n $RELAY_PORT ]] && echo "      mother_ticker_syslog_port: $RELAY_PORT"
        [[ -n $RELAY_TRANSPORT ]] && echo "      mother_ticker_syslog_transport: $RELAY_TRANSPORT"
        [[ -n $RELAY_FRAMING ]] && echo "      mother_ticker_syslog_framing: $RELAY_FRAMING"
        [[ -n $ORPHAN_STRATUM ]] && echo "      mother_ticker_orphan_stratum: $ORPHAN_STRATUM"
        [[ $OVERLAY == yes ]] && echo "      mother_ticker_overlay: true"
        [[ $OVERLAY == no ]] && echo "      mother_ticker_overlay: false"
        [[ -n $NMEA_OFFSET ]] && echo "      mother_ticker_nmea_offset: $NMEA_OFFSET"
        [[ $NTS == yes ]] && echo "      mother_ticker_nts_enabled: true"
        [[ $OFFLINE == yes ]] && echo "      mother_ticker_offline: true"
        [[ -n $WHEELHOUSE ]] && echo "      mother_ticker_wheelhouse: \"$WHEELHOUSE\""
        [[ -n $ALERT_WEBHOOK_URL ]] && echo "      mother_ticker_alert_webhook_url: \"$ALERT_WEBHOOK_URL\""
        [[ -n $ALERT_FORMAT ]] && echo "      mother_ticker_alert_format: $ALERT_FORMAT"
        [[ -n $ALERT_NTFY_TOPIC ]] && echo "      mother_ticker_alert_ntfy_topic: \"$ALERT_NTFY_TOPIC\""
        [[ -n $ALERT_TOKEN_FILE ]] && echo "      mother_ticker_alert_token_file: \"$ALERT_TOKEN_FILE\""
        [[ -n $ALERT_MIN_LEVEL ]] && echo "      mother_ticker_alert_min_level: $ALERT_MIN_LEVEL"
        true
    } > "$dir/hosts.yml"
}

if [[ -n $inventory_only ]]; then
    write_inventory "$inventory_only"
    echo "$inventory_only/hosts.yml"
    exit 0
fi

[[ $EUID -eq 0 ]] || die "run as root: sudo $0 ..."

# Locate the checkout: the one this script lives in, else the copy the role keeps.
script_dir=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
if [[ -z $REPO_DIR ]]; then
    if [[ -f $script_dir/../ansible/site.yml ]]; then REPO_DIR=$(cd "$script_dir/.." && pwd)
    elif [[ -f $REPO_FALLBACK/ansible/site.yml ]]; then REPO_DIR=$REPO_FALLBACK
    fi
fi
[[ -f $REPO_DIR/ansible/site.yml ]] || die "no checkout found; pass --repo DIR (a clone of the repository)"


if ! command -v ansible-playbook >/dev/null 2>&1; then
    if [[ $OFFLINE == yes ]]; then
        die "ansible-core is not installed and OFFLINE=yes; install it from your bundle first"
    fi
    echo "==> installing ansible-core from apt"
    apt-get update -q
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q ansible-core
fi

write_inventory "$INV_DIR"

# Save the effective configuration for bare re-runs.
mkdir -p "$(dirname "$CONF_DEFAULT")"
if [[ $conf_file != "$CONF_DEFAULT" ]]; then
    {
        echo "# Written by mother-ticker-install $(date -u +%Y-%m-%dT%H:%M:%SZ). Edit and re-run: sudo mother-ticker-install"
        echo "SITE=$SITE"
        echo "MODE=$MODE"
        echo "ADMIN_USER=$ADMIN_USER"
        echo "HOSTNAME=$HOSTNAME_SET"
        echo "NTP_ALLOW=\"$NTP_ALLOW\""
        echo "MGMT_ALLOW=\"$MGMT_ALLOW\""
        echo "METRICS_ALLOW=\"$METRICS_ALLOW\""
        echo "METRICS_PORT=$METRICS_PORT"
        echo "UPSTREAM_NTP=\"$UPSTREAM_NTP\""
        echo "RELAY_HOST=$RELAY_HOST"
        echo "RELAY_PORT=$RELAY_PORT"
        echo "RELAY_TRANSPORT=$RELAY_TRANSPORT"
        echo "RELAY_FRAMING=$RELAY_FRAMING"
        echo "ORPHAN_STRATUM=$ORPHAN_STRATUM"
        echo "OVERLAY=$OVERLAY"
        echo "NMEA_OFFSET=$NMEA_OFFSET"
        echo "NTS=$NTS"
        echo "OFFLINE=$OFFLINE"
        echo "ALERT_WEBHOOK_URL=$ALERT_WEBHOOK_URL"
        echo "ALERT_FORMAT=$ALERT_FORMAT"
        echo "ALERT_NTFY_TOPIC=$ALERT_NTFY_TOPIC"
        echo "ALERT_TOKEN_FILE=$ALERT_TOKEN_FILE"
        echo "ALERT_MIN_LEVEL=$ALERT_MIN_LEVEL"
        echo "WHEELHOUSE=$WHEELHOUSE"
        echo "EXTRA_VARS_FILE=$EXTRA_VARS_FILE"
        echo "REPO_DIR=$REPO_DIR"
    } > "$CONF_DEFAULT"
    chmod 0644 "$CONF_DEFAULT"
fi

cmd=(ansible-playbook -i "$INV_DIR/hosts.yml" -c local site.yml)
[[ -n $tags ]] && cmd+=(-t "$tags")
[[ $check -eq 1 ]] && cmd+=(--check --diff)
[[ -n $EXTRA_VARS_FILE ]] && cmd+=(-e "@$EXTRA_VARS_FILE")

echo "==> site=$SITE mode=$MODE host=$hostname_value repo=$REPO_DIR"
echo "==> ${cmd[*]}"
cd "$REPO_DIR/ansible"
export ANSIBLE_CONFIG="$REPO_DIR/ansible/ansible.cfg"
exec "${cmd[@]}"
