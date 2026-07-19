#!/usr/bin/env python3
"""Example job that appends BTC and ETH price observations to a CSV artifact."""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "prices.csv"
LOG_PATH = ROOT / "logs" / "fetch_prices.log"
API_URL = "https://api.coingecko.com/api/v3/simple/price"
SYMBOLS = {"bitcoin": "BTC", "ethereum": "ETH"}
CSV_SCHEMA = ("timestamp", "symbol", "price_usd")


def configure_logging() -> None:
    """Configure a file log that records API and output artifact evidence."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)sZ %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def fetch_price_data() -> dict[str, Any]:
    """Return the raw CoinGecko observations used to create CSV rows."""
    response = requests.get(
        API_URL,
        params={"ids": ",".join(SYMBOLS), "vs_currencies": "usd"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("CoinGecko response must be a JSON object")
    return payload


def append_prices(payload: dict[str, Any], observed_at: datetime) -> int:
    """Append price rows and return the number written to the CSV evidence file."""
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not OUTPUT_PATH.exists() or OUTPUT_PATH.stat().st_size == 0
    rows: list[dict[str, str | float]] = []

    for api_id, symbol in SYMBOLS.items():
        value = payload.get(api_id)
        if not isinstance(value, dict) or not isinstance(value.get("usd"), (int, float)):
            raise ValueError(f"CoinGecko response missing numeric USD price for {api_id}")
        rows.append(
            {
                "timestamp": observed_at.astimezone(timezone.utc).isoformat(),
                "symbol": symbol,
                "price_usd": value["usd"],
            }
        )

    with OUTPUT_PATH.open("a", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_SCHEMA)
        if needs_header:
            writer.writeheader()
        writer.writerows(rows)

    return len(rows)


def main() -> int:
    """Run the job and log the output path, appended row count, and byte size."""
    configure_logging()
    try:
        payload = fetch_price_data()
        row_count = append_prices(payload, datetime.now(timezone.utc))
        size_bytes = OUTPUT_PATH.stat().st_size
        logging.info(
            "output_path=%s rows_appended=%d size_bytes=%d",
            OUTPUT_PATH,
            row_count,
            size_bytes,
        )
    except Exception:
        logging.exception("fetch_prices_failed output_path=%s", OUTPUT_PATH)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
