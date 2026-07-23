#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
西藏电网智能运维平台 - ECS Agent（7B多模态版）
部署在监控服务器上，监听HTTP请求执行修复操作
支持截图上传转发至平台多模态API分析
版本: 6.0-7B
"""

import os
import sys
import json
import time
import socket
import signal
import psutil
import threading
import subprocess
import logging
import tempfile
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify
from flask_cors import CORS

# ==================== 故障注入子进程跟踪 ====================
# v5.50: 平台端"故障注入演示"调用 /stress 后，本地 spawn 的真实负载子进程 PID 都登记在这里，
# 之后调用 /repair 时通过 kill_injected_faults() 一键回收，让真实 CPU/内存使用率降下来。
STRESS_LOCK = threading.Lock()
STRESS_PROCESSES = []  # list[(pid, fault_type)]
STRESS_STOP_FLAGS = {}  # fault_type -> threading.Event
# v5.60: stress 文件统一目录。spawn 与 kill/cleanup 必须同源，否则 $TMPDIR≠/tmp（部分
#        ECS / systemd PrivateTmp 环境）时，ps 兜底的 marker 不匹配且 rm 清不掉，
#        会出现"注入后指标降不下来"。强制落 /tmp，与所有清理逻辑对齐。
STRESS_DIR = '/tmp'

# ==================== 配置 ====================
AGENT_PORT = int(os.environ.get('AGENT_PORT', 8089))
AGENT_TOKEN = os.environ.get('AGENT_TOKEN', 'CHANGE_ME_AGENT_TOKEN')
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'agent_config.json')

# 创建日志目录
os.makedirs(LOG_DIR, exist_ok=True)

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'agent.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# ==================== 认证装饰器 ====================
def require_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('X-Agent-Token') or request.args.get('token')
        if token != AGENT_TOKEN:
            logger.warning(f"认证失败: {request.remote_addr}")
            return jsonify({'success': False, 'message': '认证失败，Token无效'}), 401
        return f(*args, **kwargs)
    return decorated

# ==================== 系统信息采集 ====================
def get_system_status():
    """获取系统状态"""
    try:
        # CPU
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_count = psutil.cpu_count()
        
        # 内存
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        
        # 磁盘
        disk = psutil.disk_usage('/')
        
        # 网络
        net_io = psutil.net_io_counters()
        connections = len(psutil.net_connections())
        
        # 系统运行时间
        boot_time = psutil.boot_time()
        uptime_seconds = time.time() - boot_time
        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        uptime = f"{days}天{hours}小时"
        
        # 负载（Linux）
        try:
            load_avg = os.getloadavg()
        except:
            load_avg = (0, 0, 0)
        
        # v5.51: 透出故障注入标记（service_down / network_issue 无真实指标，前端靠这俩 flag 显示"故障中"）
        injected_flags = {
            'service_down': os.path.exists('/tmp/xizang_stress_service_down'),
            'network_issue': os.path.exists('/tmp/xizang_stress_network_issue'),
            'io_stress': os.path.exists('/tmp/.xizang_stress_io_lock'),
        }
        # v5.59: 实时过滤已退出的 worker pid，避免 duration 到期后仍持续误报"故障注入中"
        # v5.60 关键修复: worker 自退/被杀后会变成僵尸进程(zombie)，psutil.pid_exists(zombie)
        #   仍返回 True、is_running() 也返回 True，导致 active_workers 永不清空 →
        #   平台一直显示"故障注入中" → 客户看到的"自动降不下来"。这里主动 reap 僵尸并按 status 排除。
        with STRESS_LOCK:
            alive = []
            for pid, ftype in STRESS_PROCESSES:
                try:
                    p = psutil.Process(pid)
                    if p.status() == psutil.STATUS_ZOMBIE:
                        try:
                            os.waitpid(pid, os.WNOHANG)  # agent 是父进程，回收僵尸
                        except Exception:
                            pass
                        continue  # 僵尸 = 已停，不计入 active
                    if p.is_running():
                        alive.append((pid, ftype))
                except Exception:
                    pass  # NoSuchProcess 等 → 已退出，不计入
            # 同步回收 STRESS_PROCESSES，下次注入/查询不再带僵尸条目
            STRESS_PROCESSES[:] = alive
            injected_flags['active_workers'] = [
                {'pid': pid, 'fault_type': ftype} for pid, ftype in alive
            ]

        return {
            'hostname': socket.gethostname(),
            'ip': get_local_ip(),
            'timestamp': datetime.now().isoformat(),
            'cpu': {
                'usage': cpu_percent,
                'cores': cpu_count,
                'load_avg': list(load_avg)
            },
            'memory': {
                'total': round(memory.total / (1024**3), 2),  # GB
                'used': round(memory.used / (1024**3), 2),
                'percent': memory.percent,
                'swap_percent': swap.percent
            },
            'disk': {
                'total': round(disk.total / (1024**3), 2),  # GB
                'used': round(disk.used / (1024**3), 2),
                'percent': disk.percent
            },
            'network': {
                'bytes_sent': net_io.bytes_sent,
                'bytes_recv': net_io.bytes_recv,
                'connections': connections
            },
            'uptime': uptime,
            'fault_injected': injected_flags
        }
    except Exception as e:
        logger.error(f"获取系统状态失败: {e}")
        return {'error': str(e)}

def get_local_ip():
    """获取本机IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return '127.0.0.1'

def get_services_status():
    """获取常见服务状态"""
    services = ['nginx', 'mysql', 'mysqld', 'redis', 'redis-server', 'docker', 'httpd', 'apache2']
    result = {}
    
    for svc in services:
        try:
            # 使用systemctl检查
            ret = subprocess.run(
                ['systemctl', 'is-active', svc],
                capture_output=True, text=True, timeout=5
            )
            if ret.returncode == 0:
                result[svc] = 'running'
            else:
                # 检查进程是否存在
                ret2 = subprocess.run(
                    ['pgrep', '-x', svc],
                    capture_output=True, timeout=5
                )
                result[svc] = 'running' if ret2.returncode == 0 else 'stopped'
        except:
            pass
    
    return result

def get_inspection_data():
    """v5.7: 采集可程序化获取的系统巡检项，供平台报告"按系统分析"使用。
    只采真实可得的(服务进程/线程/连接/监听端口/中间件探测/定时任务)；
    应用层不可采项(证书过期/账号权限/审计日志/ISC登录限制/登录主页等)由平台人工录入补充，绝不在此编造。"""
    data = {}
    procs = []
    try:
        for p in psutil.process_iter(['pid', 'name', 'username', 'memory_percent', 'num_threads']):
            try:
                info = p.info
                procs.append({
                    'pid': info.get('pid'),
                    'name': (info.get('name') or '')[:40],
                    'user': (info.get('username') or '')[:24],
                    'mem_pct': round(info.get('memory_percent') or 0, 1),
                    'threads': int(info.get('num_threads') or 0),
                })
            except Exception:
                continue
    except Exception:
        pass
    procs_sorted = sorted(procs, key=lambda x: x['mem_pct'], reverse=True)
    data['top_processes'] = procs_sorted[:10]
    data['process_count'] = len(procs)
    data['thread_total'] = sum(p['threads'] for p in procs)

    # 连接数 + 监听端口（net_connections 非 root 可能受限，降级 ss）
    listen_ports, established = [], 0
    try:
        for c in psutil.net_connections(kind='inet'):
            try:
                if c.status == psutil.CONN_LISTEN and c.laddr:
                    listen_ports.append(c.laddr.port)
                elif c.status == 'ESTABLISHED':
                    established += 1
            except Exception:
                continue
    except Exception:
        try:
            out = subprocess.run("ss -ltn 2>/dev/null | awk 'NR>1{print $4}'", shell=True,
                                 capture_output=True, text=True, timeout=5).stdout
            for line in out.split('\n'):
                line = line.strip()
                if ':' in line:
                    seg = line.rsplit(':', 1)[1]
                    if seg.isdigit():
                        listen_ports.append(int(seg))
            est = subprocess.run("ss -tn state established 2>/dev/null | wc -l", shell=True,
                                 capture_output=True, text=True, timeout=5).stdout.strip()
            established = max(0, int(est) - 1) if est.isdigit() else 0
        except Exception:
            pass
    listen_ports = sorted(set(listen_ports))
    data['listen_ports'] = listen_ports
    data['established_count'] = established

    # 中间件/服务探测（按知名端口推断，端口在监听=该服务在跑）
    WELL_KNOWN = {
        6379: 'Redis 缓存', 16379: 'Redis Cluster', 3306: 'MySQL 数据库', 33060: 'MySQL X',
        5432: 'PostgreSQL 数据库', 1521: 'Oracle 数据库', 8848: 'Nacos 注册中心', 9848: 'Nacos gRPC',
        5672: 'RabbitMQ 消息队列', 15672: 'RabbitMQ 管理', 9092: 'Kafka 消息队列', 2181: 'ZooKeeper',
        80: 'Nginx/HTTP', 443: 'HTTPS', 8080: 'Tomcat/HTTP', 8443: 'Tomcat HTTPS',
        27017: 'MongoDB', 9200: 'Elasticsearch', 11211: 'Memcached', 8500: 'Consul', 2379: 'etcd',
        8089: '本机 Agent',
    }
    lp = set(listen_ports)
    data['detected_services'] = [{'port': port, 'service': name} for port, name in WELL_KNOWN.items() if port in lp]

    # 定时任务（crontab + /etc/cron.d + systemd timers）
    cron = 0
    try:
        for cmd in ("crontab -l 2>/dev/null | grep -vE '^\\s*#|^\\s*$' | wc -l",
                    "ls /etc/cron.d 2>/dev/null | wc -l"):
            o = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5).stdout.strip()
            if o.isdigit():
                cron += int(o)
    except Exception:
        pass
    data['cron_jobs'] = cron
    try:
        o = subprocess.run("systemctl list-timers --no-pager 2>/dev/null | grep -cE 'ago|left'",
                           shell=True, capture_output=True, text=True, timeout=5).stdout.strip()
        data['systemd_timers'] = int(o) if o.isdigit() else 0
    except Exception:
        data['systemd_timers'] = 0
    return data

# ==================== 修复操作 ====================
def execute_command(cmd, timeout=60, use_sudo=False, cwd=None):
    """执行系统命令 — v5.51 起 cwd 默认 = 当前用户 $HOME，并把工作目录回传给前端展示"""
    try:
        if use_sudo:
            cmd = f"sudo {cmd}"

        # v5.51: 默认在 $HOME 下执行，避免客户配的 mkdir/touch 这种命令落在 agent 程序目录
        run_cwd = cwd or os.path.expanduser('~') or '/tmp'
        logger.info(f"执行命令 (cwd={run_cwd}): {cmd}")
        result = subprocess.run(
            cmd, shell=True,
            capture_output=True, text=True,
            timeout=timeout,
            cwd=run_cwd
        )

        return {
            'success': result.returncode == 0,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'returncode': result.returncode,
            'cwd': run_cwd
        }
    except subprocess.TimeoutExpired:
        return {'success': False, 'error': f'命令超时({timeout}s)', 'cwd': cwd or os.path.expanduser('~')}
    except Exception as e:
        return {'success': False, 'error': str(e), 'cwd': cwd or os.path.expanduser('~')}

def repair_clear_cache(params=None):
    """清理系统缓存"""
    results = []
    
    # 同步文件系统
    results.append(execute_command('sync'))
    
    # 清理页面缓存
    results.append(execute_command('echo 1 > /proc/sys/vm/drop_caches', use_sudo=True))
    
    return {
        'action': 'clear_cache',
        'success': all(r.get('success', False) for r in results),
        'details': results
    }

def repair_optimize_memory(params=None):
    """优化内存使用：v5.50 起先把注入的 mem stress 子进程全部回收。"""
    results = []

    # v5.50: 先回收 memory_exhaustion 注入的真实占用
    killed = kill_injected_faults('memory_exhaustion')
    if killed:
        results.append({'killed_injected': killed, 'note': '已终止内存故障注入演示子进程'})

    # 清理缓存
    results.append(execute_command('sync && echo 3 > /proc/sys/vm/drop_caches', use_sudo=True))

    memory_before = psutil.virtual_memory().percent
    # 清理swap（如果有足够内存）
    if memory_before < 60:
        results.append(execute_command('swapoff -a && swapon -a', use_sudo=True))

    time.sleep(1.0)
    memory_after = psutil.virtual_memory().percent

    return {
        'action': 'optimize_memory',
        'success': True,
        'memory_before': memory_before,
        'memory_after': memory_after,
        'details': results
    }

def repair_kill_zombie(params=None):
    """查杀僵尸进程"""
    results = []
    
    # 查找僵尸进程
    ret = execute_command("ps aux | awk '$8==\"Z\" {print $2}'")
    if ret['success'] and ret['stdout'].strip():
        pids = ret['stdout'].strip().split('\n')
        for pid in pids:
            if pid:
                results.append(execute_command(f'kill -9 {pid}', use_sudo=True))
        return {
            'action': 'kill_zombie',
            'success': True,
            'killed_count': len(pids),
            'details': results
        }
    
    return {
        'action': 'kill_zombie',
        'success': True,
        'killed_count': 0,
        'message': '没有发现僵尸进程'
    }

def repair_restart_service(params=None):
    """重启服务 — v5.55: 先清服务停止演示标记，再做真实服务重启"""
    params = params or {}
    service_name = params.get('service', 'nginx')

    # v5.55: 先清除演示标记，让前端 fault_injected.service_down → false
    cleared_marker = False
    try:
        os.remove('/tmp/xizang_stress_service_down')
        cleared_marker = True
    except FileNotFoundError:
        pass
    except Exception:
        pass

    # 真实重启服务
    commands = [
        f'systemctl restart {service_name}',
        f'service {service_name} restart',
    ]

    for cmd in commands:
        result = execute_command(cmd, use_sudo=True)
        if result['success']:
            return {
                'action': 'restart_service',
                'service': service_name,
                'success': True,
                'command': cmd,
                'cleared_service_down_marker': cleared_marker,
                'details': result
            }

    # 即使没找到可重启的服务，演示标记若已清也算修复成功（演示场景常态）
    return {
        'action': 'restart_service',
        'service': service_name,
        'success': cleared_marker,
        'cleared_service_down_marker': cleared_marker,
        'message': (f'已清除服务停止演示标记（未找到真实 {service_name} 服务）'
                    if cleared_marker else f'无法重启服务 {service_name}')
    }

def repair_cleanup_disk(params=None):
    """清理磁盘空间：v5.50 起先回收 io_bottleneck 注入的真实 dd 子进程并删除其临时文件。"""
    results = []

    # v5.50: 先停掉 io_bottleneck 注入
    killed = kill_injected_faults('io_bottleneck')
    if killed:
        results.append({'killed_injected': killed, 'note': '已终止 IO 故障注入演示子进程'})
        # 删掉演示用的临时大文件
        results.append(execute_command('rm -f /tmp/xizang_stress_io_*.bin 2>/dev/null || true'))

    # 清理系统日志
    results.append(execute_command('journalctl --vacuum-time=3d', use_sudo=True))

    # 清理临时文件
    results.append(execute_command('rm -rf /tmp/* 2>/dev/null || true'))
    
    # 清理旧日志
    results.append(execute_command('find /var/log -name "*.gz" -mtime +7 -delete 2>/dev/null || true', use_sudo=True))
    
    # 清理yum/apt缓存
    results.append(execute_command('yum clean all 2>/dev/null || apt-get clean 2>/dev/null || true', use_sudo=True))
    
    return {
        'action': 'cleanup_disk',
        'success': True,
        'details': results
    }

def kill_injected_faults(fault_type=None):
    """v5.53: 杀掉 /stress 注入的真实负载子进程，让指标降下来。
    fault_type=None 表示全部清理；指定 type 则只清理对应一类。
    无条件用 pkill 兜底，防止 agent 重启后 STRESS_PROCESSES 丢失导致孤儿进程占用资源。"""
    killed = []
    with STRESS_LOCK:
        remain = []
        for pid, ftype in STRESS_PROCESSES:
            if fault_type and ftype != fault_type:
                remain.append((pid, ftype))
                continue
            try:
                p = psutil.Process(pid)
                for child in p.children(recursive=True):
                    try: child.kill()
                    except Exception: pass
                p.kill()
                # v5.60: 杀完立即 reap，避免留下僵尸让 active_workers / pid_exists 误报"注入中"
                try:
                    os.waitpid(pid, os.WNOHANG)
                except Exception:
                    pass
                killed.append({'pid': pid, 'fault_type': ftype})
            except psutil.NoSuchProcess:
                killed.append({'pid': pid, 'fault_type': ftype, 'note': 'already_gone'})
            except Exception as e:
                logger.warning(f"杀 stress 进程 {pid} 失败: {e}")
                remain.append((pid, ftype))
        STRESS_PROCESSES[:] = remain
        # 标记 worker thread 退出
        for ftype, ev in list(STRESS_STOP_FLAGS.items()):
            if not fault_type or ftype == fault_type:
                ev.set()
                STRESS_STOP_FLAGS.pop(ftype, None)

    # v5.58 critical fix: 之前用 pgrep -f 'xizang_stress' 会把执行 pgrep 的 sh wrapper
    # 也匹配上（因为 sh 的 cmdline 包含 "xizang_stress" 字符串），导致"5 轮都清不干净"假象。
    # 正确做法：用 ps -eo pid,comm,cmd 过滤 — 只 kill comm 为 python3/python/bash 且 cmd 包含
    # 真实 worker 标记 /tmp/xizang_stress_ 的进程；sh -c 包装的命令 comm='sh' 被自动排除。
    pattern_map = {
        'cpu_overload': '/tmp/xizang_stress_cpu_',
        'memory_exhaustion': '/tmp/xizang_stress_mem_',
        'io_bottleneck': 'xizang_stress_io',  # io 用 bash -c "...# xizang_stress_worker io..." 启动
    }
    kill_marker = pattern_map.get(fault_type, '/tmp/xizang_stress_') if fault_type else 'xizang_stress'
    my_pid = os.getpid()

    def list_real_workers():
        """只列出真实 worker 进程（python3 / bash），排除 sh wrapper / pgrep / pkill / kill 本身"""
        ret = execute_command(
            "ps -eo pid,comm,cmd --no-headers"
        )
        out = ret.get('stdout', '') or ''
        targets = []
        for line in out.split('\n'):
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            pid_str, comm, cmd = parts
            try:
                pid = int(pid_str)
            except ValueError:
                continue
            if pid == my_pid:
                continue
            # 必须包含真实 worker 标记（脚本路径 / bash 注释标记）
            if kill_marker not in cmd:
                continue
            # 必须是真实 worker 解释器（排除 sh wrapper 自己）
            # v5.60: Agent 常以自带 Miniconda 的 python 启动，worker 的 comm 可能是
            #        'python3.13' 等带次版本号的名字，旧版精确白名单会漏杀。放宽为前缀匹配。
            if not (comm.startswith('python') or comm in ('bash', 'dd')):
                continue
            # 排除明显的包装命令
            if any(tok in cmd for tok in (' pgrep ', ' pkill ', ' kill -', ' grep ', ' awk ', '/bin/sh -c')):
                continue
            targets.append((pid, comm, cmd))
        return targets

    for round_idx in range(5):
        targets = list_real_workers()
        if not targets:
            logger.info(f"[kill_injected_faults] 第 {round_idx + 1} 轮：真实 worker 已清完")
            break
        logger.info(f"[kill_injected_faults] 第 {round_idx + 1} 轮发现 {len(targets)} 个真实 worker: " +
                    ', '.join(f'{p}({c})' for p, c, _ in targets[:5]))
        for pid, _, _ in targets:
            execute_command(f"kill -9 {pid} 2>/dev/null || true")
        time.sleep(0.4)
    else:
        leftovers = list_real_workers()
        if leftovers:
            logger.warning(f"[kill_injected_faults] 5 轮 kill 后仍有 {len(leftovers)} 个真实 worker 残留")
        else:
            logger.info("[kill_injected_faults] 5 轮后已清完真实 worker")

    # v5.53: 清理临时脚本文件，下次 inject 重新生成
    if fault_type == 'cpu_overload':
        execute_command("rm -f /tmp/xizang_stress_cpu_*.py 2>/dev/null || true")
    elif fault_type == 'memory_exhaustion':
        execute_command("rm -f /tmp/xizang_stress_mem_*.py 2>/dev/null || true")
    elif fault_type == 'io_bottleneck':
        execute_command("rm -f /tmp/xizang_stress_io_*.bin 2>/dev/null || true")
        # v5.59: 定向清理路径也要删 lock，避免 health 上报 io_stress 长期 true 的误报
        try:
            os.remove('/tmp/.xizang_stress_io_lock')
        except FileNotFoundError:
            pass
        except Exception:
            pass
    else:
        execute_command("rm -f /tmp/xizang_stress_*.py /tmp/xizang_stress_io_*.bin 2>/dev/null || true")
    return killed


def _spawn_stress_worker(fault_type, duration=180, intensity=None):
    """v5.50: 启动 stress 子进程（subprocess.Popen，独立 pid 便于 kill）。
    返回 [Popen, ...]，调用方负责把 pid 登记进 STRESS_PROCESSES。

    intensity: dict — 不同故障类型可调强度
    """
    intensity = intensity or {}
    procs = []
    here = os.path.dirname(os.path.abspath(__file__))
    py = sys.executable or 'python3'

    if fault_type == 'cpu_overload':
        # v5.51: 每个 CPU 核心起一个 busy loop 子进程，跑满 duration 秒
        # 单独脚本文件以避免 -c 串接的语法陷阱
        cores = max(1, psutil.cpu_count(logical=True) or 1)
        worker_src = (
            "# xizang_stress_worker cpu\n"
            "import time, signal, os\n"
            "signal.signal(signal.SIGTERM, lambda *a: os._exit(0))\n"
            "end = time.time() + {dur}\n"
            "x = 0\n"
            "while time.time() < end:\n"
            "    x += 1\n"
            "    if x % 500000 == 0:\n"
            "        pass\n"
        ).format(dur=duration)
        script_path = os.path.join(STRESS_DIR, f'xizang_stress_cpu_{int(time.time()*1000)}.py')
        with open(script_path, 'w') as f:
            f.write(worker_src)
        for _ in range(cores):
            p = subprocess.Popen([py, script_path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 close_fds=True)
            procs.append(p)

    elif fault_type == 'memory_exhaustion':
        # v5.55: 真实占用内存
        # 关键修正：旧版 target = total * 0.6，在客户 ECS（14.7GB 总内存、基线已占 10GB）
        # 上会触发 OOM killer 把 stress worker 自己先杀掉，导致内存指标没真正爬升。
        # 改为 target = available * 0.7（available 已扣除基线 + buff/cache），
        # 既能让 used% 明显从基线爬高一截，又不会 OOM。
        vm = psutil.virtual_memory()
        available_mb = int(vm.available / (1024 * 1024))
        total_mb = int(vm.total / (1024 * 1024))
        default_target = max(200, int(available_mb * 0.7))
        target_mb = int(intensity.get('mb', default_target))
        # 上限：available 的 85%（绝不允许超 available，留 ~200MB 给系统）
        target_mb = max(200, min(target_mb, max(200, int(available_mb * 0.85))))
        logger.info(f"内存注入 target={target_mb}MB / total={total_mb}MB / available={available_mb}MB")
        worker_src = (
            "# xizang_stress_worker mem\n"
            "import time, signal, os\n"
            "signal.signal(signal.SIGTERM, lambda *a: os._exit(0))\n"
            "blocks = []\n"
            "target = {tgt}\n"
            "block_mb = 50\n"
            "count = max(1, target // block_mb)\n"
            "for _ in range(count):\n"
            "    b = bytearray(block_mb * 1024 * 1024)\n"
            "    # 真实写入触发物理分配（避免 overcommit）\n"
            "    for off in range(0, len(b), 4096):\n"
            "        b[off] = 1\n"
            "    blocks.append(b)\n"
            "end = time.time() + {dur}\n"
            "while time.time() < end:\n"
            "    time.sleep(1)\n"
        ).format(tgt=target_mb, dur=duration)
        script_path = os.path.join(STRESS_DIR, f'xizang_stress_mem_{int(time.time()*1000)}.py')
        with open(script_path, 'w') as f:
            f.write(worker_src)
        p = subprocess.Popen([py, script_path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             close_fds=True)
        procs.append(p)

    elif fault_type == 'io_bottleneck':
        # 后台 dd 持续写 4GB 临时文件，制造真实 IO 压力
        path = os.path.join(STRESS_DIR, f'xizang_stress_io_{int(time.time())}.bin')
        lock = os.path.join(STRESS_DIR, '.xizang_stress_io_lock')
        # v5.60: 关键修复 —— 旧版 `while [ -f lock ]` 死循环，只要不调 /repair 永不自停，
        #        是"IO 注入降不下来"的直接根因。现在与 CPU/内存 worker 一样按 duration 到点自停，
        #        并在退出时自清 lock + bin，保证零修复也能自愈。
        end_ts = int(time.time()) + int(duration)
        inner = (
            f"END={end_ts}; "
            f"while [ -f {lock} ] && [ $(date +%s) -lt $END ]; do "
            f"dd if=/dev/zero of={path} bs=1M count=4096 oflag=direct 2>/dev/null; "
            f"rm -f {path}; done; "
            f"rm -f {lock} {path}"
        )
        cmd = f"exec bash -c '{inner}'"
        # 写锁文件用于子进程感知何时退出（被 kill 后锁文件由 cleanup 删）
        try:
            with open(lock, 'w') as f:
                f.write(str(int(time.time())))
        except Exception:
            pass
        p = subprocess.Popen(['bash', '-c', f"# xizang_stress_worker io\n{cmd}"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             close_fds=True)
        procs.append(p)

    elif fault_type == 'service_down':
        # 演示用：写入一个标记文件，前端图标显示"服务停止"
        try:
            with open('/tmp/xizang_stress_service_down', 'w') as f:
                f.write(str(int(time.time())))
        except Exception:
            pass

    elif fault_type == 'network_issue':
        # 演示用：写入一个标记文件，前端图标显示"网络异常"
        try:
            with open('/tmp/xizang_stress_network_issue', 'w') as f:
                f.write(str(int(time.time())))
        except Exception:
            pass

    return procs


def repair_fix_high_cpu(params=None):
    """修复高CPU使用率：v5.50 起先把注入的 stress 子进程全部回收，再做常规降级。"""
    results = []
    # v5.50: 先回收 cpu_overload 注入的真实负载
    killed = kill_injected_faults('cpu_overload')
    if killed:
        results.append({'killed_injected': killed, 'note': '已终止 CPU 故障注入演示子进程'})

    # 找出CPU占用最高的进程
    ret = execute_command("ps aux --sort=-%cpu | head -5")
    results.append({'top_processes': ret.get('stdout', '')})

    # 降低非关键进程优先级
    ret = execute_command("renice 19 -p $(pgrep -f 'find|updatedb|backup' 2>/dev/null) 2>/dev/null || true")
    results.append(ret)

    # 等一拍让 psutil 反映新值
    time.sleep(1.2)
    cpu_after = psutil.cpu_percent(interval=0.5)

    return {
        'action': 'fix_high_cpu',
        'success': True,
        'cpu_after': cpu_after,
        'details': results
    }

def repair_reset_network(params=None):
    """重置网络 — v5.55: 先清网络异常演示标记，再做真实网络重启"""
    results = []

    # v5.55: 先清除演示标记，让前端 fault_injected.network_issue → false
    try:
        os.remove('/tmp/xizang_stress_network_issue')
        results.append({'action': 'clear_network_issue_marker', 'success': True,
                        'message': '已清除网络异常演示标记'})
    except FileNotFoundError:
        pass
    except Exception as e:
        results.append({'action': 'clear_network_issue_marker', 'success': False, 'error': str(e)})

    # 真实网络重启（注意：可能短暂断 SSH，SLB 转发场景几秒内会重连）
    commands = [
        'systemctl restart NetworkManager',
        'systemctl restart network',
        'service network restart'
    ]
    restarted = False
    for cmd in commands:
        ret = execute_command(cmd, use_sudo=True, timeout=30)
        if ret['success']:
            results.append(ret)
            restarted = True
            break

    return {
        'action': 'reset_network',
        'success': True,  # v5.55: 即使没找到可重启的服务，清标记也算成功
        'restarted_network_service': restarted,
        'details': results
    }


def repair_check_port_connectivity(params=None):
    """端口连通性监测（端口不通重启对应服务）"""
    params = params or {}
    port = params.get('port', 80)
    service = params.get('service', 'nginx')
    
    results = []
    
    # 检查端口是否连通
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3)
    try:
        result = sock.connect_ex(('127.0.0.1', int(port)))
        if result == 0:
            results.append({
                'check': f'端口 {port} 连通正常',
                'success': True
            })
        else:
            results.append({
                'check': f'端口 {port} 不通，尝试重启服务 {service}',
                'success': False
            })
            # 重启对应服务
            restart_result = repair_restart_service(service)
            results.append(restart_result)
    except Exception as e:
        results.append({
            'check': f'检查端口失败: {str(e)}',
            'success': False
        })
    finally:
        sock.close()
    
    return {
        'action': 'check_port_connectivity',
        'port': port,
        'service': service,
        'success': any(r.get('success', False) for r in results),
        'details': results
    }


def repair_check_network_interface(params=None):
    """网卡状态监测（检查网卡状态，异常则重启）"""
    params = params or {}
    interface = params.get('interface', 'eth0')
    
    results = []
    
    try:
        # 获取所有网卡状态
        net_if_stats = psutil.net_if_stats()
        net_if_addrs = psutil.net_if_addrs()
        
        if interface in net_if_stats:
            stats = net_if_stats[interface]
            if stats.isup:
                results.append({
                    'check': f'网卡 {interface} 状态正常 (UP)',
                    'success': True,
                    'speed': stats.speed,
                    'mtu': stats.mtu
                })
            else:
                results.append({
                    'check': f'网卡 {interface} 状态异常 (DOWN)，尝试重启',
                    'success': False
                })
                # 重启网卡
                cmds = [
                    f'ip link set {interface} down && ip link set {interface} up',
                    f'ifdown {interface} && ifup {interface}',
                    f'nmcli device disconnect {interface} && nmcli device connect {interface}'
                ]
                for cmd in cmds:
                    ret = execute_command(cmd, use_sudo=True, timeout=30)
                    if ret['success']:
                        results.append({'restart': f'网卡 {interface} 重启成功', 'success': True})
                        break
                else:
                    results.append({'restart': f'网卡 {interface} 重启失败', 'success': False})
        else:
            # 列出所有可用网卡
            available = list(net_if_stats.keys())
            results.append({
                'check': f'网卡 {interface} 不存在，可用网卡: {available}',
                'success': False
            })
    except Exception as e:
        results.append({
            'check': f'检查网卡失败: {str(e)}',
            'success': False
        })
    
    return {
        'action': 'check_network_interface',
        'interface': interface,
        'success': any(r.get('success', False) for r in results),
        'details': results
    }


def repair_fix_port_issue(params=None):
    """端口异常修复"""
    params = params or {}
    port = params.get('port', 80)
    
    results = []
    
    try:
        # 检查端口占用情况
        for conn in psutil.net_connections():
            if conn.laddr and conn.laddr.port == int(port):
                if conn.status == 'LISTEN':
                    results.append({
                        'check': f'端口 {port} 正在被进程 PID={conn.pid} 监听',
                        'success': True
                    })
                elif conn.status in ['CLOSE_WAIT', 'TIME_WAIT']:
                    results.append({
                        'check': f'端口 {port} 处于 {conn.status} 状态，尝试清理',
                        'success': False
                    })
                    # 尝试清理TIME_WAIT连接
                    execute_command(f'ss -K dport = {port}', use_sudo=True, timeout=10)
                break
        else:
            results.append({
                'check': f'端口 {port} 未被监听',
                'success': False
            })
        
        # 检查防火墙
        fw_check = execute_command(f'firewall-cmd --query-port={port}/tcp 2>/dev/null || iptables -L -n | grep {port}', timeout=10)
        if fw_check['success']:
            results.append({
                'firewall': f'防火墙已开放端口 {port}',
                'success': True
            })
        else:
            # 尝试开放端口
            open_result = execute_command(f'firewall-cmd --add-port={port}/tcp --permanent && firewall-cmd --reload', use_sudo=True, timeout=30)
            if not open_result['success']:
                open_result = execute_command(f'iptables -A INPUT -p tcp --dport {port} -j ACCEPT', use_sudo=True, timeout=10)
            results.append({
                'firewall': f'尝试开放端口 {port}',
                'success': open_result['success']
            })
            
    except Exception as e:
        results.append({
            'error': f'端口修复失败: {str(e)}',
            'success': False
        })
    
    return {
        'action': 'fix_port_issue',
        'port': port,
        'success': any(r.get('success', False) for r in results),
        'details': results
    }


def repair_restart_server(params=None):
    """重启服务器（谨慎操作）"""
    params = params or {}
    delay = params.get('delay', 60)  # 默认60秒后重启
    
    results = []
    
    try:
        # 先同步文件系统
        sync_result = execute_command('sync', timeout=30)
        results.append({'sync': '文件系统同步', 'success': sync_result['success']})
        
        # 执行重启命令
        reboot_cmd = f'shutdown -r +{delay // 60} "System reboot scheduled by admin"'
        reboot_result = execute_command(reboot_cmd, use_sudo=True, timeout=10)
        
        if reboot_result['success']:
            results.append({
                'reboot': f'服务器将在 {delay} 秒后重启',
                'success': True
            })
        else:
            # 尝试直接reboot
            reboot_result = execute_command('reboot', use_sudo=True, timeout=10)
            results.append({
                'reboot': '执行立即重启命令',
                'success': reboot_result['success']
            })
            
    except Exception as e:
        results.append({
            'error': f'重启失败: {str(e)}',
            'success': False
        })
    
    return {
        'action': 'restart_server',
        'delay': delay,
        'success': any(r.get('success', False) for r in results),
        'details': results
    }


# 修复操作映射
REPAIR_ACTIONS = {
    'clear_cache': repair_clear_cache,
    'optimize_memory': repair_optimize_memory,
    'kill_zombie': repair_kill_zombie,
    'cleanup_disk': repair_cleanup_disk,
    'fix_high_cpu': repair_fix_high_cpu,
    'reset_network': repair_reset_network,
    'check_port_connectivity': repair_check_port_connectivity,
    'check_network_interface': repair_check_network_interface,
    'fix_port_issue': repair_fix_port_issue,
    'restart_server': repair_restart_server,
    'kill_zombie_processes': repair_kill_zombie,  # 别名兼容
    'status_check': lambda p: {'action': 'status_check', 'success': True, 'message': '状态检查完成', 'status': get_system_status()},
    'health_check': lambda p: {'action': 'health_check', 'success': True, 'message': '健康检查完成', 'status': get_system_status()},
    'backup': lambda p: {'action': 'backup', 'success': True, 'message': '备份功能暂不支持'},
    'cleanup': lambda p: repair_cleanup_disk(p),  # cleanup别名
    'restart_services': lambda p: {'action': 'restart_services', 'success': True, 'message': '重启服务功能暂不支持'},
}

# ==================== API路由 ====================

@app.route('/', methods=['GET'])
def index():
    """Agent欢迎页面"""
    return '''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Agent Service</title>
    <style>
        body { font-family: Arial, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
        .container { text-align: center; color: white; padding: 40px; }
        h1 { font-size: 2.5em; margin-bottom: 10px; }
        p { font-size: 1.2em; opacity: 0.9; }
        .status { background: rgba(255,255,255,0.2); padding: 20px; border-radius: 10px; margin-top: 20px; }
        .badge { background: #28a745; padding: 5px 15px; border-radius: 20px; font-size: 0.9em; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Agent Service</h1>
        <p>服务器监控代理服务</p>
        <div class="status">
            <span class="badge">运行中</span>
            <p>端口: 8089 | 版本: 6.0-7B</p>
            <p>API: /status, /inspect, /health, /repair, /script, /stress</p>
        </div>
    </div>
</body>
</html>'''

@app.route('/health', methods=['GET'])
def health_check():
    """健康检查（无需认证）"""
    return jsonify({
        'status': 'healthy',
        'agent_version': '6.0-7B',
        'hostname': socket.gethostname(),
        'timestamp': datetime.now().isoformat()
    })

@app.route('/status', methods=['GET'])
@require_token
def get_status():
    """获取系统状态 — v5.57: 透出 agent 版本号方便平台校验"""
    status = get_system_status()
    status['services'] = get_services_status()
    status['agent_version'] = '6.0-7B'
    return jsonify({
        'success': True,
        'data': status
    })


@app.route('/inspect', methods=['GET'])
@require_token
def get_inspect():
    """v5.7: 系统巡检数据采集 — 服务进程/线程数/连接数/监听端口/中间件探测/定时任务，
    供平台报告"按系统分析"逐项填充真实值；不可程序化采集项由平台人工录入。"""
    return jsonify({
        'success': True,
        'agent_version': '6.0-7B',
        'hostname': socket.gethostname(),
        'data': get_inspection_data()
    })


@app.route('/diagnose', methods=['GET'])
@require_token
def diagnose_memory():
    """v5.57: 机器诊断 — 客户机器基线就高时，看清楚到底是谁吃了内存

    返回：
      - top 内存进程（含完整命令行）
      - top CPU 进程
      - 是否有 xizang_stress 残留
      - /proc/meminfo 详细
      - tmpfs / shm 占用
      - slab 内核缓存
    """
    diag = {}

    # 1) Top 20 内存进程
    ret = execute_command("ps auxf --sort=-rss | head -25")
    diag['top_memory_processes'] = ret.get('stdout', '')

    # 2) Top 10 CPU 进程
    ret = execute_command("ps aux --sort=-%cpu | head -12")
    diag['top_cpu_processes'] = ret.get('stdout', '')

    # 3) xizang_stress 残留检测
    ret = execute_command("ps aux | grep -E 'xizang_stress' | grep -v grep")
    stress_lines = (ret.get('stdout') or '').strip().split('\n')
    stress_lines = [l for l in stress_lines if l.strip()]
    diag['stress_residual'] = {
        'count': len(stress_lines),
        'processes': stress_lines[:10],
        'warning': '⚠️ 发现 stress 残留进程，自动修复未清干净' if stress_lines else '无残留'
    }

    # 4) /proc/meminfo 关键项
    ret = execute_command("grep -E '^(MemTotal|MemFree|MemAvailable|Buffers|Cached|Swap|Slab|SReclaimable|SUnreclaim|Shmem|HugePages_Total|AnonPages|Mapped|PageTables)' /proc/meminfo")
    diag['meminfo'] = ret.get('stdout', '')

    # 5) tmpfs / shm 占用
    ret = execute_command("df -h | grep -E 'tmpfs|/dev/shm' | head -10")
    diag['tmpfs_usage'] = ret.get('stdout', '')

    # 6) slab 内核缓存（top 15 大）
    ret = execute_command("slabtop -o -s c 2>/dev/null | head -20")
    diag['slabtop'] = ret.get('stdout', '') or '(slabtop 不可用)'

    # 7) ps 总进程数 + zombie
    ret = execute_command("ps -ef | wc -l")
    diag['process_count'] = (ret.get('stdout', '') or '').strip()
    ret = execute_command("ps aux | awk '$8==\"Z\" {print $2}' | wc -l")
    diag['zombie_count'] = (ret.get('stdout', '') or '').strip()

    # 8) psutil 视角
    vm = psutil.virtual_memory()
    diag['psutil_view'] = {
        'total_mb': round(vm.total / 1024 / 1024, 1),
        'available_mb': round(vm.available / 1024 / 1024, 1),
        'used_mb': round(vm.used / 1024 / 1024, 1),
        'free_mb': round(vm.free / 1024 / 1024, 1),
        'percent': vm.percent,
        'cached_mb': round(getattr(vm, 'cached', 0) / 1024 / 1024, 1),
        'buffers_mb': round(getattr(vm, 'buffers', 0) / 1024 / 1024, 1),
        'shared_mb': round(getattr(vm, 'shared', 0) / 1024 / 1024, 1),
    }

    # 9) uptime（很多基线高的机器是开机太久内存碎片化）
    ret = execute_command("uptime")
    diag['uptime'] = (ret.get('stdout', '') or '').strip()

    # 10) 给客户的总结建议
    advice = []
    if stress_lines:
        advice.append(f'**最重要**：发现 {len(stress_lines)} 个 xizang_stress 残留进程，建议在平台执行【自动诊断修复】或 SSH 到 Agent 跑 `pkill -9 -f xizang_stress`')
    if vm.percent > 60:
        anonymous_mb = diag['psutil_view']['used_mb'] - diag['psutil_view']['cached_mb'] - diag['psutil_view']['buffers_mb']
        advice.append(f'内存使用率 {vm.percent:.1f}%，其中匿名内存约 {anonymous_mb:.0f}MB（真实业务+残留），page cache {diag["psutil_view"]["cached_mb"]:.0f}MB（可被 drop_caches 释放）')
    advice.append('如果 top 内存进程清单里有"陌生"进程占了几 GB，说明该机器并非"干净"，需要 SSH 进去人工分析；如果全是系统服务且总和远小于 used 值，可能是内核 slab/shmem/tmpfs 占用，跑一次 `echo 3 > /proc/sys/vm/drop_caches` 看是否释放')
    diag['advice'] = advice
    diag['agent_version'] = '6.0-7B'
    diag['timestamp'] = datetime.now().isoformat()

    return jsonify({'success': True, 'diagnose': diag})

@app.route('/repair', methods=['POST'])
@require_token
def execute_repair():
    """执行修复操作"""
    data = request.get_json() or {}
    action = data.get('action', 'auto')
    params = data.get('params', {})
    
    logger.info(f"收到修复请求: {action}, params={params}")
    
    start_time = time.time()
    results = []
    
    if action == 'auto':
        # v5.53: 自动诊断并修复 — 一把清场逻辑
        # 1) kill 所有 stress 子进程（含孤儿）+ 清演示标记
        # 2) sync + drop_caches，让 buff/cache 也释放，前端监控指标立刻下降
        # 3) 再按阈值跑常规修复（CPU/内存/磁盘）
        # 拿 before 状态用于 result 对比展示
        mem_before = psutil.virtual_memory().percent
        cpu_before = psutil.cpu_percent(interval=0.3)

        injected = kill_injected_faults()
        cleaned_markers = []
        for f in ('/tmp/xizang_stress_service_down', '/tmp/xizang_stress_network_issue',
                  '/tmp/.xizang_stress_io_lock'):
            try:
                os.remove(f)
                cleaned_markers.append(f)
            except FileNotFoundError:
                pass
            except Exception:
                pass
        execute_command('rm -f /tmp/xizang_stress_io_*.bin 2>/dev/null || true')

        # v5.57: 三重兜底 pkill —— 不依赖 STRESS_PROCESSES 跟踪状态，确保孤儿一并清掉
        # SIGTERM 给个机会优雅退出，再 SIGKILL 强杀；最后等 OS 真正回收物理页
        execute_command("pkill -15 -f 'xizang_stress' 2>/dev/null || true")
        time.sleep(0.3)
        execute_command("pkill -9 -f 'xizang_stress' 2>/dev/null || true")
        # Python 主动 GC 一下，避免本进程持有的 OS handle 拖延释放
        try:
            import gc; gc.collect()
        except Exception:
            pass

        # v5.58: 多次 sync + drop_caches，让 OS 真正回收 page；同时尝试两种命令格式，root/sudo 都覆盖
        cache_results = []
        for round_idx in range(3):
            if os.geteuid() == 0:
                c1 = execute_command('sync', use_sudo=False)
                c2 = execute_command('echo 3 > /proc/sys/vm/drop_caches', use_sudo=False)
            else:
                c1 = execute_command('sync', use_sudo=True)
                c2 = execute_command('echo 3 | tee /proc/sys/vm/drop_caches > /dev/null', use_sudo=True)
            cache_results.append({
                'round': round_idx + 1,
                'sync_ok': c1.get('success', False),
                'drop_ok': c2.get('success', False),
                'stderr': (c2.get('stderr') or '')[:200],
            })
            time.sleep(0.4)
        results.append({'action': 'drop_caches_multi', 'rounds': cache_results,
                        'message': '多轮 sync + drop_caches 彻底释放页面缓存'})

        if injected or cleaned_markers:
            results.append({'action': 'kill_injected_faults', 'success': True,
                            'killed': injected,
                            'cleaned_markers': cleaned_markers,
                            'mem_before': mem_before,
                            'cpu_before': cpu_before,
                            'message': f'已回收 {len(injected)} 个故障注入子进程，清理 {len(cleaned_markers)} 个演示标记'})
        time.sleep(1.5)
        status = get_system_status()

        # CPU过高
        if status.get('cpu', {}).get('usage', 0) > 80:
            results.append(repair_fix_high_cpu())

        # 内存过高
        if status.get('memory', {}).get('percent', 0) > 80:
            results.append(repair_optimize_memory())

        # 磁盘过高
        if status.get('disk', {}).get('percent', 0) > 90:
            results.append(repair_cleanup_disk())

        # 清理僵尸进程
        results.append(repair_kill_zombie())

        if not results:
            results.append({'action': 'check', 'message': '系统状态良好，无需修复'})
    
    elif action == 'restart_service':
        service = params.get('service', 'nginx')
        results.append(repair_restart_service(params))
    
    elif action in REPAIR_ACTIONS:
        results.append(REPAIR_ACTIONS[action](params))
    
    elif action == 'custom_command':
        # v5.59: 与 /script 路由对称，先过危险命令黑名单再执行
        cmd = params.get('command', '')
        if cmd:
            safe, _, reason = check_dangerous(cmd)
            if not safe:
                return jsonify({
                    'success': False,
                    'blocked': True,
                    'message': f'命令被安全规则拦截：{reason}',
                    'hint': '如确需执行该操作，请直接 SSH 手动操作。'
                }), 400
            results.append(execute_command(cmd, use_sudo=params.get('sudo', False)))
    
    else:
        return jsonify({
            'success': False,
            'message': f'未知的修复操作: {action}'
        }), 400
    
    duration = round(time.time() - start_time, 2)
    
    # 获取修复后的状态
    status_after = get_system_status()
    
    return jsonify({
        'success': True,
        'action': action,
        'results': results,
        'duration': duration,
        'status_after': status_after
    })

@app.route('/stress', methods=['POST'])
@require_token
def inject_stress():
    """v5.50: 故障注入演示真实生效。

    平台端"故障注入演示"调用本接口，本机 spawn 真实负载子进程，使 CPU/内存/IO 真的过载。
    自动修复阶段再回收。

    Request:
      { "fault_type": "cpu_overload|memory_exhaustion|io_bottleneck|service_down|network_issue",
        "duration": 180,         # 自然结束时间，秒（被 /repair 提前 kill 也可）
        "intensity": {...}       # 可选
      }
    """
    data = request.get_json() or {}
    fault_type = data.get('fault_type', 'cpu_overload')
    duration = int(data.get('duration', 180))
    intensity = data.get('intensity', {})
    logger.info(f"收到故障注入请求: {fault_type}, duration={duration}s")

    # 先确认没有同类型残留
    kill_injected_faults(fault_type)

    procs = []
    try:
        procs = _spawn_stress_worker(fault_type, duration=duration, intensity=intensity)
    except Exception as e:
        logger.exception("故障注入失败")
        return jsonify({'success': False, 'message': f'注入失败: {e}'}), 500

    with STRESS_LOCK:
        for p in procs:
            STRESS_PROCESSES.append((p.pid, fault_type))
        STRESS_STOP_FLAGS[fault_type] = threading.Event()

    # 给子进程 1 秒爬升时间再采样状态返回
    time.sleep(1.0)
    status = get_system_status()
    return jsonify({
        'success': True,
        'fault_type': fault_type,
        'duration': duration,
        'spawned_pids': [p.pid for p in procs],
        'status_after': status,
        'message': f'已在本机注入 {fault_type}，约 {duration}s 自然结束或调用 /repair 终止'
    })


# v5.57: 危险命令黑名单 —
# 原则：客户的 rm（包括删 agent 自己目录用于卸载）是合法运维操作，全部允许；
# 只挡"会让整台机器宕机/丢盘/无法挽回"的几类操作。
DANGEROUS_PATTERNS = [
    # 删根目录 / 系统关键目录（rm /home/xxx、rm /opt/xxx、rm Agent 自身目录都允许，不在黑名单）
    (r'rm\s+-rf?\s+/(\s|$)',                       '禁止 rm -rf / — 这会删整台机器'),
    (r'rm\s+-rf?\s+/\*',                           '禁止 rm -rf /* — 等同于删根目录'),
    (r'rm\s+-rf?\s+/(etc|var|usr|bin|sbin|boot|lib|lib64|root|dev|proc|sys|run)(\s|/|$)',
                                                    '禁止删除系统关键目录（/etc /var /usr /bin /boot /lib 等），会导致 Agent 服务器宕机'),
    # 关机/重启/halt
    (r'\b(shutdown|halt|poweroff|reboot)\b',       '禁止关机/重启命令 — 会让 Agent 服务器宕机失联'),
    (r'\binit\s+[06]\b',                           '禁止 init 0/6 — 会关机或重启系统'),
    (r'\bsystemctl\s+(poweroff|halt|reboot)',      '禁止 systemctl 关机/重启'),
    # 格式化磁盘 / 写物理磁盘
    (r'\bmkfs\.',                                  '禁止 mkfs 格式化磁盘 — 会丢数据'),
    (r'dd\s+if=\S+\s+of=/dev/[sh]d',               '禁止 dd 写入物理磁盘设备 — 会损坏整盘'),
    (r'>\s*/dev/[sh]d[a-z]',                       '禁止重定向到物理磁盘设备'),
    # fork 炸弹
    (r':\(\)\s*\{\s*:\|:&?\s*\}\s*;:',             '禁止 fork 炸弹 — 会耗尽系统资源'),
]

def check_dangerous(command_text):
    """检查命令文本里是否含危险操作。返回 (is_safe: bool, hit_pattern: str|None, reason: str|None)"""
    import re
    text = command_text or ''
    for pat, reason in DANGEROUS_PATTERNS:
        if re.search(pat, text, re.IGNORECASE | re.MULTILINE):
            return False, pat, reason
    return True, None, None


def hint_from_command_result(ret):
    """v5.57: 根据 stdout/stderr/returncode 给客户具体操作建议。"""
    if not isinstance(ret, dict):
        return ''
    if ret.get('success'):
        return ''
    stderr = (ret.get('stderr') or '').lower()
    err = (ret.get('error') or '').lower()
    combined = stderr + ' ' + err
    rc = ret.get('returncode')

    if 'no such file or directory' in combined:
        return '原因：找不到文件或目录。请检查命令里的路径，必须是 Agent 服务器上真实存在的绝对路径（如 /home/xizang-agent-7B/stop.sh）。'
    if 'permission denied' in combined:
        return '原因：权限不足。请在脚本配置弹窗里勾选「需要 sudo 权限」，或换用具备权限的命令。'
    if 'command not found' in combined or 'not found' in combined:
        return '原因：命令未安装。Agent 服务器上没有这个程序，请用绝对路径或先 yum/apt install。'
    if '命令超时' in combined or 'timeout' in combined:
        return '原因：超时。可在脚本配置弹窗里把「超时时间」调大（默认 60 秒）。'
    if 'is a directory' in combined:
        return '原因：把目录当文件用了，请检查命令是否写错（例如 bash 后面应该接 .sh 文件而不是目录）。'
    if rc is not None and rc != 0:
        return f'原因：命令返回了非 0 退出码 ({rc})，请查看上面的 stderr 内容。'
    return ''


@app.route('/script', methods=['POST'])
@require_token
def execute_script():
    """执行预定义脚本"""
    data = request.get_json() or {}
    script_name = data.get('script_name', '')

    logger.info(f"收到脚本执行请求: {script_name}")

    # 加载脚本配置
    config = load_config()
    scripts = config.get('scripts', {})
    
    if script_name not in scripts:
        return jsonify({
            'success': False,
            'message': f'未找到脚本: {script_name}'
        }), 404
    
    script_config = scripts[script_name]
    
    if not script_config.get('enabled', True):
        return jsonify({
            'success': False,
            'message': f'脚本已禁用: {script_name}'
        }), 400
    
    start_time = time.time()
    results = []
    
    # v5.57: 脚本执行修复 —
    # 1) 多命令模式：旧版每条命令一个独立 shell，cd 不生效，下一条找不到文件
    #    新版把所有命令合并成一段 bash 脚本一次执行，cd/变量/&&||;|管道 都正常工作
    # 2) 自定义脚本路径模式：如果"路径"里其实是 shell 命令（含 &&/;/管道/换行），
    #    当成 inline 命令执行而不是 bash <path>，避免 bash 把整串当成文件名找不到
    timeout = script_config.get('timeout', 60)
    use_sudo = script_config.get('requires_sudo', False)

    def _looks_like_inline_cmd(s):
        # 含 shell 操作符 = inline 命令；纯 /xxx/xxx.sh 一行才当文件路径
        s = (s or '').strip()
        if not s:
            return False
        if '\n' in s:
            return True
        for tok in ('&&', '||', ';', '|', '>', '<', '`', '$('):
            if tok in s:
                return True
        # 包含空格但不是 "bash xxx" 这种简单 case，也认为是 inline
        if ' ' in s and not s.split()[0].endswith(('.sh', '.bash')) and not s.startswith('/'):
            return True
        return False

    if script_config.get('use_custom_script') and script_config.get('custom_script_path'):
        script_path = script_config['custom_script_path']
        # v5.57: 危险命令拦截
        safe, _, reason = check_dangerous(script_path)
        if not safe:
            return jsonify({
                'success': False,
                'blocked': True,
                'message': f'命令被安全规则拦截：{reason}',
                'hint': '请修改脚本去掉这条危险操作。如确实需要在 Agent 服务器上执行该动作，请直接 SSH 进去手动执行。'
            }), 400
        if _looks_like_inline_cmd(script_path):
            logger.info(f'[script] custom_script_path 识别为 inline 命令: {script_path[:120]}')
            ret = execute_command(script_path, timeout=timeout, use_sudo=use_sudo)
        else:
            ret = execute_command(f'bash {script_path}', timeout=timeout, use_sudo=use_sudo)
        hint = hint_from_command_result(ret)
        if hint:
            ret['hint'] = hint
        results.append(ret)
    else:
        # 多行命令：拼成一个 bash 脚本一次执行（每行原样保留，cd 跨行生效）
        commands = script_config.get('commands', []) or []
        if not commands:
            results.append({'success': True, 'message': '未配置命令', 'cwd': os.path.expanduser('~')})
        else:
            joined = '\n'.join(c for c in commands if c and c.strip())
            # v5.57: 危险命令拦截
            safe, _, reason = check_dangerous(joined)
            if not safe:
                return jsonify({
                    'success': False,
                    'blocked': True,
                    'message': f'命令被安全规则拦截：{reason}',
                    'hint': '请修改脚本去掉这条危险操作。如确实需要在 Agent 服务器上执行该动作，请直接 SSH 进去手动执行。'
                }), 400
            logger.info(f'[script] 合并 {len(commands)} 条命令为一段 bash 脚本')
            ret = execute_command(joined, timeout=timeout, use_sudo=use_sudo)
            ret['joined_commands'] = commands
            hint = hint_from_command_result(ret)
            if hint:
                ret['hint'] = hint
            results.append(ret)
    
    duration = round(time.time() - start_time, 2)

    # v5.51: 返回执行工作目录，方便客户排查"mkdir 111 文件去哪了"
    return jsonify({
        'success': all(r.get('success', False) for r in results),
        'script_name': script_name,
        'results': results,
        'duration': duration,
        'cwd': (results[0].get('cwd') if results else os.path.expanduser('~'))
    })

@app.route('/config', methods=['GET'])
@require_token
def get_config():
    """获取Agent配置"""
    return jsonify({
        'success': True,
        'config': load_config()
    })

@app.route('/config', methods=['POST'])
@require_token
def update_config():
    """更新Agent配置"""
    data = request.get_json() or {}
    
    try:
        config = load_config()
        # 深度合并scripts配置
        if 'scripts' in data:
            if 'scripts' not in config:
                config['scripts'] = {}
            for script_name, script_config in data['scripts'].items():
                if script_name not in config['scripts']:
                    config['scripts'][script_name] = {}
                config['scripts'][script_name].update(script_config)
        # 合并其他顶层配置
        for key, value in data.items():
            if key != 'scripts':
                config[key] = value
        save_config(config)
        logger.info(f"配置已更新: {list(data.get('scripts', {}).keys())}")
        return jsonify({'success': True, 'message': '配置已更新'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

def load_config():
    """加载配置"""
    default_config = {
        'scripts': {
            'health_check': {
                'enabled': True,
                'description': '健康检查脚本',
                'commands': [
                    'uptime',
                    'free -h',
                    'df -h',
                    'systemctl status nginx mysql redis 2>/dev/null || true'
                ],
                'timeout': 60,
                'requires_sudo': False
            },
            'cleanup': {
                'enabled': True,
                'description': '清理脚本',
                'commands': [
                    'rm -rf /tmp/* 2>/dev/null || true',
                    'journalctl --vacuum-time=3d 2>/dev/null || true',
                    'find /var/log -name "*.gz" -mtime +7 -delete 2>/dev/null || true'
                ],
                'timeout': 120,
                'requires_sudo': True
            },
            'restart_services': {
                'enabled': True,
                'description': '服务重启脚本',
                'commands': [
                    'systemctl restart nginx || service nginx restart || true',
                    'systemctl restart mysql || service mysql restart || true'
                ],
                'timeout': 60,
                'requires_sudo': True
            }
        }
    }
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    
    return default_config

def save_config(config):
    """保存配置"""
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

# ==================== 7B版新增：截图上传与日志接口 ====================

@app.route('/screenshot', methods=['POST'])
@require_token
def upload_screenshot():
    """接收截图并转发给平台的多模态API分析（7B版新增）"""
    try:
        data = request.get_json() or {}
        image_base64 = data.get('image', '')
        question = data.get('question', '请分析这张监控截图，识别异常指标和潜在问题。')
        platform_url = data.get('platform_url', '')
        mime_type = data.get('mime_type', 'image/png')

        if not image_base64:
            return jsonify({'success': False, 'message': '请提供Base64编码的图片'}), 400

        if not platform_url:
            return jsonify({'success': False, 'message': '请提供平台API地址(platform_url)'}), 400

        import requests as req
        response = req.post(
            f"{platform_url}/api/chat/image",
            json={
                'image': image_base64,
                'message': question,
                'mime_type': mime_type
            },
            timeout=120
        )

        if response.status_code == 200:
            result = response.json()
            logger.info(f"截图分析成功")
            return jsonify({'success': True, 'analysis': result.get('response', ''), 'model_type': 'llm_multimodal'})
        else:
            logger.error(f"平台API调用失败: {response.status_code}")
            return jsonify({'success': False, 'message': f'平台API调用失败: {response.status_code}'}), 502

    except Exception as e:
        logger.error(f"截图分析异常: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/logs/latest', methods=['GET'])
@require_token
def get_latest_logs():
    """获取最新日志内容（7B版新增，供截图分析参考）"""
    try:
        lines = int(request.args.get('lines', 50))
        lines = min(lines, 500)

        log_file = os.path.join(LOG_DIR, 'agent.log')
        if not os.path.exists(log_file):
            return jsonify({'success': True, 'logs': '', 'message': '日志文件不存在'})

        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            all_lines = f.readlines()
            latest = all_lines[-lines:] if len(all_lines) > lines else all_lines

        return jsonify({
            'success': True,
            'logs': ''.join(latest),
            'total_lines': len(all_lines),
            'returned_lines': len(latest)
        })
    except Exception as e:
        logger.error(f"获取日志失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


# ==================== 主入口 ====================
if __name__ == '__main__':
    logger.info(f"=" * 50)
    logger.info(f"西藏电网智能运维 Agent (7B多模态版) 启动")
    logger.info(f"端口: {AGENT_PORT}")
    logger.info(f"主机名: {socket.gethostname()}")
    logger.info(f"IP: {get_local_ip()}")
    logger.info(f"=" * 50)
    
    app.run(host='0.0.0.0', port=AGENT_PORT, debug=False, threaded=True)
