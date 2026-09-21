# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Metrics: one model, two writers.

main-lan serves Prometheus text on an HTTP port for the existing scrape stack.
malware-net formats the same values as an RFC 5424 syslog message with a JSON
body and sends it to the segment's one-way relay. Field names are identical in
both, so a Grafana panel and a SIEM search speak the same vocabulary.
"""
