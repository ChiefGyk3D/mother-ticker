# Contributing

Thanks for looking. This is an appliance project: changes are expected to be
small, tested, and documented in the same pull request.

## Development setup

```sh
git clone https://github.com/ChiefGyk3D/mother-ticker
cd mother-ticker
make venv          # .venv with the package, ruff, mypy, pytest, bandit, yamllint, ansible-lint
make check         # everything CI runs, minus the Python version matrix
```

`make check` runs ruff (lint and format), mypy in strict mode, pytest, bandit,
yamllint, ansible-lint with the production profile, shellcheck, and the
repository hygiene tests. Fix what it reports before opening a PR; CI runs the
same tools and will say the same thing.

You can run the TUI on any Linux machine without the hardware:

```sh
.venv/bin/mother-ticker tui            # gpsd and chronyd absent: every panel shows the fault
.venv/bin/mother-ticker status
.venv/bin/mother-ticker metrics
```

## Conventions

- **No em dashes.** Anywhere: code, comments, docs, commit messages, TUI text.
  `tests/test_repo_hygiene.py` fails on one. Use a comma, a colon, a
  parenthesis or a new sentence.
- **SPDX headers** on every Python and shell file:
  `SPDX-License-Identifier: AGPL-3.0-or-later` and the Renegade Penguin LLC
  copyright line. The hygiene test checks.
- **Type hints everywhere, `mypy --strict` clean.** No `Any` without a
  comment saying why.
- **`ruff format`** is enforced. Data tables that the formatter would reflow
  can be fenced with `# fmt: off` and a comment saying why.
- **Collectors are the only code that touches the system.** They return
  frozen dataclasses. Parsers are pure functions over text or JSON so they
  can be tested from fixtures. If you add a data source, add a fixture
  recorded from the real thing and a parser test.
- **One evaluation.** `health.evaluate` decides what is wrong. Do not add a
  second opinion in the TUI or the exporter; add a rule there and every
  consumer gets it.
- **Privileged actions** go through `sudo -n` with a fixed argument list and a
  matching sudoers line in the role. Never build a command from user input.
- **Ansible:** fully qualified module names, a `name` on every task, `mode` on
  every file, `changed_when` on every command, idempotent on re-run. Anything
  that needs a reboot notifies `Reboot required`; the role reboots once. A
  check that can fail must fail with a message naming what to look at.
- **Fail loudly.** A missing device, a missing tool, a service that is not
  active: assert and name the fix. Never `ignore_errors` a real check.
- Comments say *why*, especially where an obvious alternative is wrong
  (`cmdport 0`, socket activation, `dpkg -i`).

## Tests

- `tests/` is hermetic: no network beyond loopback (the conftest blocks it),
  no real gpsd, chronyd or sysfs. Fake daemons on loopback are fine.
- Every bug fix gets a regression test whose docstring states the failure it
  prevents.
- The TUI is tested with Textual's pilot in a headless terminal. If you change
  what the dashboard looks like, also run the render script in
  `tests/test_tui.py`'s style (or `textual run --dev`) and look at it; tests
  pass on things that are visibly wrong.
- A check must be falsifiable: break the thing it watches and confirm it goes
  red with a useful message before trusting it.

## Testing on hardware

The CI never sees a Pi. Before a release, and for any change to the role, the
GNSS script, chrony.conf or the boot configuration, run it on a real unit:

1. Deploy to a bench unit with `ansible-playbook site.yml -l <unit>`.
2. Confirm the verify step passes and the TUI reaches `ALL SYSTEMS NOMINAL`.
3. `chronyc sources -v` shows `#* PPS`; `ppstest /dev/pps0` shows pulses;
   `hwclock -r` agrees with `date`.
4. Reboot and confirm it all comes back without help.
5. For the isolated profile, also confirm the overlay: `mother-ticker-maint
   status`, then that a file created in `/etc` is gone after a reboot.

Say in the PR what you ran and on what OS image.

## Pull requests

- One logical change per PR. Small is good.
- Update `CHANGELOG.md` under *Unreleased* (Keep a Changelog headings: Added,
  Changed, Fixed, Removed, Security).
- Update the docs that describe what you changed: README for anything an
  operator sees, RUNBOOK for a new failure mode, ARCHITECTURE for a design
  change.
- CI must be green. The `all green` job is what branch protection watches;
  a new job must be added to its `needs` list or the hygiene test fails.
- Commit messages: imperative subject under 72 characters, a body that says
  why.

## Releases

Semantic Versioning. `src/mother_ticker/version.py` is the single source of
truth; the README badge and the CHANGELOG heading must agree, and the hygiene
test checks that they do.

```sh
scripts/release.sh 0.2.0     # bumps version.py and the badge, rolls Unreleased, commits, tags
git push origin main v0.2.0  # the release workflow verifies and publishes
```

The release workflow refuses a tag whose version does not match `version.py`
or has no CHANGELOG section, builds the sdist and wheel, and creates the
GitHub release with the CHANGELOG section as its notes.

Action pins in the workflows are commit SHAs with the version in a comment.
Resolve a new pin with `git ls-remote --tags https://github.com/<owner>/<repo>`;
never from memory.
