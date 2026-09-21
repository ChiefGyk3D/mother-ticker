# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""The shipped Grafana rules and dashboard against the metrics the exporter really emits.

A rule that names a metric nobody serves never fires and nobody notices;
a dashboard panel that does is a blank square. Both files are checked
against `snapshot_to_metrics` on a full snapshot plus the three counters
the exporter adds itself, and the rule names in docs/alerts.md are checked
against the rule file so the prose cannot drift from the data.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from mother_ticker.collectors.updates import UpdatesStatus
from mother_ticker.config import Thresholds
from mother_ticker.health.evaluate import evaluate
from mother_ticker.metrics.model import snapshot_to_metrics
from tests.conftest import make_snapshot

ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / "grafana" / "rules" / "mother-ticker.rules.yml"
DASHBOARD = ROOT / "grafana" / "dashboards" / "mother-ticker.json"
PROVISIONING = ROOT / "grafana" / "provisioning" / "dashboards" / "mother-ticker.yaml"
GENERATOR = ROOT / "scripts" / "gen_grafana_dashboard.py"
ALERTS_DOC = ROOT / "docs" / "alerts.md"

# Added by `mother-ticker exporter` around the snapshot metrics; see cli.py.
EXPORTER_METRICS = {
    "mother_ticker_alerts_sent",
    "mother_ticker_alerts_failed",
    "mother_ticker_relay_send_failures",
}
METRIC_RE = re.compile(r"\bmother_ticker_[a-z0-9_]+")


def served_metrics() -> set[str]:
    """Every metric family a healthy, fully populated unit exposes."""
    snap = replace(
        make_snapshot(),
        updates=UpdatesStatus(checked_at=1.0, pending=1, security=1, reboot_required=True),
    )
    names = {m.name for m in snapshot_to_metrics(snap, evaluate(snap, Thresholds()))}
    return names | EXPORTER_METRICS


def test_exporter_metric_list_matches_the_code() -> None:
    cli = (ROOT / "src" / "mother_ticker" / "cli.py").read_text(encoding="utf-8")
    assert {m for m in METRIC_RE.findall(cli)} == EXPORTER_METRICS


@pytest.fixture(scope="module")
def rules() -> list[dict[str, object]]:
    data = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    groups = data["groups"]
    assert [g["name"] for g in groups] == ["mother-ticker"]
    out: list[dict[str, object]] = groups[0]["rules"]
    return out


@pytest.fixture(scope="module")
def dashboard() -> dict[str, object]:
    data: dict[str, object] = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    return data


class TestRules:
    def test_every_metric_named_is_served(self, rules: list[dict[str, object]]) -> None:
        served = served_metrics()
        unknown = {
            f"{r['alert']}: {name}"
            for r in rules
            for name in METRIC_RE.findall(str(r["expr"]))
            if name not in served
        }
        assert not unknown, f"rules name metrics the exporter does not serve: {sorted(unknown)}"

    def test_every_rule_is_complete(self, rules: list[dict[str, object]]) -> None:
        names = [str(r["alert"]) for r in rules]
        assert len(names) == len(set(names)), "duplicate rule names"
        for rule in rules:
            name = rule["alert"]
            assert str(name).startswith("MotherTicker"), name
            assert "for" in rule, f"{name} has no for:"
            labels = rule["labels"]
            assert isinstance(labels, dict)
            assert labels.get("severity") in ("warning", "critical"), f"{name} severity"
            annotations = rule["annotations"]
            assert isinstance(annotations, dict) and annotations.get("summary"), f"{name} summary"

    def test_scrape_down_is_the_one_rule_not_on_our_metrics(
        self, rules: list[dict[str, object]]
    ) -> None:
        without = [r["alert"] for r in rules if not METRIC_RE.search(str(r["expr"]))]
        assert without == ["MotherTickerScrapeDown"]
        down = next(r for r in rules if r["alert"] == "MotherTickerScrapeDown")
        assert 'up{job="mother-ticker"} == 0' in str(down["expr"])

    def test_docs_list_exactly_these_rules(self, rules: list[dict[str, object]]) -> None:
        doc = ALERTS_DOC.read_text(encoding="utf-8")
        in_doc = set(re.findall(r"`(MotherTicker[A-Za-z0-9]+)`", doc))
        in_file = {str(r["alert"]) for r in rules}
        assert in_doc == in_file, (
            f"docs/alerts.md and the rule file disagree; "
            f"only in docs {sorted(in_doc - in_file)}, only in file {sorted(in_file - in_doc)}"
        )


class TestDashboard:
    def test_generated_file_is_current(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [sys.executable, str(GENERATOR), "--check"], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr
        stale = tmp_path / "stale.json"
        stale.write_text("{}")
        result = subprocess.run(
            [sys.executable, str(GENERATOR), "--check", "--output", str(stale)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1 and "stale" in result.stderr
        assert stale.read_text() == "{}", "--check must not write"

    def test_every_metric_named_is_served(self, dashboard: dict[str, object]) -> None:
        served = served_metrics()
        text = json.dumps(dashboard)
        unknown = {n for n in METRIC_RE.findall(text) if n not in served}
        assert not unknown, f"dashboard names metrics the exporter does not serve: {unknown}"

    def test_every_panel_queries_through_the_variable(self, dashboard: dict[str, object]) -> None:
        panels = dashboard["panels"]
        assert isinstance(panels, list) and len(panels) >= 12
        seen: set[tuple[int, int]] = set()
        for panel in panels:
            assert panel["datasource"] == {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}
            assert panel["targets"], panel["title"]
            for target in panel["targets"]:
                assert "mother_ticker_" in target["expr"], panel["title"]
                assert "$site" in target["expr"] and "$instance" in target["expr"], panel["title"]
            pos = panel["gridPos"]
            assert pos["x"] >= 0 and pos["x"] + pos["w"] <= 24, panel["title"]
            assert (pos["x"], pos["y"]) not in seen, f"{panel['title']} overlaps"
            seen.add((pos["x"], pos["y"]))

    def test_variables_and_identity(self, dashboard: dict[str, object]) -> None:
        assert dashboard["uid"] == "mother-ticker"
        templating = dashboard["templating"]
        assert isinstance(templating, dict)
        names = [v["name"] for v in templating["list"]]
        assert names == ["DS_PROMETHEUS", "site", "instance"]
        assert dashboard["timezone"] == "utc"
        assert chr(0x2014) not in json.dumps(dashboard)


def test_provisioning_points_at_the_dashboard_folder() -> None:
    data = yaml.safe_load(PROVISIONING.read_text(encoding="utf-8"))
    assert data["apiVersion"] == 1
    provider = data["providers"][0]
    assert provider["type"] == "file"
    assert provider["options"]["path"].endswith("mother-ticker")
    assert provider["allowUiUpdates"] is False, (
        "the JSON is generated; edits in the UI would be lost"
    )
