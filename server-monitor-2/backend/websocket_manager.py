from fastapi import WebSocket
from typing import List, Dict
import logging
import json

logger = logging.getLogger("websocket-manager")

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.connection_info: Dict[WebSocket, Dict] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        self.connection_info[websocket] = {
            'connected_at': None,
            'last_activity': None
        }
        logger.info(f"WebSocket客户端连接，当前连接数: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        if websocket in self.connection_info:
            del self.connection_info[websocket]
        logger.info(f"WebSocket客户端断开，当前连接数: {len(self.active_connections)}")

    async def send_personal_message(self, message: str, websocket: WebSocket):
        try:
            await websocket.send_text(message)
            # 更新活动时间
            if websocket in self.connection_info:
                self.connection_info[websocket]['last_activity'] = None
        except Exception as e:
            logger.error(f"发送个人消息失败: {e}")
            self.disconnect(websocket)

    async def broadcast(self, message: str):
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception as e:
                logger.error(f"广播消息失败: {e}")
                disconnected.append(connection)
        
        # 移除断开连接的客户端
        for connection in disconnected:
            self.disconnect(connection)

    async def broadcast_json(self, data: dict):
        """广播JSON数据"""
        try:
            message = json.dumps(data, ensure_ascii=False)
            await self.broadcast(message)
        except Exception as e:
            logger.error(f"广播JSON数据失败: {e}")

    def get_connection_count(self) -> int:
        """获取当前连接数"""
        return len(self.active_connections)