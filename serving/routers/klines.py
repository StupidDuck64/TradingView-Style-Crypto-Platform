import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from serving.config import INFLUX_BUCKET
from serving.connections import get_influx, get_redis, get_trino_connection

router = APIRouter(prefix="/api", tags=["klines"])

INTERVAL_SECONDS = {
    "1s": 1, "1m": 60, "5m": 300, "15m": 900,
    "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800,
}

INFLUX_1M_RETENTION_DAYS = int(os.environ.get("INFLUX_1M_RETENTION_DAYS", "90"))
MAX_RAW_CANDLES = 50_000
MAX_RAW_PER_QUERY = 12_000
MAX_BACKFILL_PAGES = 8
LIVE_MAX_BASE_ROWS = 2_500

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,20}$")


def _validate(symbol: str) -> str:
    s = symbol.strip().upper()
    if not _SYMBOL_RE.match(s):
        raise HTTPException(400, "Invalid symbol")
    return s


def _ms_to_rfc3339(ms: int) -> str:
    """Convert epoch milliseconds to RFC3339 string for InfluxDB Flux queries."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _aggregate(candles: list[dict], target_ms: int) -> list[dict]:
    """Re-sample OHLCV candles into larger-interval buckets."""
    if not candles:
        return []
    buckets: dict[int, dict] = {}
    for c in candles:
        key = (c["openTime"] // target_ms) * target_ms
        if key not in buckets:
            buckets[key] = {
                "openTime": key,
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c["volume"],
            }
        else:
            b = buckets[key]
            b["high"] = max(b["high"], c["high"])
            b["low"] = min(b["low"], c["low"])
            b["close"] = c["close"]
            b["volume"] = round(b["volume"] + c["volume"], 8)
    return sorted(buckets.values(), key=lambda x: x["openTime"])


def _to_candle_rows(rows: list[tuple]) -> list[dict]:
    return [
        {
            "openTime": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
        }
        for r in rows
    ]


def _merge_unique(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """Merge candle rows by openTime, preferring the latest version per timestamp."""
    if not incoming:
        return existing
    merged: dict[int, dict] = {int(c["openTime"]): c for c in existing}
    for c in incoming:
        merged[int(c["openTime"])] = c
    return sorted(merged.values(), key=lambda x: x["openTime"])


def _collect_base_1m_candles(
    symbol: str,
    target_sec: int,
    limit: int,
    end_ms: int,
    now_ms: int,
    influx_cutoff_ms: int,
    max_pages: int = MAX_BACKFILL_PAGES,
    allow_trino: bool = True,
) -> list[dict]:
    """Fetch 1m base candles with bounded, paged backfill from Influx then Trino.

    This avoids giant single queries for high intervals (4h/1d/1w), while still
    filling enough raw 1m candles to build `limit` aggregated bars when data exists.
    """
    mult = max(target_sec // 60, 1)
    raw_target = min((limit * mult) + mult, MAX_RAW_CANDLES)
    # Keep each page bounded; use a small multiple of target interval for efficiency.
    per_page = min(MAX_RAW_PER_QUERY, max(mult * 240, mult * 8))
    per_page = max(per_page, mult * 2)

    candles: list[dict] = []
    cursor = end_ms
    pages = 0
    while pages < max_pages and cursor > 0:
        pages += 1
        batch: list[dict] = []
        if cursor >= influx_cutoff_ms:
            range_h = min(max((per_page * 60) // 3600 + 2, 1), INFLUX_1M_RETENTION_DAYS * 24)
            batch = _query_influx_sync(symbol, "1m", per_page, range_h, cursor)
        if not batch and allow_trino:
            # Deep history fallback or Influx gap path.
            batch = _query_trino_1m_sync(symbol, cursor, per_page)
        if not batch:
            break

        candles = _merge_unique(candles, batch)
        oldest = min(c["openTime"] for c in batch)
        if oldest >= cursor:
            break
        cursor = oldest

        # Stop when we have enough raw 1m data to build `limit` bars.
        if target_sec == 60:
            if len(candles) >= limit:
                break
        else:
            if len(_aggregate(candles, target_sec * 1000)) >= limit:
                break

        # If we already reached back before retained Influx + no historical request,
        # avoid unbounded paging in live mode.
        if end_ms >= now_ms and cursor < influx_cutoff_ms and len(candles) >= raw_target:
            break

    return candles


def _query_influx_sync(symbol: str, interval: str, limit: int, range_h: int,
                       end_ms: int | None = None) -> list[dict]:
    if end_ms:
        # For historical scroll queries: query a window ending at end_ms
        # Go back enough time to cover `limit` candles
        interval_sec = INTERVAL_SECONDS.get(interval, 60)
        start_ms = end_ms - (limit * interval_sec * 1000) - (interval_sec * 1000)
        start_rfc = _ms_to_rfc3339(start_ms)
        stop_rfc = _ms_to_rfc3339(end_ms)
        query = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: {start_rfc}, stop: {stop_rfc})
  |> filter(fn: (r) => r._measurement == "candles")
  |> filter(fn: (r) => r.symbol == "{symbol}")
  |> filter(fn: (r) => r.interval == "{interval}")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
  |> tail(n: {limit})
'''
    else:
        query = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -{range_h}h)
  |> filter(fn: (r) => r._measurement == "candles")
  |> filter(fn: (r) => r.symbol == "{symbol}")
  |> filter(fn: (r) => r.interval == "{interval}")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
  |> tail(n: {limit})
'''
    tables = get_influx().query_api().query(query)
    out: list[dict] = []
    for table in tables:
        for rec in table.records:
            out.append({
                "openTime": int(rec.get_time().timestamp() * 1000),
                "open": float(rec.values.get("open", 0)),
                "high": float(rec.values.get("high", 0)),
                "low": float(rec.values.get("low", 0)),
                "close": float(rec.values.get("close", 0)),
                "volume": float(rec.values.get("volume", 0)),
            })
    return out


def _query_trino_1m_sync(symbol: str, end_ms: int, limit: int) -> list[dict]:
    """Query 1m closed candles from Iceberg via Trino for deep history scroll."""
    conn = get_trino_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                kline_start AS open_time,
                open,
                high,
                low,
                close,
                volume
            FROM coin_klines
            WHERE symbol = ?
              AND interval = '1m'
              AND is_closed = true
              AND kline_start < ?
            ORDER BY kline_start DESC
            LIMIT ?
            """,
            (symbol, end_ms, limit),
        )
        rows = cur.fetchall()
        rows.reverse()
        return _to_candle_rows(rows)
    finally:
        conn.close()


def _query_trino_hourly_sync(symbol: str, end_ms: int, limit: int) -> list[dict]:
    """Fallback query for legacy hourly history table in Iceberg via Trino."""
    conn = get_trino_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                open_time,
                open,
                high,
                low,
                close,
                volume
            FROM historical_hourly
            WHERE symbol = ?
              AND open_time < ?
            ORDER BY open_time DESC
            LIMIT ?
            """,
            (symbol, end_ms, limit),
        )
        rows = cur.fetchall()
        rows.reverse()
        return _to_candle_rows(rows)
    finally:
        conn.close()


@router.get("/klines")
async def get_klines(
    symbol: str,
    interval: str = "1m",
    limit: int = Query(200, ge=1, le=1500),
    endTime: int | None = Query(None, description="End timestamp in milliseconds (exclusive). If provided, returns candles before this time."),
):
    """
    Historical OHLCV candles.

    Response matches the shape expected by the React frontend:
    ``[{"openTime": ms, "open": float, "high": float, "low": float, "close": float, "volume": float}]``
    
    If endTime is provided, returns `limit` candles ending before endTime (useful for scroll loading).
    """
    symbol = _validate(symbol)
    interval = interval.strip().lower()
    target_sec = INTERVAL_SECONDS.get(interval)
    if target_sec is None:
        raise HTTPException(400, f"Unsupported interval: {interval}")

    # Check Redis cache first (0.1 second TTL for ultra-low latency)
    # Skip cache for endTime queries (historical scroll loading)
    r = await get_redis()
    cache_key = f"klines_cache:{symbol}:{interval}:{limit}"
    if not endTime:
        cached = await r.get(cache_key)
        if cached:
            return json.loads(cached)

    candles = []
    now_ms = int(time.time() * 1000)
    influx_cutoff_ms = now_ms - (INFLUX_1M_RETENTION_DAYS * 24 * 3600 * 1000)

    # 1s candles are served exclusively from KeyDB (speed layer).
    if interval == "1s":
        needed_1s = min(limit + 2, MAX_RAW_CANDLES)
        live_lookback_ms = max(needed_1s * 1000, 120_000)
        score_min = (endTime - needed_1s * 1000) if endTime else str(now_ms - live_lookback_ms)
        score_max = (endTime - 1) if endTime else "+inf"
        raw = await r.zrangebyscore(f"candle:1s:{symbol}", score_min, score_max)
        if not raw and not endTime:
            raw = await r.zrevrange(f"candle:1s:{symbol}", 0, needed_1s - 1)

        best_by_time: dict[int, dict] = {}
        for item in raw if raw else []:
            c = json.loads(item)
            t = int(c["t"])
            if t not in best_by_time or c["v"] > best_by_time[t]["v"]:
                best_by_time[t] = c
        for t, c in best_by_time.items():
            candles.append({
                "openTime": t,
                "open": c["o"], "high": c["h"],
                "low": c["l"], "close": c["c"],
                "volume": c["v"],
            })
        candles.sort(key=lambda x: x["openTime"])
    else:
        # 1m+ candles are built from InfluxDB 1m base candles.
        # For deep history (outside retention), fallback to Iceberg via Trino.
        is_scroll_request = endTime is not None
        if is_scroll_request:
            backfilled = await asyncio.to_thread(
                _collect_base_1m_candles,
                symbol,
                target_sec,
                limit,
                endTime,
                now_ms,
                influx_cutoff_ms,
                MAX_BACKFILL_PAGES,
                True,
            )
            candles = _merge_unique(candles, backfilled)
        else:
            raw_needed = min((limit * max(target_sec // 60, 1)) + 2, MAX_RAW_CANDLES)
            live_limit = min(max(raw_needed, limit), LIVE_MAX_BASE_ROWS)
            live_range_h = min(max((live_limit * 60) // 3600 + 2, 1), INFLUX_1M_RETENTION_DAYS * 24)
            live_rows = await asyncio.to_thread(
                _query_influx_sync,
                symbol,
                "1m",
                live_limit,
                live_range_h,
                None,
            )
            candles = _merge_unique(candles, live_rows)

        # If 1m cold storage is missing (or sparse), fallback to legacy hourly
        # history for 1h+ intervals so deep scroll remains usable.
        if endTime is not None and interval in ("1h", "4h", "1d", "1w"):
            target_h = max(target_sec // 3600, 1)
            hourly_needed = min((limit * target_h) + target_h, 5000)
            if len(candles) < max(limit, target_h * 8):
                hourly_rows = await asyncio.to_thread(
                    _query_trino_hourly_sync,
                    symbol,
                    endTime or now_ms,
                    hourly_needed,
                )
                candles = _merge_unique(candles, hourly_rows)

    # Always aggregate for intervals above 1-minute resolution.
    # All higher intervals are derived from 1m candles to avoid redundant storage.
    if interval not in ("1s", "1m") and candles:
        candles = _aggregate(candles, target_sec * 1000)

    # When endTime is provided (scroll loading), skip live ticker merge
    # and filter candles to only those before endTime
    if endTime:
        candles = [c for c in candles if c["openTime"] < endTime]
        result = candles[-limit:] if candles else []
    else:
        # Avoid ticker-based overwrite for 1s/1m to keep exchange-consistent
        # candle shapes. For 5m+ we still allow a live in-progress candle.
        if interval not in ("1s", "1m"):
            ticker = await r.hgetall(f"ticker:latest:{symbol}")
            if ticker.get("price") and ticker.get("event_time"):
                target_ms = target_sec * 1000
                live_price = float(ticker["price"])
                live_ts = int(ticker["event_time"])
                aligned_time = (live_ts // target_ms) * target_ms
                latest_candle_ts = candles[-1]["openTime"] if candles else 0
                window_is_open = int(time.time() * 1000) < (aligned_time + target_ms)
                if (
                    candles and candles[-1]["openTime"] == aligned_time
                    and live_ts > latest_candle_ts and window_is_open
                ):
                    candles[-1]["close"] = live_price
                    candles[-1]["high"] = max(candles[-1]["high"], live_price)
                    candles[-1]["low"] = min(candles[-1]["low"], live_price)
                elif not candles or aligned_time > candles[-1]["openTime"]:
                    candles.append({
                        "openTime": aligned_time,
                        "open": live_price,
                        "high": live_price,
                        "low": live_price,
                        "close": live_price,
                        "volume": 0.0,
                    })
        result = candles[-limit:]
    
    # Cache result in Redis (0.1 second TTL for ultra-low latency)
    # Skip caching for endTime queries (historical scroll loading)
    if not endTime:
        cache_data = json.dumps(result)
        ttl_ms = 200 if interval == "1s" else 1500
        pipe = r.pipeline()
        pipe.set(cache_key, cache_data)
        pipe.pexpire(cache_key, ttl_ms)
        await pipe.execute()
    
    return result
