from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener


BASE_URL = "https://kyfw.12306.cn"
PUBLIC_QUERY_PAGE_URL = f"{BASE_URL}/otn/view/queryPublicIndex.html"
PUBLIC_PRICE_URL = f"{BASE_URL}/otn/leftTicketPrice/queryAllPublicPrice"
STATION_URLS = (
    f"{BASE_URL}/otn/resources/js/framework/station_name.js",
    f"{BASE_URL}/otn/personalJS/core/common/station_name_new.js",
)

PRICE_FIELDS = {
    "swz_price": "商务座",
    "zy_price": "一等座",
    "ze_price": "二等座",
    "yz_price": "硬座",
    "yw_price": "硬卧",
    "rw_price": "软卧",
    "gr_price": "高级软卧",
}


@dataclass(frozen=True)
class PublicPriceTrain:
    train_no: str
    train_code: str
    train_class_name: str
    from_station_name: str
    to_station_name: str
    from_station_telecode: str
    to_station_telecode: str
    start_time: str
    arrive_time: str
    duration: str
    day_difference: str
    prices: dict[str, Decimal]
    raw_price_fields: dict[str, str]
    info_all_list: str


def request_text(url: str, *, timeout: int) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
        ),
        "Accept": "application/json,text/javascript,*/*;q=0.01",
        "Referer": PUBLIC_QUERY_PAGE_URL,
    }
    opener = build_opener()
    opener.open(Request(PUBLIC_QUERY_PAGE_URL, headers=headers), timeout=timeout).read()
    response = opener.open(Request(url, headers=headers), timeout=timeout)
    return response.read().decode("utf-8-sig", errors="replace")


def extract_station_codes(text: str) -> dict[str, str]:
    match = re.search(r"'(?P<data>[^']+)'", text)
    if not match:
        raise ValueError("车站列表响应格式不符合预期")

    stations: dict[str, str] = {}
    for row in match.group("data").split("@"):
        if not row:
            continue
        parts = row.split("|")
        if len(parts) >= 3:
            stations[parts[1]] = parts[2]
    return stations


def fetch_station_codes(timeout: int) -> dict[str, str]:
    last_error: Exception | None = None
    for url in STATION_URLS:
        try:
            return extract_station_codes(request_text(url, timeout=timeout))
        except (HTTPError, URLError, ValueError) as exc:
            last_error = exc
            print(f"车站列表探测失败 {url}: {type(exc).__name__}: {exc}", file=sys.stderr)
    raise RuntimeError(f"无法获取车站电报码：{last_error}")


def parse_price(value: Any) -> Decimal | None:
    if value in (None, "", "--"):
        return None
    text = str(value).strip()
    if not text or not text.isdigit():
        return None
    return Decimal(text) / Decimal("10")


def parse_public_price_train(row: dict[str, Any]) -> PublicPriceTrain:
    dto = row.get("queryLeftNewDTO")
    if not isinstance(dto, dict):
        raise ValueError("缺少 queryLeftNewDTO")

    prices: dict[str, Decimal] = {}
    raw_price_fields: dict[str, str] = {}
    for field, seat_type in PRICE_FIELDS.items():
        raw_value = dto.get(field)
        price = parse_price(raw_value)
        if price is not None:
            prices[seat_type] = price
            raw_price_fields[field] = str(raw_value)

    return PublicPriceTrain(
        train_no=str(dto.get("train_no", "")),
        train_code=str(dto.get("station_train_code", "")),
        train_class_name=str(dto.get("train_class_name", "")),
        from_station_name=str(dto.get("from_station_name", "")),
        to_station_name=str(dto.get("to_station_name", "")),
        from_station_telecode=str(dto.get("from_station_telecode", "")),
        to_station_telecode=str(dto.get("to_station_telecode", "")),
        start_time=str(dto.get("start_time", "")),
        arrive_time=str(dto.get("arrive_time", "")),
        duration=str(dto.get("lishi", "")),
        day_difference=str(dto.get("day_difference", "")),
        prices=prices,
        raw_price_fields=raw_price_fields,
        info_all_list=str(dto.get("infoAll_list", "")),
    )


def fetch_public_prices(
    travel_date: str,
    from_code: str,
    to_code: str,
    *,
    timeout: int,
) -> list[PublicPriceTrain]:
    query = urlencode(
        {
            "leftTicketDTO.train_date": travel_date,
            "leftTicketDTO.from_station": from_code,
            "leftTicketDTO.to_station": to_code,
            "purpose_codes": "ADULT",
        }
    )
    raw_text = request_text(f"{PUBLIC_PRICE_URL}?{query}", timeout=timeout)
    payload = json.loads(raw_text)
    if not payload.get("status"):
        raise ValueError(f"票价接口返回失败：{payload}")
    rows = payload.get("data", [])
    if not isinstance(rows, list):
        raise ValueError("票价接口 data 不是列表")
    return [parse_public_price_train(row) for row in rows]


def filter_trains(trains: list[PublicPriceTrain], train_code: str | None) -> list[PublicPriceTrain]:
    if train_code is None:
        return trains
    return [train for train in trains if train.train_code.upper() == train_code.upper()]


def main() -> int:
    parser = argparse.ArgumentParser(description="探测 12306 官方公布票价接口")
    parser.add_argument("--date", required=True, help="出行日期，格式 YYYY-MM-DD")
    parser.add_argument("--from", dest="from_station", required=True, help="出发站中文名")
    parser.add_argument("--to", dest="to_station", required=True, help="到达站中文名")
    parser.add_argument("--train", help="可选，指定展示车次，如 G547 或 K599")
    parser.add_argument("--limit", type=int, default=8, help="最多输出车次数量")
    parser.add_argument("--timeout", type=int, default=10, help="请求超时时间，秒")
    args = parser.parse_args()

    try:
        date.fromisoformat(args.date)
        stations = fetch_station_codes(args.timeout)
        from_code = stations[args.from_station]
        to_code = stations[args.to_station]
        trains = fetch_public_prices(args.date, from_code, to_code, timeout=args.timeout)
        trains = filter_trains(trains, args.train)
    except KeyError as exc:
        print(f"站名无法映射电报码：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"探测失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"站点: {args.from_station}({from_code}) -> {args.to_station}({to_code})")
    print(f"日期: {args.date}")
    print(f"车次数: {len(trains)}")
    for train in trains[: args.limit]:
        print("-" * 72)
        print(f"车次: {train.train_code} ({train.train_class_name})")
        print(f"train_no: {train.train_no}")
        print(
            f"区间: {train.from_station_name}({train.from_station_telecode}) -> "
            f"{train.to_station_name}({train.to_station_telecode})"
        )
        print(f"时间: {train.start_time} -> {train.arrive_time}，历时 {train.duration}，跨日 {train.day_difference}")
        print("价格:")
        print(json.dumps({key: str(value) for key, value in train.prices.items()}, ensure_ascii=False, indent=2))
        print("原始价格字段:")
        print(json.dumps(train.raw_price_fields, ensure_ascii=False, indent=2))
        if train.info_all_list:
            print(f"infoAll_list: {train.info_all_list}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())