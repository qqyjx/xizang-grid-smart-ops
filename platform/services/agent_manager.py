# -*- coding: utf-8 -*-
"""
Agent服务器管理模块
通过HTTP调用部署在ECS上的Agent执行修复操作
"""

import os
import json
import time
import random
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# 数据文件路径
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
DATA_FILE = os.path.join(DATA_DIR, 'agent_servers.json')

class AgentServerManager:
    """Agent服务器管理器"""
    
    # 默认告警阈值
    DEFAULT_THRESHOLDS = {
        'cpu': 80,
        'memory': 85,
        'disk': 90,
        'io': 80,
        'connections': 500
    }
    
    def __init__(self):
        self.servers: Dict[str, dict] = {}
        self.history: Dict[str, List[dict]] = {}  # 历史数据
        self.history_max_points = 60  # 最多保存60个数据点
        self.default_timeout = 5  # 快速超时
        self._status_cache: Dict[str, dict] = {}  # 状态缓存
        self._cache_ttl = 10  # 缓存10秒
        self._load_servers()  # 启动时加载数据
        self._heal_legacy_records()  # 兼容旧数据：补全缺失的 system 字段

    def _heal_legacy_records(self):
        """启动自愈：为旧版本保存的服务器记录补全 system 字段。"""
        changed = False
        for srv in self.servers.values():
            if 'system' not in srv or srv.get('system') in (None, ''):
                srv['system'] = 'default'
                changed = True
        if changed:
            logger.info(f"已为旧数据补全 system 字段，共 {len(self.servers)} 条记录")
            self._save_servers()
    
    def _load_servers(self):
        """从文件加载服务器数据；JSON 为空/缺失则自动从 MySQL xzyw_servers 恢复（v5.42）"""
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    self.servers = json.load(f)
                logger.info(f"已加载 {len(self.servers)} 个Agent服务器")
        except Exception as e:
            logger.error(f"加载Agent服务器数据失败: {e}")
            self.servers = {}

        # v5.42: 启动时 JSON 为空则 best-effort 尝试 MySQL 恢复（此时 DB 可能还没 init_db_from_env）
        if not self.servers:
            self._try_restore_from_mysql(tag='启动')

    def _try_restore_from_mysql(self, tag: str = '') -> int:
        """从 xzyw_servers 把 agent 记录重建回 self.servers。返回恢复的条数。
        失败 silent（DB 未启用/表不存在/网络问题），不阻塞业务。
        v5.42 加强：list_servers 首次调用若内存空，会再触发一次（这时 DB 肯定就绪）"""
        try:
            from db import get_db, is_db_enabled
            if not is_db_enabled():
                return 0
            db = get_db()
            if not db:
                return 0
            # v5.43: `system` 和 `type` 都是 MySQL 保留字，必须反引号包裹
            # 客户日志：(1064 You have an error in your SQL syntax ... near 'system, token, status FROM xzyw_servers WHERE type='agent'')
            rows = db.query(
                "SELECT `server_id`, `server_name`, `ip`, `port`, `type`, `system`, `token`, `status` "
                "FROM `xzyw_servers` WHERE `type`='agent'"
            )
            if not rows:
                return 0
            for r in rows:
                sid = r.get('server_id')
                if not sid or sid in self.servers:
                    continue
                self.servers[sid] = {
                    'server_id': sid,
                    'server_name': r.get('server_name') or sid,
                    'ip': r.get('ip', ''),
                    'port': int(r.get('port') or 8089),
                    'token': r.get('token') or 'CHANGE_ME_AGENT_TOKEN',
                    'type': 'agent',
                    'system': r.get('system') or 'default',
                    'is_virtual': False,
                    'status': r.get('status') or 'unknown',
                }
            n = len(self.servers)
            if n > 0:
                print(f'[AgentMgr] 从 MySQL 恢复 {n} 个 Agent (tag={tag})', flush=True)
                logger.info(f"从 MySQL 恢复 {n} 个 Agent (tag={tag})")
                self._save_servers()
            return n
        except Exception as e:
            logger.warning(f"[AgentMgr] MySQL 恢复失败 tag={tag}: {e}")
            return 0
        # v5.59: 删除 return 之后的不可达死代码（原 `self.servers = {}` 永不执行）

    def _save_servers(self):
        """保存服务器数据到文件"""
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.servers, f, ensure_ascii=False, indent=2)
            logger.info(f"已保存 {len(self.servers)} 个Agent服务器")
        except Exception as e:
            logger.error(f"保存Agent服务器数据失败: {e}")
    
    def add_server(self, ip: str, name: str = None, port: int = 8089,
                   token: str = 'CHANGE_ME_AGENT_TOKEN', system: str = 'default') -> dict:
        """添加Agent服务器"""
        server_id = f"agent-{ip.replace('.', '-')}"
        
        if server_id in self.servers:
            return {'success': False, 'message': f'服务器 {ip} 已存在'}
        
        # 测试连接
        test_result = self.test_connection(ip, port, token)
        if not test_result.get('success'):
            return {'success': False, 'message': f'无法连接到Agent: {test_result.get("message")}'}
        
        self.servers[server_id] = {
            'server_id': server_id,
            'server_name': name or f'Agent-{ip}',
            'ip': ip,
            'port': port,
            'token': token,
            'type': 'agent',
            'system': system,
            'is_virtual': False,
            'status': 'online',
            'last_check': datetime.now().isoformat(),
            'agent_info': test_result.get('data', {})
        }
        
        self._save_servers()
        
        # 记录初始历史数据
        if test_result.get('data'):
            self.record_history(server_id, test_result['data'])
        
        return {
            'success': True,
            'message': f'Agent服务器添加成功',
            'server': self.servers[server_id]
        }
    
    def remove_server(self, server_id: str) -> dict:
        """移除Agent服务器"""
        if server_id not in self.servers:
            return {'success': False, 'message': '服务器不存在'}
        
        del self.servers[server_id]
        if server_id in self.history:
            del self.history[server_id]
        self._save_servers()
        return {'success': True, 'message': '服务器已移除'}
    
    def get_server(self, server_id: str) -> Optional[dict]:
        """获取单个服务器信息"""
        return self.servers.get(server_id)
    
    def list_servers(self) -> List[dict]:
        """列出所有Agent服务器。
        v5.42 兜底：内存为空 → 触发 MySQL 懒加载（防止启动时 DB 未就绪导致永远空）"""
        if not self.servers:
            self._try_restore_from_mysql(tag='list_servers-lazy')
        return [
            {
                'id': srv['server_id'],
                'name': srv.get('server_name', f"Agent-{srv['ip']}"),
                'ip': srv['ip'],
                'port': srv['port'],
                'status': srv.get('status', 'unknown'),
                'last_check': srv.get('last_check'),
                'type': 'agent',
                'system': srv.get('system', 'default')
            }
            for srv in self.servers.values()
        ]
    
    def test_connection(self, ip: str, port: int, token: str) -> dict:
        """测试与Agent的连接"""
        try:
            url = f"http://{ip}:{port}/status"
            headers = {'X-Agent-Token': token}
            resp = requests.get(url, headers=headers, timeout=self.default_timeout)
            
            if resp.status_code == 200:
                return {'success': True, 'data': resp.json().get('data', {})}
            elif resp.status_code == 401:
                return {'success': False, 'message': 'Token认证失败'}
            else:
                return {'success': False, 'message': f'连接失败: HTTP {resp.status_code}'}
        except requests.exceptions.ConnectionError:
            return {'success': False, 'message': '无法连接到Agent（连接被拒绝）'}
        except requests.exceptions.Timeout:
            return {'success': False, 'message': '连接超时'}
        except Exception as e:
            return {'success': False, 'message': str(e)}
    
    def get_server_status_cached(self, server_id: str) -> dict:
        """获取服务器状态（带缓存，用于快速查询）"""
        now = time.time()
        cache = self._status_cache.get(server_id)
        if cache and (now - cache.get('time', 0)) < self._cache_ttl:
            return cache.get('result', {'success': False})
        # 缓存过期，获取新数据
        result = self.get_server_status(server_id)
        self._status_cache[server_id] = {'time': now, 'result': result}
        return result
    
    def get_server_status(self, server_id: str) -> dict:
        """获取服务器状态"""
        server = self.servers.get(server_id)
        if not server:
            return {'success': False, 'message': '服务器不存在'}
        
        try:
            url = f"http://{server['ip']}:{server['port']}/status"
            headers = {'X-Agent-Token': server['token']}
            resp = requests.get(url, headers=headers, timeout=self.default_timeout)
            
            if resp.status_code == 200:
                data = resp.json()
                server['status'] = 'online'
                server['last_check'] = datetime.now().isoformat()
                server['resources'] = data.get('data', {})
                self._save_servers()
                
                # 记录历史数据
                if data.get('data'):
                    self.record_history(server_id, data['data'])
                
                return {'success': True, 'data': data.get('data', {})}
            elif resp.status_code == 401:
                return {'success': False, 'message': 'Token认证失败'}
            else:
                return {'success': False, 'message': f'请求失败: {resp.status_code}'}
        
        except requests.exceptions.ConnectionError:
            server['status'] = 'offline'
            self._save_servers()
            return {'success': False, 'message': 'Agent离线'}
        except Exception as e:
            return {'success': False, 'message': str(e)}
    
    def record_history(self, server_id: str, data: dict):
        """记录服务器状态历史"""
        if server_id not in self.history:
            self.history[server_id] = []
        
        timestamp = datetime.now().strftime('%H:%M:%S')
        
        # 正确解析Agent返回的嵌套数据格式
        cpu_data = data.get('cpu', {})
        memory_data = data.get('memory', {})
        disk_data = data.get('disk', {})
        network_data = data.get('network', {})
        
        # 提取具体数值
        cpu_usage = cpu_data.get('usage', 0) if isinstance(cpu_data, dict) else cpu_data
        memory_usage = memory_data.get('percent', 0) if isinstance(memory_data, dict) else memory_data
        disk_usage = disk_data.get('percent', 0) if isinstance(disk_data, dict) else disk_data
        network_io = network_data.get('bytes_recv', 0) + network_data.get('bytes_sent', 0) if isinstance(network_data, dict) else data.get('io', 0)
        connections = network_data.get('connections', 0) if isinstance(network_data, dict) else data.get('connections', 0)
        
        # IO转换为百分比 - 基于磁盘IO利用率而非网络流量
        # 如果有磁盘IO数据则使用，否则使用一个合理的估算值
        if isinstance(disk_data, dict) and 'io_util' in disk_data:
            io_percent = min(100, disk_data.get('io_util', 0))
        else:
            # 基于网络活动估算一个合理的IO值（0-50%范围内波动）
            io_percent = min(50, (connections / 1000) * 10) if connections > 0 else 5
        
        self.history[server_id].append({
            'timestamp': timestamp,
            'cpu': cpu_usage,
            'memory': memory_usage,
            'disk': disk_usage,
            'io': io_percent,
            'connections': connections
        })
        
        if len(self.history[server_id]) > self.history_max_points:
            self.history[server_id] = self.history[server_id][-self.history_max_points:]
    
    def get_server_history(self, server_id: str, minutes: int = 30) -> dict:
        """获取服务器历史数据（用于图表展示）"""
        server = self.servers.get(server_id)
        if not server:
            return {'success': False, 'message': '服务器不存在'}
        
        # 先获取最新状态并记录
        status = self.get_server_status(server_id)
        is_online = status.get('success', False)

        # v6.0 关键修复(图7)：Agent 离线时绝不编造历史曲线 —— 旧版会用 random 造一条假数据，
        # 领导一眼识破"agent 没启动为啥有实时数据"。离线一律返回空 + offline 标记，前端显示"离线/暂无数据"。
        if not is_online:
            return {
                'success': True,
                'offline': True,
                'note': 'Agent 离线/不可达，无实时监控数据（不展示任何曲线，避免与真实采集混淆）',
                'history': {'metrics': {'timestamps': [], 'cpu': [], 'memory': [], 'disk': [], 'io': [], 'connections': []}}
            }

        history_data = self.history.get(server_id, [])

        # 在线但真实历史不足 10 点：只返回已累积的真实点（可能很少），标 partial；
        # 绝不用 random 补满 —— 趋势采样线程每 5s 累积一次，约 1 分钟即有真实曲线。
        if len(history_data) < 10:
            return self._real_partial_history(server_id, status.get('data'), history_data)

        return {
            'success': True,
            'history': {
                'metrics': {
                    'timestamps': [h['timestamp'] for h in history_data],
                    'cpu': [h['cpu'] for h in history_data],
                    'memory': [h['memory'] for h in history_data],
                    'disk': [h['disk'] for h in history_data],
                    'io': [h['io'] for h in history_data],
                    'connections': [h['connections'] for h in history_data]
                }
            }
        }
    
    def _real_partial_history(self, server_id: str, current_data: dict = None, history_data: list = None) -> dict:
        """v6.0: 在线但真实历史不足时，只返回已累积的真实采样点（绝不 random 补满）。
        若一个点都没有但当前在线，则用当前真实快照作为唯一一个点。趋势采样线程会持续补齐真实曲线。"""
        history_data = history_data or []
        pts = list(history_data)
        # 历史为空但当前在线：用真实快照补 1 个点（真实值，非编造）
        if not pts and current_data:
            cpu_data = current_data.get('cpu', {}) or {}
            memory_data = current_data.get('memory', {}) or {}
            disk_data = current_data.get('disk', {}) or {}
            network_data = current_data.get('network', {}) or {}
            pts = [{
                'timestamp': datetime.now().strftime('%H:%M:%S'),
                'cpu': cpu_data.get('usage', 0) if isinstance(cpu_data, dict) else (cpu_data or 0),
                'memory': memory_data.get('percent', 0) if isinstance(memory_data, dict) else (memory_data or 0),
                'disk': disk_data.get('percent', 0) if isinstance(disk_data, dict) else (disk_data or 0),
                'io': 0,
                'connections': network_data.get('connections', 0) if isinstance(network_data, dict) else 0,
            }]
        return {
            'success': True,
            'partial': True,
            'note': f'实时数据累积中（已采集 {len(pts)} 个真实采样点），约 1 分钟后展示完整趋势曲线。',
            'history': {
                'metrics': {
                    'timestamps': [h['timestamp'] for h in pts],
                    'cpu': [h['cpu'] for h in pts],
                    'memory': [h['memory'] for h in pts],
                    'disk': [h['disk'] for h in pts],
                    'io': [h['io'] for h in pts],
                    'connections': [h['connections'] for h in pts],
                }
            }
        }

    # ==================== 故障检测功能 ====================
    
    def detect_faults(self, server_id: str, thresholds: dict = None) -> dict:
        """检测服务器故障"""
        server = self.servers.get(server_id)
        if not server:
            return {'success': False, 'message': '服务器不存在'}
        
        # 获取最新状态
        status = self.get_server_status(server_id)
        if not status.get('success'):
            return {
                'success': True,
                'has_fault': True,
                'server_id': server_id,
                'server_name': server.get('server_name', f"Agent-{server['ip']}"),
                'faults': [{'type': 'offline', 'message': 'Agent离线', 'level': 'critical'}],
                'status': 'critical'
            }
        
        data = status.get('data', {})
        thresholds = thresholds or self.DEFAULT_THRESHOLDS
        
        # 正确解析Agent返回的嵌套数据格式
        cpu_data = data.get('cpu', {})
        memory_data = data.get('memory', {})
        disk_data = data.get('disk', {})
        network_data = data.get('network', {})
        
        # 提取具体数值
        cpu = cpu_data.get('usage', 0) if isinstance(cpu_data, dict) else cpu_data
        memory = memory_data.get('percent', 0) if isinstance(memory_data, dict) else memory_data
        disk = disk_data.get('percent', 0) if isinstance(disk_data, dict) else disk_data
        network_io = network_data.get('bytes_recv', 0) + network_data.get('bytes_sent', 0) if isinstance(network_data, dict) else 0
        io = min(100, (network_io / 1000000000) * 100) if network_io > 0 else data.get('io', 0)
        connections = network_data.get('connections', 0) if isinstance(network_data, dict) else data.get('connections', 0)
        
        faults = []
        
        # CPU检测
        if cpu > thresholds.get('cpu', 80):
            level = 'critical' if cpu > 95 else 'warning'
            faults.append({
                'type': 'cpu_high',
                'metric': 'cpu',
                'value': cpu,
                'threshold': thresholds['cpu'],
                'message': f'CPU使用率过高: {cpu}%',
                'level': level
            })
        
        # 内存检测
        if memory > thresholds.get('memory', 85):
            level = 'critical' if memory > 95 else 'warning'
            faults.append({
                'type': 'memory_high',
                'metric': 'memory',
                'value': memory,
                'threshold': thresholds['memory'],
                'message': f'内存使用率过高: {memory}%',
                'level': level
            })
        
        # 磁盘检测
        if disk > thresholds.get('disk', 90):
            level = 'critical' if disk > 95 else 'warning'
            faults.append({
                'type': 'disk_high',
                'metric': 'disk',
                'value': disk,
                'threshold': thresholds['disk'],
                'message': f'磁盘使用率过高: {disk}%',
                'level': level
            })
        
        # IO检测
        if io > thresholds.get('io', 80):
            level = 'critical' if io > 95 else 'warning'
            faults.append({
                'type': 'io_high',
                'metric': 'io',
                'value': io,
                'threshold': thresholds['io'],
                'message': f'IO使用率过高: {io}%',
                'level': level
            })
        
        # 连接数检测
        if connections > thresholds.get('connections', 500):
            level = 'critical' if connections > 1000 else 'warning'
            faults.append({
                'type': 'connections_high',
                'metric': 'connections',
                'value': connections,
                'threshold': thresholds['connections'],
                'message': f'连接数过高: {connections}',
                'level': level
            })

        # v5.53: 故障注入演示标记 — 即使指标没爬到阈值，也要把"还在跑的 stress worker / 标记文件"算作故障
        # 这是客户演示场景的关键，否则机器基线高于阈值时 detect_faults 看不出注入
        fault_injected = data.get('fault_injected') or {}
        active_workers = fault_injected.get('active_workers') or []
        if active_workers:
            ftypes = ','.join(set(w.get('fault_type', '?') for w in active_workers))
            faults.append({
                'type': 'inject_demo',
                'metric': 'inject',
                'value': len(active_workers),
                'message': f'检测到故障注入演示子进程: {ftypes}（共 {len(active_workers)} 个）',
                'level': 'critical'
            })
        if fault_injected.get('service_down'):
            faults.append({'type': 'inject_demo', 'metric': 'service_down',
                           'message': '检测到服务停止演示标记', 'level': 'critical'})
        if fault_injected.get('network_issue'):
            faults.append({'type': 'inject_demo', 'metric': 'network_issue',
                           'message': '检测到网络异常演示标记', 'level': 'critical'})
        if fault_injected.get('io_stress') and not active_workers:
            faults.append({'type': 'inject_demo', 'metric': 'io_stress',
                           'message': '检测到 IO 故障演示标记', 'level': 'critical'})

        has_fault = len(faults) > 0
        has_critical = any(f['level'] == 'critical' for f in faults)
        
        return {
            'success': True,
            'has_fault': has_fault,
            'server_id': server_id,
            'server_name': server.get('server_name', f"Agent-{server['ip']}"),
            'ip': server.get('ip'),
            'current_metrics': data,
            'faults': faults,
            'fault_count': len(faults),
            'status': 'critical' if has_critical else ('warning' if has_fault else 'normal'),
            'timestamp': datetime.now().isoformat()
        }
    
    def get_faulty_servers(self) -> List[dict]:
        """获取所有故障服务器列表"""
        faulty = []
        for server_id in self.servers:
            result = self.detect_faults(server_id)
            if result.get('has_fault'):
                faulty.append({
                    'server_id': server_id,
                    'server_name': result.get('server_name'),
                    'ip': result.get('ip'),
                    'status': result.get('status'),
                    'faults': result.get('faults', []),
                    'fault_count': result.get('fault_count', 0)
                })
        return faulty
    
    def smart_auto_repair(self, server_id: str) -> dict:
        """智能自动修复 - 先检测故障，再针对性修复"""
        # 1. 检测故障
        detection = self.detect_faults(server_id)
        if not detection.get('success'):
            return detection
        
        if not detection.get('has_fault'):
            return {
                'success': True,
                'message': '服务器状态正常，无需修复',
                'server_id': server_id,
                'detection': detection
            }
        
        # 2. 根据故障类型确定修复动作
        faults = detection.get('faults', [])
        repair_actions = []
        
        for fault in faults:
            fault_type = fault.get('type')
            if fault_type == 'offline':
                repair_actions.append({'action': 'restart_agent', 'reason': '重启Agent服务'})
            elif fault_type == 'cpu_high':
                repair_actions.append({'action': 'kill_high_cpu', 'reason': '终止高CPU进程'})
            elif fault_type == 'memory_high':
                repair_actions.append({'action': 'clear_memory', 'reason': '清理内存缓存'})
            elif fault_type == 'disk_high':
                repair_actions.append({'action': 'clear_disk', 'reason': '清理磁盘空间'})
            elif fault_type == 'io_high':
                repair_actions.append({'action': 'optimize_io', 'reason': '优化IO性能'})
            elif fault_type == 'connections_high':
                repair_actions.append({'action': 'reset_connections', 'reason': '重置连接'})
        
        # 3. 执行修复
        repair_result = self.execute_repair(server_id, action='auto', params={
            'detected_faults': faults,
            'repair_actions': repair_actions
        })
        
        return {
            'success': repair_result.get('success', False),
            'server_id': server_id,
            'detection': detection,
            'repair_actions': repair_actions,
            'repair_result': repair_result,
            'message': repair_result.get('message', '修复完成')
        }
    
    # ==================== 修复执行功能 ====================
    
    def execute_repair(self, server_id: str, action: str = 'auto', params: dict = None) -> dict:
        """执行修复操作"""
        server = self.servers.get(server_id)
        if not server:
            return {'success': False, 'message': '服务器不存在'}
        
        try:
            url = f"http://{server['ip']}:{server['port']}/repair"
            headers = {
                'X-Agent-Token': server['token'],
                'Content-Type': 'application/json'
            }
            payload = {'action': action, 'params': params or {}}
            
            resp = requests.post(url, headers=headers, json=payload, timeout=120)

            if resp.status_code == 200:
                return resp.json()
            else:
                return {'success': False, 'message': f'请求失败: {resp.status_code}'}

        # v6.0(图5/6)：Agent 离线时不要把 HTTPConnectionPool...Connection refused 原样抛给客户，给可操作提示
        except requests.exceptions.ConnectionError:
            return {'success': False, 'offline': True,
                    'message': f"Agent 离线/不可达（{server.get('ip')}:{server.get('port')}），无法执行修复。"
                               f"请先在该服务器上启动 Agent 服务（cd xizang-agent-7B && ./deploy.sh），再重试。"}
        except requests.exceptions.Timeout:
            return {'success': False, 'message': f"连接 Agent 超时（{server.get('ip')}:{server.get('port')}），请检查网络或 Agent 负载。"}
        except Exception as e:
            return {'success': False, 'message': f'修复请求异常：{e}'}

    def execute_script(self, server_id: str, script_name: str) -> dict:
        """执行脚本"""
        server = self.servers.get(server_id)
        if not server:
            return {'success': False, 'message': '服务器不存在'}
        
        try:
            url = f"http://{server['ip']}:{server['port']}/script"
            headers = {
                'X-Agent-Token': server['token'],
                'Content-Type': 'application/json'
            }
            payload = {'script_name': script_name}
            
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            
            if resp.status_code == 200:
                return resp.json()
            else:
                return {'success': False, 'message': f'请求失败: {resp.status_code}'}
        
        except Exception as e:
            return {'success': False, 'message': str(e)}
    
    def auto_repair(self, server_id: str) -> dict:
        """自动诊断并修复（使用智能修复）"""
        return self.smart_auto_repair(server_id)

    def diagnose(self, server_id: str) -> dict:
        """v5.57: 调用 agent /diagnose 接口拿机器深度诊断"""
        server = self.servers.get(server_id)
        if not server:
            return {'success': False, 'message': '服务器不存在'}
        try:
            url = f"http://{server['ip']}:{server['port']}/diagnose"
            headers = {'X-Agent-Token': server['token']}
            resp = requests.get(url, headers=headers, timeout=20)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 401:
                return {'success': False, 'message': 'Token认证失败'}
            else:
                return {'success': False, 'message': f'HTTP {resp.status_code}'}
        except requests.exceptions.ConnectionError:
            return {'success': False, 'message': 'Agent 离线'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def inspect(self, server_id: str) -> dict:
        """v5.7: 调用 agent /inspect 拿系统巡检采集数据（进程/线程/连接/端口/中间件/定时任务）。"""
        server = self.servers.get(server_id)
        if not server:
            return {'success': False, 'message': '服务器不存在'}
        try:
            url = f"http://{server['ip']}:{server['port']}/inspect"
            headers = {'X-Agent-Token': server['token']}
            resp = requests.get(url, headers=headers, timeout=20)
            if resp.status_code == 200:
                return resp.json()
            return {'success': False, 'message': f'HTTP {resp.status_code}'}
        except requests.exceptions.ConnectionError:
            return {'success': False, 'message': 'Agent 离线'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def inject_fault(self, server_id: str, fault_type: str, duration: int = 180,
                     intensity: dict = None) -> dict:
        """v5.50: 向真实 Agent 服务器注入故障（调用 agent /stress），令 CPU/内存/IO 真实过载。"""
        server = self.servers.get(server_id)
        if not server:
            return {'success': False, 'message': '服务器不存在'}
        try:
            url = f"http://{server['ip']}:{server['port']}/stress"
            headers = {
                'X-Agent-Token': server['token'],
                'Content-Type': 'application/json'
            }
            payload = {
                'fault_type': fault_type,
                'duration': int(duration),
                'intensity': intensity or {}
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                # 让缓存立刻失效，下一次 status 拿到真实负载
                self._status_cache.pop(server_id, None)
                return data
            elif resp.status_code == 401:
                return {'success': False, 'message': 'Token认证失败'}
            else:
                return {'success': False, 'message': f'请求失败: HTTP {resp.status_code}'}
        except requests.exceptions.ConnectionError:
            return {'success': False, 'message': 'Agent离线，无法注入故障'}
        except requests.exceptions.Timeout:
            return {'success': False, 'message': '连接超时'}
        except Exception as e:
            return {'success': False, 'message': str(e)}
    

    def get_all_servers_status_fast(self) -> Dict[str, dict]:
        """批量获取所有服务器状态（并行+缓存）"""
        if not self.servers:
            return {}
        
        results = {}
        now = time.time()
        need_refresh = []
        
        # 先检查缓存
        for server_id in self.servers:
            cache = self._status_cache.get(server_id)
            if cache and (now - cache.get('time', 0)) < self._cache_ttl:
                results[server_id] = cache.get('result', {'success': False})
            else:
                need_refresh.append(server_id)
        
        # 并行获取需要刷新的服务器
        if need_refresh:
            with ThreadPoolExecutor(max_workers=min(5, len(need_refresh))) as executor:
                futures = {executor.submit(self.get_server_status, sid): sid for sid in need_refresh}
                for future in as_completed(futures, timeout=10):
                    sid = futures[future]
                    try:
                        result = future.result(timeout=5)
                        self._status_cache[sid] = {'time': now, 'result': result}
                        results[sid] = result
                    except Exception:
                        results[sid] = {'success': False, 'message': '超时'}
        
        return results

    def refresh_all_status(self) -> dict:
        """刷新所有服务器状态"""
        results = {}
        for server_id in self.servers:
            results[server_id] = self.get_server_status(server_id)
        return results


# 全局实例
agent_manager = AgentServerManager()
