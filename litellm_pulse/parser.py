"""Prometheus text format parser."""

from __future__ import annotations

import re
from collections import defaultdict

_LINE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?:\{[^}]*\})?"
    r"\s+"
    r"(?P<value>[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)"
)

_LABELED_LINE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?:\{(?P<labels>[^}]*)\})?"
    r"\s+"
    r"(?P<value>[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)"
)

_LABEL_KV_RE = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def _unescape_label_value(value: str) -> str:
    """Unescape Prometheus label value backslash sequences."""
    return value.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n")


def parse_prometheus_text(text: str) -> dict[str, float]:
    """Parse Prometheus text exposition format and sum values per metric family.

    Labels are ignored — all samples sharing the same metric name are summed.
    This is useful for counter metrics where you want the grand total across
    all label combinations.

    Args:
        text: Raw Prometheus text exposition format string.

    Returns:
        Dict mapping metric names to their summed float values.
    """
    totals: dict[str, float] = defaultdict(float)

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE_RE.match(line)
        if match:
            totals[match.group("name")] += float(match.group("value"))

    return dict(totals)


def parse_prometheus_text_with_labels(
    text: str, label_key: str = "model"
) -> dict[str, dict[str, float]]:
    """Parse Prometheus text, extracting a single label value per sample.

    For each metric name, returns a dict mapping the extracted label's values
    to their summed values. Samples that lack the requested label are skipped.

    This is used for per-model tracking: only metrics carrying a ``model`` label
    are returned, grouped by model name.

    Args:
        text: Raw Prometheus text exposition format string.
        label_key: The label to extract (default: ``model``).

    Returns:
        Nested dict: ``{metric_name: {label_value: summed_value}}``.
    """
    result: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _LABELED_LINE_RE.match(line)
        if not match:
            continue
        labels_str = match.group("labels")
        if not labels_str:
            continue
        labels = {k: _unescape_label_value(v) for k, v in _LABEL_KV_RE.findall(labels_str)}
        label_val = labels.get(label_key)
        if label_val is None:
            continue
        result[match.group("name")][label_val] += float(match.group("value"))

    return {name: dict(vals) for name, vals in result.items()}
