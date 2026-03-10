#!/usr/bin/env python3
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, current_timestamp, udf, expr
from pyspark.sql.avro.functions import from_avro
from pyspark.sql.types import (
    BinaryType,
    BooleanType,
    DoubleType,
    LongType,
    StringType,
    StructField,
    StructType,
)

import os
import json

KAFKA_SERVER     = os.environ.get("KAFKA_BOOTSTRAP",  "kafka:9092")
MINIO_ENDPOINT   = os.environ.get("MINIO_ENDPOINT",   "http://minio:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "")

CHECKPOINT_TICKER = "s3a://cryptoprice/checkpoints/crypto_ticker_v1"
CHECKPOINT_TRADES = "s3a://cryptoprice/checkpoints/crypto_trades_v1"
CHECKPOINT_KLINES = "s3a://cryptoprice/checkpoints/crypto_klines_v1"

ICEBERG_TICKER = "iceberg_catalog.crypto_lakehouse.coin_ticker"
ICEBERG_TRADES = "iceberg_catalog.crypto_lakehouse.coin_trades"
ICEBERG_KLINES = "iceberg_catalog.crypto_lakehouse.coin_klines"

spark = (
    SparkSession.builder.appName("BinanceDualStreamToIceberg")
    .config("spark.sql.session.timeZone", "UTC")
    .config("spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
    .config("spark.sql.catalog.iceberg_catalog",
            "org.apache.iceberg.spark.SparkCatalog")
    .config("spark.sql.catalog.iceberg_catalog.type",            "jdbc")
    .config("spark.sql.catalog.iceberg_catalog.uri",
            f"jdbc:postgresql://{os.environ.get('POSTGRES_HOST', 'postgres')}:5432/iceberg_catalog")
    .config("spark.sql.catalog.iceberg_catalog.jdbc.user",       os.environ.get("POSTGRES_USER", ""))
    .config("spark.sql.catalog.iceberg_catalog.jdbc.password",   os.environ.get("POSTGRES_PASSWORD", ""))
    .config("spark.sql.catalog.iceberg_catalog.warehouse",
            "s3://cryptoprice/iceberg")
    .config("spark.sql.catalog.iceberg_catalog.io-impl",
            "org.apache.iceberg.aws.s3.S3FileIO")
    .config("spark.sql.catalog.iceberg_catalog.s3.endpoint",          MINIO_ENDPOINT)
    .config("spark.sql.catalog.iceberg_catalog.s3.access-key-id",     MINIO_ACCESS_KEY)
    .config("spark.sql.catalog.iceberg_catalog.s3.secret-access-key", MINIO_SECRET_KEY)
    .config("spark.sql.catalog.iceberg_catalog.s3.path-style-access", "true")
    .config("spark.sql.catalog.iceberg_catalog.client.region",        "us-east-1")
    .config("spark.hadoop.fs.s3a.endpoint",         MINIO_ENDPOINT)
    .config("spark.hadoop.fs.s3a.access.key",       MINIO_ACCESS_KEY)
    .config("spark.hadoop.fs.s3a.secret.key",       MINIO_SECRET_KEY)
    .config("spark.hadoop.fs.s3a.path.style.access", "true")
    .config("spark.hadoop.fs.s3a.impl",
            "org.apache.hadoop.fs.s3a.S3AFileSystem")
    .config("spark.hadoop.fs.s3.impl",
            "org.apache.hadoop.fs.s3a.S3AFileSystem")
    .config("spark.hadoop.fs.s3a.connection.ssl.enabled", "false")
    .config("spark.hadoop.fs.s3a.aws.credentials.provider",
            "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider")
    .config("spark.sql.defaultCatalog", "iceberg_catalog")
    .config("spark.cores.max", "2")
    .getOrCreate()
)
spark.sparkContext.setLogLevel("WARN")

ticker_schema = StructType([
    StructField("event_time",           LongType(),   True),
    StructField("symbol",               StringType(), True),
    StructField("close",                DoubleType(), True),
    StructField("bid",                  DoubleType(), True),
    StructField("ask",                  DoubleType(), True),
    StructField("24h_open",             DoubleType(), True),
    StructField("24h_high",             DoubleType(), True),
    StructField("24h_low",              DoubleType(), True),
    StructField("24h_volume",           DoubleType(), True),
    StructField("24h_quote_volume",     DoubleType(), True),
    StructField("24h_price_change",     DoubleType(), True),
    StructField("24h_price_change_pct", DoubleType(), True),
    StructField("24h_trade_count",      LongType(),   True),
])

trades_schema = StructType([
    StructField("event_time",     LongType(),    True),
    StructField("symbol",         StringType(),  True),
    StructField("agg_trade_id",   LongType(),    True),
    StructField("price",          DoubleType(),  True),
    StructField("quantity",       DoubleType(),  True),
    StructField("trade_time",     LongType(),    True),
    StructField("is_buyer_maker", BooleanType(), True),
])

klines_schema = StructType([
    StructField("event_time",   LongType(),    True),
    StructField("symbol",       StringType(),  True),
    StructField("kline_start",  LongType(),    True),
    StructField("kline_close",  LongType(),    True),
    StructField("interval",     StringType(),  True),
    StructField("open",         DoubleType(),  True),
    StructField("high",         DoubleType(),  True),
    StructField("low",          DoubleType(),  True),
    StructField("close",        DoubleType(),  True),
    StructField("volume",       DoubleType(),  True),
    StructField("quote_volume", DoubleType(),  True),
    StructField("trade_count",  LongType(),    True),
    StructField("is_closed",    BooleanType(), True),
])


# ══════════════════════════════════════════════════════════════════════════════
# Avro Schema Definitions (must match producer schemas)
# ══════════════════════════════════════════════════════════════════════════════

TICKER_AVRO_SCHEMA = json.dumps({
    "type": "record",
    "name": "Ticker",
    "namespace": "com.cryptoprice",
    "fields": [
        {"name": "event_time", "type": "long"},
        {"name": "symbol", "type": "string"},
        {"name": "close", "type": "double"},
        {"name": "bid", "type": "double"},
        {"name": "ask", "type": "double"},
        {"name": "h24_open", "type": "double"},
        {"name": "h24_high", "type": "double"},
        {"name": "h24_low", "type": "double"},
        {"name": "h24_volume", "type": "double"},
        {"name": "h24_quote_volume", "type": "double"},
        {"name": "h24_price_change", "type": "double"},
        {"name": "h24_price_change_pct", "type": "double"},
        {"name": "h24_trade_count", "type": "long"}
    ]
})

TRADES_AVRO_SCHEMA = json.dumps({
    "type": "record",
    "name": "AggTrade",
    "namespace": "com.cryptoprice",
    "fields": [
        {"name": "event_time", "type": "long"},
        {"name": "symbol", "type": "string"},
        {"name": "agg_trade_id", "type": "long"},
        {"name": "price", "type": "double"},
        {"name": "quantity", "type": "double"},
        {"name": "trade_time", "type": "long"},
        {"name": "is_buyer_maker", "type": "boolean"}
    ]
})

KLINES_AVRO_SCHEMA = json.dumps({
    "type": "record",
    "name": "Kline",
    "namespace": "com.cryptoprice",
    "fields": [
        {"name": "event_time", "type": "long"},
        {"name": "symbol", "type": "string"},
        {"name": "kline_start", "type": "long"},
        {"name": "kline_close", "type": "long"},
        {"name": "interval", "type": "string"},
        {"name": "open", "type": "double"},
        {"name": "high", "type": "double"},
        {"name": "low", "type": "double"},
        {"name": "close", "type": "double"},
        {"name": "volume", "type": "double"},
        {"name": "quote_volume", "type": "double"},
        {"name": "trade_count", "type": "long"},
        {"name": "is_closed", "type": "boolean"}
    ]
})


def read_kafka(topic: str, avro_schema: str):
    """
    Read from Kafka and deserialize Confluent Avro format.
    Confluent wire format: [magic_byte:1][schema_id:4][avro_binary:N]
    We need to strip first 5 bytes before passing to from_avro()
    """
    return (
        spark.readStream
        .format("kafka")
        .option("kafka.bootstrap.servers", KAFKA_SERVER)
        .option("subscribe", topic)
        .option("startingOffsets", "latest")
        .option("failOnDataLoss", "false")
        .option("maxOffsetsPerTrigger", 500_000)
        .load()
        # Strip Confluent wire format header (5 bytes: magic + schema ID)
        .selectExpr("substring(value, 6, length(value)-5) as avro_value")
        .select(from_avro(col("avro_value"), avro_schema).alias("data"))
        .select("data.*")
    )


spark.sql("CREATE DATABASE IF NOT EXISTS iceberg_catalog.crypto_lakehouse")

spark.sql("""
    CREATE TABLE IF NOT EXISTS iceberg_catalog.crypto_lakehouse.coin_ticker (
        event_time          BIGINT,
        symbol              STRING,
        close               DOUBLE,
        bid                 DOUBLE,
        ask                 DOUBLE,
        h24_open            DOUBLE,
        h24_high            DOUBLE,
        h24_low             DOUBLE,
        h24_volume          DOUBLE,
        h24_quote_volume    DOUBLE,
        h24_price_change    DOUBLE,
        h24_price_change_pct DOUBLE,
        h24_trade_count     BIGINT,
        event_timestamp     TIMESTAMP,
        ingested_at         TIMESTAMP
    )
    USING iceberg
    PARTITIONED BY (days(event_timestamp))
""")

spark.sql("""
    CREATE TABLE IF NOT EXISTS iceberg_catalog.crypto_lakehouse.coin_trades (
        event_time      BIGINT,
        symbol          STRING,
        agg_trade_id    BIGINT,
        price           DOUBLE,
        quantity        DOUBLE,
        trade_time      BIGINT,
        is_buyer_maker  BOOLEAN,
        event_timestamp TIMESTAMP,
        trade_timestamp TIMESTAMP,
        ingested_at     TIMESTAMP
    )
    USING iceberg
    PARTITIONED BY (days(trade_timestamp))
""")

spark.sql("""
    CREATE TABLE IF NOT EXISTS iceberg_catalog.crypto_lakehouse.coin_klines (
        event_time      BIGINT,
        symbol          STRING,
        kline_start     BIGINT,
        kline_close     BIGINT,
        interval        STRING,
        open            DOUBLE,
        high            DOUBLE,
        low             DOUBLE,
        close           DOUBLE,
        volume          DOUBLE,
        quote_volume    DOUBLE,
        trade_count     BIGINT,
        is_closed       BOOLEAN,
        kline_timestamp TIMESTAMP,
        ingested_at     TIMESTAMP
    )
    USING iceberg
    PARTITIONED BY (days(kline_timestamp))
""")


ticker_df = (
    read_kafka("crypto_ticker", TICKER_AVRO_SCHEMA)
    .filter(col("event_time").isNotNull())
    .withColumn("event_timestamp", (col("event_time") / 1000).cast("timestamp"))
    .withColumn("ingested_at", current_timestamp())
    .withWatermark("event_timestamp", "1 minute")
    .dropDuplicates(["symbol", "event_timestamp"])
    .withColumnRenamed("24h_open",             "h24_open")
    .withColumnRenamed("24h_high",             "h24_high")
    .withColumnRenamed("24h_low",              "h24_low")
    .withColumnRenamed("24h_volume",           "h24_volume")
    .withColumnRenamed("24h_quote_volume",     "h24_quote_volume")
    .withColumnRenamed("24h_price_change",     "h24_price_change")
    .withColumnRenamed("24h_price_change_pct", "h24_price_change_pct")
    .withColumnRenamed("24h_trade_count",      "h24_trade_count")
)

query_ticker = (
    ticker_df.writeStream
    .format("iceberg")
    .outputMode("append")
    .trigger(processingTime="1 minute")
    .option("checkpointLocation", CHECKPOINT_TICKER)
    .toTable(ICEBERG_TICKER)
)

trades_df = (
    read_kafka("crypto_trades", TRADES_AVRO_SCHEMA)
    .filter(col("event_time").isNotNull())
    .withColumn("event_timestamp", (col("event_time") / 1000).cast("timestamp"))
    .withColumn("trade_timestamp",  (col("trade_time") / 1000).cast("timestamp"))
    .withColumn("ingested_at", current_timestamp())
)

query_trades = (
    trades_df.writeStream
    .format("iceberg")
    .outputMode("append")
    .trigger(processingTime="1 minute")
    .option("checkpointLocation", CHECKPOINT_TRADES)
    .toTable(ICEBERG_TRADES)
)

klines_df = (
    read_kafka("crypto_klines", KLINES_AVRO_SCHEMA)
    .filter(col("kline_start").isNotNull())
    .withColumn("kline_timestamp", (col("kline_start") / 1000).cast("timestamp"))
    .withColumn("ingested_at", current_timestamp())
    .withWatermark("kline_timestamp", "2 minutes")
    .dropDuplicates(["symbol", "kline_start", "interval"])
)

query_klines = (
    klines_df.writeStream
    .format("iceberg")
    .outputMode("append")
    .trigger(processingTime="1 minute")
    .option("checkpointLocation", CHECKPOINT_KLINES)
    .toTable(ICEBERG_KLINES)
)

spark.streams.awaitAnyTermination()

