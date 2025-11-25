import asyncssh
import asyncio
import sqlite3
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger("ssh-monitor")

class SSHMonitor:
    def __init__(self):
        self.connections: Dict[int, asyncssh.Connection] = {}
        self._running = False
        
    async def start(self):
        """启动监控器"""
        self._running = True
        logger.info("SSH监控器已启动")
    
    async def stop(self):
        """停止监控器"""
        self._running = False
        # 关闭所有SSH连接
        for server_id, conn in self.connections.items():
            try:
                conn.close()
            except:
                pass
        self.connections.clear()
        logger.info("SSH监控器已停止")
    
    async def test_connection(self, server_id: int, server_config: dict) -> bool:
        """测试SSH连接"""
        try:
            conn = await asyncssh.connect(
                server_config['ip'],
                username=server_config['ssh_user'],
                password=server_config['ssh_password'],
                port=server_config.get('ssh_port', 22),
                known_hosts=None,
                connect_timeout=10
            )
            
            # 测试执行简单命令
            result = await conn.run('echo "Connection test"', timeout=5)
            conn.close()
            
            if result.exit_status == 0:
                self._update_server_status(server_id, 'online')
                logger.info(f"服务器 {server_config['name']} ({server_config['ip']}) 连接测试成功")
                return True
            else:
                self._update_server_status(server_id, 'offline')
                logger.debug(f"服务器 {server_config['name']} 连接测试失败: 命令执行错误")
                return False
                
        except Exception as e:
            self._update_server_status(server_id, 'offline')
            logger.debug(f"服务器 {server_config['name']} 连接测试失败: {str(e)}")
            return False
    
    async def collect_all_metrics(self):
        """收集所有服务器的指标"""
        if not self._running:
            return
            
        servers = self._get_servers()
        
        tasks = []
        for server in servers:
            task = self.collect_server_metrics(server)
            tasks.append(task)
        
        # 并行收集所有服务器指标
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理结果
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                server_name = servers[i]['name'] if i < len(servers) else 'Unknown'
                logger.debug(f"收集服务器 {server_name} 指标时发生错误: {result}")
    
    async def collect_server_metrics(self, server: dict):
        """收集单个服务器的指标"""
        try:
            conn = await asyncssh.connect(
                server['ip'],
                username=server['ssh_user'],
                password=server['ssh_password'],
                port=server.get('ssh_port', 22),
                known_hosts=None,
                connect_timeout=15
            )
            
            # 并行执行多个命令
            commands = {
                'cpu': "top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1",
                'memory': "free | grep Mem | awk '{printf \"%.1f\", $3/$2 * 100.0}'",
                'disk': "df / | awk 'NR==2 {print $5}' | sed 's/%//'",
                'load': "uptime | awk -F'load average:' '{print $2}' | awk '{print $1}' | tr -d ','",
                'processes': "ps aux | wc -l"
            }
            
            results = {}
            for key, command in commands.items():
                try:
                    result = await conn.run(command, timeout=10)
                    if result.exit_status == 0:
                        results[key] = result.stdout.strip()
                    else:
                        results[key] = None
                except asyncio.TimeoutError:
                    logger.debug(f"服务器 {server['name']} 命令 {key} 执行超时")
                    results[key] = None
                except Exception as e:
                    logger.debug(f"服务器 {server['name']} 命令 {key} 执行失败: {e}")
                    results[key] = None
            
            conn.close()
            
            # 解析结果
            cpu_usage = float(results['cpu']) if results['cpu'] and results['cpu'].replace('.', '').isdigit() else 0.0
            memory_usage = float(results['memory']) if results['memory'] and results['memory'].replace('.', '').isdigit() else 0.0
            disk_usage = float(results['disk']) if results['disk'] and results['disk'].isdigit() else 0.0
            load_avg = results['load'] or "0.0"
            processes = int(results['processes']) if results['processes'] and results['processes'].isdigit() else 0
            
            # 保存指标（只有有效数据才保存）
            if cpu_usage > 0 or memory_usage > 0 or disk_usage > 0:
                self._save_metrics(
                    server['id'], 
                    cpu_usage, 
                    memory_usage, 
                    disk_usage,
                    load_avg,
                    processes
                )
            
            # 检查告警
            await self._check_alerts(server, cpu_usage, memory_usage, disk_usage)
            
            # 更新服务器状态
            self._update_server_status(server['id'], 'online', success=True)
            
            logger.debug(f"服务器 {server['name']} 指标收集成功: CPU={cpu_usage}%, Memory={memory_usage}%, Disk={disk_usage}%")
            
        except asyncio.TimeoutError:
            logger.debug(f"服务器 {server['name']} ({server['ip']}) 连接超时")
            self._update_server_status(server['id'], 'offline', success=False)
        except asyncssh.PermissionDenied:
            logger.debug(f"服务器 {server['name']} ({server['ip']}) 认证失败")
            self._update_server_status(server['id'], 'offline', success=False)
        except asyncssh.Error as e:
            logger.debug(f"服务器 {server['name']} ({server['ip']}) SSH错误: {e}")
            self._update_server_status(server['id'], 'offline', success=False)
        except Exception as e:
            logger.debug(f"服务器 {server['name']} ({server['ip']}) 收集指标时发生错误: {e}")
            self._update_server_status(server['id'], 'offline', success=False)
    
    async def _check_alerts(self, server: dict, cpu_usage: float, memory_usage: float, disk_usage: float):
        """检查并创建告警"""
        alerts = []
        
        # CPU告警
        if cpu_usage > server['cpu_threshold']:
            level = 'critical' if cpu_usage > 90 else 'warning'
            alerts.append({
                'server_id': server['id'],
                'server_name': server['name'],
                'type': 'cpu',
                'message': f'CPU使用率过高: {cpu_usage:.1f}% (阈值: {server["cpu_threshold"]}%)',
                'level': level
            })
        
        # 内存告警
        if memory_usage > server['memory_threshold']:
            level = 'critical' if memory_usage > 90 else 'warning'
            alerts.append({
                'server_id': server['id'],
                'server_name': server['name'],
                'type': 'memory',
                'message': f'内存使用率过高: {memory_usage:.1f}% (阈值: {server["memory_threshold"]}%)',
                'level': level
            })
        
        # 磁盘告警
        if disk_usage > server.get('disk_threshold', 90):
            level = 'critical' if disk_usage > 95 else 'warning'
            alerts.append({
                'server_id': server['id'],
                'server_name': server['name'],
                'type': 'disk',
                'message': f'磁盘使用率过高: {disk_usage:.1f}% (阈值: {server.get("disk_threshold", 90)}%)',
                'level': level
            })
        
        for alert in alerts:
            self._create_alert(alert)
    
    def _get_servers(self) -> List[dict]:
        """从数据库获取服务器列表"""
        try:
            import os
            database_path = os.getenv('DATABASE_PATH', '/app/data/monitor.db')
            conn = sqlite3.connect(database_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, name, ip, ssh_user, ssh_password, ssh_port, 
                       cpu_threshold, memory_threshold, disk_threshold
                FROM servers
            ''')
            
            servers = []
            for row in cursor.fetchall():
                servers.append(dict(row))
            
            conn.close()
            return servers
        except Exception as e:
            logger.error(f"获取服务器列表失败: {e}")
            return []
    
    def _update_server_status(self, server_id: int, status: str, success: bool = True):
        """更新服务器状态"""
        try:
            import os
            database_path = os.getenv('DATABASE_PATH', '/app/data/monitor.db')
            conn = sqlite3.connect(database_path)
            cursor = conn.cursor()
            
            if success:
                cursor.execute('''
                    UPDATE servers 
                    SET status = ?, updated_at = CURRENT_TIMESTAMP, 
                        last_success = CURRENT_TIMESTAMP, failure_count = 0
                    WHERE id = ?
                ''', (status, server_id))
            else:
                cursor.execute('''
                    UPDATE servers 
                    SET status = ?, updated_at = CURRENT_TIMESTAMP, 
                        failure_count = failure_count + 1
                    WHERE id = ?
                ''', (status, server_id))
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"更新服务器状态失败: {e}")
    
    def _save_metrics(self, server_id: int, cpu_usage: float, memory_usage: float, 
                     disk_usage: float, load_avg: str, processes: int):
        """保存指标到数据库"""
        try:
            import os
            database_path = os.getenv('DATABASE_PATH', '/app/data/monitor.db')
            conn = sqlite3.connect(database_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO metrics (server_id, cpu_usage, memory_usage, disk_usage, load_avg, processes)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (server_id, cpu_usage, memory_usage, disk_usage, load_avg, processes))
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"保存指标数据失败: {e}")
    
    def _create_alert(self, alert_data: dict):
        """创建告警"""
        try:
            import os
            database_path = os.getenv('DATABASE_PATH', '/app/data/monitor.db')
            conn = sqlite3.connect(database_path)
            cursor = conn.cursor()
            
            # 检查是否已存在相同未解决的告警
            cursor.execute('''
                SELECT id FROM alerts 
                WHERE server_id = ? AND type = ? AND message = ? AND resolved = 0
            ''', (alert_data['server_id'], alert_data['type'], alert_data['message']))
            
            existing_alert = cursor.fetchone()
            
            if not existing_alert:
                cursor.execute('''
                    INSERT INTO alerts (server_id, server_name, type, message, level)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    alert_data['server_id'], 
                    alert_data['server_name'],
                    alert_data['type'], 
                    alert_data['message'], 
                    alert_data['level']
                ))
                
                logger.info(f"创建告警: {alert_data['server_name']} - {alert_data['message']}")
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"创建告警失败: {e}")