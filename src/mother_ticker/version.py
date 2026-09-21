# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Single source of truth for the running version.

The README badge, CHANGELOG headings and git tags must agree with this value.
`scripts/release.sh` bumps it and CI refuses a tag that does not match.
"""

__version__ = "0.1.0"

# Lifecycle label shown beside the version. "alpha" until a unit has run on real hardware
# and the README's verify-on-hardware items are closed; then "beta"; then "" at 1.0.
__status__ = "alpha"
