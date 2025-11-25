#!/bin/bash

echo "================================================"
echo "服务器监控系统部署脚本"
echo "================================================"

# 检查Docker是否安装
if ! command -v docker &> /dev/null; then
    echo "错误：Docker 未安装。请先安装 Docker。"
    echo "参考：https://docs.docker.com/get-docker/"
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "错误：Docker Compose 未安装。请先安装 Docker Compose。"
    echo "参考：https://docs.docker.com/compose/install/"
    exit 1
fi

# 创建项目目录结构
echo "创建项目目录结构..."
mkdir -p ./data
mkdir -p ./logs/nginx
mkdir -p ./frontend/dist
mkdir -p ./nginx

# 检查必要文件
if [ ! -f "frontend/monitoring.html" ] || [ ! -f "frontend/data-management.html" ]; then
    echo "错误：请确保 monitoring.html 和 data-management.html 文件在 frontend 目录中"
    echo "当前目录结构:"
    ls -la
    exit 1
fi

# 复制前端文件到dist目录
echo "复制前端文件..."
cp frontend/monitoring.html frontend/dist/
cp frontend/data-management.html frontend/dist/

# 设置文件权限
echo "设置文件权限..."
chmod +x backend/startup.sh
chmod +x deploy.sh

# 创建日志文件并设置权限
touch ./logs/nginx/access.log
touch ./logs/nginx/error.log
chmod 666 ./logs/nginx/*.log

# 创建数据目录并设置权限
mkdir -p ./data/db
chmod 755 ./data
chmod 755 ./data/db

# 构建和启动Docker服务
echo "构建和启动Docker服务..."
docker compose down

# 构建镜像
echo "构建Docker镜像..."
docker compose build --no-cache

# 启动服务
echo "启动服务..."
docker compose up -d

# 等待服务启动
echo "等待服务启动..."
sleep 30

# 检查服务状态
echo "检查服务状态..."
if docker compose ps | grep -q "Up"; then
    echo "================================================"
    echo "部署成功！"
    echo ""
    echo "访问地址:"
    echo "监控大屏: http://192.168.211.149:8080/monitoring.html"
    echo "数据管理: http://192.168.211.149:8080/data-management.html"
    echo "API文档: http://192.168.211.149:8080/api/docs"
    echo ""
    echo "如果无法访问，请检查:"
    echo "1. Docker服务是否正常运行"
    echo "2. 端口8080是否被占用"
    echo "3. 防火墙设置"
    echo ""
    echo "查看日志: docker compose logs -f"
    echo "停止服务: docker compose down"
    echo "================================================"
else
    echo "================================================"
    echo "部署可能存在问题，请检查日志:"
    echo "docker compose logs"
    echo "================================================"
fi