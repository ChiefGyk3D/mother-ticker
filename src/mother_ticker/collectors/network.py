# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Interfaces and addresses from `ip -j addr`, iproute2's JSON output."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from mother_ticker.collectors.model import Address, Interface, NetworkStatus


def parse_ip_addr(data: list[dict[str, Any]]) -> NetworkStatus:
    interfaces: list[Interface] = []
    for raw in data:
        addrs: list[Address] = []
        for info in raw.get("addr_info", []):
            if not isinstance(info, dict) or "local" not in info:
                continue
            addrs.append(
                Address(
                    family=str(info.get("family", "")),
                    address=str(info["local"]),
                    prefix=int(info.get("prefixlen", 0)),
                    scope=str(info.get("scope", "")),
                )
            )
        interfaces.append(
            Interface(
                name=str(raw.get("ifname", "?")),
                state=str(raw.get("operstate", "UNKNOWN")),
                mac=raw.get("address"),
                mtu=raw.get("mtu"),
                addresses=tuple(addrs),
            )
        )
    return NetworkStatus(interfaces=tuple(interfaces))


def collect_network() -> NetworkStatus:
    try:
        result = subprocess.run(
            ["ip", "-j", "addr", "show"], capture_output=True, text=True, timeout=5, check=False
        )
        data = json.loads(result.stdout) if result.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        data = []
    if not isinstance(data, list):
        data = []
    return parse_ip_addr(data)
