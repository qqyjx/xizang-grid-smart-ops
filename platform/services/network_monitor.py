# -*- coding: utf-8 -*-
"""
网络监控模块
通过Ping和端口扫描监控远程服务器（无需SSH）
适用于政府云等限制SSH的环境
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
from concurrent.futures import ThreadPoolExecutor, as_completed

# 北京时间
BEIJING_TZ = timezone(timedelta(hours=8))

# 配置目录
DATA_DIR = Path(__file__).parent.parent / 'data'
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 服务器配置文件
SERVERS_CONFIG_FILE = DATA_DIR / 'monitored_servers.json'


class MonitoredServer:
    """被监控服务器类（基于Ping+端口扫描）"""
    
    # 常用端口及服务名称
    COMMON_PORTS = {
        22: 'SSH',
        80: 'HTTP',
        443: 'HTTPS',
        3306: 'MySQL',
        5432: 'PostgreSQL',
        6379: 'Redis',
        27017: 'MongoDB',
        8080: 'HTTP-Alt',
        8443: 'HTTPS-Alt',
        21: 'FTP',
        25: 'SMTP',
        53: 'DNS',
        110: 'POP3',
        143: 'IMAP',
        3389: 'RDP',
        5000: 'Flask',
        5001: 'API',
        9000: 'PHP-FPM',
        9090: 'Prometheus',
        9200: 'Elasticsearch'
    }
    
    def __init__(self, server_id: str, server_name: str, ip: str,
                 monitored_ports: List[int] = None, http_endpoints: List[str] = None):
        self.server_id = server_id
        self.server_name = server_name
        self.ip = ip
        self.monitored_ports = monitored_ports or [22, 80, 443, 3306, 8080]
        self.http_endpoints = http_endpoints or []  # 如 ["http://ip/health", "http://ip:8080/api/status"]
        
        self.status = "unknown"
        self.last_check = None
        self.created_time = datetime.now(BEIJING_TZ)
        self.server_type = "real"  # 区别于虚拟服务器
        
        # 监控数据
        self.ping_status = {
            'reachable': False,
            'latency_ms': 0,
            'packet_loss': 100,
            'last_success': None
        }
        
        self.port_status = {}  # {port: {'open': bool, 'service': str, 'response_time': float}}
        
        self.http_status = {}  # {url: {'ok': bool, 'status_code': int, 'response_time': float}}
        
        # 历史数据
        self.history = {
            'ping_latency': [],
            'port_checks': [],
            'timestamps': []
        }
        
        # 告警
        self.alerts = []
        
        # 后台监控
        self._monitor_thread = None
        self._monitor_running = False
    
    def _get_time(self) -> str:
        """获取北京时间字符串"""
        return datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')
    
    def ping(self, count: int = 3, timeout: int = 5) -> Dict:
        """
        Ping测试服务器
        返回: {reachable, latency_ms, packet_loss, message}
        """
        result = {
            'reachable': False,
            'latency_ms': 0,
            'packet_loss': 100,
            'message': ''
        }
        
        try:
            cmd = ['ping', '-c', str(count), '-W', str(timeout), self.ip]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout * count + 5)
            
            if proc.returncode == 0:
                result['reachable'] = True
                
                # 解析延迟和丢包率
                output = proc.stdout
                
                # 解析平均延迟 (rtt min/avg/max/mdev = x.xxx/x.xxx/x.xxx/x.xxx ms)
                for line in output.split('\n'):
                    if 'rtt' in line or 'round-trip' in line:
                        parts = line.split('=')[-1].strip().split('/')
                        if len(parts) >= 2:
                            try:
                                result['latency_ms'] = float(parts[1])
                            except:
                                pass
                    
                    # 解析丢包率 (x packets transmitted, x received, x% packet loss)
                    if 'packet loss' in line or '丢包' in line:
                        import re
                        match = re.search(r'(\d+)%', line)
                        if match:
                            result['packet_loss'] = int(match.group(1))
                
                result['message'] = f"延迟: {result['latency_ms']:.1f}ms, 丢包: {result['packet_loss']}%"
            else:
                result['message'] = "主机不可达"
                
        except subprocess.TimeoutExpired:
            result['message'] = f"Ping超时 ({timeout}秒)"
        except Exception as e:
            result['message'] = f"Ping错误: {str(e)}"
        
        # 更新状态
        self.ping_status = {
            'reachable': result['reachable'],
            'latency_ms': result['latency_ms'],
            'packet_loss': result['packet_loss'],
            'last_success': self._get_time() if result['reachable'] else self.ping_status.get('last_success')
        }
        
        return result
    
    def check_port(self, port: int, timeout: float = 2.0) -> Dict:
        """
        检测端口是否开放
        返回: {open, service, response_time, message}
        """
        result = {
            'port': port,
            'open': False,
            'service': self.COMMON_PORTS.get(port, f'Port-{port}'),
            'response_time': 0,
            'message': ''
        }
        
        start_time = time.time()
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            
            conn_result = sock.connect_ex((self.ip, port))
            
            result['response_time'] = (time.time() - start_time) * 1000  # 转换为毫秒
            
            if conn_result == 0:
                result['open'] = True
                result['message'] = f"端口开放 ({result['response_time']:.1f}ms)"
            else:
                result['message'] = "端口关闭"
                
            sock.close()
            
        except socket.timeout:
            result['message'] = "连接超时"
        except socket.error as e:
            result['message'] = f"连接错误: {str(e)}"
        except Exception as e:
            result['message'] = f"检测错误: {str(e)}"
        
        # 更新端口状态
        self.port_status[port] = {
            'open': result['open'],
            'service': result['service'],
            'response_time': result['response_time'],
            'last_check': self._get_time()
        }
        
        return result
    
    def check_all_ports(self) -> Dict[int, Dict]:
        """并行检测所有监控端口"""
        results = {}
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(self.check_port, port): port for port in self.monitored_ports}
            for future in as_completed(futures):
                port = futures[future]
                try:
                    results[port] = future.result()
                except Exception as e:
                    results[port] = {'port': port, 'open': False, 'message': str(e)}
        
        return results
    
    def check_http_endpoint(self, url: str, timeout: float = 5.0) -> Dict:
        """
        检测HTTP端点健康状态
        返回: {ok, status_code, response_time, message}
        """
        result = {
            'url': url,
            'ok': False,
            'status_code': 0,
            'response_time': 0,
            'message': ''
        }
        
        try:
            import urllib.request
            import urllib.error
            
            start_time = time.time()
            
            req = urllib.request.Request(url, headers={'User-Agent': 'HealthCheck/1.0'})
            response = urllib.request.urlopen(req, timeout=timeout)
            
            result['response_time'] = (time.time() - start_time) * 1000
            result['status_code'] = response.getcode()
            result['ok'] = 200 <= result['status_code'] < 400
            result['message'] = f"HTTP {result['status_code']} ({result['response_time']:.1f}ms)"
            
        except urllib.error.HTTPError as e:
            result['status_code'] = e.code
            result['message'] = f"HTTP错误: {e.code}"
        except urllib.error.URLError as e:
            result['message'] = f"URL错误: {str(e.reason)}"
        except Exception as e:
            result['message'] = f"请求错误: {str(e)}"
        
        # 更新HTTP状态
        self.http_status[url] = {
            'ok': result['ok'],
            'status_code': result['status_code'],
            'response_time': result['response_time'],
            'last_check': self._get_time()
        }
        
        return result
    
    def full_check(self) -> Dict:
        """执行完整健康检查"""
        self.last_check = self._get_time()
        
        result = {
            'server_id': self.server_id,
            'server_name': self.server_name,
            'ip': self.ip,
            'timestamp': self.last_check,
            'ping': self.ping(),
            'ports': self.check_all_ports(),
            'http': {}
        }
        
        # HTTP检测
        for url in self.http_endpoints:
            result['http'][url] = self.check_http_endpoint(url)
        
        # 计算整体状态
        ping_ok = result['ping']['reachable']
        ports_open = sum(1 for p in result['ports'].values() if p.get('open', False))
        total_ports = len(result['ports'])
        
        if not ping_ok:
            self.status = 'offline'
        elif ports_open == 0 and total_ports > 0:
            self.status = 'warning'
        elif ports_open < total_ports:
            self.status = 'degraded'
        else:
            self.status = 'healthy'
        
        # 记录历史
        self.history['ping_latency'].append(result['ping']['latency_ms'])
        self.history['port_checks'].append(ports_open)
        self.history['timestamps'].append(self.last_check)
        
        # 保持历史数据在合理范围
        max_history = 100
        for key in self.history:
            if len(self.history[key]) > max_history:
                self.history[key] = self.history[key][-max_history:]
        
        # 检查告警
        self._check_alerts(result)
        
        return result
    
    def _check_alerts(self, check_result: Dict):
        """检查并生成告警"""
        alerts = []
        
        # Ping告警
        if not check_result['ping']['reachable']:
            alerts.append({
                'level': 'critical',
                'type': 'ping',
                'message': f"服务器 {self.server_name} ({self.ip}) 无法ping通",
                'timestamp': self._get_time()
            })
        elif check_result['ping']['packet_loss'] > 20:
            alerts.append({
                'level': 'warning',
                'type': 'ping',
                'message': f"服务器 {self.server_name} 丢包率 {check_result['ping']['packet_loss']}%",
                'timestamp': self._get_time()
            })
        
        # 端口告警
        for port, status in check_result['ports'].items():
            if not status.get('open', False):
                service = self.COMMON_PORTS.get(port, f'Port-{port}')
                alerts.append({
                    'level': 'warning',
                    'type': 'port',
                    'message': f"服务器 {self.server_name} 端口 {port} ({service}) 不可达",
                    'timestamp': self._get_time()
                })
        
        # HTTP告警
        for url, status in check_result.get('http', {}).items():
            if not status.get('ok', False):
                alerts.append({
                    'level': 'warning',
                    'type': 'http',
                    'message': f"HTTP端点 {url} 不可用: {status.get('message', '未知')}",
                    'timestamp': self._get_time()
                })
        
        self.alerts = alerts[-20:]  # 保留最近20条告警
    
    def get_status(self) -> Dict:
        """获取服务器状态摘要"""
        ports_open = sum(1 for p in self.port_status.values() if p.get('open', False))
        total_ports = len(self.monitored_ports)
        
        return {
            'server_id': self.server_id,
            'server_name': self.server_name,
            'ip': self.ip,
            'server_type': 'real',
            'status': self.status,
            'last_check': self.last_check,
            'ping': self.ping_status,
            'ports_summary': f"{ports_open}/{total_ports} 端口开放",
            'ports_open': ports_open,
            'ports_total': total_ports,
            'port_status': self.port_status,
            'http_status': self.http_status,
            'monitored_ports': self.monitored_ports,
            'http_endpoints': self.http_endpoints,
            'alerts_count': len(self.alerts),
            'created_time': self.created_time.strftime('%Y-%m-%d %H:%M:%S')
        }
    
    def start_monitoring(self, interval: int = 60):
        """启动后台监控"""
        if self._monitor_running:
            return
        
        self._monitor_running = True
        
        def monitor_loop():
            while self._monitor_running:
                try:
                    self.full_check()
                except Exception as e:
                    print(f"[Monitor] {self.server_name} 监控错误: {e}")
                
                # 分段睡眠，便于快速停止
                for _ in range(interval):
                    if not self._monitor_running:
                        break
                    time.sleep(1)
        
        self._monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self._monitor_thread.start()
        print(f"[Monitor] 启动监控: {self.server_name} ({self.ip})")
    
    def stop_monitoring(self):
        """停止后台监控"""
        self._monitor_running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        print(f"[Monitor] 停止监控: {self.server_name}")
    
    def to_dict(self) -> Dict:
        """序列化为字典（用于保存配置）"""
        return {
            'server_id': self.server_id,
            'server_name': self.server_name,
            'ip': self.ip,
            'monitored_ports': self.monitored_ports,
            'http_endpoints': self.http_endpoints
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'MonitoredServer':
        """从字典创建实例"""
        return cls(
            server_id=data['server_id'],
            server_name=data['server_name'],
            ip=data['ip'],
            monitored_ports=data.get('monitored_ports', [22, 80, 443]),
            http_endpoints=data.get('http_endpoints', [])
        )


class NetworkMonitorManager:
    """网络监控管理器"""
    
    def __init__(self):
        self.servers: Dict[str, MonitoredServer] = {}
        self._server_counter = 0
        self._load_servers()
    
    def _load_servers(self):
        """加载保存的服务器配置"""
        try:
            if SERVERS_CONFIG_FILE.exists():
                with open(SERVERS_CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                self._server_counter = data.get('counter', 0)
                
                for srv_data in data.get('servers', []):
                    try:
                        server = MonitoredServer.from_dict(srv_data)
                        self.servers[server.server_id] = server
                        server.start_monitoring(interval=60)
                    except Exception as e:
                        print(f"[NetworkMonitor] 加载服务器失败: {e}")
                
                print(f"[NetworkMonitor] 已加载 {len(self.servers)} 台服务器配置")
        except Exception as e:
            print(f"[NetworkMonitor] 加载配置失败: {e}")
    
    def _save_servers(self):
        """保存服务器配置"""
        try:
            data = {
                'counter': self._server_counter,
                'servers': [srv.to_dict() for srv in self.servers.values()]
            }
            with open(SERVERS_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[NetworkMonitor] 保存配置失败: {e}")
    
    def add_server(self, ip: str, name: str = None, 
                   ports: List[int] = None, http_endpoints: List[str] = None) -> Dict:
        """添加被监控服务器"""
        # 检查IP是否已存在
        for srv in self.servers.values():
            if srv.ip == ip:
                return {'success': False, 'message': f'IP {ip} 已存在'}
        
        self._server_counter += 1
        server_id = f'monitor-{self._server_counter:03d}'
        
        if not name:
            name = f'服务器-{self._server_counter:02d}'
        
        # 默认监控端口
        if not ports:
            ports = [22, 80, 443, 3306, 8080]
        
        server = MonitoredServer(
            server_id=server_id,
            server_name=name,
            ip=ip,
            monitored_ports=ports,
            http_endpoints=http_endpoints or []
        )
        
        # 立即执行一次检查
        check_result = server.full_check()
        
        if not check_result['ping']['reachable']:
            return {
                'success': False,
                'message': f'无法ping通 {ip}: {check_result["ping"]["message"]}'
            }
        
        self.servers[server_id] = server
        self._save_servers()
        
        # 启动后台监控
        server.start_monitoring(interval=60)
        
        ports_open = sum(1 for p in check_result['ports'].values() if p.get('open', False))
        
        return {
            'success': True,
            'message': f'服务器已添加，Ping延迟: {check_result["ping"]["latency_ms"]:.1f}ms，{ports_open}/{len(ports)}个端口开放',
            'server_id': server_id,
            'server': server.get_status()
        }
    
    def remove_server(self, server_id: str) -> Dict:
        """删除服务器"""
        if server_id not in self.servers:
            return {'success': False, 'message': '服务器不存在'}
        
        server = self.servers[server_id]
        server.stop_monitoring()
        del self.servers[server_id]
        self._save_servers()
        
        return {'success': True, 'message': f'服务器 {server.server_name} 已删除'}
    
    def get_server(self, server_id: str) -> Optional[MonitoredServer]:
        """获取服务器"""
        return self.servers.get(server_id)
    
    def get_all_servers(self) -> List[Dict]:
        """获取所有服务器状态"""
        return [srv.get_status() for srv in self.servers.values()]
    
    def check_server(self, server_id: str) -> Dict:
        """手动触发服务器检查"""
        server = self.servers.get(server_id)
        if not server:
            return {'success': False, 'message': '服务器不存在'}
        
        result = server.full_check()
        return {'success': True, 'result': result, 'status': server.get_status()}
    
    def update_server_ports(self, server_id: str, ports: List[int]) -> Dict:
        """更新服务器监控端口"""
        server = self.servers.get(server_id)
        if not server:
            return {'success': False, 'message': '服务器不存在'}
        
        server.monitored_ports = ports
        self._save_servers()
        
        return {'success': True, 'message': f'已更新监控端口: {ports}'}
    
    def update_server_http(self, server_id: str, endpoints: List[str]) -> Dict:
        """更新服务器HTTP端点"""
        server = self.servers.get(server_id)
        if not server:
            return {'success': False, 'message': '服务器不存在'}
        
        server.http_endpoints = endpoints
        self._save_servers()
        
        return {'success': True, 'message': f'已更新HTTP端点: {endpoints}'}
    
    def test_connection(self, ip: str, ports: List[int] = None) -> Dict:
        """测试连接（不添加）"""
        result = {
            'ip': ip,
            'ping': {},
            'ports': {},
            'success': False
        }
        
        # 创建临时服务器对象
        temp = MonitoredServer(
            server_id='temp',
            server_name='测试',
            ip=ip,
            monitored_ports=ports or [22, 80, 443]
        )
        
        result['ping'] = temp.ping()
        
        if result['ping']['reachable']:
            result['ports'] = temp.check_all_ports()
            result['success'] = True
        
        return result


# 全局管理器实例
_network_monitor_manager = None


def get_network_monitor_manager() -> NetworkMonitorManager:
    """获取网络监控管理器实例"""
    global _network_monitor_manager
    if _network_monitor_manager is None:
        _network_monitor_manager = NetworkMonitorManager()
    return _network_monitor_manager
