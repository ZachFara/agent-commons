from datetime import UTC, date, datetime, timedelta

import pytest

from commons.web import charts


@pytest.mark.parametrize(
    ("value", "expected"), [(0, 1), (0.7, 1), (3, 5), (12, 20), (23, 25), (240, 250), (999, 1000)]
)
def test_nice_max(value: float, expected: float) -> None:
    assert charts.nice_max(value) == expected


def test_stacked_bars_geometry() -> None:
    days = [date(2026, 10, 1) + timedelta(days=i) for i in range(3)]
    chart = charts.stacked_bars(days, {"A": [2, 0, 4], "B": [1, 0, 0]}, classes=("x", "y"))
    assert not chart["empty"]
    assert [bar["cls"] for bar in chart["bars"]] == ["x", "y", "x"]
    tallest = max(bar["h"] for bar in chart["bars"])
    assert all(bar["y"] + bar["h"] <= chart["bottom"] + 0.01 for bar in chart["bars"])
    assert tallest == pytest.approx((chart["bottom"] - 8) * 4 / 5)
    assert chart["bars"][0]["title"] == "Oct 01: 2 a"
    assert [tick["label"] for tick in chart["ticks"]] == ["0", "2.5", "5"]
    assert charts.stacked_bars(days, {"A": [0, 0, 0]})["empty"]


def test_split_bars_share_one_scale() -> None:
    rows = [{"k": "big", "a": 6, "b": 2}, {"k": "small", "a": 0, "b": 4}]
    chart = charts.split_bars(rows, "k", {"a": "A", "b": "B"})
    big, small = chart["rows"]
    assert big["total"] == 8
    assert [s["width"] for s in big["segments"]] == [75, 25]
    assert small["segments"] == [{"left": 0, "width": 50, "cls": "s2", "title": "4 b"}]


def test_line_maps_scores_to_the_plot() -> None:
    start = datetime(2026, 10, 1, tzinfo=UTC)
    chart = charts.line([(start, 0.0), (start + timedelta(days=2), 1.0)], width=200, height=100)
    first, last = chart["points"]
    assert first["y"] == chart["bottom"]
    assert last["y"] == pytest.approx(8)
    assert [label["label"] for label in chart["labels"]] == ["Oct 01", "Oct 03"]
    assert charts.line([])["empty"]


def test_sparkline_has_one_point_per_value() -> None:
    assert charts.sparkline([1, 3, 2]).count("L") == 2
