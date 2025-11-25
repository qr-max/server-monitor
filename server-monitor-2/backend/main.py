from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import asyncio
import sqlite3
import os
import logging
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
import json
from typing import List, Optional
from ssh_monitor import SSHMonitor
from websocket_manager import ConnectionManager

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/app/logs/app.log', encoding='utf-8')
    ]
)
logger = logging.getLogger("server-monitor")

# 数据库路径
DATABASE_PATH = os.getenv('DATABASE_PATH', '/app/data/monitor.db')

# 全局变量
manager = ConnectionManager()
ssh_monitor = SSHMonitor()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时
    logger.info("启动服务器监控系统...")
    
    # 确保日志目录存在
    os.makedirs('/app/logs', exist_ok=True)
    
    # 初始化数据库
    init_db()
    await ssh_monitor.start()
    
    # 启动监控任务
    monitor_task = asyncio.create_task(monitoring_loop())
    
    yield
    
    # 关闭时
    logger.info("关闭服务器监控系统...")
    monitor_task.cancel()
    try:
        await monitor_task
    except asyncio.CancelledError:
        pass
    await ssh_monitor.stop()

app = FastAPI(
    title="服务器监控系统",
    description="实时监控服务器状态的Web应用",
    version="3.0.0",
    lifespan=lifespan
)

# CORS配置 - 允许所有来源，便于开发测试
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db_connection():
    """获取数据库连接"""
    try:
        os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.error(f"数据库连接失败: {e}")
        raise

def init_db():
    """初始化数据库"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 服务器表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                ip TEXT NOT NULL,
                ssh_user TEXT NOT NULL,
                ssh_password TEXT,
                ssh_port INTEGER DEFAULT 22,
                cpu_threshold INTEGER DEFAULT 80,
                memory_threshold INTEGER DEFAULT 85,
                disk_threshold INTEGER DEFAULT 90,
                status TEXT DEFAULT 'unknown',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_success TIMESTAMP,
                failure_count INTEGER DEFAULT 0
            )
        ''')
        
        # 指标表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER,
                cpu_usage REAL,
                memory_usage REAL,
                disk_usage REAL,
                load_avg TEXT,
                processes INTEGER,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES servers (id) ON DELETE CASCADE
            )
        ''')
        
        # 告警表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER,
                server_name TEXT,
                type TEXT,
                message TEXT,
                level TEXT DEFAULT 'warning',
                resolved BOOLEAN DEFAULT FALSE,
                resolved_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES servers (id) ON DELETE CASCADE
            )
        ''')
        
        # 创建索引
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_metrics_server_id ON metrics(server_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_alerts_resolved ON alerts(resolved)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_servers_status ON servers(status)')
        
        conn.commit()
        logger.info("数据库初始化完成")
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")
        raise
    finally:
        conn.close()

# 页面路由 - 由nginx服务静态文件，这里提供备用访问
@app.get("/")
async def read_root():
    """根路径 - 返回监控大屏"""
    return {"message": "服务器监控系统 API", "version": "3.0.0"}

@app.get("/monitoring")
async def monitoring_page():
    """监控大屏页面 - 备用访问"""
    return {"message": "监控大屏页面请访问 /monitoring.html"}

@app.get("/data-management")
async def data_management_page():
    """数据管理页面 - 备用访问"""
    return {"message": "数据管理页面请访问 /data-management.html"}

# API路由
@app.get("/api/health")
async def health_check():
    """健康检查端点"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        db_healthy = True
    except Exception as e:
        db_healthy = False
        logger.error(f"数据库健康检查失败: {e}")
    finally:
        conn.close()
    
    return {
        "status": "healthy", 
        "timestamp": datetime.now().isoformat(),
        "database": "healthy" if db_healthy else "unhealthy",
        "version": "3.0.0"
    }

@app.get("/api/servers")
async def get_servers():
    """获取所有服务器列表"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT s.*, 
                   (SELECT cpu_usage FROM metrics WHERE server_id = s.id ORDER BY timestamp DESC LIMIT 1) as cpu_usage,
                   (SELECT memory_usage FROM metrics WHERE server_id = s.id ORDER BY timestamp DESC LIMIT 1) as memory_usage,
                   (SELECT disk_usage FROM metrics WHERE server_id = s.id ORDER BY timestamp DESC LIMIT 1) as disk_usage,
                   (SELECT COUNT(*) FROM alerts WHERE server_id = s.id AND resolved = 0) as alert_count
            FROM servers s
            ORDER BY 
                CASE 
                    WHEN s.status = 'online' THEN 1
                    WHEN s.status = 'unknown' THEN 2
                    ELSE 3
                END,
                s.name ASC
        ''')
        
        servers = []
        for row in cursor.fetchall():
            server = dict(row)
            # 转换数据类型
            server['cpu_usage'] = float(server['cpu_usage'] or 0)
            server['memory_usage'] = float(server['memory_usage'] or 0)
            server['disk_usage'] = float(server['disk_usage'] or 0)
            server['alert_count'] = server['alert_count'] or 0
            servers.append(server)
        
        return servers
    except Exception as e:
        logger.error(f"获取服务器列表失败: {e}")
        raise HTTPException(status_code=500, detail="获取服务器列表失败")
    finally:
        conn.close()

@app.post("/api/servers")
async def create_server(server: dict):
    """创建新服务器"""
    # 验证必要字段
    required_fields = ['name', 'ip', 'ssh_user', 'ssh_password']
    for field in required_fields:
        if not server.get(field):
            raise HTTPException(status_code=400, detail=f"缺少必要字段: {field}")
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO servers (name, ip, ssh_user, ssh_password, ssh_port, cpu_threshold, memory_threshold, disk_threshold)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            server['name'], 
            server['ip'], 
            server['ssh_user'], 
            server['ssh_password'], 
            server.get('ssh_port', 22), 
            server.get('cpu_threshold', 80), 
            server.get('memory_threshold', 85),
            server.get('disk_threshold', 90)
        ))
        
        server_id = cursor.lastrowid
        conn.commit()
        
        # 测试连接
        test_result = await ssh_monitor.test_connection(server_id, dict(server))
        
        return {
            "id": server_id, 
            "message": "服务器添加成功",
            "connection_status": "online" if test_result else "offline"
        }
        
    except sqlite3.IntegrityError as e:
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=400, detail="服务器名称已存在")
        else:
            logger.error(f"数据库完整性错误: {e}")
            raise HTTPException(status_code=500, detail="数据库错误")
    except Exception as e:
        logger.error(f"创建服务器失败: {e}")
        raise HTTPException(status_code=500, detail="服务器创建失败")
    finally:
        conn.close()

@app.put("/api/servers/{server_id}")
async def update_server(server_id: int, server: dict):
    """更新服务器信息"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 检查服务器是否存在
        cursor.execute('SELECT id FROM servers WHERE id = ?', (server_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="服务器不存在")
        
        # 更新服务器信息
        cursor.execute('''
            UPDATE servers 
            SET name = ?, ip = ?, ssh_user = ?, ssh_password = ?, ssh_port = ?,
                cpu_threshold = ?, memory_threshold = ?, disk_threshold = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (
            server['name'],
            server['ip'],
            server['ssh_user'],
            server['ssh_password'],
            server.get('ssh_port', 22),
            server.get('cpu_threshold', 80),
            server.get('memory_threshold', 85),
            server.get('disk_threshold', 90),
            server_id
        ))
        
        conn.commit()
        
        return {"message": "服务器更新成功"}
        
    except sqlite3.IntegrityError as e:
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=400, detail="服务器名称已存在")
        else:
            raise HTTPException(status_code=500, detail="数据库错误")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新服务器失败: {e}")
        raise HTTPException(status_code=500, detail="服务器更新失败")
    finally:
        conn.close()

@app.delete("/api/servers/{server_id}")
async def delete_server(server_id: int):
    """删除服务器"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT name FROM servers WHERE id = ?', (server_id,))
        server = cursor.fetchone()
        
        if not server:
            raise HTTPException(status_code=404, detail="服务器不存在")
        
        cursor.execute('DELETE FROM servers WHERE id = ?', (server_id,))
        conn.commit()
        
        logger.info(f"删除服务器: {server['name']} (ID: {server_id})")
        return {"message": f"服务器 {server['name']} 删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除服务器失败: {e}")
        raise HTTPException(status_code=500, detail="服务器删除失败")
    finally:
        conn.close()

@app.get("/api/metrics")
async def get_metrics(server_id: Optional[int] = None, hours: int = 24, limit: int = 1000):
    """获取监控指标数据"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        query = '''
            SELECT m.*, s.name as server_name 
            FROM metrics m 
            JOIN servers s ON m.server_id = s.id 
            WHERE m.timestamp >= datetime('now', ?)
        '''
        params = [f'-{hours} hours']
        
        if server_id:
            query += ' AND m.server_id = ?'
            params.append(server_id)
        
        query += ' ORDER BY m.timestamp DESC LIMIT ?'
        params.append(limit)
        
        cursor.execute(query, params)
        
        metrics = []
        for row in cursor.fetchall():
            metric = dict(row)
            metrics.append(metric)
        
        return metrics
    except Exception as e:
        logger.error(f"获取监控指标失败: {e}")
        raise HTTPException(status_code=500, detail="获取监控指标失败")
    finally:
        conn.close()

@app.delete("/api/metrics")
async def clear_all_metrics():
    """清空所有指标数据（匹配前端需求）"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM metrics')
        deleted_count = cursor.rowcount
        
        conn.commit()
        
        return {
            "message": f"已成功清空 {deleted_count} 条监控数据记录",
            "deleted_count": deleted_count
        }
    except Exception as e:
        logger.error(f"清空数据失败: {e}")
        raise HTTPException(status_code=500, detail="清空数据失败")
    finally:
        conn.close()

@app.get("/api/stats")
async def get_stats():
    """获取系统统计信息"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 获取服务器统计
        cursor.execute('''
            SELECT 
                COUNT(*) as total_servers,
                SUM(CASE WHEN status = "online" THEN 1 ELSE 0 END) as online_servers,
                SUM(CASE WHEN status = "offline" THEN 1 ELSE 0 END) as offline_servers,
                SUM(CASE WHEN status = "online" AND alert_count > 0 THEN 1 ELSE 0 END) as servers_with_alerts
            FROM (
                SELECT s.*, 
                    (SELECT COUNT(*) FROM alerts WHERE server_id = s.id AND resolved = 0) as alert_count
                FROM servers s
            )
        ''')
        server_stats = cursor.fetchone()
        
        # 获取平均资源使用率（最近1小时）
        cursor.execute('''
            SELECT 
                AVG(cpu_usage) as avg_cpu,
                AVG(memory_usage) as avg_memory,
                AVG(disk_usage) as avg_disk
            FROM metrics 
            WHERE timestamp >= datetime('now', '-1 hour')
        ''')
        resource_stats = cursor.fetchone()
        
        # 获取告警统计
        cursor.execute('''
            SELECT 
                COUNT(*) as total_alerts,
                SUM(CASE WHEN level = 'critical' THEN 1 ELSE 0 END) as critical_alerts,
                SUM(CASE WHEN level = 'warning' THEN 1 ELSE 0 END) as warning_alerts
            FROM alerts 
            WHERE resolved = 0
        ''')
        alert_stats = cursor.fetchone()
        
        # 获取数据点数量
        cursor.execute('SELECT COUNT(*) FROM metrics')
        total_metrics = cursor.fetchone()[0]
        
        stats = {
            'total_servers': server_stats['total_servers'] or 0,
            'online_servers': server_stats['online_servers'] or 0,
            'offline_servers': server_stats['offline_servers'] or 0,
            'servers_with_alerts': server_stats['servers_with_alerts'] or 0,
            'average_cpu': round(resource_stats['avg_cpu'] or 0, 1),
            'average_memory': round(resource_stats['avg_memory'] or 0, 1),
            'average_disk': round(resource_stats['avg_disk'] or 0, 1),
            'total_alerts': alert_stats['total_alerts'] or 0,
            'critical_alerts': alert_stats['critical_alerts'] or 0,
            'warning_alerts': alert_stats['warning_alerts'] or 0,
            'total_metrics': total_metrics or 0,
            'last_update': datetime.now().isoformat()
        }
        
        return stats
    except Exception as e:
        logger.error(f"获取统计信息失败: {e}")
        raise HTTPException(status_code=500, detail="获取统计信息失败")
    finally:
        conn.close()

@app.get("/api/alerts")
async def get_alerts(resolved: bool = False, limit: int = 50):
    """获取告警信息"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT a.*, s.name as server_name, s.ip as server_ip
            FROM alerts a
            LEFT JOIN servers s ON a.server_id = s.id
            WHERE a.resolved = ?
            ORDER BY a.created_at DESC
            LIMIT ?
        ''', (resolved, limit))
        
        alerts = []
        for row in cursor.fetchall():
            alert = dict(row)
            alerts.append(alert)
        
        return alerts
    except Exception as e:
        logger.error(f"获取告警信息失败: {e}")
        raise HTTPException(status_code=500, detail="获取告警信息失败")
    finally:
        conn.close()

@app.post("/api/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: int):
    """解决告警"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE alerts 
            SET resolved = 1, resolved_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        ''', (alert_id,))
        
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="告警不存在")
        
        conn.commit()
        return {"message": "告警已解决"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"解决告警失败: {e}")
        raise HTTPException(status_code=500, detail="解决告警失败")
    finally:
        conn.close()

@app.post("/api/alerts/resolve-all")
async def resolve_all_alerts():
    """解决所有告警"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE alerts 
            SET resolved = 1, resolved_at = CURRENT_TIMESTAMP 
            WHERE resolved = 0
        ''')
        
        resolved_count = cursor.rowcount
        conn.commit()
        
        return {"message": f"已解决 {resolved_count} 个告警"}
    except Exception as e:
        logger.error(f"解决所有告警失败: {e}")
        raise HTTPException(status_code=500, detail="解决所有告警失败")
    finally:
        conn.close()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket连接端点 - 修复连接处理"""
    await manager.connect(websocket)
    try:
        # 发送初始数据
        stats = await get_stats()
        servers = await get_servers()
        
        initial_message = {
            'type': 'initial',
            'stats': stats,
            'servers': servers,
            'timestamp': datetime.now().isoformat()
        }
        await websocket.send_text(json.dumps(initial_message))
        
        # 保持连接活跃
        while True:
            try:
                # 设置接收超时，避免长时间阻塞
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if data == "ping":
                    await websocket.send_text("pong")
                elif data == "refresh":
                    # 客户端请求刷新数据
                    await broadcast_updates()
            except asyncio.TimeoutError:
                # 发送心跳保持连接
                try:
                    await websocket.send_text(json.dumps({"type": "ping", "timestamp": datetime.now().isoformat()}))
                except Exception:
                    break  # 如果发送心跳失败，断开连接
                
    except WebSocketDisconnect:
        logger.info("WebSocket客户端正常断开连接")
    except Exception as e:
        logger.error(f"WebSocket错误: {e}")
    finally:
        manager.disconnect(websocket)

# 监控任务
async def monitoring_loop():
    """监控主循环"""
    logger.info("启动监控循环...")
    while True:
        try:
            await ssh_monitor.collect_all_metrics()
            await broadcast_updates()
            await asyncio.sleep(30)  # 每30秒收集一次数据
        except asyncio.CancelledError:
            logger.info("监控循环被取消")
            break
        except Exception as e:
            logger.error(f"监控循环错误: {e}")
            await asyncio.sleep(10)

async def broadcast_updates():
    """广播更新到所有连接的客户端"""
    try:
        stats = await get_stats()
        servers = await get_servers()
        
        message = {
            'type': 'update',
            'stats': stats,
            'servers': servers,
            'timestamp': datetime.now().isoformat()
        }
        
        await manager.broadcast(json.dumps(message))
    except Exception as e:
        logger.error(f"广播更新失败: {e}")

# 测试连接端点
@app.get("/api/test-connection/{server_id}")
async def test_server_connection(server_id: int):
    """测试服务器连接"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, name, ip, ssh_user, ssh_password, ssh_port
            FROM servers WHERE id = ?
        ''', (server_id,))
        
        server = cursor.fetchone()
        if not server:
            raise HTTPException(status_code=404, detail="服务器不存在")
        
        server_dict = dict(server)
        test_result = await ssh_monitor.test_connection(server_id, server_dict)
        
        return {
            "server_id": server_id,
            "server_name": server_dict['name'],
            "connection_status": "online" if test_result else "offline",
            "timestamp": datetime.now().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"测试连接失败: {e}")
        raise HTTPException(status_code=500, detail="测试连接失败")
    finally:
        conn.close()

# 系统信息端点
@app.get("/api/system-info")
async def get_system_info():
    """获取系统信息"""
    try:
        # 获取数据库文件大小
        db_size = 0
        if os.path.exists(DATABASE_PATH):
            db_size = os.path.getsize(DATABASE_PATH)
        
        # 获取日志文件信息
        log_files = []
        log_dir = '/app/logs'
        if os.path.exists(log_dir):
            for file in os.listdir(log_dir):
                if file.endswith('.log'):
                    file_path = os.path.join(log_dir, file)
                    log_files.append({
                        'name': file,
                        'size': os.path.getsize(file_path),
                        'modified': datetime.fromtimestamp(os.path.getmtime(file_path)).isoformat()
                    })
        
        return {
            "database_size": db_size,
            "database_path": DATABASE_PATH,
            "log_files": log_files,
            "python_version": os.sys.version,
            "server_time": datetime.now().isoformat(),
            "uptime": "unknown"  # 在实际部署中可以添加更详细的运行时间信息
        }
    except Exception as e:
        logger.error(f"获取系统信息失败: {e}")
        raise HTTPException(status_code=500, detail="获取系统信息失败")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000, 
        log_level="info",
        access_log=True
    )