#!/bin/sh
# Managed by Ansible (mother-ticker).
# Maintenance mode for the read-only overlay root (raspi-config overlayfs).
#   on      make the root and boot filesystems writable (for updates); reboots
#   off     re-enable the read-only overlay; reboots
#   status  print the current and configured state
# Add --no-reboot to skip the reboot (Ansible does this and reboots itself).
set -eu

usage() { echo "usage: $0 on|off|status [--no-reboot]" >&2; exit 64; }

[ "$#" -ge 1 ] || usage
action=$1
shift
do_reboot=1
for arg in "$@"; do
    case "$arg" in
        --no-reboot) do_reboot=0 ;;
        *) usage ;;
    esac
done

RC=/usr/bin/raspi-config
[ -x "$RC" ] || { echo "raspi-config not found; overlay management needs Raspberry Pi OS" >&2; exit 1; }

configured() {
    # 0 when the next boot uses the overlay
    grep -q 'boot=overlay' /boot/firmware/cmdline.txt 2>/dev/null
}
running() {
    grep -q 'boot=overlay' /proc/cmdline
}

case "$action" in
    status)
        if running; then echo "running: read-only overlay"; else echo "running: read-write"; fi
        if configured; then echo "next boot: read-only overlay"; else echo "next boot: read-write"; fi
        exit 0
        ;;
    on)
        if ! configured && ! running; then
            echo "already read-write; nothing to do"
            exit 0
        fi
        # Boot partition first: raspi-config needs to edit cmdline.txt.
        "$RC" nonint disable_bootro
        "$RC" nonint disable_overlayfs
        echo "maintenance mode ON: next boot is read-write"
        ;;
    off)
        if configured; then
            echo "already configured for the overlay; nothing to do"
            exit 0
        fi
        "$RC" nonint enable_overlayfs
        "$RC" nonint enable_bootro
        echo "maintenance mode OFF: next boot is a read-only overlay"
        ;;
    *) usage ;;
esac

if [ "$do_reboot" -eq 1 ]; then
    echo "rebooting in 5 seconds"
    sleep 5
    systemctl reboot
else
    echo "reboot required"
fi
