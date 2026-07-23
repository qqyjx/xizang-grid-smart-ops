# -*- coding: utf-8 -*-
"""
真实服务器连接与操作模块
通过SSH连接内网服务器，执行监控和修复操作
"""

import os
import subprocess
import socket
import time
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

# 北京时间
BEIJING_TZ = timezone(timedelta(hours=8))

# 日志目录
LOG_DIR = Path(__file__).parent.parent / 'logs' / 'real_servers'
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 脚本配置文件路径
SCRIPTS_CONFIG_FILE = Path(__file__).parent.parent / 'data' / 'scripts_config.json'


def load_scripts_config() -> Dict:
    """加载脚本配置"""
    try:
        if SCRIPTS_CONFIG_FILE.exists():
            with open(SCRIPTS_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"[RealServer] 加载脚本配置失败: {e}")
    return {}


def save_scripts_config(config: Dict) -> bool:
    """保存脚本配置"""
    try:
        with open(SCRIPTS_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        print(f"[RealServer] 保存脚本配置失败: {e}")
        return False


def get_script_commands(script_name: str) -> Tuple[List[str], Dict]:
    """
    获取脚本命令列表
    返回: (命令列表, 脚本配置信息)
    """
    config = load_scripts_config()
    scripts = config.get('scripts', {})
    
    if script_name in scripts:
        script_config = scripts[script_name]
        if script_config.get('use_custom_script') and script_config.get('custom_script_path'):
            # 使用自定义脚本路径
            return [f"bash {script_config['custom_script_path']}"], script_config
        else:
            # 使用配置的命令列表
            return script_config.get('commands', []), script_config
    
    # 默认命令
    default_commands = {
        'health_check': ['echo "健康检查"', 'uptime', 'free -h', 'df -h'],
        'backup': ['echo "执行备份"'],
        'cleanup': ['echo "执行清理"', 'sync', 'echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true'],
        'restart_services': ['echo "重启服务"'],
        'restart_server': ['echo "即将重启服务器"', 'shutdown -r now'],
        'status_check': ['uptime', 'free -h', 'df -h', 'netstat -tlnp 2>/dev/null || ss -tlnp']
    }
    
    return default_commands.get(script_name, ['echo "未知脚本"']), {}


class RealServerConnection:
    """真实服务器SSH连接类"""
    
    def __init__(self, server_id: str, server_name: str, ip: str, 
                 ssh_key: str = None, ssh_password: str = None,
                 ssh_user: str = 'root', ssh_port: int = 22):
        self.server_id = server_id
        self.server_name = server_name
        self.ip = ip
        self.ssh_key = ssh_key  # SSH私钥内容或路径
        self.ssh_password = ssh_password  # SSH密码
        self.ssh_user = ssh_user
        self.ssh_port = ssh_port
        self.status = "unknown"
        self.last_check = None
        self.created_time = datetime.now(BEIJING_TZ)
        
        # 认证方式: 'key' 或 'password'
        self.auth_type = 'key' if ssh_key else ('password' if ssh_password else 'none')
        
        # SSH密钥临时文件
        self._key_file = None
        if ssh_key:
            self._setup_ssh_key(ssh_key)
        
        # 监控数据
        self.metrics = {
            'cpu': {'usage': 0, 'cores': 0},
            'memory': {'total': 0, 'used': 0, 'free': 0},
            'disk': {'total': 0, 'used': 0, 'usage_percent': 0},
            'network': {'rx_bytes': 0, 'tx_bytes': 0},
            'uptime': ''
        }
        
        # 历史监控数据
        self.metrics_history = {
            'cpu': [],
            'memory': [],
            'disk': [],
            'io': [],
            'connections': [],
            'timestamps': []
        }
        
        # 告警阈值
        self.alert_thresholds = {
            'cpu': 80,
            'memory': 85,
            'disk': 90,
            'io': 80,
            'connections': 500
        }
        
        self.alerts = []
        
        # 操作日志
        self.operation_logs = []
        
        # 运行日志文件
        self.log_file = LOG_DIR / f"{server_id}.log"
        
        # 后台监控线程
        self._monitor_thread = None
        self._monitor_running = False
        
    def _setup_ssh_key(self, ssh_key: str):
        """设置SSH密钥"""
        key_dir = Path(__file__).parent.parent / 'data' / 'ssh_keys'
        key_dir.mkdir(parents=True, exist_ok=True)
        
        self._key_file = key_dir / f"{self.server_id}_key"
        
        # 如果是路径，检查文件是否存在
        if os.path.isfile(ssh_key):
            self._key_file = Path(ssh_key)
        else:
            # 假设是密钥内容，写入文件
            with open(self._key_file, 'w') as f:
                f.write(ssh_key)
            os.chmod(self._key_file, 0o600)
    
    def _get_time(self) -> str:
        """获取北京时间字符串"""
        return datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')
    
    def ping(self, timeout: int = 5) -> Tuple[bool, str]:
        """
        Ping测试服务器连通性
        返回: (是否连通, 消息)
        """
        try:
            result = subprocess.run(
                ['ping', '-c', '3', '-W', str(timeout), self.ip],
                capture_output=True,
                text=True,
                timeout=timeout + 5
            )
            if result.returncode == 0:
                # 解析延迟
                lines = result.stdout.split('\n')
                for line in lines:
                    if 'avg' in line or '平均' in line:
                        return True, f"Ping成功: {line.strip()}"
                return True, "Ping成功"
            else:
                return False, f"Ping失败: 主机不可达"
        except subprocess.TimeoutExpired:
            return False, f"Ping超时 ({timeout}秒)"
        except Exception as e:
            return False, f"Ping错误: {str(e)}"
    
    def test_ssh_connection(self, timeout: int = 10) -> Tuple[bool, str]:
        """
        测试SSH连接
        返回: (是否成功, 消息)
        """
        try:
            # 检查密码认证是否可用
            if self.ssh_password:
                sshpass_check = subprocess.run(['which', 'sshpass'], capture_output=True)
                if sshpass_check.returncode != 0:
                    return False, "密码认证需要安装sshpass工具 (apt install sshpass 或 yum install sshpass)"
            
            ssh_cmd = self._build_ssh_command(['echo', 'SSH_OK'], timeout)
            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 5
            )
            if result.returncode == 0 and 'SSH_OK' in result.stdout:
                return True, "SSH连接成功"
            else:
                error_msg = result.stderr.strip() if result.stderr else "未知错误"
                return False, f"SSH连接失败: {error_msg}"
        except subprocess.TimeoutExpired:
            return False, f"SSH连接超时 ({timeout}秒)"
        except Exception as e:
            return False, f"SSH连接错误: {str(e)}"
    
    def _build_ssh_command(self, remote_cmd: List[str], timeout: int = 30) -> List[str]:
        """构建SSH命令"""
        # 密码认证使用sshpass
        if self.ssh_password:
            ssh_cmd = ['sshpass', '-p', self.ssh_password, 'ssh']
        else:
            ssh_cmd = ['ssh']
        
        # 添加选项
        ssh_cmd.extend([
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            '-o', f'ConnectTimeout={timeout}',
            '-p', str(self.ssh_port)
        ])
        
        # 密钥认证需要BatchMode
        if not self.ssh_password:
            ssh_cmd.extend(['-o', 'BatchMode=yes'])
        
        # 添加密钥
        if self._key_file and self._key_file.exists():
            ssh_cmd.extend(['-i', str(self._key_file)])
        
        # 添加目标
        ssh_cmd.append(f'{self.ssh_user}@{self.ip}')
        
        # 添加远程命令
        if isinstance(remote_cmd, list):
            ssh_cmd.extend(remote_cmd)
        else:
            ssh_cmd.append(remote_cmd)
        
        return ssh_cmd
    
    def execute_command(self, command: str, timeout: int = 60) -> Dict[str, Any]:
        """
        在远程服务器执行命令
        返回执行结果
        """
        result = {
            'success': False,
            'command': command,
            'stdout': '',
            'stderr': '',
            'return_code': -1,
            'execution_time': 0,
            'timestamp': self._get_time()
        }
        
        start_time = time.time()
        
        try:
            ssh_cmd = self._build_ssh_command([command], timeout)
            proc = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 10,
                shell=False
            )
            
            result['stdout'] = proc.stdout
            result['stderr'] = proc.stderr
            result['return_code'] = proc.returncode
            result['success'] = (proc.returncode == 0)
            
        except subprocess.TimeoutExpired:
            result['stderr'] = f'命令执行超时 ({timeout}秒)'
        except Exception as e:
            result['stderr'] = f'执行错误: {str(e)}'
        
        result['execution_time'] = round(time.time() - start_time, 2)
        
        # 记录日志
        self._log_operation('execute_command', command, 
                          'success' if result['success'] else 'failed')
        
        return result
    
    def get_system_metrics(self) -> Dict[str, Any]:
        """获取系统监控指标"""
        metrics = {
            'timestamp': self._get_time(),
            'success': False,
            'cpu': {},
            'memory': {},
            'disk': {},
            'network': {},
            'load': {},
            'uptime': ''
        }
        
        # 收集各项指标的命令
        commands = {
            'cpu': "top -bn1 | grep 'Cpu(s)' | awk '{print 100-$8}'",
            'memory': "free -m | awk 'NR==2{printf \"%d %d %d %.2f\", $2,$3,$4,$3*100/$2}'",
            'disk': "df -h / | awk 'NR==2{print $2,$3,$4,$5}'",
            'uptime': "uptime -p",
            'load': "cat /proc/loadavg | awk '{print $1,$2,$3}'",
            'cpu_cores': "nproc",
            'network': "cat /proc/net/dev | grep -E 'eth0|ens' | awk '{print $2,$10}'"
        }
        
        try:
            # CPU使用率
            cpu_result = self.execute_command(commands['cpu'])
            if cpu_result['success']:
                try:
                    metrics['cpu']['usage'] = float(cpu_result['stdout'].strip())
                except:
                    metrics['cpu']['usage'] = 0
            
            # CPU核数
            cores_result = self.execute_command(commands['cpu_cores'])
            if cores_result['success']:
                try:
                    metrics['cpu']['cores'] = int(cores_result['stdout'].strip())
                except:
                    metrics['cpu']['cores'] = 1
            
            # 内存
            mem_result = self.execute_command(commands['memory'])
            if mem_result['success']:
                try:
                    parts = mem_result['stdout'].strip().split()
                    metrics['memory']['total'] = int(parts[0])
                    metrics['memory']['used'] = int(parts[1])
                    metrics['memory']['free'] = int(parts[2])
                    metrics['memory']['usage_percent'] = float(parts[3])
                except:
                    pass
            
            # 磁盘
            disk_result = self.execute_command(commands['disk'])
            if disk_result['success']:
                try:
                    parts = disk_result['stdout'].strip().split()
                    metrics['disk']['total'] = parts[0]
                    metrics['disk']['used'] = parts[1]
                    metrics['disk']['free'] = parts[2]
                    metrics['disk']['usage_percent'] = parts[3].replace('%', '')
                except:
                    pass
            
            # 运行时间
            uptime_result = self.execute_command(commands['uptime'])
            if uptime_result['success']:
                metrics['uptime'] = uptime_result['stdout'].strip()
            
            # 负载
            load_result = self.execute_command(commands['load'])
            if load_result['success']:
                try:
                    parts = load_result['stdout'].strip().split()
                    metrics['load']['1min'] = float(parts[0])
                    metrics['load']['5min'] = float(parts[1])
                    metrics['load']['15min'] = float(parts[2])
                except:
                    pass
            
            # 网络
            net_result = self.execute_command(commands['network'])
            if net_result['success']:
                try:
                    parts = net_result['stdout'].strip().split()
                    if len(parts) >= 2:
                        metrics['network']['rx_bytes'] = int(parts[0])
                        metrics['network']['tx_bytes'] = int(parts[1])
                except:
                    pass
            
            metrics['success'] = True
            self.metrics = metrics
            self.status = 'running'
            self.last_check = self._get_time()
            
            # 记录历史数据
            self._record_metrics_history(metrics)
            
        except Exception as e:
            metrics['error'] = str(e)
            self.status = 'error'
        
        return metrics
    
    def _record_metrics_history(self, metrics: Dict):
        """记录历史监控数据"""
        timestamp = datetime.now(BEIJING_TZ).strftime('%H:%M:%S')
        
        self.metrics_history['cpu'].append(metrics.get('cpu', {}).get('usage', 0))
        self.metrics_history['memory'].append(metrics.get('memory', {}).get('usage_percent', 0))
        
        disk_usage = 0
        try:
            disk_usage = float(str(metrics.get('disk', {}).get('usage_percent', 0)).replace('%', ''))
        except:
            pass
        self.metrics_history['disk'].append(disk_usage)
        
        self.metrics_history['io'].append(0)  # 需要额外采集
        self.metrics_history['connections'].append(0)  # 需要额外采集
        self.metrics_history['timestamps'].append(timestamp)
        
        # 保留最近60条
        max_records = 60
        for key in self.metrics_history:
            if len(self.metrics_history[key]) > max_records:
                self.metrics_history[key] = self.metrics_history[key][-max_records:]
        
        # 检查告警
        self._check_alerts(metrics)
    
    def _check_alerts(self, metrics: Dict):
        """检查告警"""
        self.alerts = []
        now = self._get_time()
        
        cpu_usage = metrics.get('cpu', {}).get('usage', 0)
        if cpu_usage > self.alert_thresholds['cpu']:
            self.alerts.append({
                'type': 'cpu',
                'level': 'warning' if cpu_usage < 90 else 'critical',
                'message': f"CPU使用率过高: {cpu_usage:.1f}%",
                'threshold': self.alert_thresholds['cpu'],
                'current': cpu_usage,
                'time': now
            })
        
        mem_usage = metrics.get('memory', {}).get('usage_percent', 0)
        if mem_usage > self.alert_thresholds['memory']:
            self.alerts.append({
                'type': 'memory',
                'level': 'warning' if mem_usage < 95 else 'critical',
                'message': f"内存使用率过高: {mem_usage:.1f}%",
                'threshold': self.alert_thresholds['memory'],
                'current': mem_usage,
                'time': now
            })
    
    def get_status(self) -> Dict[str, Any]:
        """获取服务器状态"""
        return {
            'server_id': self.server_id,
            'server_name': self.server_name,
            'ip': self.ip,
            'ssh_user': self.ssh_user,
            'ssh_port': self.ssh_port,
            'ssh_key': '******' if self.ssh_key else None,
            'is_virtual': False,
            'status': self.status,
            'last_check': self.last_check,
            'metrics': self.metrics,
            'alerts': self.alerts,
            'created_time': self.created_time.isoformat()
        }
    
    def get_metrics_history(self) -> Dict:
        """获取历史监控数据"""
        return {
            'server_id': self.server_id,
            'server_name': self.server_name,
            'metrics': self.metrics_history,
            'alerts': self.alerts,
            'thresholds': self.alert_thresholds
        }
    
    def _log_operation(self, operation: str, details: str, status: str = "success"):
        """记录操作日志"""
        log_entry = {
            'timestamp': self._get_time(),
            'operation': operation,
            'details': details[:500] if len(details) > 500 else details,
            'status': status,
            'server': self.server_name
        }
        self.operation_logs.append(log_entry)
        if len(self.operation_logs) > 100:
            self.operation_logs = self.operation_logs[-100:]
        
        # 写入日志文件
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        except:
            pass
        
        return log_entry
    
    def get_operation_logs(self, limit: int = 20) -> List[Dict]:
        """获取操作日志"""
        return self.operation_logs[-limit:][::-1]
    
    def start_monitoring(self, interval: int = 30):
        """启动后台监控"""
        if self._monitor_running:
            return
        
        self._monitor_running = True
        self._monitor_thread = threading.Thread(
            target=self._monitoring_loop, 
            args=(interval,),
            daemon=True
        )
        self._monitor_thread.start()
    
    def stop_monitoring(self):
        """停止后台监控"""
        self._monitor_running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
    
    def _monitoring_loop(self, interval: int):
        """监控循环"""
        while self._monitor_running:
            try:
                # 先检查ping
                ping_ok, _ = self.ping(timeout=5)
                if ping_ok:
                    self.get_system_metrics()
                else:
                    self.status = 'unreachable'
            except Exception as e:
                self.status = 'error'
                print(f"Monitoring error for {self.server_id}: {e}")
            
            time.sleep(interval)
    
    # ==================== 修复操作 ====================
    
    def execute_repair(self, repair_action: str, params: Dict = None) -> Dict:
        """执行修复操作"""
        params = params or {}
        start_time = time.time()
        
        result = {
            'action': repair_action,
            'server': self.server_name,
            'ip': self.ip,
            'is_real_server': True,
            'start_time': self._get_time(),
            'success': False,
            'steps': [],
            'output': [],
            'changes': []
        }
        
        # 根据修复操作类型执行不同逻辑
        if repair_action == 'status_check':
            result = self._status_check(result)
        elif repair_action == 'restart_service':
            result = self._restart_service(params.get('service_name', 'nginx'), result)
        elif repair_action == 'clear_cache':
            result = self._clear_cache(result)
        elif repair_action == 'cleanup_disk':
            result = self._cleanup_disk(result)
        elif repair_action == 'optimize_memory':
            result = self._optimize_memory(result)
        elif repair_action == 'reduce_cpu_load':
            result = self._reduce_cpu_load(result)
        elif repair_action == 'restart_server':
            result = self._restart_server(result)
        elif repair_action == 'check_port_connectivity':
            result = self._check_port(params.get('port', 80), result)
        elif repair_action == 'execute_command':
            result = self._execute_custom_command(params.get('command', 'echo OK'), result)
        elif repair_action in ['health_check', 'backup', 'cleanup', 'restart_services']:
            # 执行配置化的脚本
            result = self._execute_configured_script(repair_action, result)
        else:
            result['steps'].append({
                'step': 1,
                'action': '未知操作',
                'status': 'failed',
                'message': f'不支持的操作: {repair_action}'
            })
        
        result['end_time'] = self._get_time()
        result['duration'] = round(time.time() - start_time, 2)
        
        self._log_operation(repair_action, json.dumps(result, ensure_ascii=False)[:500],
                          'success' if result['success'] else 'failed')
        
        return result
    
    def _status_check(self, result: Dict) -> Dict:
        """状态检查"""
        # Step 1: Ping测试
        result['steps'].append({
            'step': 1,
            'action': '网络连通性测试',
            'status': 'running',
            'message': '正在Ping...',
            'command': f'ping -c 3 {self.ip}'
        })
        
        ping_ok, ping_msg = self.ping()
        result['steps'][0]['status'] = 'completed' if ping_ok else 'failed'
        result['steps'][0]['message'] = ping_msg
        result['output'].append(ping_msg)
        
        if not ping_ok:
            result['changes'].append('服务器网络不可达')
            return result
        
        # Step 2: SSH连接测试
        result['steps'].append({
            'step': 2,
            'action': 'SSH连接测试',
            'status': 'running',
            'message': '正在连接...',
            'command': f'ssh {self.ssh_user}@{self.ip}'
        })
        
        ssh_ok, ssh_msg = self.test_ssh_connection()
        result['steps'][1]['status'] = 'completed' if ssh_ok else 'failed'
        result['steps'][1]['message'] = ssh_msg
        result['output'].append(ssh_msg)
        
        if not ssh_ok:
            result['changes'].append('SSH连接失败')
            return result
        
        # Step 3: 获取系统状态
        result['steps'].append({
            'step': 3,
            'action': '获取系统状态',
            'status': 'running',
            'message': '正在获取...',
            'command': 'uptime && free -h && df -h /'
        })
        
        metrics = self.get_system_metrics()
        if metrics.get('success'):
            result['steps'][2]['status'] = 'completed'
            result['steps'][2]['message'] = '系统状态获取成功'
            result['output'].append(f"CPU使用率: {metrics.get('cpu', {}).get('usage', 0):.1f}%")
            result['output'].append(f"内存使用: {metrics.get('memory', {}).get('used', 0)}MB / {metrics.get('memory', {}).get('total', 0)}MB")
            result['output'].append(f"磁盘使用: {metrics.get('disk', {}).get('usage_percent', 0)}%")
            result['output'].append(f"运行时间: {metrics.get('uptime', 'unknown')}")
            result['changes'].append('服务器状态正常')
            result['success'] = True
        else:
            result['steps'][2]['status'] = 'failed'
            result['steps'][2]['message'] = '获取系统状态失败'
        
        return result
    
    def _restart_service(self, service_name: str, result: Dict) -> Dict:
        """重启服务"""
        # Step 1: 检查服务状态
        result['steps'].append({
            'step': 1,
            'action': f'检查服务 {service_name} 状态',
            'status': 'running',
            'message': '正在检查...',
            'command': f'systemctl status {service_name}'
        })
        
        check_result = self.execute_command(f'systemctl status {service_name}')
        result['steps'][0]['status'] = 'completed'
        result['steps'][0]['message'] = '服务检查完成'
        result['output'].append(check_result['stdout'][:200] if check_result['stdout'] else '无输出')
        
        # Step 2: 重启服务
        result['steps'].append({
            'step': 2,
            'action': f'重启服务 {service_name}',
            'status': 'running',
            'message': '正在重启...',
            'command': f'systemctl restart {service_name}'
        })
        
        restart_result = self.execute_command(f'systemctl restart {service_name}')
        if restart_result['success']:
            result['steps'][1]['status'] = 'completed'
            result['steps'][1]['message'] = '服务重启成功'
            result['output'].append(f'服务 {service_name} 已重启')
        else:
            result['steps'][1]['status'] = 'failed'
            result['steps'][1]['message'] = f'重启失败: {restart_result["stderr"]}'
            result['output'].append(restart_result['stderr'])
            return result
        
        # Step 3: 验证服务状态
        result['steps'].append({
            'step': 3,
            'action': '验证服务状态',
            'status': 'running',
            'message': '正在验证...',
            'command': f'systemctl is-active {service_name}'
        })
        
        verify_result = self.execute_command(f'systemctl is-active {service_name}')
        if 'active' in verify_result['stdout']:
            result['steps'][2]['status'] = 'completed'
            result['steps'][2]['message'] = '服务运行正常'
            result['changes'].append(f'{service_name} 服务已重启并正常运行')
            result['success'] = True
        else:
            result['steps'][2]['status'] = 'failed'
            result['steps'][2]['message'] = '服务未正常启动'
        
        return result
    
    def _clear_cache(self, result: Dict) -> Dict:
        """清理系统缓存"""
        # Step 1: 同步文件系统
        result['steps'].append({
            'step': 1,
            'action': '同步文件系统',
            'status': 'running',
            'message': '正在同步...',
            'command': 'sync'
        })
        
        sync_result = self.execute_command('sync')
        result['steps'][0]['status'] = 'completed'
        result['steps'][0]['message'] = '文件系统已同步'
        
        # Step 2: 清理缓存
        result['steps'].append({
            'step': 2,
            'action': '清理页面缓存',
            'status': 'running',
            'message': '正在清理...',
            'command': 'echo 3 > /proc/sys/vm/drop_caches'
        })
        
        # 获取清理前内存
        before_result = self.execute_command("free -m | awk 'NR==2{print $4}'")
        before_free = int(before_result['stdout'].strip()) if before_result['success'] else 0
        
        clear_result = self.execute_command('echo 3 > /proc/sys/vm/drop_caches')
        
        # 获取清理后内存
        after_result = self.execute_command("free -m | awk 'NR==2{print $4}'")
        after_free = int(after_result['stdout'].strip()) if after_result['success'] else 0
        
        freed = after_free - before_free
        
        result['steps'][1]['status'] = 'completed'
        result['steps'][1]['message'] = f'已释放 {freed}MB 内存'
        result['output'].append(f'释放前可用内存: {before_free}MB')
        result['output'].append(f'释放后可用内存: {after_free}MB')
        result['output'].append(f'释放内存: {freed}MB')
        
        result['changes'].append(f'释放了 {freed}MB 系统缓存')
        result['success'] = True
        
        return result
    
    def _cleanup_disk(self, result: Dict) -> Dict:
        """清理磁盘空间"""
        # Step 1: 清理临时文件
        result['steps'].append({
            'step': 1,
            'action': '清理临时文件',
            'status': 'running',
            'message': '正在清理 /tmp...',
            'command': 'rm -rf /tmp/*'
        })
        
        tmp_result = self.execute_command('find /tmp -type f -mtime +7 -delete 2>/dev/null; echo OK')
        result['steps'][0]['status'] = 'completed'
        result['steps'][0]['message'] = '临时文件已清理'
        
        # Step 2: 清理日志
        result['steps'].append({
            'step': 2,
            'action': '清理旧日志',
            'status': 'running',
            'message': '正在清理...',
            'command': 'journalctl --vacuum-time=7d'
        })
        
        log_result = self.execute_command('journalctl --vacuum-time=7d 2>&1 || echo "journalctl not available"')
        result['steps'][1]['status'] = 'completed'
        result['steps'][1]['message'] = '日志清理完成'
        result['output'].append(log_result['stdout'][:200] if log_result['stdout'] else '')
        
        # Step 3: 检查磁盘使用
        result['steps'].append({
            'step': 3,
            'action': '检查磁盘使用',
            'status': 'running',
            'message': '正在检查...',
            'command': 'df -h /'
        })
        
        df_result = self.execute_command('df -h /')
        result['steps'][2]['status'] = 'completed'
        result['steps'][2]['message'] = '磁盘检查完成'
        result['output'].append(df_result['stdout'])
        
        result['changes'].append('磁盘清理完成')
        result['success'] = True
        
        return result
    
    def _optimize_memory(self, result: Dict) -> Dict:
        """优化内存"""
        # 调用缓存清理
        return self._clear_cache(result)
    
    def _reduce_cpu_load(self, result: Dict) -> Dict:
        """降低CPU负载"""
        # Step 1: 查找高CPU进程
        result['steps'].append({
            'step': 1,
            'action': '查找高CPU进程',
            'status': 'running',
            'message': '正在分析...',
            'command': 'ps aux --sort=-%cpu | head -10'
        })
        
        ps_result = self.execute_command('ps aux --sort=-%cpu | head -10')
        result['steps'][0]['status'] = 'completed'
        result['steps'][0]['message'] = '进程分析完成'
        result['output'].append(ps_result['stdout'])
        
        # Step 2: 建议操作（不自动杀进程，太危险）
        result['steps'].append({
            'step': 2,
            'action': '生成优化建议',
            'status': 'running',
            'message': '正在生成...',
            'command': '分析高CPU进程'
        })
        
        result['steps'][1]['status'] = 'completed'
        result['steps'][1]['message'] = '建议已生成'
        result['output'].append('建议: 检查上述高CPU进程是否正常')
        result['output'].append('如需终止进程，请手动执行: kill -9 <PID>')
        
        result['changes'].append('已分析CPU使用情况，请查看高CPU进程列表')
        result['success'] = True
        
        return result
    
    def _restart_server(self, result: Dict) -> Dict:
        """重启服务器（谨慎操作）"""
        result['steps'].append({
            'step': 1,
            'action': '警告：服务器重启',
            'status': 'warning',
            'message': '此操作将重启整个服务器！',
            'command': 'reboot'
        })
        
        result['output'].append('⚠️ 服务器重启是危险操作，需要确认后手动执行')
        result['output'].append('如确需重启，请SSH登录后执行: reboot')
        result['changes'].append('已生成重启建议，请手动确认后执行')
        result['success'] = True
        
        return result
    
    def _check_port(self, port: int, result: Dict) -> Dict:
        """检查端口连通性"""
        result['steps'].append({
            'step': 1,
            'action': f'检查端口 {port}',
            'status': 'running',
            'message': '正在检查...',
            'command': f'netstat -tlnp | grep :{port}'
        })
        
        port_result = self.execute_command(f'netstat -tlnp | grep :{port} || ss -tlnp | grep :{port}')
        
        if port_result['stdout'].strip():
            result['steps'][0]['status'] = 'completed'
            result['steps'][0]['message'] = f'端口 {port} 正在监听'
            result['output'].append(port_result['stdout'])
            result['changes'].append(f'端口 {port} 状态正常')
        else:
            result['steps'][0]['status'] = 'warning'
            result['steps'][0]['message'] = f'端口 {port} 未监听'
            result['output'].append(f'端口 {port} 未找到监听进程')
            result['changes'].append(f'端口 {port} 未在监听，可能服务未启动')
        
        result['success'] = True
        return result
    
    def _execute_custom_command(self, command: str, result: Dict) -> Dict:
        """执行自定义命令"""
        result['steps'].append({
            'step': 1,
            'action': '执行自定义命令',
            'status': 'running',
            'message': '正在执行...',
            'command': command
        })
        
        cmd_result = self.execute_command(command)
        
        if cmd_result['success']:
            result['steps'][0]['status'] = 'completed'
            result['steps'][0]['message'] = '命令执行成功'
            result['output'].append(cmd_result['stdout'])
            result['changes'].append('命令执行完成')
            result['success'] = True
        else:
            result['steps'][0]['status'] = 'failed'
            result['steps'][0]['message'] = f'命令执行失败: {cmd_result["stderr"]}'
            result['output'].append(cmd_result['stderr'])
        
        return result
    
    def _execute_configured_script(self, script_name: str, result: Dict) -> Dict:
        """执行配置化脚本"""
        # 获取脚本配置
        commands, script_config = get_script_commands(script_name)
        
        script_display_name = script_config.get('name', script_name)
        script_description = script_config.get('description', '')
        
        result['action'] = script_name
        result['script_name'] = script_display_name
        result['script_description'] = script_description
        
        # Step 1: 准备执行
        result['steps'].append({
            'step': 1,
            'action': f'准备执行: {script_display_name}',
            'status': 'completed',
            'message': script_description or '开始执行脚本',
            'command': f'共 {len(commands)} 个命令'
        })
        
        # Step 2-N: 执行每个命令
        all_success = True
        step_num = 2
        
        for cmd in commands:
            if not cmd.strip():
                continue
                
            result['steps'].append({
                'step': step_num,
                'action': f'执行命令 {step_num - 1}',
                'status': 'running',
                'message': '正在执行...',
                'command': cmd[:100] + ('...' if len(cmd) > 100 else '')
            })
            
            cmd_result = self.execute_command(cmd, timeout=script_config.get('timeout', 60))
            
            if cmd_result['success']:
                result['steps'][-1]['status'] = 'completed'
                result['steps'][-1]['message'] = '执行成功'
                if cmd_result['stdout'].strip():
                    result['output'].append(f"[命令{step_num-1}输出]\n{cmd_result['stdout']}")
            else:
                result['steps'][-1]['status'] = 'failed'
                result['steps'][-1]['message'] = f'执行失败: {cmd_result["stderr"][:100]}'
                result['output'].append(f"[命令{step_num-1}错误]\n{cmd_result['stderr']}")
                all_success = False
                # 继续执行，不中断
            
            step_num += 1
        
        # 最终结果
        if all_success:
            result['changes'].append(f'{script_display_name}执行完成')
            result['success'] = True
        else:
            result['changes'].append(f'{script_display_name}执行完成，但部分命令失败')
            result['success'] = True  # 标记为成功，因为脚本已执行
        
        return result
    
    def cleanup(self):
        """清理资源"""
        self.stop_monitoring()
        # 不删除SSH密钥文件，可能需要保留


# 真实服务器管理器
class RealServerManager:
    """真实服务器管理器"""
    
    def __init__(self):
        self.servers: Dict[str, RealServerConnection] = {}
        self._server_counter = 0
        self._config_file = Path(__file__).parent.parent / 'data' / 'real_servers.json'
        self._load_servers()
    
    def _load_servers(self):
        """从配置文件加载服务器"""
        try:
            if self._config_file.exists():
                with open(self._config_file, 'r', encoding='utf-8') as f:
                    configs = json.load(f)
                    for config in configs:
                        self._server_counter += 1
                        server = RealServerConnection(
                            server_id=config.get('server_id', f'real-{self._server_counter:03d}'),
                            server_name=config.get('server_name', f'真实服务器-{self._server_counter}'),
                            ip=config['ip'],
                            ssh_key=config.get('ssh_key'),
                            ssh_user=config.get('ssh_user', 'root'),
                            ssh_port=config.get('ssh_port', 22)
                        )
                        self.servers[server.server_id] = server
        except Exception as e:
            print(f"[RealServerManager] 加载配置失败: {e}")
    
    def _save_servers(self):
        """保存服务器配置到文件"""
        try:
            self._config_file.parent.mkdir(parents=True, exist_ok=True)
            configs = []
            for server in self.servers.values():
                configs.append({
                    'server_id': server.server_id,
                    'server_name': server.server_name,
                    'ip': server.ip,
                    'ssh_key': server.ssh_key,
                    'ssh_user': server.ssh_user,
                    'ssh_port': server.ssh_port
                })
            with open(self._config_file, 'w', encoding='utf-8') as f:
                json.dump(configs, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[RealServerManager] 保存配置失败: {e}")
    
    def add_server(self, ip: str, name: str = None, ssh_key: str = None,
                   ssh_password: str = None, ssh_user: str = 'root', ssh_port: int = 22) -> Dict:
        """添加真实服务器"""
        # 检查IP是否已存在
        for srv in self.servers.values():
            if srv.ip == ip:
                return {
                    'success': False,
                    'message': f'IP {ip} 已存在'
                }
        
        self._server_counter += 1
        server_id = f'real-{self._server_counter:03d}'
        
        if not name:
            name = f'真实服务器-{self._server_counter:02d}'
        
        server = RealServerConnection(
            server_id=server_id,
            server_name=name,
            ip=ip,
            ssh_key=ssh_key,
            ssh_password=ssh_password,
            ssh_user=ssh_user,
            ssh_port=ssh_port
        )
        
        # 测试连接
        ping_ok, ping_msg = server.ping()
        if not ping_ok:
            return {
                'success': False,
                'message': f'无法连接到 {ip}: {ping_msg}'
            }
        
        # 如果有SSH凭据，测试SSH连接
        if ssh_key or ssh_password:
            ssh_ok, ssh_msg = server.test_ssh_connection()
            if not ssh_ok:
                return {
                    'success': False,
                    'message': f'SSH连接失败: {ssh_msg}'
                }
        
        self.servers[server_id] = server
        self._save_servers()
        
        # 启动后台监控
        server.start_monitoring(interval=60)
        
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
        
        server = self.servers[server_id]
        server.cleanup()
        
        server_name = server.server_name
        del self.servers[server_id]
        self._save_servers()
        
        return {
            'success': True,
            'message': f'服务器 {server_name} 已删除'
        }
    
    def get_server(self, server_id: str) -> Optional[RealServerConnection]:
        """获取服务器"""
        return self.servers.get(server_id)
    
    def get_all_servers(self) -> List[Dict]:
        """获取所有服务器状态"""
        return [server.get_status() for server in self.servers.values()]
    
    def test_server_connection(self, ip: str, ssh_key: str = None,
                                ssh_password: str = None,
                                ssh_user: str = 'root', ssh_port: int = 22) -> Dict:
        """测试服务器连接（不添加）"""
        result = {
            'ip': ip,
            'ping_ok': False,
            'ping_msg': '',
            'ssh_ok': False,
            'ssh_msg': '',
            'success': False
        }
        
        # 创建临时连接
        temp_server = RealServerConnection(
            server_id='temp',
            server_name='临时测试',
            ip=ip,
            ssh_key=ssh_key,
            ssh_password=ssh_password,
            ssh_user=ssh_user,
            ssh_port=ssh_port
        )
        
        # Ping测试
        result['ping_ok'], result['ping_msg'] = temp_server.ping()
        
        if result['ping_ok'] and (ssh_key or ssh_password):
            # SSH测试
            result['ssh_ok'], result['ssh_msg'] = temp_server.test_ssh_connection()
        
        result['success'] = result['ping_ok'] and (not (ssh_key or ssh_password) or result['ssh_ok'])
        
        temp_server.cleanup()
        
        return result


# 全局真实服务器管理器实例
_real_server_manager = None


def get_real_server_manager() -> RealServerManager:
    """获取真实服务器管理器实例"""
    global _real_server_manager
    if _real_server_manager is None:
        _real_server_manager = RealServerManager()
    return _real_server_manager


def get_real_server(server_id: str) -> Optional[RealServerConnection]:
    """获取真实服务器实例"""
    manager = get_real_server_manager()
    return manager.get_server(server_id)
