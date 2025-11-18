-- 创建数据库（如果不存在）
CREATE DATABASE IF NOT EXISTS server_monitor;

-- 使用该数据库
USE server_monitor;

-- 以下表结构会在后端应用启动时自动创建，这里仅作为初始化标记
SELECT '数据库初始化完成' AS status;