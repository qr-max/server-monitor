# 使用Node.js 16的Alpine版本作为基础镜像（轻量级）
FROM node:16-alpine

# 设置工作目录
WORKDIR /app

# 复制package.json和server.js到工作目录
COPY package.json server.js ./

# 复制HTML文件
COPY index.html ./

# 安装依赖
RUN npm install --production

# 暴露3000端口
EXPOSE 3000

# 启动命令
CMD ["npm", "start"]
