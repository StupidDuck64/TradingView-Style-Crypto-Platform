#!/usr/bin/env python3
"""
Candle Data Query Helper for Backend API

Provides high-level functions to query candle data from KeyDB and InfluxDB
with automatic routing based on data retention policies.

Usage Example:
    from candle_query_helper import CandleQueryHelper
    
    helper = CandleQueryHelper()
    
    # Get latest candle
    latest = helper.get_latest_candle("btcusdt")
    
    # Get 1s candles for last hour
    candles_1s = helper.get_candles("btcusdt", "1s", limit=3600)
    
    # Get 1m candles for specific time range
    candles_1m = helper.get_candles("btcusdt", "1m", 
                                    start_ms=start_time, 
                                    end_ms=end_time)
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import redis
from influxdb_client import InfluxDBClient

# Environment variables
REDIS_HOST = os.environ.get("REDIS_HOST", "keydb")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

INFLUX_URL = os.environ.get("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN", "")
INFLUX_ORG = os.environ.get("INFLUX_ORG", "vi")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "crypto")

# Retention constants
TTL_1S_MS = 7_200_000  # 2 hours in milliseconds
TTL_1M_MS = 604_800_000  # 7 days in milliseconds

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


@dataclass
class Candle:
    """Candle data structure."""

    timestamp: int  # milliseconds
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trade_count: int
    is_closed: bool = True
    interval: str = "1m"

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "timestamp": self.timestamp,
            "datetime": datetime.fromtimestamp(self.timestamp / 1000).isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "quote_volume": self.quote_volume,
            "trade_count": self.trade_count,
            "is_closed": self.is_closed,
            "interval": self.interval,
        }


class CandleQueryHelper:
    """Helper class for querying candle data with automatic routing."""

    def __init__(self):
        self.redis = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=0,
            decode_responses=True,
            socket_keepalive=True,
        )
        self.influx = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
        self.query_api = self.influx.query_api()

    def get_latest_candle(self, symbol: str) -> Optional[Candle]:
        """
        Get the latest candle for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., 'btcusdt')

        Returns:
            Candle object or None if not found
        """
        try:
            key = f"candle:latest:{symbol}"
            data = self.redis.hgetall(key)

            if not data:
                return None

            return Candle(
                timestamp=int(data.get("kline_start", 0)),
                open=float(data.get("open", 0)),
                high=float(data.get("high", 0)),
                low=float(data.get("low", 0)),
                close=float(data.get("close", 0)),
                volume=float(data.get("volume", 0)),
                quote_volume=float(data.get("quote_volume", 0)),
                trade_count=int(data.get("trade_count", 0)),
                is_closed=bool(int(data.get("is_closed", 1))),
                interval=data.get("interval", "1m"),
            )

        except Exception as e:
            log.error("Error getting latest candle for %s: %s", symbol, e)
            return None

    def get_candles(
        self,
        symbol: str,
        interval: str = "1m",
        start_ms: Optional[int] = None,
        end_ms: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[Candle]:
        """
        Get candles for a symbol with automatic routing to KeyDB or InfluxDB.

        Args:
            symbol: Trading pair symbol (e.g., 'btcusdt')
            interval: Candle interval ('1s' or '1m')
            start_ms: Start timestamp in milliseconds (inclusive)
            end_ms: End timestamp in milliseconds (inclusive)
            limit: Maximum number of candles to return (only for KeyDB queries)

        Returns:
            List of Candle objects, sorted by timestamp ascending

        Raises:
            ValueError: If interval is invalid or time range exceeds retention
        """
        now_ms = int(time.time() * 1000)

        # Validate interval
        if interval not in ("1s", "1m"):
            raise ValueError(f"Invalid interval: {interval}. Must be '1s' or '1m'")

        # Set default end_ms if not provided
        if end_ms is None:
            end_ms = now_ms

        # Validate 1s interval retention
        if interval == "1s":
            oldest_allowed = now_ms - TTL_1S_MS
            if start_ms and start_ms < oldest_allowed:
                raise ValueError(
                    f"1s candles only available for last 2 hours. "
                    f"Requested start: {datetime.fromtimestamp(start_ms/1000)}, "
                    f"Oldest available: {datetime.fromtimestamp(oldest_allowed/1000)}"
                )
            # Query from KeyDB only
            return self._query_keydb(symbol, interval, start_ms, end_ms, limit)

        # For 1m interval, check if data is in KeyDB or InfluxDB
        redis_cutoff = now_ms - TTL_1M_MS

        if start_ms is None:
            # No start specified, query recent data from KeyDB
            return self._query_keydb(symbol, interval, start_ms, end_ms, limit)

        if start_ms >= redis_cutoff:
            # All data in KeyDB
            return self._query_keydb(symbol, interval, start_ms, end_ms, limit)

        elif end_ms < redis_cutoff:
            # All data in InfluxDB
            return self._query_influxdb(symbol, interval, start_ms, end_ms)

        else:
            # Split query: part from InfluxDB, part from KeyDB
            log.info(
                "Split query for %s: InfluxDB[%s to %s] + KeyDB[%s to %s]",
                symbol,
                datetime.fromtimestamp(start_ms / 1000),
                datetime.fromtimestamp(redis_cutoff / 1000),
                datetime.fromtimestamp(redis_cutoff / 1000),
                datetime.fromtimestamp(end_ms / 1000),
            )

            old_candles = self._query_influxdb(symbol, interval, start_ms, redis_cutoff - 1)
            new_candles = self._query_keydb(symbol, interval, redis_cutoff, end_ms, limit)

            # Merge and sort
            all_candles = old_candles + new_candles
            all_candles.sort(key=lambda c: c.timestamp)

            return all_candles

    def _query_keydb(
        self,
        symbol: str,
        interval: str,
        start_ms: Optional[int],
        end_ms: Optional[int],
        limit: Optional[int],
    ) -> List[Candle]:
        """Query candles from KeyDB sorted set."""
        try:
            key = f"candle:{interval}:{symbol}"

            # Determine query parameters
            if start_ms is not None and end_ms is not None:
                # Range query
                candles_json = self.redis.zrangebyscore(
                    key, start_ms, end_ms, withscores=False
                )
            elif limit is not None:
                # Recent N candles
                candles_json = self.redis.zrevrange(key, 0, limit - 1, withscores=False)
                candles_json.reverse()  # Sort ascending by time
            else:
                # All candles (be careful with this!)
                candles_json = self.redis.zrange(key, 0, -1, withscores=False)

            # Parse JSON candles
            candles = []
            for c_json in candles_json:
                try:
                    c = json.loads(c_json)
                    candles.append(
                        Candle(
                            timestamp=c["t"],
                            open=c["o"],
                            high=c["h"],
                            low=c["l"],
                            close=c["c"],
                            volume=c["v"],
                            quote_volume=c["qv"],
                            trade_count=c["n"],
                            is_closed=c.get("x", True),
                            interval=interval,
                        )
                    )
                except (json.JSONDecodeError, KeyError) as e:
                    log.warning("Failed to parse candle JSON: %s", e)
                    continue

            return candles

        except Exception as e:
            log.error("Error querying KeyDB for %s %s: %s", symbol, interval, e)
            return []

    def _query_influxdb(
        self, symbol: str, interval: str, start_ms: int, end_ms: int
    ) -> List[Candle]:
        """Query candles from InfluxDB."""
        try:
            # Convert milliseconds to RFC3339 format for InfluxDB
            start_rfc = datetime.fromtimestamp(start_ms / 1000).isoformat() + "Z"
            end_rfc = datetime.fromtimestamp(end_ms / 1000).isoformat() + "Z"

            # Build Flux query
            query = f'''
            from(bucket: "{INFLUX_BUCKET}")
              |> range(start: {start_rfc}, stop: {end_rfc})
              |> filter(fn: (r) => r._measurement == "candles")
              |> filter(fn: (r) => r.symbol == "{symbol}")
              |> filter(fn: (r) => r.interval == "{interval}")
              |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
              |> sort(columns: ["_time"], desc: false)
            '''

            result = self.query_api.query(query, org=INFLUX_ORG)

            candles = []
            for table in result:
                for record in table.records:
                    try:
                        candles.append(
                            Candle(
                                timestamp=int(record.get_time().timestamp() * 1000),
                                open=float(record.values.get("open", 0)),
                                high=float(record.values.get("high", 0)),
                                low=float(record.values.get("low", 0)),
                                close=float(record.values.get("close", 0)),
                                volume=float(record.values.get("volume", 0)),
                                quote_volume=float(record.values.get("quote_volume", 0)),
                                trade_count=int(record.values.get("trade_count", 0)),
                                is_closed=bool(record.values.get("is_closed", True)),
                                interval=interval,
                            )
                        )
                    except (KeyError, ValueError) as e:
                        log.warning("Failed to parse InfluxDB record: %s", e)
                        continue

            return candles

        except Exception as e:
            log.error("Error querying InfluxDB for %s %s: %s", symbol, interval, e)
            return []

    def close(self):
        """Close connections."""
        try:
            self.redis.close()
            self.influx.close()
        except Exception as e:
            log.error("Error closing connections: %s", e)


# Example usage for testing
if __name__ == "__main__":
    helper = CandleQueryHelper()

    try:
        # Test 1: Get latest candle
        print("\n=== Test 1: Latest Candle ===")
        latest = helper.get_latest_candle("btcusdt")
        if latest:
            print(f"Latest: {latest.to_dict()}")
        else:
            print("No latest candle found")

        # Test 2: Get last 100 1s candles
        print("\n=== Test 2: Last 100 1s Candles ===")
        candles_1s = helper.get_candles("btcusdt", "1s", limit=100)
        print(f"Retrieved {len(candles_1s)} 1s candles")
        if candles_1s:
            print(f"First: {candles_1s[0].to_dict()}")
            print(f"Last: {candles_1s[-1].to_dict()}")

        # Test 3: Get 1m candles for last 24 hours
        print("\n=== Test 3: 1m Candles (Last 24h) ===")
        now_ms = int(time.time() * 1000)
        day_ago_ms = now_ms - (24 * 3600 * 1000)
        candles_1m = helper.get_candles("btcusdt", "1m", start_ms=day_ago_ms, end_ms=now_ms)
        print(f"Retrieved {len(candles_1m)} 1m candles")

    finally:
        helper.close()
