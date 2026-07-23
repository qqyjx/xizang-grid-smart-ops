# -*- coding: utf-8 -*-
"""
运维操作服务
"""

import os
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
import uuid
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import OperationConfig, PathConfig

# 北京时间
BEIJING_TZ = timezone(timedelta(hours=8))


class OperationService:
    """运维操作服务类"""
    
    def __init__(self):
        self.servers = OperationConfig.SERVERS
        self.allowed_operations = OperationConfig.ALLOWED_OPERATIONS
        self.timeout = OperationConfig.OPERATION_TIMEOUT
        
        self.log_dir = PathConfig.LOGS_DIR
        os.makedirs(self.log_dir, exist_ok=True)
        
        self.history_file = os.path.join(self.log_dir, 'operation_history.json')
    
    def _get_beijing_time(self) -> str:
        """获取北京时间字符串"""
        return datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')
    
    def execute_operation(
        self,
        server_name: str,
        operation: str,
        params: Dict[str, Any] = None,
        dry_run: bool = True
    ) -> Dict[str, Any]:
        """执行运维操作"""
        operation_id = str(uuid.uuid4())[:8].upper()
        timestamp = self._get_beijing_time()
        
        result = {
            "operation_id": operation_id,
            "timestamp": timestamp,
            "server": server_name,
            "operation": operation,
            "dry_run": dry_run,
            "status": "pending",
            "message": "",
            "details": {}
        }
        
        if operation not in self.allowed_operations:
            result["status"] = "failed"
            result["message"] = f"不支持的操作类型: {operation}"
            self._log_operation(result)
            return result
        
        # 模拟执行各种操作
        if operation == "restart":
            result = self._execute_restart(server_name, result, dry_run)
        elif operation == "status_check":
            result = self._execute_status_check(server_name, result)
        elif operation == "log_collect":
            result = self._execute_log_collect(server_name, result, dry_run)
        elif operation == "process_check":
            result = self._execute_process_check(server_name, result)
        elif operation == "memory_check":
            result = self._execute_memory_check(server_name, result)
        elif operation == "cache_clear":
            result = self._execute_cache_clear(server_name, result, dry_run)
        elif operation == "io_check":
            result = self._execute_io_check(server_name, result)
        
        # v5.59: 显式标记本服务为演示模拟（真实运维走 real_server/agent_manager 路径）
        result['simulated'] = True
        result['source'] = 'demo_operations_service'
        self._log_operation(result)
        return result

    def _execute_restart(self, server_name: str, result: Dict, dry_run: bool) -> Dict:
        """执行重启操作"""
        result["status"] = "simulated" if dry_run else "success"
        result["message"] = f"{'[模拟] ' if dry_run else ''}重启服务器 {server_name}"
        result["details"] = {
            "command": f"systemctl restart all-services",
            "expected_downtime": "2-5分钟"
        }
        return result
    
    def _execute_status_check(self, server_name: str, result: Dict) -> Dict:
        """执行状态检查"""
        result["status"] = "success"
        result["message"] = f"服务器 {server_name} 状态检查完成"
        result["details"] = {
            "ping": "OK",
            "ssh": "OK",
            "services": {
                "main-service": "running",
                "monitor-agent": "running"
            }
        }
        return result
    
    def _execute_log_collect(self, server_name: str, result: Dict, dry_run: bool) -> Dict:
        """执行日志收集"""
        result["status"] = "simulated" if dry_run else "success"
        result["message"] = f"{'[模拟] ' if dry_run else ''}日志收集完成"
        result["details"] = {
            "logs_collected": ["system.log", "application.log", "error.log"]
        }
        return result
    
    def _execute_process_check(self, server_name: str, result: Dict) -> Dict:
        """执行进程检查"""
        result["status"] = "success"
        result["message"] = f"进程检查完成"
        result["details"] = {
            "top_cpu_processes": [
                {"pid": 1234, "name": "python", "cpu": "15.2%"},
                {"pid": 5678, "name": "java", "cpu": "8.5%"}
            ]
        }
        return result
    
    def _execute_memory_check(self, server_name: str, result: Dict) -> Dict:
        """执行内存检查"""
        result["status"] = "success"
        result["message"] = f"内存检查完成"
        result["details"] = {
            "total": "16GB",
            "used": "12GB",
            "free": "4GB",
            "cached": "3GB"
        }
        return result
    
    def _execute_cache_clear(self, server_name: str, result: Dict, dry_run: bool) -> Dict:
        """执行缓存清理"""
        result["status"] = "simulated" if dry_run else "success"
        result["message"] = f"{'[模拟] ' if dry_run else ''}缓存清理完成"
        result["details"] = {
            "command": "sync && echo 3 > /proc/sys/vm/drop_caches",
            "freed_memory": "2.5GB"
        }
        return result
    
    def _execute_io_check(self, server_name: str, result: Dict) -> Dict:
        """执行IO检查"""
        result["status"] = "success"
        result["message"] = f"IO检查完成"
        result["details"] = {
            "disk_usage": {"sda": "65%", "sdb": "45%"},
            "io_wait": "5.2%"
        }
        return result
    
    def _log_operation(self, result: Dict):
        """记录操作日志"""
        try:
            history = []
            if os.path.exists(self.history_file):
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)
            
            history.append(result)
            
            # 只保留最近100条记录
            if len(history) > 100:
                history = history[-100:]
            
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"记录操作日志失败: {e}")
    
    def get_operation_history(self, limit: int = 20) -> List:
        """获取操作历史"""
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)
                    return history[-limit:]
        except:
            pass
        return []


# 单例模式
_operation_instance = None

def get_operation_service() -> OperationService:
    """获取操作服务实例"""
    global _operation_instance
    if _operation_instance is None:
        _operation_instance = OperationService()
    return _operation_instance
