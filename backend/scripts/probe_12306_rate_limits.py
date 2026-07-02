from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Awaitable, Callable

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.adapters.railway_12306_public_price import Railway12306Error  # noqa: E402
from app.adapters.railway_12306_snapshot import Railway12306SnapshotClient  # noqa: E402


@dataclass(frozen=True)
class ProbeProfile:
    concurrency: int
    delay_seconds: float
    requests: int


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    elapsed_seconds: float
    row_count: int
    error_type: str = ""
    error_message: str = ""


@dataclass(frozen=True)
class ProbeSummary:
    target: str
    concurrency: int
    delay_seconds: float
    requests: int
    success: int
    failed: int
    rows: int
    elapsed_seconds: float
    rps: float
    p50_ms: float
    p95_ms: float
    error_counts: dict[str, int]


def parse_profiles(value: str, *, requests: int) -> list[ProbeProfile]:
    profiles: list[ProbeProfile] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":")
        if len(parts) != 2:
            raise ValueError(f"profile 格式错误：{item}，应为 concurrency:delay，例如 4:0.1")
        concurrency = int(parts[0])
        delay_seconds = float(parts[1])
        if concurrency <= 0 or delay_seconds < 0:
            raise ValueError(f"profile 参数非法：{item}")
        profiles.append(ProbeProfile(concurrency=concurrency, delay_seconds=delay_seconds, requests=requests))
    if not profiles:
        raise ValueError("至少需要一个 profile")
    return profiles


def summarize_results(target: str, profile: ProbeProfile, results: list[ProbeResult], elapsed_seconds: float) -> ProbeSummary:
    durations = sorted(result.elapsed_seconds * 1000 for result in results)
    error_counts: dict[str, int] = {}
    for result in results:
        if not result.ok:
            key = result.error_type or "UnknownError"
            error_counts[key] = error_counts.get(key, 0) + 1
    success = sum(1 for result in results if result.ok)
    failed = len(results) - success
    rows = sum(result.row_count for result in results)
    return ProbeSummary(
        target=target,
        concurrency=profile.concurrency,
        delay_seconds=profile.delay_seconds,
        requests=profile.requests,
        success=success,
        failed=failed,
        rows=rows,
        elapsed_seconds=elapsed_seconds,
        rps=(len(results) / elapsed_seconds) if elapsed_seconds > 0 else 0,
        p50_ms=statistics.median(durations) if durations else 0,
        p95_ms=percentile(durations, 95),
        error_counts=error_counts,
    )


def percentile(values: list[float], percent: int) -> float:
    if not values:
        return 0
    index = min(len(values) - 1, max(0, round((percent / 100) * (len(values) - 1))))
    return values[index]


async def run_profile(
    target: str,
    profile: ProbeProfile,
    request_factory: Callable[[int], Awaitable[int]],
) -> ProbeSummary:
    semaphore = asyncio.Semaphore(profile.concurrency)
    results: list[ProbeResult] = []

    async def run_one(index: int) -> None:
        await asyncio.sleep(index * profile.delay_seconds)
        started = time.perf_counter()
        try:
            async with semaphore:
                row_count = await request_factory(index)
            results.append(ProbeResult(ok=True, elapsed_seconds=time.perf_counter() - started, row_count=row_count))
        except Exception as exc:  # noqa: BLE001 - probe must classify all remote failures.
            results.append(
                ProbeResult(
                    ok=False,
                    elapsed_seconds=time.perf_counter() - started,
                    row_count=0,
                    error_type=type(exc).__name__,
                    error_message=str(exc)[:300],
                )
            )

    started = time.perf_counter()
    await asyncio.gather(*(run_one(index) for index in range(profile.requests)))
    return summarize_results(target, profile, results, time.perf_counter() - started)


def print_summary(summary: ProbeSummary) -> None:
    print(
        f"{summary.target}: concurrency={summary.concurrency}, delay={summary.delay_seconds:.3f}s, "
        f"requests={summary.requests}, success={summary.success}, failed={summary.failed}, "
        f"rows={summary.rows}, elapsed={summary.elapsed_seconds:.2f}s, rps={summary.rps:.2f}, "
        f"p50={summary.p50_ms:.0f}ms, p95={summary.p95_ms:.0f}ms, errors={summary.error_counts}"
    )


async def probe_train_info(args: argparse.Namespace, profiles: list[ProbeProfile], client: Railway12306SnapshotClient) -> None:
    trains = await client.fetch_train_names(date.fromisoformat(args.date))
    trains = trains[: args.train_sample_size]
    if not trains:
        raise Railway12306Error("没有可用于探测的 train_no")

    async def request_factory(index: int) -> int:
        train = trains[index % len(trains)]
        return len(await client.fetch_train_stops(date.fromisoformat(args.date), train.train_no))

    for profile in profiles:
        print_summary(await run_profile("train-info", profile, request_factory))


async def probe_public_price(args: argparse.Namespace, profiles: list[ProbeProfile], client: Railway12306SnapshotClient) -> None:
    ods = parse_ods(args.ods)

    async def request_factory(index: int) -> int:
        from_station, to_station = ods[index % len(ods)]
        return len(await client.query_public_prices(date.fromisoformat(args.date), from_station, to_station))

    for profile in profiles:
        print_summary(await run_profile("public-price", profile, request_factory))


def parse_ods(value: str) -> list[tuple[str, str]]:
    ods: list[tuple[str, str]] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":")
        if len(parts) != 2:
            raise ValueError(f"OD 格式错误：{item}，应为 出发站:到达站")
        ods.append((parts[0], parts[1]))
    if not ods:
        raise ValueError("至少需要一个 OD")
    return ods


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="小步探测 12306 时刻表和公布票价接口的请求频率容忍度。")
    parser.add_argument("--date", required=True)
    parser.add_argument("--target", choices=("train-info", "public-price", "both"), default="both")
    parser.add_argument("--profiles", default="1:0.5,2:0.2,4:0.1", help="逗号分隔的 concurrency:delay 列表。")
    parser.add_argument("--requests-per-profile", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--train-sample-size", type=int, default=50)
    parser.add_argument("--ods", default="北京:上海,北京南:上海虹桥,广州:深圳", help="逗号分隔 OD，格式 出发站:到达站。")
    return parser.parse_args(argv)


async def async_main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    profiles = parse_profiles(args.profiles, requests=args.requests_per_profile)
    client = Railway12306SnapshotClient(timeout_seconds=args.timeout_seconds)
    if args.target in ("train-info", "both"):
        await probe_train_info(args, profiles, client)
    if args.target in ("public-price", "both"):
        await probe_public_price(args, profiles, client)
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())