#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
#
# Prepare the machine you deploy FROM (a laptop or admin box, not the Pi):
# install Ansible into a virtualenv, create the private inventory from the
# example, and check that the units answer. Safe to re-run.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"

if ! command -v python3 >/dev/null; then
    echo "python3 is required" >&2
    exit 1
fi

if [[ ! -x .venv/bin/ansible-playbook ]]; then
    echo "==> creating .venv with Ansible"
    python3 -m venv .venv
    .venv/bin/pip install --quiet --upgrade pip
    .venv/bin/pip install --quiet "ansible-core>=2.16" ansible-lint
fi

if [[ ! -d ansible/inventory/local ]]; then
    echo "==> creating ansible/inventory/local from the example (gitignored)"
    cp -r ansible/inventory/example ansible/inventory/local
    echo "    edit ansible/inventory/local/hosts.yml, group_vars/ and host_vars/ with your real values"
fi

echo "==> checking that the inventory hosts answer (ssh key auth, sudo)"
(cd ansible && ../.venv/bin/ansible -i inventory/local mother_ticker -m ping) || {
    echo
    echo "A host did not answer. Check ansible_host, ansible_user, and that your SSH key is authorized." >&2
    exit 1
}

cat <<EOT

Ready. Deploy with:
  cd ansible
  ../.venv/bin/ansible-playbook site.yml -l ntp-main
  ../.venv/bin/ansible-playbook site.yml -l ntp-malware
EOT
