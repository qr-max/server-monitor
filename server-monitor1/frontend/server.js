const express = require('express');
const path = require('path');
const { createProxyMiddleware } = require('http-proxy-middleware');
const cors = require('cors');

// 创建Express应用
const app = express();
// 设置端口，优先使用环境变量，否则使用3000
const PORT = process.env.PORT || 3000;

// 解决跨域问题
app.use(cors());

// 提供静态文件服务（HTML、CSS、JS等）
app.use(express.static(path.join(__dirname)));

// API代理到后端服务 - 修复代理配置
app.use('/api', createProxyMiddleware({
  target: process.env.BACKEND_URL || 'http://192.168.211.149:5000',  // 使用环境变量或固定IP
  changeOrigin: true,
  pathRewrite: {
    '^/api': '/api'  // 保持路径一致
  },
  onError: (err, req, res) => {
    console.error('代理错误:', err);
    res.status(500).json({ error: '后端服务连接失败' });
  },
  timeout: 10000  // 增加超时时间
}));

// 首页路由
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'index.html'));
});

// 健康检查接口
app.get('/health', (req, res) => {
  res.json({ 
    status: 'ok', 
    service: 'frontend',
    timestamp: new Date().toISOString()
  });
});

// 启动服务，监听指定端口和所有网络接口
app.listen(PORT, '0.0.0.0', () => {
  console.log(`前端服务启动成功：http://0.0.0.0:${PORT}`);
  console.log(`后端API代理目标：${process.env.BACKEND_URL || 'http://192.168.211.149:5000'}`);
});