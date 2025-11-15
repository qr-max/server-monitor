# 使用Python 3.9的Alpine版本作为基础镜像
FROM python:3.9-alpine

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY requirements.txt ./

# 安装系统依赖和Python依赖
# 安装gcc等工具是为了编译某些Python包
RUN apk add --no-cache gcc musl-dev linux-headers mariadb-dev build-base \
    && pip install --no-cache-dir -r requirements.txt \
    && apk del gcc musl-dev linux-headers build-base

# 复制应用代码
COPY app.py ./

# 创建非root用户并切换
RUN adduser -D -s /bin/sh monitor
USER monitor

# 暴露5000端口
EXPOSE 5000

# 启动命令
CMD ["python", "app.py"]
