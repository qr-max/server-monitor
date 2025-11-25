#!/bin/bash
set -e

echo "================================================"
echo "服务器监控系统后端服务启动脚本"
echo "================================================"

# 设置环境变量
export PYTHONPATH=/app
export DATABASE_PATH=${DATABASE_PATH:-/app/data/monitor.db}

# 创建必要的目录
mkdir -p /app/data
mkdir -p /app/logs

# 等待数据库文件就绪（最多等待30秒）
timeout=30
counter=0
while [ ! -f "$DATABASE_PATH" ] && [ $counter -lt $timeout ]; do
    echo "等待数据库文件就绪... ($counter/$timeout)"
    sleep 1
    counter=$((counter + 1))
done

# 检查数据库文件是否存在，如果不存在则初始化
if [ ! -f "$DATABASE_PATH" ]; then
    echo "初始化数据库..."
    python -c "
import os
import sys
sys.path.append('/app')
from main import init_db
init_db()
print('数据库初始化完成')
"
else
    echo "数据库已存在，跳过初始化"
fi

# 检查数据库连接
echo "检查数据库连接..."
python -c "
import sqlite3
import os
db_path = os.getenv('DATABASE_PATH', '/app/data/monitor.db')
try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('SELECT 1')
    print('数据库连接正常')
    conn.close()
except Exception as e:
    print(f'数据库连接失败: {e}')
    exit(1)
"

# 启动应用前等待一下，确保所有组件就绪
echo "等待组件就绪..."
sleep 5

# 启动应用
echo "启动FastAPI服务..."
echo "服务地址: http://0.0.0.0:8000"
echo "API文档: http://0.0.0.0:8000/docs"
echo "健康检查: http://0.0.0.0:8000/api/health"

exec uvicorn main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --log-level info \
    --access-log \
    --proxy-headers \
    --forwarded-allow-ips "*"