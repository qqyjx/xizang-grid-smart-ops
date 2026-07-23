# -*- coding: utf-8 -*-
"""
虚拟服务器模拟模块
模拟真实服务器环境，提供可执行的修复操作演示
支持实时运行模拟、日志写入、服务器动态添加删除
"""

import os
import json
import time
import random
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional
from pathlib import Path

# 北京时间
BEIJING_TZ = timezone(timedelta(hours=8))

# 日志目录
LOG_DIR = Path(__file__).parent.parent / 'logs' / 'servers'
LOG_DIR.mkdir(parents=True, exist_ok=True)


class VirtualServer:
    """虚拟服务器类 - 模拟真实服务器环境"""
    
    # 监控数据保留时长（分钟）
    HISTORY_RETENTION_MINUTES = 60
    # 监控频率（秒）- 实际每3秒采集，但按5分钟间隔展示
    MONITOR_INTERVAL = 3
    
    def __init__(self, server_id: str, server_name: str, ip: str, ssh_key: str = None, is_virtual: bool = True, system: str = 'default'):
        self.server_id = server_id
        self.server_name = server_name
        self.ip = ip
        self.ssh_key = ssh_key  # SSH密钥（用于真实服务器）
        self.is_virtual = is_virtual  # 是否为虚拟服务器
        self.system = system  # 所属系统分组
        self.status = "running"  # running, stopped, restarting, maintenance
        self.created_time = datetime.now(BEIJING_TZ)
        
        # 历史监控数据（用于折线图展示）
        self.metrics_history = {
            'cpu': [],       # CPU使用率历史
            'memory': [],    # 内存使用率历史
            'disk': [],      # 磁盘使用率历史
            'io': [],        # IO网络流量历史
            'connections': [],  # 连接数历史
            'timestamps': []    # 时间戳
        }
        
        # 告警阈值配置
        self.alert_thresholds = {
            'cpu': 80,        # CPU使用率告警阈值
            'memory': 85,     # 内存使用率告警阈值
            'disk': 90,       # 磁盘使用率告警阈值
            'io': 80,         # IO利用率告警阈值
            'connections': 500  # 连接数告警阈值
        }
        
        # 当前告警状态
        self.alerts = []
        
        # 网卡状态
        self.network_interfaces = {
            'eth0': {'status': 'up', 'ip': ip, 'mac': 'AA:BB:CC:DD:EE:01'},
            'eth1': {'status': 'up', 'ip': '', 'mac': 'AA:BB:CC:DD:EE:02'}
        }
        
        # 端口监控
        self.monitored_ports = {
            80: {'service': 'nginx', 'status': 'open'},
            3306: {'service': 'mysql', 'status': 'open'},
            6379: {'service': 'redis', 'status': 'open'},
            8080: {'service': 'app-server', 'status': 'open'}
        }
        
        # 预设脚本路径
        self.preset_scripts = {
            'health_check': '/opt/scripts/health_check.sh',
            'backup': '/opt/scripts/backup.sh',
            'cleanup': '/opt/scripts/cleanup.sh',
            'restart_services': '/opt/scripts/restart_services.sh'
        }
        
        # 日志文件路径
        self.log_file = LOG_DIR / f"{server_id}.log"
        
        # 实时模拟线程
        self._simulation_thread = None
        self._simulation_running = False
        
        # 服务器资源状态 (初始化为有一定负载的状态)
        self.resources = {
            'cpu': {
                'usage': random.uniform(30, 50),
                'cores': 8,
                'frequency': 2.4,  # GHz
                'temperature': random.uniform(45, 55)
            },
            'memory': {
                'total': 32,  # GB
                'used': random.uniform(10, 16),
                'cached': random.uniform(2, 4),
                'swap_used': random.uniform(0, 1)
            },
            'disk': {
                'total': 500,  # GB
                'used': random.uniform(100, 200),
                'io_util': random.uniform(10, 30),
                'read_speed': random.uniform(50, 100),  # MB/s
                'write_speed': random.uniform(30, 70)
            },
            'network': {
                'rx_bytes': random.randint(1000000, 10000000),
                'tx_bytes': random.randint(500000, 5000000),
                'connections': random.randint(50, 200),
                'latency': random.uniform(1, 10)  # ms
            }
        }
        
        # 运行的服务列表
        self.services = {
            'nginx': {'status': 'running', 'pid': 1234, 'port': 80, 'memory': 128},
            'mysql': {'status': 'running', 'pid': 2345, 'port': 3306, 'memory': 512},
            'redis': {'status': 'running', 'pid': 3456, 'port': 6379, 'memory': 256},
            'app-server': {'status': 'running', 'pid': 4567, 'port': 8080, 'memory': 1024}
        }
        
        # 进程列表
        self.processes = []
        self._generate_processes()
        
        # 操作日志
        self.operation_logs = []
        
        # 故障状态
        self.fault_injected = False
        self.fault_type = None
        
        # 启动实时模拟
        if is_virtual:
            self.start_simulation()
    
    def start_simulation(self):
        """启动实时运行模拟"""
        if self._simulation_running:
            return
        self._simulation_running = True
        self._simulation_thread = threading.Thread(target=self._simulation_loop, daemon=True)
        self._simulation_thread.start()
    
    def stop_simulation(self):
        """停止实时模拟"""
        self._simulation_running = False
        if self._simulation_thread:
            self._simulation_thread.join(timeout=2)
    
    def _simulation_loop(self):
        """模拟循环 - 实时更新服务器状态并写入日志"""
        while self._simulation_running:
            try:
                # 模拟资源波动
                self._simulate_resource_changes()
                
                # 记录历史数据
                self._record_metrics_history()
                
                # 检查告警
                self._check_alerts()
                
                # 写入日志
                self._write_log()
                
                # 每3秒更新一次
                time.sleep(3)
            except Exception as e:
                print(f"Simulation error for {self.server_id}: {e}")
                time.sleep(5)
    
    def _record_metrics_history(self):
        """记录历史监控数据"""
        now = datetime.now(BEIJING_TZ)
        timestamp = now.strftime('%H:%M:%S')
        
        # 计算各指标
        cpu_usage = self.resources['cpu']['usage']
        mem_total = self.resources['memory']['total']
        mem_used = self.resources['memory']['used']
        mem_usage = (mem_used / mem_total * 100) if mem_total > 0 else 0
        disk_total = self.resources['disk']['total']
        disk_used = self.resources['disk']['used']
        disk_usage = (disk_used / disk_total * 100) if disk_total > 0 else 0
        io_util = self.resources['disk']['io_util']
        connections = self.resources['network']['connections']
        
        # 记录数据
        self.metrics_history['cpu'].append(round(cpu_usage, 1))
        self.metrics_history['memory'].append(round(mem_usage, 1))
        self.metrics_history['disk'].append(round(disk_usage, 1))
        self.metrics_history['io'].append(round(io_util, 1))
        self.metrics_history['connections'].append(connections)
        self.metrics_history['timestamps'].append(timestamp)
        
        # 保留最近60条数据（约3分钟的数据）
        max_records = 60
        for key in self.metrics_history:
            if len(self.metrics_history[key]) > max_records:
                self.metrics_history[key] = self.metrics_history[key][-max_records:]
    
    def _check_alerts(self):
        """检查告警状态"""
        self.alerts = []
        now = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')
        
        # CPU告警
        if self.resources['cpu']['usage'] > self.alert_thresholds['cpu']:
            self.alerts.append({
                'type': 'cpu',
                'level': 'warning' if self.resources['cpu']['usage'] < 90 else 'critical',
                'message': f"CPU使用率过高: {self.resources['cpu']['usage']:.1f}%",
                'threshold': self.alert_thresholds['cpu'],
                'current': self.resources['cpu']['usage'],
                'time': now
            })
        
        # 内存告警
        mem_usage = self.resources['memory']['used'] / self.resources['memory']['total'] * 100
        if mem_usage > self.alert_thresholds['memory']:
            self.alerts.append({
                'type': 'memory',
                'level': 'warning' if mem_usage < 95 else 'critical',
                'message': f"内存使用率过高: {mem_usage:.1f}%",
                'threshold': self.alert_thresholds['memory'],
                'current': mem_usage,
                'time': now
            })
        
        # 磁盘告警
        disk_usage = self.resources['disk']['used'] / self.resources['disk']['total'] * 100
        if disk_usage > self.alert_thresholds['disk']:
            self.alerts.append({
                'type': 'disk',
                'level': 'warning' if disk_usage < 95 else 'critical',
                'message': f"磁盘使用率过高: {disk_usage:.1f}%",
                'threshold': self.alert_thresholds['disk'],
                'current': disk_usage,
                'time': now
            })
        
        # IO告警
        if self.resources['disk']['io_util'] > self.alert_thresholds['io']:
            self.alerts.append({
                'type': 'io',
                'level': 'warning' if self.resources['disk']['io_util'] < 90 else 'critical',
                'message': f"IO利用率过高: {self.resources['disk']['io_util']:.1f}%",
                'threshold': self.alert_thresholds['io'],
                'current': self.resources['disk']['io_util'],
                'time': now
            })
        
        # 连接数告警
        if self.resources['network']['connections'] > self.alert_thresholds['connections']:
            self.alerts.append({
                'type': 'connections',
                'level': 'warning',
                'message': f"连接数过高: {self.resources['network']['connections']}",
                'threshold': self.alert_thresholds['connections'],
                'current': self.resources['network']['connections'],
                'time': now
            })
    
    def get_metrics_history(self) -> Dict:
        """获取历史监控数据"""
        return {
            'server_id': self.server_id,
            'server_name': self.server_name,
            'metrics': self.metrics_history,
            'alerts': self.alerts,
            'thresholds': self.alert_thresholds
        }
    
    def _simulate_resource_changes(self):
        """模拟资源变化"""
        if self.status != 'running':
            return
        
        # CPU变化
        if self.fault_injected and self.fault_type == 'cpu_overload':
            # 故障状态下CPU持续高位
            self.resources['cpu']['usage'] = max(85, min(99, self.resources['cpu']['usage'] + random.uniform(-2, 3)))
        else:
            # 正常波动
            self.resources['cpu']['usage'] = max(5, min(70, self.resources['cpu']['usage'] + random.uniform(-5, 5)))
        self.resources['cpu']['temperature'] = 40 + self.resources['cpu']['usage'] * 0.4 + random.uniform(-2, 2)
        
        # 内存变化
        if self.fault_injected and self.fault_type == 'memory_exhaustion':
            self.resources['memory']['used'] = max(28, min(31.5, self.resources['memory']['used'] + random.uniform(-0.5, 1)))
        else:
            self.resources['memory']['used'] = max(8, min(24, self.resources['memory']['used'] + random.uniform(-1, 1)))
        
        # IO变化
        if self.fault_injected and self.fault_type == 'io_bottleneck':
            self.resources['disk']['io_util'] = max(85, min(99, self.resources['disk']['io_util'] + random.uniform(-3, 5)))
        else:
            self.resources['disk']['io_util'] = max(5, min(50, self.resources['disk']['io_util'] + random.uniform(-5, 5)))
        
        # 网络变化
        if self.fault_injected and self.fault_type == 'network_issue':
            self.resources['network']['latency'] = max(200, min(2000, self.resources['network']['latency'] + random.uniform(-50, 100)))
        else:
            self.resources['network']['latency'] = max(1, min(20, self.resources['network']['latency'] + random.uniform(-2, 2)))
        self.resources['network']['connections'] = max(10, self.resources['network']['connections'] + random.randint(-10, 10))
        self.resources['network']['rx_bytes'] += random.randint(10000, 100000)
        self.resources['network']['tx_bytes'] += random.randint(5000, 50000)
    
    def _write_log(self):
        """写入服务器运行日志"""
        timestamp = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')
        log_entry = {
            'timestamp': timestamp,
            'server_id': self.server_id,
            'server_name': self.server_name,
            'ip': self.ip,
            'status': self.status,
            'fault_injected': self.fault_injected,
            'fault_type': self.fault_type,
            'resources': {
                'cpu_usage': round(self.resources['cpu']['usage'], 2),
                'cpu_temp': round(self.resources['cpu']['temperature'], 2),
                'memory_used': round(self.resources['memory']['used'], 2),
                'memory_total': self.resources['memory']['total'],
                'disk_io_util': round(self.resources['disk']['io_util'], 2),
                'network_latency': round(self.resources['network']['latency'], 2),
                'network_connections': self.resources['network']['connections']
            }
        }
        
        # 追加写入日志文件
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
            
            # 限制日志文件大小（保留最近1000条）
            self._rotate_log_file()
        except Exception as e:
            print(f"Log write error for {self.server_id}: {e}")
    
    def _rotate_log_file(self):
        """日志文件轮转"""
        try:
            if self.log_file.exists() and self.log_file.stat().st_size > 1024 * 1024:  # 1MB
                lines = self.log_file.read_text(encoding='utf-8').strip().split('\n')
                # 只保留最后500条
                with open(self.log_file, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(lines[-500:]) + '\n')
        except:
            pass
    
    def get_recent_logs(self, limit: int = 50) -> List[Dict]:
        """获取最近的日志记录"""
        try:
            if not self.log_file.exists():
                return []
            lines = self.log_file.read_text(encoding='utf-8').strip().split('\n')
            logs = []
            for line in lines[-limit:]:
                try:
                    logs.append(json.loads(line))
                except:
                    pass
            return logs[::-1]  # 最新的在前
        except:
            return []
        
    def _generate_processes(self):
        """生成模拟进程列表"""
        process_templates = [
            {'name': 'systemd', 'user': 'root', 'cpu': 0.1, 'mem': 0.5},
            {'name': 'sshd', 'user': 'root', 'cpu': 0.0, 'mem': 0.2},
            {'name': 'nginx', 'user': 'www-data', 'cpu': 2.5, 'mem': 2.0},
            {'name': 'mysqld', 'user': 'mysql', 'cpu': 5.0, 'mem': 8.0},
            {'name': 'redis-server', 'user': 'redis', 'cpu': 1.0, 'mem': 3.0},
            {'name': 'python3 app.py', 'user': 'app', 'cpu': 3.0, 'mem': 5.0},
            {'name': 'java -jar service.jar', 'user': 'app', 'cpu': 8.0, 'mem': 12.0},
            {'name': 'node server.js', 'user': 'app', 'cpu': 2.0, 'mem': 4.0},
        ]
        
        for i, proc in enumerate(process_templates):
            self.processes.append({
                'pid': 1000 + i * 100 + random.randint(1, 99),
                'name': proc['name'],
                'user': proc['user'],
                'cpu': proc['cpu'] + random.uniform(-0.5, 0.5),
                'mem': proc['mem'] + random.uniform(-0.5, 0.5),
                'status': 'running',
                'start_time': (datetime.now(BEIJING_TZ) - timedelta(hours=random.randint(1, 100))).isoformat()
            })
    
    def _get_time(self) -> str:
        """获取当前时间字符串"""
        return datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')
    
    def _log_operation(self, operation: str, details: str, status: str = "success"):
        """记录操作日志"""
        log_entry = {
            'timestamp': self._get_time(),
            'operation': operation,
            'details': details,
            'status': status,
            'server': self.server_name
        }
        self.operation_logs.append(log_entry)
        # 只保留最近100条
        if len(self.operation_logs) > 100:
            self.operation_logs = self.operation_logs[-100:]
        return log_entry
    
    def get_status(self) -> Dict:
        """获取服务器当前状态"""
        return {
            'server_id': self.server_id,
            'server_name': self.server_name,
            'ip': self.ip,
            'ssh_key': '******' if self.ssh_key else None,  # 隐藏密钥
            'is_virtual': self.is_virtual,
            'system': self.system,
            'status': self.status,
            'uptime': str(datetime.now(BEIJING_TZ) - self.created_time),
            'resources': self.resources,
            'services': self.services,
            'fault_injected': self.fault_injected,
            'fault_type': self.fault_type,
            'last_updated': self._get_time()
        }
    
    def inject_fault(self, fault_type: str) -> Dict:
        """
        注入故障 - 模拟各种故障场景
        """
        self.fault_injected = True
        self.fault_type = fault_type
        
        result = {
            'success': True,
            'fault_type': fault_type,
            'timestamp': self._get_time(),
            'changes': []
        }
        
        if fault_type == 'cpu_overload':
            # 模拟CPU过载
            self.resources['cpu']['usage'] = random.uniform(92, 99)
            self.resources['cpu']['temperature'] = random.uniform(75, 85)
            # 添加一个高CPU进程
            self.processes.append({
                'pid': 9999,
                'name': 'stress-test',
                'user': 'root',
                'cpu': 85.0,
                'mem': 5.0,
                'status': 'running',
                'start_time': self._get_time()
            })
            result['changes'].append('CPU使用率升至 {:.1f}%'.format(self.resources['cpu']['usage']))
            result['changes'].append('CPU温度升至 {:.1f}°C'.format(self.resources['cpu']['temperature']))
            
        elif fault_type == 'memory_exhaustion':
            # 模拟内存不足
            self.resources['memory']['used'] = self.resources['memory']['total'] * 0.95
            self.resources['memory']['swap_used'] = 4.0
            result['changes'].append('内存使用率升至 95%')
            result['changes'].append('Swap使用增加')
            
        elif fault_type == 'io_bottleneck':
            # 模拟IO瓶颈
            self.resources['disk']['io_util'] = random.uniform(92, 99)
            self.resources['disk']['read_speed'] = random.uniform(5, 15)
            self.resources['disk']['write_speed'] = random.uniform(3, 10)
            result['changes'].append('磁盘IO利用率升至 {:.1f}%'.format(self.resources['disk']['io_util']))
            
        elif fault_type == 'service_down':
            # 模拟服务停止
            self.services['app-server']['status'] = 'stopped'
            self.services['app-server']['pid'] = None
            result['changes'].append('app-server 服务已停止')
            
        elif fault_type == 'network_issue':
            # 模拟网络问题
            self.resources['network']['latency'] = random.uniform(500, 2000)
            self.resources['network']['connections'] = random.randint(800, 1000)
            result['changes'].append('网络延迟升至 {:.0f}ms'.format(self.resources['network']['latency']))
        
        self._log_operation('inject_fault', f'注入故障: {fault_type}', 'warning')
        return result
    
    def execute_repair(self, repair_action: str, params: Dict = None) -> Dict:
        """
        执行修复操作 - 核心方法
        返回带有执行过程的详细结果
        """
        params = params or {}
        start_time = time.time()
        
        result = {
            'action': repair_action,
            'server': self.server_name,
            'ip': self.ip,
            'start_time': self._get_time(),
            'success': False,
            'steps': [],
            'output': [],
            'changes': []
        }
        
        # 根据不同的修复操作执行不同的逻辑
        if repair_action == 'restart_service':
            result = self._restart_service(params.get('service_name', 'app-server'), result)
            
        elif repair_action == 'restart_server':
            result = self._restart_server(result)
            
        elif repair_action == 'clear_cache':
            result = self._clear_cache(result)
            
        elif repair_action == 'kill_process':
            result = self._kill_process(params.get('pid') or params.get('process_name'), result)
            
        elif repair_action == 'cleanup_disk':
            result = self._cleanup_disk(result)
            
        elif repair_action == 'reset_network':
            result = self._reset_network(result)
            
        elif repair_action == 'optimize_memory':
            result = self._optimize_memory(result)
            
        elif repair_action == 'reduce_cpu_load':
            result = self._reduce_cpu_load(result)
            
        elif repair_action == 'status_check':
            result = self._status_check(result)
        
        # === 新增修复操作 ===
        elif repair_action == 'kill_zombie_processes':
            result = self._kill_zombie_processes(result)
            
        elif repair_action == 'check_port_connectivity':
            result = self._check_port_connectivity(params.get('port'), result)
            
        elif repair_action == 'check_network_interface':
            result = self._check_network_interface(params.get('interface', 'eth0'), result)
            
        elif repair_action == 'fix_port_issue':
            result = self._fix_port_issue(params.get('port'), result)
            
        elif repair_action == 'execute_script':
            result = self._execute_script(params.get('script_name', 'health_check'), result)
            
        else:
            result['steps'].append({
                'step': 1,
                'action': '未知操作',
                'status': 'failed',
                'message': f'不支持的操作: {repair_action}'
            })
        
        result['end_time'] = self._get_time()
        result['duration'] = round(time.time() - start_time, 2)
        
        # 记录操作日志
        self._log_operation(repair_action, json.dumps(result, ensure_ascii=False)[:200], 
                          'success' if result['success'] else 'failed')
        
        return result
    
    def _restart_service(self, service_name: str, result: Dict) -> Dict:
        """重启服务"""
        result['steps'].append({
            'step': 1,
            'action': f'检查服务 {service_name} 状态',
            'status': 'running',
            'message': '正在检查...',
            'command': f'systemctl status {service_name}'
        })
        time.sleep(0.5)  # 模拟执行时间
        
        if service_name in self.services:
            old_status = self.services[service_name]['status']
            result['steps'][0]['status'] = 'completed'
            result['steps'][0]['message'] = f'服务当前状态: {old_status}'
            result['output'].append(f'● {service_name}.service - {service_name} Service')
            result['output'].append(f'   Active: {old_status}')
            
            # 停止服务
            result['steps'].append({
                'step': 2,
                'action': f'停止服务 {service_name}',
                'status': 'running',
                'message': '正在停止...',
                'command': f'systemctl stop {service_name}'
            })
            time.sleep(0.8)
            self.services[service_name]['status'] = 'stopped'
            result['steps'][1]['status'] = 'completed'
            result['steps'][1]['message'] = '服务已停止'
            result['output'].append(f'Stopping {service_name}...')
            result['output'].append('Done.')
            
            # 启动服务
            result['steps'].append({
                'step': 3,
                'action': f'启动服务 {service_name}',
                'status': 'running',
                'message': '正在启动...',
                'command': f'systemctl start {service_name}'
            })
            time.sleep(1.0)
            self.services[service_name]['status'] = 'running'
            self.services[service_name]['pid'] = random.randint(10000, 60000)
            result['steps'][2]['status'] = 'completed'
            result['steps'][2]['message'] = '服务已启动'
            result['output'].append(f'Starting {service_name}...')
            result['output'].append(f'[OK] {service_name} started with PID {self.services[service_name]["pid"]}')
            
            # 验证服务
            result['steps'].append({
                'step': 4,
                'action': '验证服务状态',
                'status': 'running',
                'message': '正在验证...',
                'command': f'systemctl is-active {service_name}'
            })
            time.sleep(0.3)
            result['steps'][3]['status'] = 'completed'
            result['steps'][3]['message'] = '服务运行正常'
            result['output'].append('active')
            
            result['success'] = True
            result['changes'].append(f'{service_name} 服务已重启')
            result['changes'].append(f'新PID: {self.services[service_name]["pid"]}')
            
            # 清除相关故障
            if self.fault_type == 'service_down':
                self.fault_injected = False
                self.fault_type = None
        else:
            result['steps'][0]['status'] = 'failed'
            result['steps'][0]['message'] = f'服务 {service_name} 不存在'
            result['output'].append(f'Unit {service_name}.service could not be found.')
        
        return result
    
    def _restart_server(self, result: Dict) -> Dict:
        """重启服务器"""
        result['steps'].append({
            'step': 1,
            'action': '准备重启服务器',
            'status': 'running',
            'message': '正在通知所有用户...',
            'command': 'wall "System will restart in 10 seconds"'
        })
        time.sleep(0.5)
        result['steps'][0]['status'] = 'completed'
        result['steps'][0]['message'] = '已广播重启通知'
        result['output'].append('Broadcast message sent to all users')
        
        result['steps'].append({
            'step': 2,
            'action': '停止所有服务',
            'status': 'running',
            'message': '正在停止服务...',
            'command': 'systemctl stop --all'
        })
        time.sleep(1.0)
        for svc in self.services:
            self.services[svc]['status'] = 'stopped'
        result['steps'][1]['status'] = 'completed'
        result['steps'][1]['message'] = '所有服务已停止'
        result['output'].append('Stopping all services... Done.')
        
        result['steps'].append({
            'step': 3,
            'action': '执行系统重启',
            'status': 'running',
            'message': '正在重启...',
            'command': 'shutdown -r now'
        })
        time.sleep(2.0)  # 模拟重启时间
        self.status = 'restarting'
        result['steps'][2]['status'] = 'completed'
        result['steps'][2]['message'] = '系统正在重启'
        result['output'].append('System is going down for reboot NOW!')
        result['output'].append('...')
        result['output'].append('[  OK  ] Reached target Reboot.')
        
        # 重启后恢复
        time.sleep(1.5)
        self.status = 'running'
        self._reset_resources()
        
        result['steps'].append({
            'step': 4,
            'action': '服务器启动完成',
            'status': 'running',
            'message': '正在启动服务...',
            'command': 'systemctl start multi-user.target'
        })
        time.sleep(1.0)
        for svc in self.services:
            self.services[svc]['status'] = 'running'
            self.services[svc]['pid'] = random.randint(10000, 60000)
        result['steps'][3]['status'] = 'completed'
        result['steps'][3]['message'] = '所有服务已恢复'
        result['output'].append('[  OK  ] Started all services.')
        result['output'].append(f'System boot completed. Uptime: 0 seconds')
        
        result['success'] = True
        result['changes'].append('服务器已完成重启')
        result['changes'].append('所有服务已自动恢复')
        
        # 清除故障
        self.fault_injected = False
        self.fault_type = None
        
        return result
    
    def _clear_cache(self, result: Dict) -> Dict:
        """清理缓存"""
        result['steps'].append({
            'step': 1,
            'action': '同步文件系统',
            'status': 'running',
            'message': '正在同步...',
            'command': 'sync'
        })
        time.sleep(0.3)
        result['steps'][0]['status'] = 'completed'
        result['steps'][0]['message'] = '文件系统已同步'
        result['output'].append('Synchronizing filesystems... Done.')
        
        result['steps'].append({
            'step': 2,
            'action': '清理页面缓存',
            'status': 'running',
            'message': '正在清理...',
            'command': 'echo 1 > /proc/sys/vm/drop_caches'
        })
        time.sleep(0.5)
        old_cached = self.resources['memory']['cached']
        self.resources['memory']['cached'] = random.uniform(0.5, 1.0)
        result['steps'][1]['status'] = 'completed'
        result['steps'][1]['message'] = f'已释放 {old_cached - self.resources["memory"]["cached"]:.1f}GB 缓存'
        result['output'].append('Dropping caches... Done.')
        
        result['steps'].append({
            'step': 3,
            'action': '清理Slab缓存',
            'status': 'running',
            'message': '正在清理...',
            'command': 'echo 2 > /proc/sys/vm/drop_caches'
        })
        time.sleep(0.3)
        result['steps'][2]['status'] = 'completed'
        result['steps'][2]['message'] = 'Slab缓存已清理'
        result['output'].append('Dropping slab caches... Done.')
        
        result['success'] = True
        result['changes'].append(f'释放了 {old_cached - self.resources["memory"]["cached"]:.1f}GB 内存缓存')
        
        return result
    
    def _kill_process(self, process_identifier, result: Dict) -> Dict:
        """终止进程"""
        target_process = None
        
        # 查找进程
        for proc in self.processes:
            if (isinstance(process_identifier, int) and proc['pid'] == process_identifier) or \
               (isinstance(process_identifier, str) and process_identifier in proc['name']):
                target_process = proc
                break
        
        result['steps'].append({
            'step': 1,
            'action': f'查找进程 {process_identifier}',
            'status': 'running',
            'message': '正在查找...',
            'command': f'ps aux | grep {process_identifier}'
        })
        time.sleep(0.3)
        
        if target_process:
            result['steps'][0]['status'] = 'completed'
            result['steps'][0]['message'] = f'找到进程 PID={target_process["pid"]}'
            result['output'].append(f'{target_process["user"]} {target_process["pid"]} {target_process["cpu"]}% {target_process["mem"]}% {target_process["name"]}')
            
            result['steps'].append({
                'step': 2,
                'action': f'终止进程 {target_process["pid"]}',
                'status': 'running',
                'message': '发送SIGTERM...',
                'command': f'kill -15 {target_process["pid"]}'
            })
            time.sleep(0.5)
            result['steps'][1]['status'] = 'completed'
            result['steps'][1]['message'] = '已发送终止信号'
            result['output'].append(f'Sent SIGTERM to {target_process["pid"]}')
            
            result['steps'].append({
                'step': 3,
                'action': '等待进程退出',
                'status': 'running',
                'message': '等待中...',
                'command': f'timeout 5 tail --pid={target_process["pid"]} -f /dev/null'
            })
            time.sleep(0.8)
            
            # 移除进程
            self.processes = [p for p in self.processes if p['pid'] != target_process['pid']]
            
            # 如果是高CPU进程，降低CPU使用率
            if target_process.get('cpu', 0) > 50:
                self.resources['cpu']['usage'] = max(20, self.resources['cpu']['usage'] - target_process['cpu'])
                if self.fault_type == 'cpu_overload':
                    self.fault_injected = False
                    self.fault_type = None
            
            result['steps'][2]['status'] = 'completed'
            result['steps'][2]['message'] = '进程已终止'
            result['output'].append(f'Process {target_process["pid"]} terminated.')
            
            result['success'] = True
            result['changes'].append(f'进程 {target_process["name"]} (PID: {target_process["pid"]}) 已终止')
            if target_process.get('cpu', 0) > 50:
                result['changes'].append(f'CPU使用率降至 {self.resources["cpu"]["usage"]:.1f}%')
        else:
            result['steps'][0]['status'] = 'failed'
            result['steps'][0]['message'] = '未找到指定进程'
            result['output'].append('No matching process found.')
        
        return result
    
    def _cleanup_disk(self, result: Dict) -> Dict:
        """清理磁盘空间"""
        result['steps'].append({
            'step': 1,
            'action': '清理临时文件',
            'status': 'running',
            'message': '正在清理 /tmp...',
            'command': 'rm -rf /tmp/*'
        })
        time.sleep(0.5)
        freed = random.uniform(1, 5)
        result['steps'][0]['status'] = 'completed'
        result['steps'][0]['message'] = f'已清理 {freed:.1f}GB 临时文件'
        result['output'].append(f'Removed {freed:.1f}GB from /tmp')
        
        result['steps'].append({
            'step': 2,
            'action': '清理日志文件',
            'status': 'running',
            'message': '正在轮转日志...',
            'command': 'logrotate -f /etc/logrotate.conf'
        })
        time.sleep(0.8)
        log_freed = random.uniform(2, 8)
        result['steps'][1]['status'] = 'completed'
        result['steps'][1]['message'] = f'已压缩 {log_freed:.1f}GB 日志'
        result['output'].append(f'Rotated and compressed {log_freed:.1f}GB of logs')
        
        result['steps'].append({
            'step': 3,
            'action': '清理包缓存',
            'status': 'running',
            'message': '正在清理...',
            'command': 'apt-get clean && yum clean all'
        })
        time.sleep(0.5)
        pkg_freed = random.uniform(0.5, 2)
        result['steps'][2]['status'] = 'completed'
        result['steps'][2]['message'] = f'已清理 {pkg_freed:.1f}GB 包缓存'
        result['output'].append(f'Cleaned {pkg_freed:.1f}GB package cache')
        
        total_freed = freed + log_freed + pkg_freed
        self.resources['disk']['used'] -= total_freed
        
        result['success'] = True
        result['changes'].append(f'总共释放 {total_freed:.1f}GB 磁盘空间')
        result['changes'].append(f'当前磁盘使用: {self.resources["disk"]["used"]:.1f}GB / {self.resources["disk"]["total"]}GB')
        
        return result
    
    def _reset_network(self, result: Dict) -> Dict:
        """重置网络"""
        result['steps'].append({
            'step': 1,
            'action': '检查网络接口',
            'status': 'running',
            'message': '正在检查...',
            'command': 'ip link show'
        })
        time.sleep(0.3)
        result['steps'][0]['status'] = 'completed'
        result['steps'][0]['message'] = '网络接口: eth0, lo'
        result['output'].append('1: lo: <LOOPBACK,UP> mtu 65536')
        result['output'].append('2: eth0: <BROADCAST,MULTICAST,UP> mtu 1500')
        
        result['steps'].append({
            'step': 2,
            'action': '重启网络服务',
            'status': 'running',
            'message': '正在重启...',
            'command': 'systemctl restart networking'
        })
        time.sleep(1.0)
        result['steps'][1]['status'] = 'completed'
        result['steps'][1]['message'] = '网络服务已重启'
        result['output'].append('Restarting networking service... Done.')
        
        result['steps'].append({
            'step': 3,
            'action': '清除连接状态',
            'status': 'running',
            'message': '正在清除...',
            'command': 'conntrack -F'
        })
        time.sleep(0.5)
        old_connections = self.resources['network']['connections']
        self.resources['network']['connections'] = random.randint(50, 100)
        self.resources['network']['latency'] = random.uniform(1, 5)
        result['steps'][2]['status'] = 'completed'
        result['steps'][2]['message'] = f'清除了 {old_connections - self.resources["network"]["connections"]} 个连接'
        result['output'].append('Connection tracking table flushed.')
        
        # 清除网络故障
        if self.fault_type == 'network_issue':
            self.fault_injected = False
            self.fault_type = None
        
        result['success'] = True
        result['changes'].append('网络服务已重置')
        result['changes'].append(f'网络延迟降至 {self.resources["network"]["latency"]:.1f}ms')
        result['changes'].append(f'活动连接数: {self.resources["network"]["connections"]}')
        
        return result
    
    def _optimize_memory(self, result: Dict) -> Dict:
        """优化内存"""
        result['steps'].append({
            'step': 1,
            'action': '分析内存使用',
            'status': 'running',
            'message': '正在分析...',
            'command': 'free -h && ps aux --sort=-%mem | head -10'
        })
        time.sleep(0.5)
        result['steps'][0]['status'] = 'completed'
        result['steps'][0]['message'] = f'当前内存使用: {self.resources["memory"]["used"]:.1f}GB'
        result['output'].append(f'Total: {self.resources["memory"]["total"]}GB')
        result['output'].append(f'Used: {self.resources["memory"]["used"]:.1f}GB')
        result['output'].append(f'Cached: {self.resources["memory"]["cached"]:.1f}GB')
        
        result['steps'].append({
            'step': 2,
            'action': '清理内存缓存',
            'status': 'running',
            'message': '正在清理...',
            'command': 'sync && echo 3 > /proc/sys/vm/drop_caches'
        })
        time.sleep(0.8)
        old_used = self.resources['memory']['used']
        freed_cache = self.resources['memory']['cached'] * 0.8
        self.resources['memory']['cached'] = self.resources['memory']['cached'] * 0.2
        result['steps'][1]['status'] = 'completed'
        result['steps'][1]['message'] = f'释放了 {freed_cache:.1f}GB 缓存'
        result['output'].append('Caches dropped successfully.')
        
        result['steps'].append({
            'step': 3,
            'action': '清理Swap',
            'status': 'running',
            'message': '正在清理...',
            'command': 'swapoff -a && swapon -a'
        })
        time.sleep(1.0)
        old_swap = self.resources['memory']['swap_used']
        self.resources['memory']['swap_used'] = 0
        # 内存使用会因swap清理而调整
        self.resources['memory']['used'] = min(
            self.resources['memory']['total'] * 0.65,
            self.resources['memory']['used'] - freed_cache
        )
        result['steps'][2]['status'] = 'completed'
        result['steps'][2]['message'] = f'释放了 {old_swap:.1f}GB Swap'
        result['output'].append('Swap cleared and re-enabled.')
        
        # 清除内存故障
        if self.fault_type == 'memory_exhaustion':
            self.fault_injected = False
            self.fault_type = None
        
        result['success'] = True
        result['changes'].append(f'内存使用从 {old_used:.1f}GB 降至 {self.resources["memory"]["used"]:.1f}GB')
        result['changes'].append(f'释放了 {freed_cache + old_swap:.1f}GB 内存空间')
        
        return result
    
    def _reduce_cpu_load(self, result: Dict) -> Dict:
        """降低CPU负载"""
        result['steps'].append({
            'step': 1,
            'action': '分析高CPU进程',
            'status': 'running',
            'message': '正在分析...',
            'command': 'top -bn1 | head -20'
        })
        time.sleep(0.5)
        
        # 找出高CPU进程
        high_cpu_procs = [p for p in self.processes if p.get('cpu', 0) > 30]
        result['steps'][0]['status'] = 'completed'
        result['steps'][0]['message'] = f'发现 {len(high_cpu_procs)} 个高CPU进程'
        for p in high_cpu_procs:
            result['output'].append(f'PID {p["pid"]}: {p["name"]} - CPU: {p["cpu"]:.1f}%')
        
        if high_cpu_procs:
            result['steps'].append({
                'step': 2,
                'action': '调整进程优先级',
                'status': 'running',
                'message': '正在调整...',
                'command': 'renice 19 -p <high_cpu_pids>'
            })
            time.sleep(0.5)
            for p in high_cpu_procs:
                p['cpu'] = p['cpu'] * 0.5  # 降低50%
            result['steps'][1]['status'] = 'completed'
            result['steps'][1]['message'] = '已降低高CPU进程优先级'
            result['output'].append('Process priorities adjusted.')
        
        # 终止测试进程(如果存在)
        stress_procs = [p for p in self.processes if 'stress' in p.get('name', '').lower()]
        if stress_procs:
            result['steps'].append({
                'step': 3,
                'action': '终止压力测试进程',
                'status': 'running',
                'message': '正在终止...',
                'command': 'pkill -f stress'
            })
            time.sleep(0.5)
            for p in stress_procs:
                self.processes.remove(p)
            result['steps'][-1]['status'] = 'completed'
            result['steps'][-1]['message'] = f'已终止 {len(stress_procs)} 个压力测试进程'
            result['output'].append('Stress test processes terminated.')
        
        # 重新计算CPU使用率
        old_cpu = self.resources['cpu']['usage']
        self.resources['cpu']['usage'] = sum(p.get('cpu', 0) for p in self.processes)
        self.resources['cpu']['usage'] = max(15, min(40, self.resources['cpu']['usage']))
        self.resources['cpu']['temperature'] = random.uniform(45, 55)
        
        # 清除CPU故障
        if self.fault_type == 'cpu_overload':
            self.fault_injected = False
            self.fault_type = None
        
        result['success'] = True
        result['changes'].append(f'CPU使用率从 {old_cpu:.1f}% 降至 {self.resources["cpu"]["usage"]:.1f}%')
        result['changes'].append(f'CPU温度降至 {self.resources["cpu"]["temperature"]:.1f}°C')
        
        return result
    
    def _status_check(self, result: Dict) -> Dict:
        """状态检查"""
        result['steps'].append({
            'step': 1,
            'action': '检查系统状态',
            'status': 'running',
            'message': '正在检查...',
            'command': 'uptime && free -h && df -h'
        })
        time.sleep(0.3)
        result['steps'][0]['status'] = 'completed'
        result['steps'][0]['message'] = '系统状态正常'
        result['output'].append(f'Server: {self.server_name} ({self.ip})')
        result['output'].append(f'Status: {self.status}')
        result['output'].append(f'CPU: {self.resources["cpu"]["usage"]:.1f}%')
        result['output'].append(f'Memory: {self.resources["memory"]["used"]:.1f}/{self.resources["memory"]["total"]}GB')
        result['output'].append(f'Disk: {self.resources["disk"]["used"]:.1f}/{self.resources["disk"]["total"]}GB')
        
        result['success'] = True
        result['changes'].append('状态检查完成')
        
        return result
    
    def _kill_zombie_processes(self, result: Dict) -> Dict:
        """查杀僵尸进程"""
        result['steps'].append({
            'step': 1,
            'action': '扫描僵尸进程',
            'status': 'running',
            'message': '正在扫描...',
            'command': 'ps aux | grep -w Z'
        })
        time.sleep(0.5)
        
        # 模拟找到的僵尸进程
        zombie_count = random.randint(0, 3)
        zombie_pids = [random.randint(10000, 60000) for _ in range(zombie_count)]
        
        result['steps'][0]['status'] = 'completed'
        result['steps'][0]['message'] = f'发现 {zombie_count} 个僵尸进程'
        result['output'].append(f'Scanning for zombie processes...')
        result['output'].append(f'Found {zombie_count} zombie process(es)')
        
        if zombie_count > 0:
            for pid in zombie_pids:
                result['output'].append(f'  PID {pid}: <defunct>')
            
            result['steps'].append({
                'step': 2,
                'action': '终止僵尸进程父进程',
                'status': 'running',
                'message': '正在清理...',
                'command': f'kill -9 {" ".join(map(str, zombie_pids))}'
            })
            time.sleep(0.8)
            result['steps'][1]['status'] = 'completed'
            result['steps'][1]['message'] = f'已清理 {zombie_count} 个僵尸进程'
            result['output'].append(f'Killed {zombie_count} zombie process(es)')
            result['changes'].append(f'清理了 {zombie_count} 个僵尸进程')
        else:
            result['changes'].append('未发现僵尸进程')
        
        result['success'] = True
        return result
    
    def _check_port_connectivity(self, port: int, result: Dict) -> Dict:
        """端口连通性监测"""
        if port is None:
            port = 80  # 默认检查80端口
        
        result['steps'].append({
            'step': 1,
            'action': f'检查端口 {port} 连通性',
            'status': 'running',
            'message': '正在检测...',
            'command': f'netstat -tlnp | grep :{port}'
        })
        time.sleep(0.4)
        
        port_info = self.monitored_ports.get(port, {'service': 'unknown', 'status': 'open'})
        is_open = port_info.get('status') == 'open'
        
        result['steps'][0]['status'] = 'completed'
        result['output'].append(f'Checking port {port}...')
        
        if is_open:
            result['steps'][0]['message'] = f'端口 {port} 连通正常'
            result['output'].append(f'tcp   0   0 0.0.0.0:{port}   0.0.0.0:*   LISTEN   {port_info.get("service", "unknown")}')
            result['changes'].append(f'端口 {port} 连通正常')
            result['success'] = True
        else:
            result['steps'][0]['message'] = f'端口 {port} 不通'
            result['output'].append(f'Port {port} is not listening')
            
            # 尝试重启服务
            service_name = port_info.get('service', 'unknown')
            if service_name in self.services:
                result['steps'].append({
                    'step': 2,
                    'action': f'重启服务 {service_name}',
                    'status': 'running',
                    'message': '正在重启...',
                    'command': f'systemctl restart {service_name}'
                })
                time.sleep(1.0)
                
                self.services[service_name]['status'] = 'running'
                self.services[service_name]['pid'] = random.randint(10000, 60000)
                self.monitored_ports[port]['status'] = 'open'
                
                result['steps'][1]['status'] = 'completed'
                result['steps'][1]['message'] = f'服务 {service_name} 已重启'
                result['output'].append(f'Service {service_name} restarted successfully')
                result['changes'].append(f'重启服务 {service_name}，端口 {port} 已恢复')
                result['success'] = True
            else:
                result['changes'].append(f'端口 {port} 不通，未找到对应服务')
        
        return result
    
    def _check_network_interface(self, interface: str, result: Dict) -> Dict:
        """网卡状态监测"""
        result['steps'].append({
            'step': 1,
            'action': f'检查网卡 {interface} 状态',
            'status': 'running',
            'message': '正在检测...',
            'command': f'ip link show {interface}'
        })
        time.sleep(0.4)
        
        nic_info = self.network_interfaces.get(interface)
        
        if nic_info is None:
            result['steps'][0]['status'] = 'failed'
            result['steps'][0]['message'] = f'网卡 {interface} 不存在'
            result['output'].append(f'Device "{interface}" does not exist.')
            return result
        
        result['output'].append(f'{interface}: <BROADCAST,MULTICAST,{"UP" if nic_info["status"] == "up" else "DOWN"}>')
        result['output'].append(f'    link/ether {nic_info["mac"]} brd ff:ff:ff:ff:ff:ff')
        
        if nic_info['status'] == 'up':
            result['steps'][0]['status'] = 'completed'
            result['steps'][0]['message'] = f'网卡 {interface} 状态正常'
            result['changes'].append(f'网卡 {interface} 状态正常 (UP)')
            result['success'] = True
        else:
            result['steps'][0]['status'] = 'completed'
            result['steps'][0]['message'] = f'网卡 {interface} 状态异常 (DOWN)'
            
            # 重启网卡
            result['steps'].append({
                'step': 2,
                'action': f'重启网卡 {interface}',
                'status': 'running',
                'message': '正在重启...',
                'command': f'ip link set {interface} down && ip link set {interface} up'
            })
            time.sleep(0.8)
            
            self.network_interfaces[interface]['status'] = 'up'
            
            result['steps'][1]['status'] = 'completed'
            result['steps'][1]['message'] = f'网卡 {interface} 已重启'
            result['output'].append(f'Interface {interface} restarted: DOWN -> UP')
            result['changes'].append(f'网卡 {interface} 已重启恢复')
            result['success'] = True
        
        return result
    
    def _fix_port_issue(self, port: int, result: Dict) -> Dict:
        """端口异常修复"""
        if port is None:
            port = 8080  # 默认修复8080端口
        
        result['steps'].append({
            'step': 1,
            'action': f'检查端口 {port} 占用情况',
            'status': 'running',
            'message': '正在检查...',
            'command': f'lsof -i :{port}'
        })
        time.sleep(0.4)
        
        result['steps'][0]['status'] = 'completed'
        result['steps'][0]['message'] = f'端口 {port} 检查完成'
        
        # 模拟端口被异常占用
        fake_pid = random.randint(20000, 50000)
        result['output'].append(f'COMMAND   PID   USER   FD   TYPE   DEVICE   SIZE/OFF   NODE   NAME')
        result['output'].append(f'unknown   {fake_pid}   root   3u   IPv4   12345   0t0   TCP   *:{port} (LISTEN)')
        
        result['steps'].append({
            'step': 2,
            'action': f'释放端口 {port}',
            'status': 'running',
            'message': '正在释放...',
            'command': f'fuser -k {port}/tcp'
        })
        time.sleep(0.6)
        
        result['steps'][1]['status'] = 'completed'
        result['steps'][1]['message'] = f'端口 {port} 已释放'
        result['output'].append(f'{port}/tcp: {fake_pid}')
        result['output'].append(f'Process {fake_pid} killed')
        
        # 重启对应服务
        port_info = self.monitored_ports.get(port, {})
        service_name = port_info.get('service')
        
        if service_name and service_name in self.services:
            result['steps'].append({
                'step': 3,
                'action': f'重启服务 {service_name}',
                'status': 'running',
                'message': '正在重启...',
                'command': f'systemctl restart {service_name}'
            })
            time.sleep(0.8)
            
            self.services[service_name]['pid'] = random.randint(10000, 60000)
            
            result['steps'][2]['status'] = 'completed'
            result['steps'][2]['message'] = f'服务 {service_name} 已重启'
            result['output'].append(f'[OK] {service_name} started on port {port}')
        
        result['success'] = True
        result['changes'].append(f'端口 {port} 异常已修复')
        if service_name:
            result['changes'].append(f'服务 {service_name} 已重新启动')
        
        return result
    
    def _execute_script(self, script_name: str, result: Dict) -> Dict:
        """执行预设脚本"""
        script_path = self.preset_scripts.get(script_name)
        
        if not script_path:
            result['steps'].append({
                'step': 1,
                'action': f'查找脚本 {script_name}',
                'status': 'failed',
                'message': f'脚本 {script_name} 不存在'
            })
            return result
        
        result['steps'].append({
            'step': 1,
            'action': f'检查脚本权限',
            'status': 'running',
            'message': '正在检查...',
            'command': f'ls -la {script_path}'
        })
        time.sleep(0.3)
        result['steps'][0]['status'] = 'completed'
        result['steps'][0]['message'] = '脚本存在且可执行'
        result['output'].append(f'-rwxr-xr-x 1 root root 1024 Dec 24 10:00 {script_path}')
        
        result['steps'].append({
            'step': 2,
            'action': f'执行脚本 {script_name}',
            'status': 'running',
            'message': '正在执行...',
            'command': f'bash {script_path}'
        })
        time.sleep(1.5)  # 模拟脚本执行时间
        
        # 模拟脚本输出
        script_outputs = {
            'health_check': [
                '=== Health Check Report ===',
                f'Server: {self.server_name}',
                f'Status: OK',
                f'CPU: {self.resources["cpu"]["usage"]:.1f}% - OK',
                f'Memory: {self.resources["memory"]["used"]:.1f}GB - OK',
                f'Disk: {self.resources["disk"]["used"]:.1f}GB - OK',
                '=== Check Complete ==='
            ],
            'backup': [
                '=== Backup Started ===',
                'Creating backup directory...',
                'Backing up config files...',
                'Backing up database...',
                'Compressing backup...',
                f'Backup saved to /backup/{self.server_id}_backup.tar.gz',
                '=== Backup Complete ==='
            ],
            'cleanup': [
                '=== Cleanup Started ===',
                'Cleaning /tmp...',
                'Cleaning /var/log/old...',
                'Removing cached files...',
                'Freed 1.2GB disk space',
                '=== Cleanup Complete ==='
            ],
            'restart_services': [
                '=== Restarting Services ===',
                'Stopping nginx... OK',
                'Stopping mysql... OK',
                'Stopping redis... OK',
                'Starting nginx... OK',
                'Starting mysql... OK',
                'Starting redis... OK',
                '=== All Services Restarted ==='
            ]
        }
        
        for line in script_outputs.get(script_name, ['Script executed successfully']):
            result['output'].append(line)
        
        result['steps'][1]['status'] = 'completed'
        result['steps'][1]['message'] = '脚本执行完成'
        
        result['success'] = True
        result['changes'].append(f'脚本 {script_name} ({script_path}) 执行成功')
        
        return result
    
    def _reset_resources(self):
        """重置资源到正常状态"""
        self.resources['cpu']['usage'] = random.uniform(20, 40)
        self.resources['cpu']['temperature'] = random.uniform(45, 55)
        self.resources['memory']['used'] = random.uniform(8, 14)
        self.resources['memory']['cached'] = random.uniform(2, 4)
        self.resources['memory']['swap_used'] = 0
        self.resources['disk']['io_util'] = random.uniform(10, 25)
        self.resources['network']['latency'] = random.uniform(1, 5)
        self.resources['network']['connections'] = random.randint(50, 150)
    
    def get_operation_logs(self, limit: int = 20) -> List[Dict]:
        """获取操作日志"""
        return self.operation_logs[-limit:][::-1]


class VirtualServerCluster:
    """虚拟服务器集群管理"""
    
    def __init__(self):
        self.servers: Dict[str, VirtualServer] = {}
        self._server_counter = 4  # 初始4台服务器
        self._initialize_servers()
    
    def _initialize_servers(self):
        """初始化虚拟服务器集群"""
        server_configs = [
            {'id': 'srv-001', 'name': '测试虚拟服务器', 'ip': '192.168.1.100'},
        ]
        
        for config in server_configs:
            server = VirtualServer(config['id'], config['name'], config['ip'], is_virtual=True)
            self.servers[config['id']] = server
    
    def add_server(self, ip: str, name: str = None, ssh_key: str = None, system: str = 'default') -> Dict:
        """添加服务器"""
        # 检查IP是否已存在
        for srv in self.servers.values():
            if srv.ip == ip:
                return {
                    'success': False,
                    'message': f'IP {ip} 已存在'
                }

        self._server_counter += 1
        server_id = f'srv-{self._server_counter:03d}'

        # 如果没有名称，自动生成
        if not name:
            name = f'服务器-{self._server_counter:02d}'

        # 判断是否为虚拟服务器（无SSH密钥则为虚拟）
        is_virtual = not bool(ssh_key)

        server = VirtualServer(server_id, name, ip, ssh_key=ssh_key, is_virtual=is_virtual, system=system)
        self.servers[server_id] = server
        
        return {
            'success': True,
            'message': f'服务器 {name} ({ip}) 添加成功',
            'server_id': server_id,
            'server': server.get_status()
        }
    
    def remove_server(self, server_id: str) -> Dict:
        """删除服务器"""
        if server_id not in self.servers:
            return {
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }
        
        # 不允许删除内置的4台虚拟服务器
        if server_id == 'srv-001':  # 只保护测试虚拟服务器
            return {
                'success': False,
                'message': '不能删除内置虚拟服务器'
            }
        
        server = self.servers[server_id]
        server.stop_simulation()  # 停止模拟线程
        
        server_name = server.server_name
        del self.servers[server_id]
        
        return {
            'success': True,
            'message': f'服务器 {server_name} 已删除'
        }
    
    def get_server(self, server_id: str) -> Optional[VirtualServer]:
        """获取指定服务器"""
        return self.servers.get(server_id)
    
    def get_server_by_ip(self, ip: str) -> Optional[VirtualServer]:
        """根据IP获取服务器"""
        for server in self.servers.values():
            if server.ip == ip:
                return server
        return None
    
    def get_server_by_name(self, name: str) -> Optional[VirtualServer]:
        """根据名称获取服务器"""
        for server in self.servers.values():
            if server.server_name == name or name in server.server_name:
                return server
        return None
    
    def get_default_server(self) -> VirtualServer:
        """获取默认服务器"""
        return list(self.servers.values())[0] if self.servers else None
    
    def get_all_servers(self) -> List[Dict]:
        """获取所有服务器状态"""
        return [server.get_status() for server in self.servers.values()]
    
    def get_faulty_servers(self) -> List[Dict]:
        """获取所有有故障的服务器"""
        faulty = []
        for server in self.servers.values():
            status = server.get_status()
            if status['fault_injected']:
                faulty.append(status)
            elif status['resources']['cpu']['usage'] > 85:
                status['detected_fault'] = 'cpu_high'
                faulty.append(status)
            elif status['resources']['memory']['used'] / status['resources']['memory']['total'] > 0.90:
                status['detected_fault'] = 'memory_high'
                faulty.append(status)
            elif status['resources']['disk']['io_util'] > 85:
                status['detected_fault'] = 'io_high'
                faulty.append(status)
        return faulty
    
    def execute_repair_on_server(self, server_id: str, action: str, params: Dict = None) -> Dict:
        """在指定服务器上执行修复操作"""
        server = self.get_server(server_id)
        if not server:
            return {
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }
        return server.execute_repair(action, params)
    
    def inject_fault_to_server(self, server_id: str, fault_type: str) -> Dict:
        """向指定服务器注入故障"""
        server = self.get_server(server_id)
        if not server:
            return {
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }
        return server.inject_fault(fault_type)


# 全局虚拟服务器集群实例
_virtual_cluster = None


def get_virtual_cluster() -> VirtualServerCluster:
    """获取虚拟服务器集群实例"""
    global _virtual_cluster
    if _virtual_cluster is None:
        _virtual_cluster = VirtualServerCluster()
    return _virtual_cluster


def get_virtual_server(server_id: str = None) -> VirtualServer:
    """获取虚拟服务器实例"""
    cluster = get_virtual_cluster()
    if server_id:
        return cluster.get_server(server_id)
    return cluster.get_default_server()
