#!/bin/sh
# Managed by Ansible (mother-ticker).
# Apply the GNSS constellation policy to the u-blox module via gpsd.
# Environment (from /etc/default/mother-ticker-gnss):
#   UBX_PROTVER    ubxtool -P value (18 for M8 firmware 3.01)
#   UBX_ENABLE     space-separated ubxtool names to enable, e.g. "GPS GALILEO"
#   UBX_DISABLE    space-separated names to disable, e.g. "GLONASS BEIDOU"
#   UBX_STATIONARY 1 to set the stationary dynamic model (better timing)
set -eu

PROTVER="${UBX_PROTVER:-18}"
ENABLE="${UBX_ENABLE:-GPS GALILEO}"
DISABLE="${UBX_DISABLE:-GLONASS BEIDOU}"
STATIONARY="${UBX_STATIONARY:-1}"
WAIT_S="${UBX_WAIT_S:-60}"

log() { printf 'mother-ticker-gnss-config: %s\n' "$*"; }

command -v ubxtool >/dev/null 2>&1 || { log "ubxtool not found"; exit 1; }
command -v gpspipe >/dev/null 2>&1 || { log "gpspipe not found"; exit 1; }

# Wait for gpsd to answer and to have a device.
i=0
until gpspipe -w -n 3 2>/dev/null | grep -q '"class":"DEVICES".*"path"'; do
    i=$((i + 1))
    if [ "$i" -ge "$WAIT_S" ]; then
        log "gpsd did not report a device within ${WAIT_S}s"
        exit 1
    fi
    sleep 1
done

ubx() {
    # ubxtool talks to gpsd on localhost:2947 by default and gpsd forwards to the receiver.
    if ! ubxtool -P "$PROTVER" -w 2 "$@" >/dev/null 2>&1; then
        log "ubxtool $* failed"
        return 1
    fi
}

rc=0
for gnss in $ENABLE; do ubx -e "$gnss" || rc=1; done
for gnss in $DISABLE; do ubx -d "$gnss" || rc=1; done
if [ "$STATIONARY" = "1" ]; then
    ubx -p MODEL,2 || rc=1
fi
# Persist to the receiver's battery-backed RAM and flash where present.
ubx -p SAVE || rc=1

if [ "$rc" -eq 0 ]; then
    log "applied: enable [$ENABLE] disable [$DISABLE] stationary=$STATIONARY"
else
    log "one or more commands failed; the receiver keeps its previous configuration"
fi
exit "$rc"
