# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Bench tools: stand-ins for hardware the unit does not have yet.

Nothing in this package runs on a unit in service. It exists so the rest of
the appliance can be exercised end to end on a bare Pi or a laptop, with the
real collectors talking to a pretend receiver instead of fabricated
snapshots (that is what `tui --demo` does, one layer up).
"""
