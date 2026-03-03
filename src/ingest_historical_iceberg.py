#!/usr/bin/env python3
import argparse
import logging
import time
from datetime import datetime, timedelta, timezone

import requests
from pyspark.sql import SparkSession
from pyspark.sql.types import (
    DoubleType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)
from pyspark.sql import functions as F

import os

MINIO_ENDPOINT   = os.environ.get("MINIO_ENDPOINT",   "http://minio:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET     = "cryptoprice"
ICEBERG_CATALOG  = "local"
ICEBERG_DB       = "crypto"
ICEBERG_TABLE    = "historical_hourly"
FULL_TABLE_NAME  = f"{ICEBERG_CATALOG}.{ICEBERG_DB}.{ICEBERG_TABLE}"

BINANCE_EXCHANGE_INFO = "https://api.binance.com/api/v3/exchangeInfo"
BINANCE_KLINES        = "https://api.binance.com/api/v3/klines"

KLINE_INTERVAL   = "1h"
KLINES_PER_REQ   = 1000
API_RATE_SLEEP   = 0.12
MAX_RETRIES      = 5

BINANCE_EPOCH_MS = int(datetime(2017, 7, 14, tzinfo=timezone.utc).timestamp() * 1000)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

KLINE_SCHEMA = StructType([
    StructField("open_time",   LongType(),      False),
    StructField("symbol",      StringType(),    False),
    StructField("open",        DoubleType(),    True),
    StructField("high",        DoubleType(),    True),
    StructField("low",         DoubleType(),    True),
    StructField("close",       DoubleType(),    True),
    StructField("volume",      DoubleType(),    True),
    StructField("quote_volume",DoubleType(),    True),
    StructField("trade_count", LongType(),      True),
    StructField("taker_buy_volume",       DoubleType(), True),
    StructField("taker_buy_quote_volume", DoubleType(), True),
])
def fetch_usdt_symbols() -> list[str]:
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(BINANCE_EXCHANGE_INFO, timeout=15)
            resp.raise_for_status()
            symbols = [
                s["symbol"]
                for s in resp.json().get("symbols", [])
                if s["quoteAsset"] == "USDT"
                and s["status"] == "TRADING"
                and s.get("isSpotTradingAllowed", False)
            ]
            log.info("Found %d active USDT spot pairs.", len(symbols))
            return sorted(symbols)
        except Exception as e:
            log.warning("fetch_usdt_symbols attempt %d failed: %s", attempt + 1, e)
            time.sleep(2 ** attempt)
    raise RuntimeError("Cannot fetch symbol list from Binance after retries.")


def process_and_write_chunk(spark: SparkSession, rows: list, symbol: str, chunk_start_ms: int) -> int:
    if not rows:
        return 0

    df = (
        spark.createDataFrame(rows, schema=KLINE_SCHEMA)
        .withColumn("event_time", (F.col("open_time") / 1000).cast(TimestampType()))
    )
    df = df.sortWithinPartitions("symbol", "event_time")

    df.writeTo(FULL_TABLE_NAME).append()

    log.info("[%s] Wrote %d candles (chunk start=%s).", symbol, len(rows),
             datetime.fromtimestamp(chunk_start_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d"))
    return len(rows)

def build_spark() -> SparkSession:
    return (
        SparkSession.builder
        .appName("BinanceHistoricalHourly_to_Iceberg")
        .config("spark.sql.extensions",
                "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
        .config("spark.sql.catalog.local",
                "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.local.type",       "hadoop")
        .config("spark.sql.catalog.local.warehouse",
                f"s3a://{MINIO_BUCKET}/iceberg-warehouse")
        .config("spark.hadoop.fs.s3a.endpoint",          MINIO_ENDPOINT)
        .config("spark.hadoop.fs.s3a.access.key",        MINIO_ACCESS_KEY)
        .config("spark.hadoop.fs.s3a.secret.key",        MINIO_SECRET_KEY)
        .config("spark.hadoop.fs.s3a.path.style.access", "true")
        .config("spark.hadoop.fs.s3a.impl",
                "org.apache.hadoop.fs.s3a.S3AFileSystem")
        .config("spark.hadoop.fs.s3a.aws.credentials.provider",
                "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider")
        .config("spark.sql.shuffle.partitions", "8")
        .getOrCreate()
    )


def ensure_table(spark: SparkSession):
    spark.sql(f"CREATE DATABASE IF NOT EXISTS {ICEBERG_CATALOG}.{ICEBERG_DB}")
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {FULL_TABLE_NAME} (
            open_time               BIGINT,
            symbol                  STRING,
            open                    DOUBLE,
            high                    DOUBLE,
            low                     DOUBLE,
            close                   DOUBLE,
            volume                  DOUBLE,
            quote_volume            DOUBLE,
            trade_count             BIGINT,
            taker_buy_volume        DOUBLE,
            taker_buy_quote_volume  DOUBLE,
            event_time              TIMESTAMP
        )
        USING iceberg
        PARTITIONED BY (symbol, years(event_time))
        TBLPROPERTIES (
            'write.format.default'        = 'parquet',
            'write.parquet.compression-codec' = 'zstd',
            'write.metadata.compression-codec' = 'gzip',
            'write.target-file-size-bytes' = '134217728'
        )
    """)
    log.info("Iceberg table %s is ready.", FULL_TABLE_NAME)


def get_last_open_time(spark: SparkSession, symbol: str) -> int:
    try:
        row = spark.sql(f"""
            SELECT MAX(open_time) AS max_ts
            FROM {FULL_TABLE_NAME}
            WHERE symbol = '{symbol}'
        """).first()
        if row and row["max_ts"]:
            return int(row["max_ts"]) + 3_600_000
    except Exception:
        pass
    return BINANCE_EPOCH_MS


def write_symbol(spark: SparkSession, symbol: str, start_ms: int, end_ms: int) -> int:
    FLUSH_THRESHOLD = 10_000

    total_written  = 0
    current_ms     = start_ms
    chunk_start_ms = start_ms
    chunk_buffer: list = []

    while current_ms < end_ms:
        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.get(
                    BINANCE_KLINES,
                    params={
                        "symbol":    symbol,
                        "interval":  KLINE_INTERVAL,
                        "startTime": current_ms,
                        "endTime":   end_ms,
                        "limit":     KLINES_PER_REQ,
                    },
                    timeout=15,
                )
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 60))
                    log.warning("[%s] Rate limited. Sleeping %ds.", symbol, retry_after)
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                klines = resp.json()
                break
            except Exception as e:
                log.warning("[%s] klines attempt %d failed: %s", symbol, attempt + 1, e)
                time.sleep(2 ** attempt)
        else:
            log.error("[%s] Giving up on window starting %d.", symbol, current_ms)
            break

        if not klines:
            break

        for k in klines:
            chunk_buffer.append([
                int(k[0]),
                symbol,
                float(k[1]),
                float(k[2]),
                float(k[3]),
                float(k[4]),
                float(k[5]),
                float(k[7]),
                int(k[8]),
                float(k[9]),
                float(k[10]),
            ])

        if len(chunk_buffer) >= FLUSH_THRESHOLD:
            total_written += process_and_write_chunk(spark, chunk_buffer, symbol, chunk_start_ms)
            chunk_buffer.clear()
            chunk_start_ms = int(klines[-1][0]) + 3_600_000

        current_ms = int(klines[-1][0]) + 3_600_000
        time.sleep(API_RATE_SLEEP)

    if chunk_buffer:
        total_written += process_and_write_chunk(spark, chunk_buffer, symbol, chunk_start_ms)

    if total_written == 0:
        log.info("[%s] No new candles.", symbol)
    return total_written
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode", choices=["backfill", "incremental"], default="incremental",
        help=(
            "backfill   = kéo từ BINANCE_EPOCH đến hôm nay (chạy 1 lần đầu)\n"
            "incremental = chỉ kéo từ nến cuối đã lưu đến hôm nay (chạy hàng ngày)"
        ),
    )
    parser.add_argument(
        "--symbols", nargs="*", default=None,
        help="Danh sách mã cụ thể, VD: BTCUSDT ETHUSDT. Mặc định: tất cả USDT pairs.",
    )
    args = parser.parse_args()

    now_ms  = int(datetime.now(timezone.utc).timestamp() * 1000)
    end_ms  = now_ms - (now_ms % 3_600_000)

    symbols = args.symbols or fetch_usdt_symbols()
    log.info("Mode: %s | Symbols: %d | End: %s",
             args.mode, len(symbols),
             datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))

    spark = build_spark()
    spark.sparkContext.setLogLevel("WARN")
    ensure_table(spark)

    total_rows  = 0
    total_syms  = len(symbols)

    for idx, symbol in enumerate(symbols, 1):
        log.info("[%d/%d] Processing %s ...", idx, total_syms, symbol)
        if args.mode == "backfill":
            start_ms = BINANCE_EPOCH_MS
        else:
            start_ms = get_last_open_time(spark, symbol)

        if start_ms >= end_ms:
            log.info("[%s] Already up-to-date.", symbol)
            continue

        try:
            n = write_symbol(spark, symbol, start_ms, end_ms)
            total_rows += n
        except Exception as e:
            log.error("[%s] Failed: %s — skipping.", symbol, e)

    log.info("Done. Total candles written: %d", total_rows)

    try:
        size_df = spark.sql(f"""
            SELECT
                COUNT(*)                                        AS total_rows,
                COUNT(DISTINCT symbol)                          AS total_symbols,
                MIN(event_time)                                 AS earliest,
                MAX(event_time)                                 AS latest
            FROM {FULL_TABLE_NAME}
        """)
        size_df.show(truncate=False)
        files_df = spark.sql(f"SELECT SUM(file_size_in_bytes) AS bytes FROM {FULL_TABLE_NAME}.files")
        files_df.show()
    except Exception as e:
        log.warning("Could not compute table stats: %s", e)

    spark.stop()


if __name__ == "__main__":
    main()
