#!/usr/bin/env python3
import os
import subprocess
from pathlib import Path

from dagster import (
    AssetExecutionContext,
    Definitions,
    ScheduleDefinition,
    asset,
    get_dagster_logger,
)

PROJECT_DIR = Path(os.environ.get("CRYPTO_PROJECT_DIR", "/app"))

SPARK_HOME = Path(os.environ.get("SPARK_HOME", "/opt/spark"))

SPARK_EVENTS_DIR = Path(os.environ.get("SPARK_EVENTS_DIR", "/opt/spark-events"))

SPARK_MASTER = os.environ.get("SPARK_MASTER", "spark://spark-master:7077")

SPARK_PACKAGES = ",".join([
    "org.apache.iceberg:iceberg-spark-runtime-3.4_2.12:1.5.2",
    "org.apache.iceberg:iceberg-aws-bundle:1.5.2",
    "org.apache.hadoop:hadoop-aws:3.3.4",
    "org.postgresql:postgresql:42.7.2",
])

def _run_spark_job(context: AssetExecutionContext, script_name: str, extra_args: list[str] = None) -> None:
    logger = get_dagster_logger()
    script_path = PROJECT_DIR / "src" / script_name

    if not script_path.exists():
        raise FileNotFoundError(f"Script not found: {script_path}")

    SPARK_EVENTS_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(SPARK_HOME / "bin" / "spark-submit"),
        "--master", SPARK_MASTER,
        "--packages", SPARK_PACKAGES,
        "--conf", "spark.eventLog.enabled=true",
        "--conf", f"spark.eventLog.dir=file://{SPARK_EVENTS_DIR}",
        str(script_path),
    ]

    if extra_args:
        cmd.extend(extra_args)

    logger.info("Running command: %s", " ".join(cmd))

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,    # merge stderr into stdout for a single stream
        text=True,
        bufsize=1,                   # line-buffered
        cwd=str(PROJECT_DIR),
    )

    for line in process.stdout:
        line = line.rstrip()
        if line:
            logger.info(line)

    process.wait()

    if process.returncode != 0:
        raise Exception(
            f"Spark job '{script_name}' failed with exit code {process.returncode}. "
            f"Check logs in Dagster UI or Spark History Server (http://localhost:18080)."
        )

    logger.info("Spark job '%s' completed successfully.", script_name)


@asset(
    description=(
        "Pulls 1h OHLCV klines from Binance API for all USDT pairs, "
        "fetches only rows newer than the last run, then writes to Iceberg "
        "table local.crypto.historical_hourly on MinIO."
    ),
    group_name="ingestion",
)
def iceberg_historical_klines(context: AssetExecutionContext) -> None:
    _run_spark_job(
        context,
        script_name="ingest_historical_iceberg.py",
        extra_args=["--mode", "incremental"],
    )


@asset(
    description=(
        "Runs 4 maintenance tasks on Iceberg tables: "
        "(1) Compact small files into ~128 MB files, "
        "(2) Rewrite manifests to reduce metadata overhead, "
        "(3) Expire snapshots older than 48 hours, "
        "(4) Remove orphan files no longer referenced."
    ),
    group_name="maintenance",
)
def iceberg_table_maintenance(context: AssetExecutionContext) -> None:
    _run_spark_job(
        context,
        script_name="iceberg_maintenance.py",
    )


schedule_daily_klines = ScheduleDefinition(
    name="daily_kline_update",
    target=iceberg_historical_klines,
    cron_schedule="0 2 * * *",
    description="Runs daily at 02:00 AM to ingest latest klines into Iceberg.",
)

schedule_weekly_maintenance = ScheduleDefinition(
    name="weekly_iceberg_maintenance",
    target=iceberg_table_maintenance,
    cron_schedule="0 3 * * 0",
    description="Runs every Sunday at 03:00 AM to compact and clean up Iceberg tables.",
)

defs = Definitions(
    assets=[
        iceberg_historical_klines,
        iceberg_table_maintenance,
    ],
    schedules=[
        schedule_daily_klines,
        schedule_weekly_maintenance,
    ],
)
