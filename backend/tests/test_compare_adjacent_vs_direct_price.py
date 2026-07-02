from datetime import date, datetime, timezone
from decimal import Decimal
import sqlite3

from app.services.static_price_repository import SQLiteStaticPriceRepository
from scripts.compare_adjacent_vs_direct_price import (
    PriceComparison,
    SampleOd,
    ScheduleStop,
    build_sample_ods_for_train,
    compare_sample_from_rows,
    extract_train_prices_by_seat,
    load_sample_ods,
    report_to_dict,
    summarize_comparisons,
)


def make_row(train_no: str, train_code: str, **prices: str) -> dict:
    dto = {"train_no": train_no, "station_train_code": train_code}
    dto.update(prices)
    return {"queryLeftNewDTO": dto}


def test_build_sample_ods_for_train_picks_adjacent_medium_and_long_od() -> None:
    stops = [ScheduleStop("A", 1), ScheduleStop("B", 2), ScheduleStop("C", 3), ScheduleStop("D", 4), ScheduleStop("E", 5)]

    samples = build_sample_ods_for_train("G1NO", "G1", stops)

    assert [(sample.from_station, sample.to_station, sample.sample_kind) for sample in samples] == [
        ("A", "B", "adjacent"),
        ("A", "D", "medium"),
        ("A", "E", "long"),
    ]
    assert samples[1].adjacent_pairs == (("A", "B"), ("B", "C"), ("C", "D"))
    assert samples[2].adjacent_pairs == (("A", "B"), ("B", "C"), ("C", "D"), ("D", "E"))


def test_extract_train_prices_by_seat_filters_target_train_and_parses_mock_rows() -> None:
    rows = [
        make_row("G1NO", "G1", ze_price="01000", zy_price="02000"),
        make_row("G2NO", "G2", ze_price="99990"),
    ]

    prices = extract_train_prices_by_seat(rows, train_no="G1NO", train_code="G1")

    assert prices == {"一等座": Decimal("200"), "二等座": Decimal("100")}


def test_compare_sample_from_rows_compares_direct_and_adjacent_sum_by_seat_type() -> None:
    sample = SampleOd(
        train_no="G1NO",
        train_code="G1",
        from_station="A",
        to_station="D",
        from_station_no=1,
        to_station_no=4,
        adjacent_pairs=(("A", "B"), ("B", "C"), ("C", "D")),
        sample_kind="medium",
    )
    direct_rows = [make_row("G1NO", "G1", ze_price="03000", zy_price="06000")]
    adjacent_rows_by_pair = {
        ("A", "B"): [make_row("G1NO", "G1", ze_price="01000", zy_price="02000")],
        ("B", "C"): [make_row("G1NO", "G1", ze_price="01100", zy_price="02000")],
        ("C", "D"): [make_row("G1NO", "G1", ze_price="00900", zy_price="02000")],
    }

    comparisons = compare_sample_from_rows(sample, direct_rows=direct_rows, adjacent_rows_by_pair=adjacent_rows_by_pair)

    by_seat = {comparison.seat_type: comparison for comparison in comparisons}
    assert by_seat["二等座"].direct_price == Decimal("300")
    assert by_seat["二等座"].adjacent_sum_price == Decimal("300")
    assert by_seat["二等座"].diff == Decimal("0")
    assert by_seat["一等座"].direct_price == Decimal("600")
    assert by_seat["一等座"].adjacent_sum_price == Decimal("600")
    assert by_seat["一等座"].abs_diff == Decimal("0")


def test_compare_sample_from_rows_skips_seat_type_when_any_adjacent_segment_is_missing() -> None:
    sample = SampleOd(
        train_no="G1NO",
        train_code="G1",
        from_station="A",
        to_station="C",
        from_station_no=1,
        to_station_no=3,
        adjacent_pairs=(("A", "B"), ("B", "C")),
        sample_kind="long",
    )

    comparisons = compare_sample_from_rows(
        sample,
        direct_rows=[make_row("G1NO", "G1", ze_price="01800")],
        adjacent_rows_by_pair={
            ("A", "B"): [make_row("G1NO", "G1", ze_price="01000")],
            ("B", "C"): [make_row("G1NO", "G1", zy_price="01000")],
        },
    )

    assert comparisons == []


def test_summarize_comparisons_reports_global_and_by_seat_type_stats() -> None:
    comparisons = [
        PriceComparison("G1NO", "G1", "A", "D", "二等座", Decimal("100"), Decimal("100"), Decimal("0"), Decimal("0"), "medium"),
        PriceComparison("G1NO", "G1", "A", "D", "一等座", Decimal("200"), Decimal("210"), Decimal("10"), Decimal("10"), "medium"),
        PriceComparison("G2NO", "G2", "A", "D", "一等座", Decimal("200"), Decimal("190"), Decimal("-10"), Decimal("10"), "medium"),
    ]

    report = summarize_comparisons(comparisons)
    payload = report_to_dict(report)

    assert payload["sample_count"] == 3
    assert payload["exact_match_count"] == 1
    assert payload["diff_count"] == 2
    assert payload["avg_abs_diff"] == "6.67"
    assert payload["max_abs_diff"] == "10.00"
    assert payload["by_seat_type_diff_stats"]["二等座"] == {
        "sample_count": 1,
        "exact_match_count": 1,
        "diff_count": 0,
        "avg_abs_diff": "0.00",
        "max_abs_diff": "0.00",
    }
    assert payload["by_seat_type_diff_stats"]["一等座"] == {
        "sample_count": 2,
        "exact_match_count": 0,
        "diff_count": 2,
        "avg_abs_diff": "10.00",
        "max_abs_diff": "10.00",
    }


def test_load_sample_ods_reads_local_schedule_snapshot(tmp_path) -> None:
    db = tmp_path / "static.sqlite3"
    SQLiteStaticPriceRepository(db)
    fetched_at = datetime(2026, 7, 2, tzinfo=timezone.utc).isoformat()
    with sqlite3.connect(db) as connection:
        connection.executemany(
            """
            INSERT INTO train_stop_snapshot(
                provider, service_date, train_no, train_code, station_name, station_no,
                arrive_at, depart_at, arrive_day_diff, running_time, fetched_at, raw_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            [
                ("p", "2026-07-12", "G1NO", "G1", "A", 1, "08:00", "08:00", "00:00", fetched_at, "h1"),
                ("p", "2026-07-12", "G1NO", "G1", "B", 2, "09:00", "09:00", "01:00", fetched_at, "h2"),
                ("p", "2026-07-12", "G1NO", "G1", "C", 3, "10:00", "10:00", "02:00", fetched_at, "h3"),
                ("p", "2026-07-12", "G1NO", "G1", "D", 4, "11:00", "11:00", "03:00", fetched_at, "h4"),
            ],
        )

    samples = load_sample_ods(db, provider="p", service_date=date(2026, 7, 12), sample_trains=1, min_stop_count=4)

    assert {(sample.from_station, sample.to_station, sample.sample_kind) for sample in samples} == {
        ("A", "B", "adjacent"),
        ("A", "D", "medium"),
    }