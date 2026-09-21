# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Build a full Snapshot from the individual collectors."""

from __future__ import annotations

from mother_ticker.collectors.chrony import collect_chrony
from mother_ticker.collectors.gpsd import collect_gnss
from mother_ticker.collectors.model import NetworkStatus, Snapshot
from mother_ticker.collectors.network import collect_network
from mother_ticker.collectors.pps import collect_pps
from mother_ticker.collectors.services import collect_services
from mother_ticker.collectors.system import collect_system
from mother_ticker.collectors.updates import collect_updates
from mother_ticker.config import Config
from mother_ticker.version import __version__


def gather(
    config: Config, network: NetworkStatus | None = None, with_text: bool = False
) -> Snapshot:
    """Collect everything. `network` may be passed in to refresh it less often."""
    return Snapshot(
        taken_at=Snapshot.now(),
        site=config.site,
        version=__version__,
        gnss=collect_gnss(config.gpsd.host, config.gpsd.port, config.gpsd.timeout_s),
        chrony=collect_chrony(with_text=with_text),
        pps=collect_pps(config.pps.device, config.pps.stale_after_s),
        system=collect_system(),
        network=network if network is not None else collect_network(),
        services=collect_services(config.tui.services),
        updates=collect_updates(config.updates.state_path),
    )
