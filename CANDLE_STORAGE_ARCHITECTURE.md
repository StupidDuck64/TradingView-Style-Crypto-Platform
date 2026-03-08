# Candle Data Storage Architecture

## Overview

Hệ thống lưu trữ dữ liệu nến (candles) được thiết kế để tối ưu cho cả real-time query và historical analysis với 2 độ chi tiết khác nhau:
- **1-second candles**: Dữ liệu độ chi tiết cao cho giao dịch tần suất cao
- **1-minute candles**: Dữ liệu tổng hợp cho phân tích lịch sử

## Storage Schema

### KeyDB (Redis)

KeyDB lưu trữ dữ liệu với TTL tự động để tiết kiệm bộ nhớ:

```
candle:1s:{symbol}      → Sorted Set, TTL 2 giờ (7200s)
                          Lưu nến 1 giây cho 1-2 giờ gần nhất
                          Score: kline_start timestamp (ms)
                          Value: JSON encoded OHLCV data

candle:1m:{symbol}      → Sorted Set, TTL 7 ngày (604800s)
                          Lưu nến 1 phút cho 7 ngày gần nhất
                          Score: kline_start timestamp (ms)
                          Value: JSON encoded OHLCV data

candle:latest:{symbol}  → Hash, no TTL
                          Thông tin nến mới nhất (real-time)
                          Fields: open, high, low, close, volume, 
                                  quote_volume, trade_count, kline_start, interval
```

#### Candle JSON Format (in Sorted Sets):
```json
{
  "t": 1234567890000,     // kline_start timestamp (ms)
  "o": 50000.00,          // open price
  "h": 50100.00,          // high price
  "l": 49900.00,          // low price
  "c": 50050.00,          // close price
  "v": 123.456,           // volume (base asset)
  "qv": 6172800.00,       // quote volume (USDT)
  "n": 1234,              // trade count
  "x": true               // is_closed
}
```

### InfluxDB

InfluxDB lưu trữ time-series data với retention policies tự động:

```
Measurement: candles
Tags:
  - symbol: BTCUSDT, ETHUSDT, etc.
  - exchange: binance
  - interval: 1s, 1m

Fields:
  - open: float
  - high: float
  - low: float
  - close: float
  - volume: float
  - quote_volume: float
  - trade_count: integer
  - is_closed: boolean

Timestamp: kline_start (millisecond precision)
```

#### Retention Policies:
- **interval=1s**: 1 giờ retention (delete data older than 1 hour)
- **interval=1m**: 30 ngày retention (delete data older than 30 days)

## Data Flow

### 1. Real-time Ingestion (Flink)

Producer → Kafka → Flink → KeyDB + InfluxDB

- Producer nhận 1s candles từ Binance WebSocket
- Flink consumer đọc từ Kafka topic `crypto_klines`
- `KeyDBKlineWriter` ghi vào:
  - `candle:1s:{symbol}` với TTL 2 giờ
  - `candle:latest:{symbol}` (always updated)
- `InfluxDBKlineWriter` ghi vào measurement `candles` với tag `interval=1s`

### 2. Aggregation Job (1s → 1m)

Chạy mỗi phút qua cron job trong container `candle-aggregator`:

```
* * * * * python3 src/aggregate_candles.py --mode 1s-to-1m
```

**Workflow:**
1. Đọc tất cả `candle:1s:{symbol}` từ KeyDB
2. Với mỗi symbol, lấy 60 nến 1s của phút trước đó
3. Tổng hợp OHLCV:
   - Open: nến đầu tiên
   - High: max(all highs)
   - Low: min(all lows)
   - Close: nến cuối cùng
   - Volume: sum(all volumes)
   - Quote Volume: sum(all quote_volumes)
   - Trade Count: sum(all trade_counts)
4. Ghi kết quả vào:
   - KeyDB: `candle:1m:{symbol}` với TTL 7 ngày
   - InfluxDB: measurement `candles` với tag `interval=1m`

### 3. Data Cleanup

**KeyDB:**
- TTL tự động xóa dữ liệu cũ
- `candle:1s:{symbol}`: auto-expire sau 2 giờ
- `candle:1m:{symbol}`: auto-expire sau 7 ngày

**InfluxDB:**
- Downsampling tasks chạy định kỳ:
  - Task `delete_old_1s_candles`: Chạy mỗi 5 phút, xóa dữ liệu 1s cũ hơn 1 giờ
  - Task `delete_old_1m_candles`: Chạy mỗi ngày, xóa dữ liệu 1m cũ hơn 30 ngày

## API Query Patterns

### Backend FastAPI Endpoints

**1. Get Latest Candle (real-time)**
```python
GET /api/v1/candle/{symbol}/latest
→ Redis: HGETALL candle:latest:{symbol}
Response time: ~1-2ms
```

**2. Get 1s Candles (last 1-2 hours)**
```python
GET /api/v1/candle/{symbol}/1s?limit=100
→ Redis: ZREVRANGE candle:1s:{symbol} 0 {limit-1} WITHSCORES
Response time: ~5-10ms
```

**3. Get 1m Candles (last 7 days)**
```python
GET /api/v1/candle/{symbol}/1m?start=timestamp&end=timestamp
→ Redis: ZRANGEBYSCORE candle:1m:{symbol} {start} {end}
Response time: ~10-50ms (depending on range)
```

**4. Get Historical 1m Candles (beyond 7 days)**
```python
GET /api/v1/candle/{symbol}/1m/history?start=timestamp&end=timestamp
→ InfluxDB: SELECT from candles WHERE interval='1m' AND symbol={symbol} 
            AND time >= {start} AND time <= {end}
Response time: ~50-200ms (depending on range)
```

### Query Strategy (Backend Implementation)

```python
def get_candles(symbol: str, interval: str, start: int, end: int):
    now = int(time.time() * 1000)
    
    if interval == "1s":
        # Only available for last 2 hours
        if start < (now - 7200_000):  # 2 hours ago
            raise HTTPException(400, "1s candles only available for last 2 hours")
        return query_keydb_1s(symbol, start, end)
    
    elif interval == "1m":
        # Check if within KeyDB retention (7 days)
        redis_cutoff = now - 604_800_000  # 7 days ago
        
        if start >= redis_cutoff:
            # All data in KeyDB
            return query_keydb_1m(symbol, start, end)
        elif end < redis_cutoff:
            # All data in InfluxDB
            return query_influxdb_1m(symbol, start, end)
        else:
            # Split query: part from InfluxDB, part from KeyDB
            old_data = query_influxdb_1m(symbol, start, redis_cutoff - 1)
            new_data = query_keydb_1m(symbol, redis_cutoff, end)
            return merge_candles(old_data, new_data)
```

## Monitoring

### KeyDB Memory Usage

```bash
# Check memory usage
docker exec keydb redis-cli INFO memory

# Count keys per pattern
docker exec keydb redis-cli --scan --pattern "candle:1s:*" | wc -l
docker exec keydb redis-cli --scan --pattern "candle:1m:*" | wc -l

# Check TTL for specific symbol
docker exec keydb redis-cli TTL "candle:1s:btcusdt"
docker exec keydb redis-cli TTL "candle:1m:btcusdt"
```

### InfluxDB Storage

```bash
# Check bucket size
docker exec influxdb influx bucket list

# Check measurement cardinality
docker exec influxdb influx query '
  from(bucket: "crypto")
    |> range(start: -1h)
    |> filter(fn: (r) => r._measurement == "candles")
    |> group(columns: ["interval"])
    |> count()
'
```

### Aggregation Job Logs

```bash
# View aggregation job logs
docker exec candle-aggregator tail -f /var/log/aggregator.log

# Check cron status
docker exec candle-aggregator crontab -l
```

## Performance Characteristics

### Storage Estimates (for 400 symbols)

**KeyDB:**
- 1s candles (2 hours): ~400 symbols × 7200 candles × 150 bytes ≈ 432 MB
- 1m candles (7 days): ~400 symbols × 10080 candles × 150 bytes ≈ 604 MB
- Total: ~1 GB RAM

**InfluxDB:**
- 1s candles (1 hour): ~400 symbols × 3600 candles × 200 bytes ≈ 288 MB
- 1m candles (30 days): ~400 symbols × 43200 candles × 200 bytes ≈ 3.4 GB
- Total: ~3.7 GB disk (with compression)

### Query Performance

| Query Type | Data Source | Latency | Throughput |
|------------|-------------|---------|------------|
| Latest candle | KeyDB hash | 1-2ms | 10k+ RPS |
| Last 100 × 1s | KeyDB sorted set | 5-10ms | 5k RPS |
| Last 1000 × 1m | KeyDB sorted set | 10-50ms | 2k RPS |
| Historical 1m (1 month) | InfluxDB | 50-200ms | 500 RPS |

## Troubleshooting

### Issue: Aggregation job not running

```bash
# Check if container is running
docker ps | grep candle-aggregator

# Check cron logs
docker logs candle-aggregator

# Manually trigger aggregation
docker exec candle-aggregator python3 /app/src/aggregate_candles.py --mode 1s-to-1m
```

### Issue: KeyDB memory growing

```bash
# Check if TTL is working
docker exec keydb redis-cli --scan --pattern "candle:1s:*" | 
  xargs -I {} docker exec keydb redis-cli TTL {}

# Manually cleanup old data
docker exec keydb redis-cli --eval cleanup.lua 0
```

### Issue: Missing 1m candles

```bash
# Check aggregation job history
docker exec candle-aggregator cat /var/log/aggregator.log | tail -100

# Verify 1s data exists
docker exec keydb redis-cli ZCARD "candle:1s:btcusdt"

# Manually re-aggregate a specific minute
# Edit aggregate_candles.py to target specific timestamp
```

## Migration from Old Schema

If you have existing data with old schema (`candle:history:{symbol}`):

```bash
# Backup old data
docker exec keydb redis-cli --rdb /data/backup.rdb

# Migration script (run once)
python3 scripts/migrate_candle_schema.py
```

## Configuration

### Adjust TTL Values

Edit `src/ingest_flink_crypto.py`:

```python
class KeyDBKlineWriter(FlatMapFunction):
    TTL_1S = 7_200        # 2 hours (change as needed)
    TTL_1M = 604_800      # 7 days (change as needed)
```

### Adjust Retention Policies

Edit InfluxDB downsampling tasks via:

```bash
influx task list
influx task update -id <task-id> --file new_task.flux
```

## Best Practices

1. **Monitor memory usage**: KeyDB can grow quickly with high-frequency data
2. **Tune batch sizes**: Adjust Flink batch sizes based on throughput
3. **Use appropriate intervals**: Don't query 1s data for long time ranges
4. **Cache frequently accessed data**: Add application-level caching for popular symbols
5. **Archive old data**: Consider exporting old 1m data to S3/Iceberg for long-term storage
