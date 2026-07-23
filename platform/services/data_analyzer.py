# -*- coding: utf-8 -*-
"""
数据分析服务
用于分析运行数据、检测异常、评估系统状态
"""

import os
import re
import glob
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import KnowledgeBaseConfig, OperationConfig, PathConfig

# 北京时间
BEIJING_TZ = timezone(timedelta(hours=8))

def get_beijing_time():
    """获取北京时间"""
    return datetime.now(BEIJING_TZ)


class DataAnalyzer:
    """数据分析服务类"""
    
    def __init__(self):
        self.thresholds = KnowledgeBaseConfig.THRESHOLDS
        self.servers = OperationConfig.SERVERS
        self.data_dir = PathConfig.RAW_DATA_DIR
        
        # 模拟数据
        self._mock_data = {
            'worker01': {
                'cpu': {'current': 45.2, 'avg': 38.5, 'max': 72.3},
                'memory': {'current': 62.8, 'avg': 58.2, 'max': 78.5},
                'io': {'current': 125.5, 'avg': 98.3, 'max': 256.8},
                'status': 'normal'
            },
            'worker02': {
                'cpu': {'current': 78.5, 'avg': 65.2, 'max': 92.1},
                'memory': {'current': 71.3, 'avg': 68.5, 'max': 85.2},
                'io': {'current': 180.2, 'avg': 145.6, 'max': 320.5},
                'status': 'warning'
            },
            'master01': {
                'cpu': {'current': 28.6, 'avg': 25.3, 'max': 55.8},
                'memory': {'current': 52.1, 'avg': 48.6, 'max': 68.3},
                'io': {'current': 85.3, 'avg': 72.1, 'max': 156.2},
                'status': 'normal'
            }
        }
    
    def _add_variance(self, value: float, variance: float = 5.0) -> float:
        """为模拟数据添加随机波动"""
        return round(value + random.uniform(-variance, variance), 2)
    
    # ==================== SAR数据解析方法 ====================
    
    def parse_sar_cpu(self, content: str) -> Dict[str, Any]:
        """解析SAR CPU数据"""
        lines = content.strip().split('\n')
        data_points = []
        
        for line in lines:
            if not line.strip() or 'CPU' in line and '%user' in line:
                continue
            if 'Linux' in line or 'Average' in line:
                continue
                
            parts = line.split()
            if len(parts) >= 8 and 'all' in parts:
                try:
                    time_str = parts[0] + ' ' + parts[1]
                    idle = float(parts[-1])
                    usage = round(100.0 - idle, 2)
                    user = float(parts[3])
                    system = float(parts[5])
                    iowait = float(parts[6])
                    
                    data_points.append({
                        'time': time_str,
                        'usage': usage,
                        'user': round(user, 2),
                        'system': round(system, 2),
                        'iowait': round(iowait, 2),
                        'idle': round(idle, 2)
                    })
                except (ValueError, IndexError):
                    continue
        
        if not data_points:
            return {'error': '无法解析CPU数据', 'data_points': []}
        
        usage_values = [d['usage'] for d in data_points]
        iowait_values = [d['iowait'] for d in data_points]
        
        return {
            'type': 'cpu',
            'data_points': data_points,
            'total_records': len(data_points),
            'statistics': {
                'current': usage_values[-1],
                'avg': round(sum(usage_values) / len(usage_values), 2),
                'max': round(max(usage_values), 2),
                'min': round(min(usage_values), 2),
                'iowait_avg': round(sum(iowait_values) / len(iowait_values), 2)
            }
        }
    
    def parse_sar_memory(self, content: str) -> Dict[str, Any]:
        """解析SAR内存数据"""
        lines = content.strip().split('\n')
        data_points = []
        
        for line in lines:
            if not line.strip() or 'kbmemfree' in line:
                continue
            if 'Linux' in line or 'Average' in line:
                continue
                
            parts = line.split()
            if len(parts) >= 5:
                try:
                    time_str = parts[0] + ' ' + parts[1]
                    mem_used_pct = float(parts[4])
                    kbmemfree = int(parts[2])
                    kbmemused = int(parts[3])
                    
                    data_points.append({
                        'time': time_str,
                        'usage': round(mem_used_pct, 2),
                        'free_mb': round(kbmemfree / 1024, 2),
                        'used_mb': round(kbmemused / 1024, 2)
                    })
                except (ValueError, IndexError):
                    continue
        
        if not data_points:
            return {'error': '无法解析内存数据', 'data_points': []}
        
        usage_values = [d['usage'] for d in data_points]
        
        return {
            'type': 'memory',
            'data_points': data_points,
            'total_records': len(data_points),
            'statistics': {
                'current': usage_values[-1],
                'avg': round(sum(usage_values) / len(usage_values), 2),
                'max': round(max(usage_values), 2),
                'min': round(min(usage_values), 2)
            }
        }
    
    def parse_sar_io(self, content: str) -> Dict[str, Any]:
        """解析SAR IO数据"""
        lines = content.strip().split('\n')
        data_points = []
        
        for line in lines:
            if not line.strip() or 'DEV' in line and 'tps' in line:
                continue
            if 'Linux' in line or 'Average' in line:
                continue
                
            parts = line.split()
            if len(parts) >= 10:
                try:
                    time_str = parts[0] + ' ' + parts[1]
                    device = parts[2]
                    tps = float(parts[3])
                    util = float(parts[-1])
                    
                    data_points.append({
                        'time': time_str,
                        'device': device,
                        'tps': round(tps, 2),
                        'usage': round(util, 2)
                    })
                except (ValueError, IndexError):
                    continue
        
        if not data_points:
            return {'error': '无法解析IO数据', 'data_points': []}
        
        usage_values = [d['usage'] for d in data_points]
        tps_values = [d['tps'] for d in data_points]
        
        return {
            'type': 'io',
            'data_points': data_points,
            'total_records': len(data_points),
            'statistics': {
                'current': usage_values[-1] if usage_values else 0,
                'avg': round(sum(usage_values) / len(usage_values), 2) if usage_values else 0,
                'max': round(max(usage_values), 2) if usage_values else 0,
                'tps_avg': round(sum(tps_values) / len(tps_values), 2) if tps_values else 0
            }
        }
    
    def auto_detect_and_parse(self, content: str) -> Dict[str, Any]:
        """自动检测数据类型并解析"""
        content_lower = content.lower()
        
        if '%user' in content_lower and '%idle' in content_lower:
            return self.parse_sar_cpu(content)
        elif 'kbmemfree' in content_lower or 'memused' in content_lower:
            return self.parse_sar_memory(content)
        elif 'tps' in content_lower and ('dev' in content_lower or 'device' in content_lower):
            return self.parse_sar_io(content)
        else:
            # 尝试通用解析
            return self._parse_generic(content)
    
    def _parse_generic(self, content: str) -> Dict[str, Any]:
        """通用数据解析"""
        lines = content.strip().split('\n')
        numbers = []
        
        for line in lines:
            # 提取所有数字
            found = re.findall(r'[\d.]+', line)
            for num in found:
                try:
                    numbers.append(float(num))
                except:
                    pass
        
        if not numbers:
            return {'type': 'text_log', 'total_records': len(lines), 'data_points': [], 'raw_lines': lines[:50]}
        
        return {
            'type': 'generic',
            'data_points': [{'value': n} for n in numbers],
            'total_records': len(numbers),
            'statistics': {
                'current': numbers[-1],
                'avg': round(sum(numbers) / len(numbers), 2),
                'max': round(max(numbers), 2),
                'min': round(min(numbers), 2)
            }
        }
    
    def detect_faults(self, analysis_result: Dict[str, Any]) -> Dict[str, Any]:
        """检测故障"""
        stats = analysis_result.get('statistics', {})
        data_type = analysis_result.get('type', 'generic')
        
        faults = []
        status = 'normal'
        
        # CPU故障检测
        if data_type == 'cpu':
            current = stats.get('current', 0)
            avg = stats.get('avg', 0)
            iowait = stats.get('iowait_avg', 0)
            
            if current >= 90 or avg >= 85:
                faults.append({
                    'type': 'CPU过载',
                    'level': 'critical',
                    'message': f'CPU使用率过高: 当前{current}%, 平均{avg}%',
                    'value': current
                })
                status = 'critical'
            elif current >= 70 or avg >= 65:
                faults.append({
                    'type': 'CPU使用率偏高',
                    'level': 'warning',
                    'message': f'CPU使用率偏高: 当前{current}%, 平均{avg}%',
                    'value': current
                })
                status = 'warning'
            
            if iowait >= 30:
                faults.append({
                    'type': 'IOWait过高',
                    'level': 'warning',
                    'message': f'IO等待过高: {iowait}%',
                    'value': iowait
                })
                if status == 'normal':
                    status = 'warning'
        
        # 内存故障检测
        elif data_type == 'memory':
            current = stats.get('current', 0)
            avg = stats.get('avg', 0)
            
            if current >= 90 or avg >= 85:
                faults.append({
                    'type': '内存不足',
                    'level': 'critical',
                    'message': f'内存使用率过高: 当前{current}%, 平均{avg}%',
                    'value': current
                })
                status = 'critical'
            elif current >= 75 or avg >= 70:
                faults.append({
                    'type': '内存使用率偏高',
                    'level': 'warning',
                    'message': f'内存使用率偏高: 当前{current}%, 平均{avg}%',
                    'value': current
                })
                status = 'warning'
        
        # IO故障检测
        elif data_type == 'io':
            current = stats.get('current', 0)
            avg = stats.get('avg', 0)
            
            if current >= 80 or avg >= 70:
                faults.append({
                    'type': 'IO瓶颈',
                    'level': 'critical',
                    'message': f'磁盘IO利用率过高: 当前{current}%, 平均{avg}%',
                    'value': current
                })
                status = 'critical'
            elif current >= 50 or avg >= 40:
                faults.append({
                    'type': 'IO利用率偏高',
                    'level': 'warning',
                    'message': f'磁盘IO利用率偏高: 当前{current}%, 平均{avg}%',
                    'value': current
                })
                status = 'warning'
        
        # 生成建议操作
        recommended_actions = []
        for fault in faults:
            fault_type = fault['type']
            if 'CPU' in fault_type:
                recommended_actions.extend([
                    '使用 top 或 htop 查看高CPU进程',
                    '检查是否有异常进程或死循环',
                    '考虑优化代码或增加服务器资源'
                ])
            elif '内存' in fault_type:
                recommended_actions.extend([
                    '使用 free -h 查看内存详情',
                    '清理系统缓存: sync && echo 3 > /proc/sys/vm/drop_caches',
                    '检查是否有内存泄漏'
                ])
            elif 'IO' in fault_type:
                recommended_actions.extend([
                    '使用 iotop 查看高IO进程',
                    '检查磁盘空间: df -h',
                    '检查磁盘健康状态'
                ])
        
        return {
            'has_fault': len(faults) > 0,
            'status': status,
            'faults': faults,
            'recommended_actions': list(set(recommended_actions))
        }
    
    def get_all_servers_status(self) -> Dict[str, Any]:
        """v6.0 红线整改：彻底移除 worker01/02/master01 + 随机波动的"假服务器状态"。
        历史上此方法用 self._mock_data + _add_variance 编造 CPU/内存/IO，仅被遗留死端点
        /api/status、/api/status/summary 调用（前端 loadServerStatusOld 已无调用点）。
        为根除"造假数据"红线隐患，这里直接返回空集合——真实服务器状态一律走
        agent_manager 的 /api/agent/* 与 /api/virtual/* 实时接口。"""
        return {
            'timestamp': get_beijing_time().strftime('%Y-%m-%d %H:%M:%S'),
            'servers': [],
            'summary': {'total': 0, 'normal': 0, 'warning': 0, 'critical': 0},
            'note': '该旧接口已停用模拟数据，请使用 /api/agent/servers 与 /api/virtual/* 实时接口'
        }


# 单例模式
_analyzer_instance = None

def get_data_analyzer() -> DataAnalyzer:
    """获取数据分析器实例"""
    global _analyzer_instance
    if _analyzer_instance is None:
        _analyzer_instance = DataAnalyzer()
    return _analyzer_instance
