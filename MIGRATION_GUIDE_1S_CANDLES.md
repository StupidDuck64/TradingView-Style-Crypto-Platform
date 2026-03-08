# Migration Guide: 1-Minute to 1-Second Candles

## Changes Summary

### 1. Producer Configuration
- **File**: `docker-compose.yml`
- **Change**: Added `KLINE_INTERVAL: "1s"` environment variable to producer service
- **Impact**: Producer now subscribes to 1-second candles from Binance WebSocket

### 2. KeyDB Storage Schema
- **File**: `src/ingest_flink_crypto.py`
- **Changes**:
  - `KeyDBKlineWriter` class redesigned with interval-specific keys
  - New keys:
    - `candle:1s:{symbol}` - TTL 2 hours (7200s)
    - `candle:1m:{symbol}` - TTL 7 days (604800s)
  - Old `candle:history:{symbol}` deprecated
- **Impact**: Optimized memory usage with automatic cleanup via TTL

### 3. Aggregation Job (NEW)
- **File**: `src/aggregate_candles.py` (mode `--mode 1s-to-1m`)
- **Purpose**: Aggregate 60 × 1s candles into 1 × 1m candle
- **Schedule**: Runs every minute via cron
- **Writes to**:
  - KeyDB: `candle:1m:{symbol}`
  - InfluxDB: measurement `candles` with tag `interval=1m`

### 4. Candle Aggregator Service (NEW)
- **File**: `docker-compose.yml`
- **Service**: `candle-aggregator`
- **Function**: Runs cron job to execute `aggregate_candles.py --mode 1s-to-1m` every minute
- **Dependencies**: KeyDB, InfluxDB

### 5. InfluxDB Retention Setup (NEW)
- **File**: `setup_influx_retention.sh`
- **Purpose**: Configure retention policies for InfluxDB
- **Policies**:
  - 1s candles: 1 hour retention
  - 1m candles: 30 days retention

### 6. Query Helper Library (NEW)
- **File**: `src/candle_query_helper.py`
- **Purpose**: Provides high-level API for backend to query candles
- **Features**:
  - Automatic routing between KeyDB and InfluxDB
  - Smart query splitting for time ranges crossing retention boundaries
  - Type-safe Candle dataclass

### 7. Documentation
- **File**: `CANDLE_STORAGE_ARCHITECTURE.md`
- **Content**: Complete architecture documentation with:
  - Storage schema details
  - Data flow diagrams
  - API query patterns
  - Performance characteristics
  - Troubleshooting guide

## Deployment Steps

### Step 1: Backup Existing Data (IMPORTANT!)

```bash
# Backup KeyDB
docker exec keydb redis-cli --rdb /data/backup-$(date +%Y%m%d).rdb

# Backup InfluxDB
docker exec influxdb influx backup /tmp/influx-backup-$(date +%Y%m%d)
```

### Step 2: Stop Existing Services

```bash
cd cryptoprice_local
docker-compose down
```

### Step 3: Pull Latest Code

```bash
git pull origin main
# or manually copy updated files
```

### Step 4: Update Docker Compose

File changes are already in place:
- `docker-compose.yml`: Added `KLINE_INTERVAL: "1s"` and `candle-aggregator` service
- Source files updated: `ingest_flink_crypto.py`, new files added

### Step 5: Start Services

```bash
# Start all services
docker-compose up -d

# Check if all services are running
docker-compose ps

# Expected services:
# - producer (with KLINE_INTERVAL=1s)
# - flink-jobmanager
# - flink-taskmanager
# - candle-aggregator (NEW)
# - keydb
# - influxdb
# - kafka
# - ... (other services)
```

### Step 6: Verify Data Flow

```bash
# 1. Check producer is receiving 1s candles
docker logs -f producer | grep "kline_1s"

# 2. Check Flink is processing
docker logs -f flink-taskmanager | grep "candle"

# 3. Verify KeyDB has 1s data
docker exec keydb redis-cli KEYS "candle:1s:*"
docker exec keydb redis-cli ZCARD "candle:1s:btcusdt"

# 4. Check aggregation job is running
docker logs -f candle-aggregator

# Expected output every minute:
# "Aggregating 1s→1m for minute: ..."
# "Successfully aggregated X/Y symbols"

# 5. Verify 1m aggregated data exists
docker exec keydb redis-cli ZCARD "candle:1m:btcusdt"
```

### Step 7: Setup InfluxDB Retention (Optional)

```bash
# Make script executable
chmod +x setup_influx_retention.sh

# Run setup script
./setup_influx_retention.sh

# OR manually create tasks via InfluxDB UI
# http://localhost:8086 → Tasks → Create Task
```

### Step 8: Monitor Initial Run

```bash
# Watch logs for first hour
docker-compose logs -f producer flink-taskmanager candle-aggregator

# Check memory usage
docker stats --no-stream keydb

# Verify data in KeyDB
docker exec keydb redis-cli INFO memory
docker exec keydb redis-cli DBSIZE
```

## Verification Checklist

- [ ] Producer logs show `@kline_1s` instead of `@kline_1m`
- [ ] KeyDB has keys matching `candle:1s:*` pattern
- [ ] KeyDB has keys matching `candle:1m:*` pattern (after 1st minute)
- [ ] Aggregation job logs show successful runs every minute
- [ ] InfluxDB contains both `interval=1s` and `interval=1m` data
- [ ] Memory usage is within acceptable range (< 2GB for KeyDB)
- [ ] No error logs in any service

## Testing the Query Helper

```bash
# Enter Flink container (or any container with Python)
docker exec -it flink-jobmanager bash

# Run test
cd /app
python3 src/candle_query_helper.py

# Expected output:
# === Test 1: Latest Candle ===
# Latest: {'timestamp': ..., 'open': ..., ...}
#
# === Test 2: Last 100 1s Candles ===
# Retrieved 100 1s candles
# ...
```

## Rollback Procedure (if needed)

If something goes wrong:

```bash
# 1. Stop services
docker-compose down

# 2. Revert code changes
git checkout HEAD~1
# or manually restore old files

# 3. Remove KLINE_INTERVAL from docker-compose.yml
# Edit docker-compose.yml and remove:
#   KLINE_INTERVAL: "1s"

# 4. Restore KeyDB backup
docker run --rm -v cryptoprice_local_keydb_data:/data \
  -v $(pwd):/backup redis:alpine \
  sh -c "redis-cli --rdb /data/dump.rdb < /backup/backup-YYYYMMDD.rdb"

# 5. Start services
docker-compose up -d
```

## Performance Tuning

### If KeyDB Memory Usage is High

```bash
# Reduce TTL for 1s candles (from 2 hours to 1 hour)
# Edit src/ingest_flink_crypto.py:
TTL_1S = 3_600  # 1 hour instead of 2

# Rebuild and restart
docker-compose up -d --build flink-jobmanager flink-taskmanager
```

### If Aggregation Job is Slow

```bash
# Reduce number of symbols processed
# Edit src/aggregate_candles.py:
# Add limit to get_all_symbols():
symbols = symbols[:100]  # Process only first 100 symbols

# Or increase batch size for InfluxDB writes
```

### If InfluxDB Storage is Growing

```bash
# Reduce retention for 1s candles (from 1 hour to 30 minutes)
# Edit InfluxDB task: delete_old_1s_candles
# Change range from:
#   |> range(start: -2h, stop: -1h)
# To:
#   |> range(start: -1h, stop: -30m)
```

## Monitoring Dashboards

### Grafana Dashboard Queries

**1. KeyDB Memory Usage**
```
# Query: redis_memory_used_bytes / redis_memory_max_bytes * 100
```

**2. Candle Data Points per Interval**
```flux
from(bucket: "crypto")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "candles")
  |> group(columns: ["interval"])
  |> count()
```

**3. Aggregation Job Success Rate**
```
# Check logs for successful aggregations
# Create custom metric from logs
```

## FAQ

### Q: Why do I see both `candle:history:{symbol}` and `candle:1s:{symbol}` keys?

A: Old keys from previous schema. They will auto-expire based on old TTL. You can manually delete them:

```bash
docker exec keydb redis-cli --scan --pattern "candle:history:*" | \
  xargs docker exec keydb redis-cli DEL
```

### Q: How much more storage does 1s data use vs 1m?

A: Approximately 60× more for the same time period. However, with TTL of 2 hours vs 7 days, the total is actually less overall.

### Q: Can I query 1s candles from 2 days ago?

A: No. 1s candles are only retained for 2 hours in KeyDB and 1 hour in InfluxDB. For historical data, use 1m candles.

### Q: What happens if aggregation job fails?

A: Missing 1m candles for that minute. The job processes one minute at a time, so only that specific minute is affected. You can manually re-run for specific timestamps.

### Q: Can I change interval back to 1m?

A: Yes. Simply change `KLINE_INTERVAL: "1s"` to `KLINE_INTERVAL: "1m"` in docker-compose.yml and restart producer. The system will continue to work with both schemas.

## Next Steps

1. **Implement Backend API**: Use `candle_query_helper.py` in your FastAPI backend
2. **Add Caching**: Implement Redis caching layer in your API
3. **Setup Monitoring**: Create Grafana dashboards for candle data metrics
4. **Load Testing**: Test query performance under high load
5. **Optimize Queries**: Add indexes and tune InfluxDB queries if needed
6. **Archive Old Data**: Setup periodic export of old 1m data to S3/Iceberg

## Support

For issues or questions:
1. Check logs: `docker-compose logs -f [service-name]`
2. Review architecture doc: `CANDLE_STORAGE_ARCHITECTURE.md`
3. Test queries manually: `docker exec keydb redis-cli` or InfluxDB UI
4. Check this migration guide for common issues
