#!/bin/bash

# ==========================================
# 1. KHỞI ĐỘNG FLINK
# ==========================================
echo "Đang chờ Flink Cluster khởi động ở cổng 8081..."
# Lệnh curl -s ẩn output, chỉ cần kết nối TCP thành công là thoát vòng lặp
until curl -s http://127.0.0.1:8081 > /dev/null; do
    printf '.'
    sleep 5
done
echo " Flink đã sẵn sàng!"

# Đợi thêm 10s để TaskManager kết nối hẳn vào JobManager
sleep 10

# Submit Flink Job (Cờ -d chạy ngầm)
docker exec flink-jobmanager flink run -d -py /app/src/ingest_flink_crypto.py
echo "Đã submit Flink Job!"


# ==========================================
# 2. KHỞI ĐỘNG SPARK
# ==========================================
echo "Đang chờ Spark Master khởi động ở cổng 8080..."
# Lệnh curl -s kiểm tra cổng 8080 của Spark
until curl -s http://127.0.0.1:8080 > /dev/null; do
    printf '.'
    sleep 5
done
echo " Spark Master đã sẵn sàng!"

# Submit Spark Job chạy ngầm
docker exec -d spark-master /opt/spark/bin/spark-submit \
  --master spark://spark-master:7077 \
  --total-executor-cores 1 \
  --packages "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.2,org.apache.iceberg:iceberg-aws-bundle:1.5.2,org.apache.hadoop:hadoop-aws:3.3.4,org.postgresql:postgresql:42.7.2,org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.5" \
  --conf spark.driver.memory=2g \
  --conf spark.executor.memory=2g \
  /app/src/ingest_crypto.py

echo "Đã submit thành công 2 Streaming Jobs!"
