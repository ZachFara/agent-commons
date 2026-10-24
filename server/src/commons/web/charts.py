"""Chart geometry for the dashboard. Templates draw the SVG; this computes where things go.

Styling follows the dataviz method: thin bars with a 2px gap, a recessive grid, one axis,
and labels in text colors (series colors only on marks).
"""

import math
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

Chart = dict[str, Any]
GAP = 2


def nice_max(value: float) -> float:
    """Round an axis maximum up to 1, 2, 2.5, or 5 times a power of ten."""
    if value <= 0:
        return 1.0
    power = 10.0 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * power:
            return step * power
    return 10 * power


def stacked_bars(
    days: Sequence[date],
    series: dict[str, Sequence[int]],
    *,
    classes: Sequence[str] = ("s1", "s2", "s3"),
    width: int = 720,
    height: int = 200,
) -> Chart:
    """Daily stacked bars. `series` maps a legend label to one count per day, bottom first.

    `classes` are the CSS fill classes per series, so color follows meaning across charts.
    """
    plot_left, plot_bottom = 36, height - 22
    plot_height = plot_bottom - 8
    totals = [sum(values[i] for values in series.values()) for i in range(len(days))]
    top = nice_max(max(totals, default=0))
    slot = (width - plot_left) / max(len(days), 1)
    bars = []
    for i, day in enumerate(days):
        base = float(plot_bottom)
        for index, (label, values) in enumerate(series.items()):
            value = values[i]
            if not value:
                continue
            h = value / top * plot_height
            bars.append(
                {
                    "x": plot_left + i * slot + GAP / 2,
                    "y": base - h + (GAP if base < plot_bottom else 0),
                    "w": max(slot - GAP, 1),
                    "h": max(h - (GAP if base < plot_bottom else 0), 1),
                    "cls": classes[index],
                    "title": f"{day:%b %d}: {value} {label.lower()}",
                }
            )
            base -= h
    ticks = [
        {"y": plot_bottom - fraction * plot_height, "label": _count(top * fraction)}
        for fraction in (0, 0.5, 1)
    ]
    labels = [
        {"x": plot_left + i * slot + slot / 2, "label": f"{day:%b %d}"}
        for i, day in enumerate(days)
        if i % 7 == len(days) % 7 or i == len(days) - 1
    ]
    return {
        "width": width,
        "height": height,
        "left": plot_left,
        "bottom": plot_bottom,
        "bars": bars,
        "ticks": ticks,
        "labels": labels,
        "legend": [{"label": label, "cls": classes[i]} for i, label in enumerate(series)],
        "empty": not any(totals),
    }


def split_bars(
    rows: Sequence[dict[str, Any]],
    key: str,
    parts: dict[str, str],
    classes: Sequence[str] = ("s1", "s2", "s3"),
) -> Chart:
    """Horizontal bars, one per row, split into parts (column -> legend label)."""
    top = max((sum(row[column] for column in parts) for row in rows), default=0) or 1
    out = []
    for row in rows:
        offset = 0.0
        segments = []
        for index, (column, label) in enumerate(parts.items()):
            share = row[column] / top * 100
            if row[column]:
                segments.append(
                    {
                        "left": offset,
                        "width": share,
                        "cls": classes[index],
                        "title": f"{row[column]} {label.lower()}",
                    }
                )
            offset += share
        total = sum(row[column] for column in parts)
        out.append({"label": row[key], "total": total, "segments": segments})
    legend = [{"label": label, "cls": classes[i]} for i, label in enumerate(parts.values())]
    return {"rows": out, "legend": legend}


def line(points: Sequence[tuple[datetime, float]], *, width: int = 640, height: int = 160) -> Chart:
    """Lay out a 0-1 line chart (scores over time)."""
    left, bottom, top_pad = 32, height - 20, 8
    plot_height = bottom - top_pad
    if not points:
        return {"width": width, "height": height, "empty": True}
    first, last = points[0][0], points[-1][0]
    span = (last - first).total_seconds() or 1
    coords = [
        (
            left + (moment - first).total_seconds() / span * (width - left - 8),
            bottom - value * plot_height,
        )
        for moment, value in points
    ]
    if len(coords) == 1:
        coords = [(left, coords[0][1]), (width - 8, coords[0][1])]
    return {
        "width": width,
        "height": height,
        "left": left,
        "bottom": bottom,
        "path": " ".join(
            f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(coords)
        ),
        "points": [
            {"x": x, "y": y, "title": f"{moment:%b %d}: {value:.2f}"}
            for (x, y), (moment, value) in zip(coords, points, strict=False)
        ],
        "ticks": [{"y": bottom - f * plot_height, "label": f"{f:.1f}"} for f in (0, 0.5, 1)],
        "labels": [{"x": left, "label": f"{first:%b %d}"}]
        + ([{"x": width - 8, "label": f"{last:%b %d}"}] if last.date() != first.date() else []),
        "empty": False,
    }


def sparkline(values: Sequence[int], *, width: int = 120, height: int = 28) -> str:
    """Return an SVG path for a small trend line."""
    top = max(values, default=0) or 1
    step = width / max(len(values) - 1, 1)
    return " ".join(
        f"{'M' if i == 0 else 'L'}{i * step:.1f},{height - 2 - v / top * (height - 4):.1f}"
        for i, v in enumerate(values)
    )


def _count(value: float) -> str:
    return f"{value:.0f}" if value == int(value) else f"{value:.1f}"
