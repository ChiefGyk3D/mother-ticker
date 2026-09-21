# Network Time Security (NTS)

NTS (RFC 8915) lets a client authenticate the time it gets from this server.
It is optional in Mother Ticker, off by default, and worth turning on where
your clients support it: chrony (3.5+), ntpsec, and a few embedded stacks.
systemd-timesyncd and most routers do not; they keep using plain NTP, which
the server continues to offer.

What NTS needs from you is a TLS certificate the clients trust. On a LAN that
means an internal CA. This page walks through one with `openssl`; adapt it if
you already run a CA (step-ca, Vault, an AD CS).

## How it works, briefly

The client connects once to the NTS-KE port (TCP 4460) over TLS, checks the
server's certificate against a CA it trusts, and receives cookies. From then
on ordinary NTP packets on UDP 123 carry an authenticated extension. If
someone alters a packet in flight the client discards it. Nothing about the
time itself changes; PPS still disciplines the server.

A subtlety worth knowing: the client must validate the certificate's validity
period, so a client whose own clock is badly wrong cannot bootstrap NTS. The
RTC on these units keeps the server side sane; on clients, chrony has
`nocerttimecheck` for the first sync if you need it.

## 1. Create an internal CA (once, on an admin machine)

```sh
mkdir -p ~/lab-ca && cd ~/lab-ca
openssl genrsa -out ca.key 4096
openssl req -x509 -new -key ca.key -sha256 -days 3650 -subj "/CN=Lab Time CA" -out ca.crt
chmod 600 ca.key
```

Keep `ca.key` off the units. `ca.crt` goes to every client.

## 2. Issue the server certificate

The name in the certificate must be the name clients use to reach the unit.
Put every such name in the SAN list:

```sh
cat > ntp-main.cnf <<'CNF'
[req]
distinguished_name = dn
req_extensions = ext
prompt = no
[dn]
CN = ntp-main.lab.example
[ext]
subjectAltName = DNS:ntp-main.lab.example, DNS:ntp-main, IP:192.0.2.20
extendedKeyUsage = serverAuth
CNF
openssl genrsa -out ntp-main.key 2048
openssl req -new -key ntp-main.key -config ntp-main.cnf -out ntp-main.csr
openssl x509 -req -in ntp-main.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -days 825 -sha256 -extfile ntp-main.cnf -extensions ext -out ntp-main.crt
```

## 3. Install the files on the unit

chrony reads the certificate and key as `_chrony`, so:

```sh
scp ntp-main.crt ntp-main.key admin@ntp-main:/tmp/
ssh admin@ntp-main 'sudo install -d -m 750 -o root -g _chrony /etc/mother-ticker/nts &&
  sudo install -m 640 -o root -g _chrony /tmp/ntp-main.crt /etc/mother-ticker/nts/server.crt &&
  sudo install -m 640 -o root -g _chrony /tmp/ntp-main.key /etc/mother-ticker/nts/server.key &&
  shred -u /tmp/ntp-main.key'
```

On the isolated unit, do this in maintenance mode (overlay off) or the files
vanish at the next reboot.

## 4. Turn it on

In the unit's host_vars:

```yaml
mother_ticker_nts_enabled: true
```

Then `ansible-playbook site.yml -l ntp-main -t chrony,firewall`. The role
checks that both files exist before touching chrony.conf, adds
`ntsservercert`, `ntsserverkey` and `ntsport`, and opens TCP 4460 to the same
subnets that may query NTP.

Check on the unit:

```sh
chronyc serverstats          # "NTS-KE connections accepted" appears
sudo ss -ltnp | grep 4460
```

## 5. Configure a client (chrony)

```
# /etc/chrony/conf.d/mother-ticker.conf on the client
server ntp-main.lab.example iburst nts
ntstrustedcerts /usr/local/share/ca-certificates/lab-time-ca.crt
```

Then `systemctl restart chrony` and:

```sh
chronyc -N authdata           # Mode NTS, KeyID and cookies present
chronyc sources               # the server shows up as usual
```

For ntpsec: `server ntp-main.lab.example nts ca /path/to/ca.crt`.

## Renewal

Certificates expire. Put the expiry in your calendar, or better, script the
reissue and rerun step 3. chrony picks up new files on restart; `-t chrony`
restarts it. `ntsdumpdir` is set so clients' cookies survive a chronyd
restart without a new key exchange.

## On the malware net

Everything above works there too; the CA and the certificate are files, not
network services. Distribute `ca.crt` to the segment's clients the same way
you distribute anything else there. There is no ACME and no OCSP involved, by
design.
