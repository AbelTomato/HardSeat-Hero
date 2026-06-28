from __future__ import annotations

import argparse
import csv
from pathlib import Path


DEFAULT_SOURCE = Path(r"D:\AbelTomato_Files\Developer\Others\China-rail-way-stations-data\src\station.csv")
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "app" / "data" / "station_metadata.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import railway station metadata into app data CSV.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="Source station.csv path")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output station_metadata.csv path")
    parser.add_argument(
        "--include-stops",
        action="store_true",
        help="Include low-value passenger halts. By default only 客运站 rows are imported.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = import_station_metadata(args.source, include_stops=args.include_stops)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "name",
                "telecode",
                "latitude",
                "longitude",
                "centrality_score",
                "province",
                "city",
                "railway_bureau",
                "station_type",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"Imported {len(rows)} stations to {args.output}")


def import_station_metadata(source: Path, include_stops: bool = False) -> list[dict[str, str]]:
    if not source.exists():
        raise FileNotFoundError(source)

    by_name: dict[str, dict[str, str]] = {}
    with source.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            name = normalize_station_name(row.get("站名", ""))
            station_kind = (row.get("性质") or "").strip()
            if not name:
                continue
            if not include_stops and station_kind != "客运站":
                continue
            longitude = parse_float_text(row.get("WGS84_Lng"))
            latitude = parse_float_text(row.get("WGS84_Lat"))
            if longitude is None or latitude is None:
                continue
            by_name[name] = {
                "name": name,
                "telecode": "",
                "latitude": format_float(latitude),
                "longitude": format_float(longitude),
                "centrality_score": format_float(centrality_score(row.get("srcCount"))),
                "province": (row.get("省") or "").strip(),
                "city": (row.get("市") or "").strip(),
                "railway_bureau": (row.get("铁路局") or "").strip(),
                "station_type": station_kind,
            }
    return sorted(by_name.values(), key=lambda item: item["name"])


def normalize_station_name(value: str) -> str:
    name = value.strip()
    if name.endswith("站"):
        name = name[:-1]
    return name


def parse_float_text(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def centrality_score(value: str | None) -> float:
    source_count = parse_float_text(value) or 0
    return min(100, 50 + source_count * 5)


def format_float(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


if __name__ == "__main__":
    main()