#!/bin/sh
# Managed by Ansible (mother-ticker).
# Login shell for the TUI user, and the process the console service runs.
#   - Interactive login (ssh, tty1): run the TUI. If it exits with 10 the
#     operator chose "drop to a shell"; give them one, then return to the TUI.
#   - `ssh user@host <subcommand>`: allow only the read-only subcommands.
set -u

MT=/opt/mother-ticker/venv/bin/mother-ticker
export MOTHER_TICKER_CONFIG="${MOTHER_TICKER_CONFIG:-/etc/mother-ticker/config.toml}"

if [ "${1:-}" = "-c" ]; then
    # Non-interactive: sshd passes the remote command as $2.
    case "${2:-}" in
        status|metrics|"mother-ticker status"|"mother-ticker metrics")
            sub=${2#mother-ticker }
            exec "$MT" "$sub"
            ;;
        *)
            echo "mother-ticker: only 'status' and 'metrics' may be run non-interactively" >&2
            exit 64
            ;;
    esac
fi

while :; do
    "$MT" tui
    rc=$?
    if [ "$rc" -eq 10 ]; then
        echo
        echo "Mother Ticker: shell as $(id -un). Type 'exit' to return to the TUI."
        /bin/bash -l
        continue
    fi
    exit "$rc"
done
