#!/usr/bin/env python3
import json
import logging
import os
import time

import redis
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from pyflink.common import Configuration, Types
from pyflink.datastream import CheckpointingMode, StreamExecutionEnvironment
from pyflink.datastream.functions import FlatMapFunction, SinkFunction
from pyflink.datastream.state_backend import HashMapStateBackend
from pyflink.table import StreamTableEnvironment

KAFKA_BOOTSTRAP  = os.environ.get("KAFKA_BOOTSTRAP",   "kafka:9092")
MINIO_ENDPOINT   = os.environ.get("MINIO_ENDPOINT",    "http://minio:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY",  "minioadmin")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY",  "minioadmin")
REDIS_HOST       = os.environ.get("REDIS_HOST",        "keydb")
REDIS_PORT       = int(os.environ.get("REDIS_PORT",    "6379"))

INFLUX_URL    = os.environ.get("INFLUX_URL",    "http://influxdb:8086")
INFLUX_TOKEN  = os.environ.get("INFLUX_TOKEN",  "")
INFLUX_ORG    = os.environ.get("INFLUX_ORG",    "vi")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "crypto")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


class NoopSink(SinkFunction):
    """Terminal sink that discards output.
    KeyDBWriter / InfluxDBWriter work via side-effects inside flat_map;
    the downstream stream is always empty, but Flink needs a terminal sink.
    """
    def invoke(self, value, context=None):
        pass


class KeyDBWriter(FlatMapFunction):
    def open(self, runtime_context):
        self._r = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=0,
            decode_responses=True,
            socket_keepalive=True,
        )

    def close(self):
        try:
            self._r.close()
        except Exception as e:
            log.error("[KeyDB] close error: %s", e)

    def flat_map(self, value):
        try:
            if isinstance(value, (str, bytes)):
                value = json.loads(value)
            symbol = value.get("symbol")
            if not symbol:
                return []
            event_time = int(value.get("event_time", 0))
            price      = float(value.get("close", 0))
            volume     = float(value.get("h24_volume", 0))
            cutoff     = event_time - 300_000
            pipe = self._r.pipeline()
            pipe.hset(
                f"ticker:latest:{symbol}",
                mapping={
                    "price":      price,
                    "bid":        float(value.get("bid", 0)),
                    "ask":        float(value.get("ask", 0)),
                    "volume":     volume,
                    "event_time": event_time,
                },
            )
            pipe.zadd(f"ticker:history:{symbol}", {f"{price}:{volume}": event_time})
            pipe.zremrangebyscore(f"ticker:history:{symbol}", 0, cutoff)
            pipe.execute()
        except Exception as e:
            s = value.get("symbol") if isinstance(value, dict) else "unknown"
            log.error("[KeyDB] flat_map error | symbol=%s error=%s", s, e)
        return []


class InfluxDBWriter(FlatMapFunction):
    def __init__(self, batch_size: int = 500, flush_interval_sec: float = 1.0):
        self.batch_size = batch_size
        self.flush_interval_sec = flush_interval_sec

    def open(self, runtime_context):
        self._client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
        self._write_api = self._client.write_api(write_options=SYNCHRONOUS)
        self._buffer = []
        self._last_flush_time = time.time()

    def _flush(self):
        if not self._buffer:
            return
        try:
            self._write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=self._buffer)
        except Exception as e:
            log.error("[InfluxDB] flush error (dropped %d points): %s", len(self._buffer), e)
        finally:
            self._buffer.clear()
            self._last_flush_time = time.time()

    def close(self):
        try:
            self._flush()
            self._client.close()
        except Exception as e:
            log.error("[InfluxDB] close error: %s", e)

    def flat_map(self, value):
        try:
            if isinstance(value, (str, bytes)):
                value = json.loads(value)
            point = (
                Point("market_ticks")
                .tag("symbol",   value["symbol"])
                .tag("exchange", "binance")
                .field("price",             float(value.get("close", 0)))
                .field("bid",               float(value.get("bid", 0)))
                .field("ask",               float(value.get("ask", 0)))
                .field("volume",            float(value.get("h24_volume", 0)))
                .field("quote_volume",      float(value.get("h24_quote_volume", 0)))
                .field("price_change_pct",  float(value.get("h24_price_change_pct", 0)))
                .field("trade_count",       int(value.get("h24_trade_count", 0)))
                .time(int(value["event_time"]), WritePrecision.MS)
            )
            self._buffer.append(point)
            if (
                len(self._buffer) >= self.batch_size
                or (time.time() - self._last_flush_time) >= self.flush_interval_sec
            ):
                self._flush()
        except Exception as e:
            s = value.get("symbol") if isinstance(value, dict) else "unknown"
            log.error("[InfluxDB] flat_map error | symbol=%s error=%s", s, e)
        return []


def run():
    env = StreamExecutionEnvironment.get_execution_environment()
    env.set_state_backend(HashMapStateBackend())

    s3_config = Configuration()
    s3_config.set_string("s3.endpoint",          MINIO_ENDPOINT)
    s3_config.set_string("s3.access-key",        MINIO_ACCESS_KEY)
    s3_config.set_string("s3.secret-key",        MINIO_SECRET_KEY)
    s3_config.set_string("s3.path.style.access", "true")
    s3_config.set_string("fs.s3a.endpoint",           MINIO_ENDPOINT)
    s3_config.set_string("fs.s3a.access.key",         MINIO_ACCESS_KEY)
    s3_config.set_string("fs.s3a.secret.key",         MINIO_SECRET_KEY)
    s3_config.set_string("fs.s3a.path.style.access",  "true")
    s3_config.set_string("fs.s3a.impl",               "org.apache.hadoop.fs.s3a.S3AFileSystem")
    env.configure(s3_config)

    env.get_checkpoint_config().set_checkpoint_storage_dir(
        "file:///tmp/flink-checkpoints"
    )
    env.enable_checkpointing(30_000)
    chk = env.get_checkpoint_config()
    chk.set_checkpointing_mode(CheckpointingMode.EXACTLY_ONCE)
    chk.enable_unaligned_checkpoints()
    chk.set_min_pause_between_checkpoints(10_000)
    chk.set_checkpoint_timeout(60_000)
    t_env = StreamTableEnvironment.create(env)

    t_env.execute_sql(f"""
        CREATE TABLE kafka_ticker (
            event_time             BIGINT,
            symbol                 STRING,
            `close`                DOUBLE,
            bid                    DOUBLE,
            ask                    DOUBLE,
            `24h_volume`           DOUBLE,
            `24h_quote_volume`     DOUBLE,
            `24h_price_change_pct` DOUBLE,
            `24h_trade_count`      BIGINT
        ) WITH (
            'connector'                    = 'kafka',
            'topic'                        = 'crypto_ticker',
            'properties.bootstrap.servers' = '{KAFKA_BOOTSTRAP}',
            'properties.group.id'          = 'flink_crypto_ticker_v1',
            'scan.startup.mode'            = 'latest-offset',
            'format'                       = 'json',
            'json.ignore-parse-errors'     = 'true',
            'json.fail-on-missing-field'   = 'false'
        )
    """)

    table = t_env.sql_query("""
        SELECT
            event_time,
            symbol,
            `close`,
            bid,
            ask,
            `24h_volume`           AS h24_volume,
            `24h_quote_volume`     AS h24_quote_volume,
            `24h_price_change_pct` AS h24_price_change_pct,
            `24h_trade_count`      AS h24_trade_count
        FROM kafka_ticker
    """)
    ds_row = t_env.to_data_stream(table)

    def row_to_dict(row):
        return json.dumps({
            "event_time":           row[0],
            "symbol":               row[1],
            "close":                row[2],
            "bid":                  row[3],
            "ask":                  row[4],
            "h24_volume":           row[5],
            "h24_quote_volume":     row[6],
            "h24_price_change_pct": row[7],
            "h24_trade_count":      row[8],
        })

    ds_dict = ds_row.map(row_to_dict, output_type=Types.STRING())
    ds_dict.flat_map(KeyDBWriter(),    output_type=Types.STRING()).name("Write_To_KeyDB").add_sink(NoopSink())
    ds_dict.flat_map(InfluxDBWriter(), output_type=Types.STRING()).name("Write_To_InfluxDB").add_sink(NoopSink())

    env.execute("CryptoTicker_Kafka_to_KeyDB_InfluxDB")


if __name__ == "__main__":
    run()
