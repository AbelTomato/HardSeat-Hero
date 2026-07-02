from scripts.probe_12306_rate_limits import ProbeProfile, ProbeResult, parse_ods, parse_profiles, summarize_results


def test_parse_profiles() -> None:
    profiles = parse_profiles("1:0.5,4:0.1", requests=20)

    assert profiles == [ProbeProfile(concurrency=1, delay_seconds=0.5, requests=20), ProbeProfile(concurrency=4, delay_seconds=0.1, requests=20)]


def test_parse_ods() -> None:
    assert parse_ods("北京:上海,广州:深圳") == [("北京", "上海"), ("广州", "深圳")]


def test_summarize_results_counts_success_and_errors() -> None:
    summary = summarize_results(
        "public-price",
        ProbeProfile(concurrency=2, delay_seconds=0.1, requests=3),
        [
            ProbeResult(ok=True, elapsed_seconds=0.1, row_count=2),
            ProbeResult(ok=True, elapsed_seconds=0.2, row_count=3),
            ProbeResult(ok=False, elapsed_seconds=0.3, row_count=0, error_type="Railway12306Error"),
        ],
        elapsed_seconds=1.0,
    )

    assert summary.success == 2
    assert summary.failed == 1
    assert summary.rows == 5
    assert summary.rps == 3
    assert summary.error_counts == {"Railway12306Error": 1}