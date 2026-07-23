# -*- coding: utf-8 -*-
"""
西藏电网智能运维平台 - Flask后端应用
7B多模态版本 - 基于百炼平台Qwen2-VL-7B多模态模型
支持图片上传分析（监控截图、巡检记录等）
"""

import os
import json
import uuid
from pathlib import Path
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
import traceback

# 北京时间
BEIJING_TZ = timezone(timedelta(hours=8))

def get_beijing_time():
    """获取北京时间"""
    return datetime.now(BEIJING_TZ)

from config import ServerConfig, PathConfig, get_runtime_config
from models.ml_models import get_intelligent_assistant as get_local_assistant
from services.data_analyzer import get_data_analyzer

# 尝试导入LLM助手
try:
    from models.llm_models import get_llm_intelligent_assistant as get_llm_assistant
    LLM_AVAILABLE = True
    from config import LLMConfig
    print(f"[启动] ✓ LLM模块加载成功")
    print(f"[启动]   API地址: {LLMConfig.QWEN_API_BASE}")
    print(f"[启动]   模型: {LLMConfig.QWEN_MODEL}")
    print(f"[启动]   API Key: {LLMConfig.QWEN_API_KEY[:8]}..." if LLMConfig.QWEN_API_KEY else "[启动]   API Key: 未配置!")
except ImportError as e:
    LLM_AVAILABLE = False
    get_llm_assistant = None
    print(f"[启动] ✗ LLM模块加载失败: {e}")
    print(f"[启动]   将使用本地小模型(ML+DL)模式")
    print(f"[启动]   如需7B大模型，请确认 requests 库已安装")


def get_intelligent_assistant():
    """根据运行时配置动态获取智能助手"""
    runtime_config = get_runtime_config()

    if runtime_config.model_type == 'llm' and LLM_AVAILABLE:
        return get_llm_assistant()
    else:
        if runtime_config.model_type == 'llm' and not LLM_AVAILABLE:
            print(f"[助手] 请求LLM模式但LLM不可用，降级到本地模型")
        return get_local_assistant()


from services.report_generator import get_report_generator, INSPECTION_DIMS, INSPECTION_REMARK
from services.knowledge_base import get_knowledge_base
from services.operations import get_operation_service
from services.auto_repair import get_auto_repair_service, store_uploaded_fault, get_uploaded_fault, clear_uploaded_fault
from services.virtual_server import get_virtual_cluster, get_virtual_server
from services.real_server import get_real_server_manager, get_real_server, load_scripts_config, save_scripts_config
from services.network_monitor import get_network_monitor_manager
from services.agent_manager import agent_manager
# v5.57: 智能告警聚合 / 趋势预测 / 多方案对比
from services.alert_aggregator import get_aggregator as get_alert_aggregator, analyze_root_cause
from services.trend_predictor import push_metrics as push_trend_metrics, predict_trend
from services.repair_strategies import get_strategies, get_supported_fault_types

app = Flask(__name__, static_folder='.', static_url_path='')
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20MB 最大请求体
CORS(app)

# ==================== 聊天会话管理（SQLite + 可选 MySQL） ====================
import sqlite3

# v5.38: MySQL 支持（可选，通过 DB_ENABLED 环境变量启用）
# 建表失败不阻断启动，但会把原因 print 到 stdout（nohup 写入 app.log）
try:
    from db import init_db_from_env, get_db as get_mysql_db, is_db_enabled as is_mysql_enabled
    from db.schema import init_schema as init_mysql_schema
    _mysql_client = init_db_from_env()
    if _mysql_client:
        _schema_ok = init_mysql_schema(_mysql_client)
        if _schema_ok:
            print('[v5.38] MySQL 持久化已启用，schema 初始化完成', flush=True)
        else:
            print('[v5.38] MySQL 连接成功但 schema 初始化未全部完成，部分表可能缺失', flush=True)
    else:
        print('[v5.38] MySQL 未启用或连接失败，使用 JSON/SQLite 存储', flush=True)
except ImportError as _e:
    print(f'[v5.38] MySQL 模块加载失败: {_e}，使用 JSON/SQLite 存储', flush=True)
    _mysql_client = None
    def get_mysql_db(): return None
    def is_mysql_enabled(): return False
    def init_db_from_env(): return None


DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'smart_ops.db')

def get_db():
    """获取数据库连接（每次请求独立连接，线程安全）"""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db

def init_db():
    """初始化数据库表"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = get_db()
    db.executescript('''
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id TEXT PRIMARY KEY,
            title TEXT DEFAULT '新对话',
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            model TEXT DEFAULT '',
            created_at TEXT,
            FOREIGN KEY(session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
        );
    ''')
    db.commit()
    # 迁移旧 JSON 数据
    _migrate_json_to_sqlite(db)
    db.close()

def _migrate_json_to_sqlite(db):
    """将旧 chat_sessions.json 迁移到 SQLite"""
    json_path = os.path.join(os.path.dirname(__file__), 'data', 'chat_sessions.json')
    if not os.path.exists(json_path):
        return
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            old_sessions = json.load(f)
        if not old_sessions:
            return
        for s in old_sessions:
            sid = s.get('id', uuid.uuid4().hex[:8])
            existing = db.execute('SELECT id FROM chat_sessions WHERE id=?', [sid]).fetchone()
            if existing:
                continue
            db.execute('INSERT INTO chat_sessions (id, title, created_at, updated_at) VALUES (?,?,?,?)',
                       [sid, s.get('title', '新对话'), s.get('created_at', ''), s.get('created_at', '')])
            for msg in s.get('messages', []):
                db.execute('INSERT INTO chat_messages (session_id, role, content, created_at) VALUES (?,?,?,?)',
                           [sid, msg.get('role', 'user'), msg.get('content', ''), msg.get('timestamp', '')])
        db.commit()
        os.rename(json_path, json_path + '.bak')
        print(f'[DB] 已迁移 {len(old_sessions)} 个旧会话到 SQLite')
    except Exception as e:
        print(f'[DB] JSON迁移失败: {e}')

init_db()


# ==================== v5.36 MySQL 镜像辅助函数 ====================
# 策略：SQLite 继续作为本地主存储（保证线下可用）
# MySQL 启用时将数据镜像一份到客户中心库，所有写入静默失败不影响主流程

def _mirror_session_to_mysql(session_id: str, title: str = '新对话'):
    """对话会话镜像到 MySQL"""
    if not is_mysql_enabled():
        return
    try:
        db = get_mysql_db()
        if db:
            db.upsert('xzyw_chat_sessions',
                      {'session_id': session_id, 'title': title},
                      key_cols=['session_id'])
    except Exception as e:
        print(f'[DB-Mirror] session 镜像失败: {e}')


def _mirror_message_to_mysql(session_id: str, role: str, content: str, model: str = ''):
    """对话消息镜像到 MySQL"""
    if not is_mysql_enabled():
        return
    try:
        db = get_mysql_db()
        if db:
            db.insert('xzyw_chat_messages', {
                'session_id': session_id,
                'role': role,
                'content': content,
                'model': model
            })
    except Exception as e:
        print(f'[DB-Mirror] message 镜像失败: {e}')


def _mirror_server_to_mysql(server: dict):
    """服务器信息镜像到 MySQL xzyw_servers"""
    if not is_mysql_enabled() or not server:
        return
    try:
        db = get_mysql_db()
        if not db:
            return
        import json as _json
        extra_fields = {k: v for k, v in server.items()
                        if k not in ('server_id', 'server_name', 'ip', 'port', 'type', 'system',
                                     'token', 'ssh_user', 'ssh_port', 'status', 'last_check')}
        db.upsert('xzyw_servers', {
            'server_id': server.get('server_id') or server.get('id', ''),
            'server_name': server.get('server_name') or server.get('name', ''),
            'ip': server.get('ip'),
            'port': server.get('port'),
            'type': server.get('type', 'agent'),
            'system': server.get('system', 'default'),
            'token': server.get('token'),
            'ssh_user': server.get('ssh_user'),
            'ssh_port': server.get('ssh_port'),
            'status': server.get('status', 'unknown'),
            'last_check': server.get('last_check'),
            'extra': _json.dumps(extra_fields, ensure_ascii=False) if extra_fields else None
        }, key_cols=['server_id'])
    except Exception as e:
        print(f'[DB-Mirror] server 镜像失败: {e}')


def _mirror_report_to_mysql(report_id: str, title: str, rtype: str,
                             server_id: str = None, file_path: str = None,
                             content: str = None, summary: str = None):
    """运维报告元数据镜像到 MySQL"""
    if not is_mysql_enabled():
        return
    try:
        db = get_mysql_db()
        if db:
            db.upsert('xzyw_reports', {
                'report_id': report_id,
                'title': title,
                'type': rtype,
                'server_id': server_id,
                'file_path': file_path,
                'content': content,
                'summary': summary
            }, key_cols=['report_id'])
    except Exception as e:
        print(f'[DB-Mirror] report 镜像失败: {e}')


def _mirror_fault_to_mysql(server_id: str, fault_type: str, severity: str, description: str):
    """故障记录镜像到 MySQL"""
    if not is_mysql_enabled():
        return
    try:
        db = get_mysql_db()
        if db:
            db.insert('xzyw_fault_records', {
                'server_id': server_id,
                'fault_type': fault_type,
                'severity': severity,
                'description': description
            })
    except Exception as e:
        print(f'[DB-Mirror] fault 镜像失败: {e}')


def _mirror_repair_to_mysql(server_id: str, action: str, status: str,
                            result: str = None, triggered_by: str = 'manual'):
    """修复历史镜像到 MySQL"""
    if not is_mysql_enabled():
        return
    try:
        db = get_mysql_db()
        if db:
            db.insert('xzyw_repair_records', {
                'server_id': server_id,
                'action': action,
                'status': status,
                'result': result,
                'triggered_by': triggered_by
            })
    except Exception as e:
        print(f'[DB-Mirror] repair 镜像失败: {e}')


def _mirror_operation_log(operation: str, target: str = None, details: dict = None,
                          status: str = 'success', user: str = 'system'):
    """操作审计日志镜像到 MySQL"""
    if not is_mysql_enabled():
        return
    try:
        db = get_mysql_db()
        if db:
            import json as _json
            db.insert('xzyw_operation_log', {
                'operation': operation,
                'target': target,
                'details': _json.dumps(details, ensure_ascii=False) if details else None,
                'user': user,
                'status': status
            })
    except Exception as e:
        print(f'[DB-Mirror] operation 镜像失败: {e}')


def _mirror_metrics_to_mysql(server_id: str, metrics: dict):
    """监控指标写入 xzyw_metrics_history"""
    if not is_mysql_enabled() or not server_id or not metrics:
        return
    try:
        db = get_mysql_db()
        if not db:
            return
        import json as _json
        from datetime import datetime as _dt
        db.insert('xzyw_metrics_history', {
            'server_id': server_id,
            'timestamp': _dt.now().strftime('%Y-%m-%d %H:%M:%S'),
            'cpu_usage': _safe_num(metrics.get('cpu') or metrics.get('cpu_usage') or metrics.get('cpu_percent')),
            'mem_percent': _safe_num(metrics.get('memory') or metrics.get('mem_percent') or metrics.get('memory_percent')),
            'disk_percent': _safe_num(metrics.get('disk') or metrics.get('disk_percent')),
            'io_util': _safe_num(metrics.get('io') or metrics.get('io_util')),
            'connections': _safe_int(metrics.get('connections') or metrics.get('conn_count')),
            'raw_data': _json.dumps(metrics, ensure_ascii=False, default=str)[:65000]
        })
    except Exception as e:
        print(f'[DB-Mirror] metrics 镜像失败: {e}')


def _safe_num(v):
    try:
        return round(float(v), 2) if v is not None else None
    except (ValueError, TypeError):
        return None


def _safe_int(v):
    try:
        return int(v) if v is not None else None
    except (ValueError, TypeError):
        return None


def _start_metrics_mirror_thread():
    """后台线程：每 60 秒抓一次所有 agent 的最新指标写入 MySQL"""
    import threading, time as _t
    def _loop():
        while True:
            try:
                if is_mysql_enabled():
                    for srv in agent_manager.list_servers():
                        sid = srv.get('id') or srv.get('server_id')
                        if not sid:
                            continue
                        try:
                            st = agent_manager.get_server_status(sid)
                            if st.get('success') and st.get('data'):
                                _mirror_metrics_to_mysql(sid, st['data'])
                        except Exception:
                            pass
            except Exception as e:
                print(f'[DB-Mirror] metrics thread error: {e}')
            _t.sleep(60)
    t = threading.Thread(target=_loop, daemon=True, name='mysql-metrics-mirror')
    t.start()
    print('[DB-Mirror] 监控指标定时入库线程已启动（60s 一次）')


# 启动指标定时入库线程（仅在 MySQL 启用时实际写入，否则空转）
if is_mysql_enabled():
    _start_metrics_mirror_thread()


def _start_trend_sampler_thread():
    """v5.59: 趋势预测后台采样线程 — 无条件启动(独立于 MySQL)。
    每 5s 拉一次所有 agent /status 喂 trend_predictor，使首屏约 25s 后即有 5 点历史，
    /api/predict/trend 不再因"历史不足"返回 unknown。"""
    import threading, time as _t
    def _loop():
        while True:
            try:
                for srv in agent_manager.list_servers():
                    sid = srv.get('id') or srv.get('server_id')
                    if not sid:
                        continue
                    try:
                        st = agent_manager.get_server_status(sid)
                        if st.get('success') and st.get('data'):
                            push_trend_metrics(sid, st['data'])
                    except Exception:
                        pass
            except Exception as e:
                print(f'[TrendSampler] error: {e}')
            _t.sleep(int(os.environ.get('TREND_SAMPLE_SEC', '5')))
    t = threading.Thread(target=_loop, daemon=True, name='trend-sampler')
    t.start()
    print('[TrendSampler] 趋势采样线程已启动（5s 一次，独立于 MySQL）')


# v5.59: 趋势采样线程无条件启动，演示/纯离线无 MySQL 也能持续累积历史
_start_trend_sampler_thread()


# ==================== v5.60: 后端自愈守护线程 ====================
# 根治"自动模式注入故障后降不下来"：旧版自动巡检只在浏览器 setInterval 里跑，
# 运维一旦切走标签页 / 关闭页面，巡检即停止，注入的故障再也无人调 /repair 回收。
# 现改由平台后端常驻巡检，关掉浏览器也照样每 10s 自检自愈。
AUTO_REPAIR_STATE = {'enabled': False, 'last_run': None, 'log': []}
_AUTO_REPAIR_LOG_MAX = 200


def _auto_repair_log(level, msg):
    from datetime import datetime as _dt
    AUTO_REPAIR_STATE['log'].append({'ts': _dt.now().strftime('%H:%M:%S'), 'level': level, 'msg': msg})
    if len(AUTO_REPAIR_STATE['log']) > _AUTO_REPAIR_LOG_MAX:
        AUTO_REPAIR_STATE['log'] = AUTO_REPAIR_STATE['log'][-_AUTO_REPAIR_LOG_MAX:]


def _start_auto_repair_daemon():
    """v5.60: 后端自愈守护线程。开关由 /api/auto-repair/mode 控制（前端"自动修复"toggle 同步）。
    检测逻辑复用 agent_manager.detect_faults（含注入标记 + active_workers），
    阈值与前端演示一致（CPU/内存 50%、磁盘 80%）；发现故障即调 execute_repair(auto) 一把清场。"""
    import threading, time as _t
    THRESH = {'cpu': 50, 'memory': 50, 'disk': 80, 'io': 80, 'connections': 500}

    def _loop():
        while True:
            interval = int(os.environ.get('AUTO_REPAIR_SEC', '10'))
            try:
                if AUTO_REPAIR_STATE['enabled']:
                    from datetime import datetime as _dt
                    AUTO_REPAIR_STATE['last_run'] = _dt.now().isoformat()
                    for srv in agent_manager.list_servers():
                        sid = srv.get('id') or srv.get('server_id')
                        if not sid:
                            continue
                        try:
                            det = agent_manager.detect_faults(sid, THRESH)
                            if not det.get('success') or not det.get('has_fault'):
                                continue
                            faults = det.get('faults', []) or []
                            name = det.get('server_name', sid)
                            if any(f.get('type') == 'offline' for f in faults):
                                _auto_repair_log('warning', f'{name} Agent 离线，无法自动修复')
                                continue
                            reason = '，'.join(f.get('message', f.get('type', '?')) for f in faults[:4])
                            _auto_repair_log('danger', f'{name} 触发自愈 — {reason}')
                            res = agent_manager.execute_repair(sid, 'auto')
                            if res.get('success'):
                                after = res.get('status_after', {}) or {}
                                cpu_a = (after.get('cpu') or {}).get('usage')
                                mem_a = (after.get('memory') or {}).get('percent')
                                tail = ''
                                if cpu_a is not None and mem_a is not None:
                                    tail = f'（修复后 CPU {cpu_a:.1f}% / 内存 {mem_a:.1f}%）'
                                _auto_repair_log('success', f'{name} 自愈完成{tail}')
                            else:
                                _auto_repair_log('warning', f"{name} 自愈失败：{res.get('message', '未知')}")
                        except Exception as e:
                            _auto_repair_log('warning', f'{sid} 巡检异常：{e}')
            except Exception as e:
                print(f'[AutoRepairDaemon] error: {e}')
            _t.sleep(max(3, interval))

    t = threading.Thread(target=_loop, daemon=True, name='auto-repair-daemon')
    t.start()
    print('[AutoRepairDaemon] 后端自愈守护线程已启动（默认 10s，受 /api/auto-repair/mode 开关控制）')


_start_auto_repair_daemon()


@app.route('/api/auto-repair/mode', methods=['GET', 'POST'])
def api_auto_repair_mode():
    """v5.60: 查询/设置后端自愈守护开关。前端切到"自动"时 POST {enabled:true}，
    使自动巡检脱离浏览器由平台后端常驻执行；切回"手动"POST {enabled:false}。"""
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        enabled = bool(data.get('enabled', False))
        AUTO_REPAIR_STATE['enabled'] = enabled
        _auto_repair_log('info', f"后端自愈守护已{'启用（关掉浏览器也持续巡检）' if enabled else '关闭'}")
        return jsonify({'success': True, 'enabled': enabled})
    return jsonify({'success': True, 'enabled': AUTO_REPAIR_STATE['enabled'],
                    'last_run': AUTO_REPAIR_STATE['last_run']})


@app.route('/api/auto-repair/log', methods=['GET'])
def api_auto_repair_log():
    """v5.60: 拉取后端自愈守护最近日志，供前端"修复执行过程"卡片展示后端自愈轨迹。"""
    try:
        limit = int(request.args.get('limit', 50))
    except Exception:
        limit = 50
    return jsonify({'success': True, 'enabled': AUTO_REPAIR_STATE['enabled'],
                    'last_run': AUTO_REPAIR_STATE['last_run'],
                    'log': AUTO_REPAIR_STATE['log'][-limit:]})


# ==================== v5.7: 系统巡检项人工录入 ====================
# 领导要求"按系统分析、每系统覆盖附件17项"。其中应用层项(证书过期/ISC登录限制/账号权限/
# 审计日志/登录主页)无法由 Agent 程序化采集，由运维按系统人工录入真实值，报告据此填充——零造假。
_INSPECTION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'system_inspection.json')
# v5.8: 人工录入字段从统一数据源 INSPECTION_DIMS 派生 —— 覆盖全部 17 项（每个项都能人工录入/补充，
# 客户反馈"待录入的没地方填,比如 redis"），末尾加补充说明。auto 项标注"可留空(自动采集)"。
INSPECTION_MANUAL_FIELDS = [
    {'key': d['key'], 'label': d['label'], 'desc': d['desc'], 'auto': bool(d.get('auto')),
     'placeholder': d.get('ph', '')}
    for d in INSPECTION_DIMS
] + [
    {'key': INSPECTION_REMARK['key'], 'label': INSPECTION_REMARK['label'],
     'desc': INSPECTION_REMARK['desc'], 'auto': False, 'placeholder': INSPECTION_REMARK['ph']}
]


def _load_inspection_manual():
    try:
        if os.path.exists(_INSPECTION_FILE):
            with open(_INSPECTION_FILE, 'r', encoding='utf-8') as f:
                return json.load(f) or {}
    except Exception as e:
        print(f'[Inspection] 读取人工巡检项失败: {e}')
    return {}


def _save_inspection_manual(data):
    try:
        os.makedirs(os.path.dirname(_INSPECTION_FILE), exist_ok=True)
        tmp = _INSPECTION_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _INSPECTION_FILE)
        return True
    except Exception as e:
        print(f'[Inspection] 保存人工巡检项失败: {e}')
        return False


def _hidden_system(name):
    """v5.8: 与前端 isHiddenSystem 一致 —— 隐藏默认分组/空系统。"""
    n = (name or '').strip()
    return n in ('', 'default', '默认分组')


def _list_systems():
    """汇总当前监控的系统名（按 server.system 分组），供巡检录入下拉与报告分组。
    v5.8: 过滤默认分组/未分组（客户要求去掉默认分组），只列已分组的真实系统。"""
    systems = {}
    try:
        for srv in agent_manager.list_servers():
            sysname = (srv.get('system') or '').strip()
            if _hidden_system(sysname):
                continue
            systems[sysname] = systems.get(sysname, 0) + 1
    except Exception:
        pass
    try:
        for srv in get_virtual_cluster().get_all_servers():
            sysname = (srv.get('system') or '').strip()
            if _hidden_system(sysname):
                continue
            systems[sysname] = systems.get(sysname, 0) + 1
    except Exception:
        pass
    return systems


@app.route('/api/inspection/manual', methods=['GET'])
def api_inspection_manual_get():
    """v5.7: 拉取各系统人工录入巡检项 + 字段定义 + 当前系统清单。"""
    return jsonify({
        'success': True,
        'fields': INSPECTION_MANUAL_FIELDS,
        'systems': _list_systems(),
        'manual': _load_inspection_manual(),
    })


@app.route('/api/inspection/manual', methods=['POST'])
def api_inspection_manual_save():
    """v5.7: 保存某系统的人工录入巡检项。body: {system, values:{key:val}}；或 {manual:{system:{...}}} 整体保存。"""
    data = request.get_json(silent=True) or {}
    store = _load_inspection_manual()
    if 'manual' in data and isinstance(data['manual'], dict):
        store = data['manual']
    else:
        system = (data.get('system') or '').strip()
        if not system:
            return jsonify({'success': False, 'message': '缺少 system'}), 400
        values = data.get('values') or {}
        allowed = {f['key'] for f in INSPECTION_MANUAL_FIELDS}
        store[system] = {k: str(v) for k, v in values.items() if k in allowed}
    ok = _save_inspection_manual(store)
    return jsonify({'success': ok, 'manual': store})


# ==================== 静态文件路由 ====================

@app.route('/')
def index():
    """返回主页"""
    return send_from_directory('.', 'index.html')


@app.route('/<path:path>')
def static_files(path):
    """返回静态文件"""
    return send_from_directory('.', path)


# ==================== LLM动态数据获取 ====================

def fetch_extra_context_for_query(message, online_agents):
    """根据用户问题关键词，从Agent动态获取补充数据"""
    extra = {}
    msg = message.lower()

    KEYWORD_MAP = {
        'port': ['端口', 'port', 'listen', '监听'],
        'service': ['服务', 'service', 'nginx', 'mysql', 'redis', 'docker', 'httpd'],
        'log': ['日志', 'log', 'error', '报错', '错误'],
        'network': ['网络', '网卡', 'interface', '带宽', 'eth'],
        'load': ['负载', 'load', 'uptime', '运行时间', '启动时间'],
    }

    matched = [k for k, kws in KEYWORD_MAP.items() if any(kw in msg for kw in kws)]
    if not matched:
        return extra

    for srv in online_agents[:5]:
        srv_ip = srv.get('ip', '')
        srv_port = srv.get('port', 8089)
        srv_token = srv.get('token', 'CHANGE_ME_AGENT_TOKEN')
        srv_name = srv.get('name', srv_ip)
        headers = {'X-Agent-Token': srv_token}
        base = f'http://{srv_ip}:{srv_port}'

        for group in matched:
            try:
                if group == 'log':
                    r = requests.get(f'{base}/logs/latest?lines=20', headers=headers, timeout=5)
                    if r.ok:
                        extra.setdefault('logs', {})[srv_name] = r.json().get('logs', '')[:2000]
                elif group == 'port':
                    r = requests.post(f'{base}/repair', headers=headers,
                                      json={'action': 'check_port_connectivity', 'params': {}}, timeout=5)
                    if r.ok:
                        extra.setdefault('port', {})[srv_name] = str(r.json().get('results', ''))[:2000]
                elif group == 'network':
                    r = requests.post(f'{base}/repair', headers=headers,
                                      json={'action': 'check_network_interface', 'params': {}}, timeout=5)
                    if r.ok:
                        extra.setdefault('network', {})[srv_name] = str(r.json().get('results', ''))[:2000]
                elif group == 'service':
                    r = requests.get(f'{base}/status', headers=headers, timeout=5)
                    if r.ok:
                        d = r.json().get('data', {})
                        extra.setdefault('services', {})[srv_name] = d.get('services', {})
                elif group == 'load':
                    r = requests.get(f'{base}/status', headers=headers, timeout=5)
                    if r.ok:
                        d = r.json().get('data', {})
                        extra.setdefault('load', {})[srv_name] = {
                            'load_avg': d.get('cpu', {}).get('load_avg', []),
                            'uptime': d.get('uptime', ''),
                            'hostname': d.get('hostname', '')
                        }
            except Exception:
                pass

    # v5.59: 真 RAG —— 把知识库三表的命中结果拼进 extra_context，让 7B 对话真的检索 KB 后作答
    # 旧版本 llm_models 内置了一份硬编码 dict 死代码，从未注入 prompt；现改为本地 kb.search 实查
    try:
        kb = get_knowledge_base()
        kb_hits = kb.search(message)
        top_hits = (kb_hits.get('results') or [])[:3]
        if top_hits:
            simplified = []
            for h in top_hits:
                d = h.get('data') or {}
                item = {
                    '类型': {'fault_pattern': '故障模式', 'solution': '处置方案', 'historical_case': '历史案例'}.get(h.get('type'), '知识'),
                    '标题': d.get('name') or d.get('title') or '',
                }
                if d.get('description'):
                    item['描述'] = str(d.get('description'))[:300]
                if d.get('root_cause'):
                    item['根因'] = str(d.get('root_cause'))[:300]
                if d.get('resolution'):
                    item['处置'] = str(d.get('resolution'))[:300]
                if d.get('steps'):
                    item['步骤'] = ' → '.join(d.get('steps', [])[:5])
                simplified.append(item)
            extra['knowledge'] = simplified
    except Exception:
        pass

    return extra


# ==================== 聊天API ====================

@app.route('/api/chat', methods=['POST'])
def chat():
    """聊天接口 - SQLite持久化，per-tab会话隔离"""
    try:
        data = request.get_json() or {}
        message = data.get('message') or ''
        session_id = data.get('session_id') or ''

        if data.get('clear_history'):
            session_id = ''  # 清除后让下面自动创建新会话

        # SQLite 会话管理
        db = get_db()
        now = get_beijing_time().isoformat()
        if not session_id:
            session_id = uuid.uuid4().hex[:8]
            db.execute('INSERT INTO chat_sessions (id, title, created_at, updated_at) VALUES (?,?,?,?)',
                       [session_id, '新对话', now, now])
            db.commit()
            _mirror_session_to_mysql(session_id, '新对话')
        else:
            existing = db.execute('SELECT id FROM chat_sessions WHERE id=?', [session_id]).fetchone()
            if not existing:
                db.execute('INSERT INTO chat_sessions (id, title, created_at, updated_at) VALUES (?,?,?,?)',
                           [session_id, '新对话', now, now])
                db.commit()
                _mirror_session_to_mysql(session_id, '新对话')

        if not message:
            return jsonify({
                'success': False,
                'message': '消息不能为空'
            }), 400
        
        message_lower = message.lower()
        
        # 获取智能助手
        assistant = get_intelligent_assistant()
        analyzer = get_data_analyzer()
        
        # ========== 收集所有服务器信息 ==========
        all_servers = []
        
        # 1. 虚拟服务器 - 使用用户添加的虚拟服务器
        virtual_cluster = get_virtual_cluster()
        virtual_servers = virtual_cluster.get_all_servers()
        for srv in virtual_servers:
            # 兼容不同字段名
            srv_name = srv.get('server_name') or srv.get('name') or srv.get('server_id') or srv.get('id') or 'Unknown'
            srv_id = srv.get('server_id') or srv.get('id')
            
            # 资源数据可能在resources或metrics字段中
            resources = srv.get('resources', srv.get('metrics', {}))
            
            # 提取CPU和内存指标
            cpu_data = resources.get('cpu', {})
            mem_data = resources.get('memory', {})
            cpu_val = cpu_data.get('usage', 0)
            mem_used = mem_data.get('used', 0)
            mem_total = mem_data.get('total', 1)
            mem_val = (mem_used / mem_total * 100) if mem_total > 0 else 0
            
            # 构建统一的metrics格式
            metrics = {
                'cpu': {'usage': round(cpu_val, 1)},
                'memory': {'usage': round(mem_val, 1)},
                'disk': resources.get('disk', {}),
                'network': resources.get('network', {})
            }
            
            status = srv.get('status', 'running')
            if status == 'running':
                status = 'normal'
            
            # 检测异常
            anomalies = []
            if cpu_val > 80:
                anomalies.append('CPU使用率偏高')
                status = 'warning'
            if mem_val > 85:
                anomalies.append('内存使用率偏高')
                if status == 'normal':
                    status = 'warning'
            
            all_servers.append({
                'id': srv_id,
                'name': srv_name,
                'type': 'virtual',
                'system': srv.get('system', 'default'),
                'status': status,
                'metrics': metrics,
                'anomalies': anomalies
            })
        
        # 2. Agent服务器（用户添加的真实服务器）- 使用并行获取加速
        agent_servers = agent_manager.list_servers()
        agent_status_map = agent_manager.get_all_servers_status_fast() if agent_servers else {}
        for srv in agent_servers:
            srv_status = agent_status_map.get(srv['id'], {'success': False})
            srv_name = srv.get('name') or srv.get('ip') or 'Unknown'
            is_online = srv_status.get('success', False)
            
            # 获取原始指标数据
            raw_data = srv_status.get('data', {}) if is_online else {}
            
            # 标准化metrics格式
            cpu_data = raw_data.get('cpu', {})
            mem_data = raw_data.get('memory', {})
            disk_data = raw_data.get('disk', {})
            
            # 兼容多种字段名
            cpu_val = cpu_data.get('percent', cpu_data.get('usage', cpu_data.get('usage_percent', 0)))
            
            # 内存计算
            mem_percent = mem_data.get('percent', mem_data.get('usage_percent', 0))
            if not mem_percent and mem_data.get('total'):
                mem_used = mem_data.get('used', 0)
                mem_total = mem_data.get('total', 1)
                mem_percent = (mem_used / mem_total * 100) if mem_total > 0 else 0
            mem_val = mem_percent
            
            # 磁盘
            disk_val = disk_data.get('percent', disk_data.get('usage', 0))
            if isinstance(disk_val, str):
                disk_val = float(disk_val.rstrip('%')) if disk_val.rstrip('%') else 0
            
            # 构建统一的metrics格式
            metrics = {
                'cpu': {'usage': round(float(cpu_val), 1) if cpu_val else 0},
                'memory': {'usage': round(float(mem_val), 1) if mem_val else 0},
                'disk': {'usage': round(float(disk_val), 1) if disk_val else 0},
                'io': raw_data.get('io', raw_data.get('disk_io', {})),
                'network': raw_data.get('network', {})
            }
            
            # 检测异常
            anomalies = []
            status = 'online' if is_online else 'offline'
            
            if is_online:
                if cpu_val and float(cpu_val) > 80:
                    anomalies.append('CPU使用率偏高')
                    status = 'warning'
                if mem_val and float(mem_val) > 85:
                    anomalies.append('内存使用率偏高')
                    if status == 'online':
                        status = 'warning'
                if disk_val and float(disk_val) > 90:
                    anomalies.append('磁盘使用率偏高')
                    if status in ['online', 'warning']:
                        status = 'critical' if float(disk_val) > 95 else 'warning'
            
            all_servers.append({
                'id': srv['id'],
                'name': srv_name,
                'type': 'agent',
                'ip': srv.get('ip'),
                'system': srv.get('system', 'default'),
                'status': status,
                'metrics': metrics,
                'anomalies': anomalies,
                'online': is_online
            })
        
        # ========== 意图识别 ==========
        # 分析数据相关关键词（不含单独的"分析"，避免图片分析后误触发）
        analyze_keywords = ['分析数据', '运行数据', '数据分析', '分析服务器', '分析系统', '分析报告']
        is_analyze = any(kw in message for kw in analyze_keywords)
        
        # 系统状态相关关键词
        status_keywords = ['状态', '系统状态', '当前状态', '运行状态', '查看状态', '服务器状态']
        is_status = any(kw in message for kw in status_keywords)
        
        # 服务器列表相关关键词
        list_keywords = ['列表', '服务器列表', '有哪些服务器', '所有服务器', '服务器管理',
                         '监控哪些', '哪些系统', '系统名', '有几个系统', '几台服务器', '多少服务器', '监控了什么']
        is_list = any(kw in message for kw in list_keywords)
        
        # 拓扑图关键词
        topology_keywords = ['拓扑', '拓扑图', '架构图', '网络图', '生成拓扑', '系统拓扑']
        is_topology = any(kw in message for kw in topology_keywords)

        # 报告生成关键词
        report_keywords = ['生成报告', '出报告', '写报告', '分析报告', '运维报告']
        is_report = any(kw in message for kw in report_keywords)

        # 打开/查看报告关键词
        open_report_keywords = ['打开报告', '查看报告', '报告内容', '报告详情', '打开.*运维报告', '看看报告']
        is_open_report = any(kw in message for kw in open_report_keywords) and not is_report
        
        # 尝试匹配用户指定的服务器
        target_server = None
        for srv in all_servers:
            srv_name = (srv.get('name') or '').lower()
            srv_ip = (srv.get('ip') or '').lower()
            if srv_name and srv_name != 'unknown' and srv_name in message_lower:
                target_server = srv
                break
            if srv_ip and srv_ip in message_lower:
                target_server = srv
                break
        
        # ========== v5.7: 多轮对话连贯性闸门 ==========
        # 旧版关键词意图把"追问"截胡(含"状态/分析"等词就给固定回复)，导致问答"傻傻的"、无上下文。
        # 现：本会话已有历史 + 消息含指代/追问词时，跳过结构化意图，直接走带历史的 LLM 分支(豆包式连贯)。
        followup_markers = ['它', '它们', '他们', '她', '这个', '这台', '那个', '那台', '刚才', '刚刚',
                            '上面', '上述', '前面', '继续', '接着', '然后', '为什么', '为何',
                            '怎么办', '具体', '详细', '展开', '还有', '另外', '对比', '相比',
                            '深入', '原因', '呢']
        try:
            _hc = db.execute('SELECT COUNT(*) AS c FROM chat_messages WHERE session_id=?', [session_id]).fetchone()
            _session_has_history = bool(_hc and _hc['c'] > 0)
        except Exception:
            _session_has_history = False
        is_followup = _session_has_history and any(m in message for m in followup_markers)
        # 结构化"命令式"意图(生成报告/拓扑/打开报告)即使追问也保留；状态/列表/分析这类描述性意图在追问时让位给 LLM
        if is_followup and not (is_topology or is_report or is_open_report):
            is_analyze = is_status = is_list = False
            target_server = None

        # ========== 根据意图生成响应 ==========

        if is_topology:
            # 拓扑图 — 每个系统生成一张简洁的拓扑图
            system_groups = {}
            for srv in all_servers:
                sys_name = srv.get('system', 'default')
                if sys_name == 'default':
                    sys_name = '默认分组'
                if sys_name not in system_groups:
                    system_groups[sys_name] = []
                system_groups[sys_name].append(srv)

            # v5.17: 从用户消息中识别具体系统名称，只生成该系统的拓扑图
            # 按系统名长度从长到短匹配，避免"默认分组"被"默认"误配
            target_system = None
            for sys_name in sorted(system_groups.keys(), key=lambda x: -len(x)):
                if sys_name in message:
                    target_system = sys_name
                    break
            if target_system:
                system_groups = {target_system: system_groups[target_system]}

            if not system_groups:
                result = {'intent': 'topology', 'confidence': 0.9, 'response': '🗺️ 当前没有监控的服务器，无法生成拓扑图。请先添加服务器。'}
            else:
                if target_system:
                    topo_parts = [f'🗺️ **{target_system} - 系统拓扑图**\n']
                else:
                    topo_parts = [f'🗺️ **系统拓扑图** （共 {len(system_groups)} 个系统）\n']
                node_idx = 0
                for sys_name, srvs in system_groups.items():
                    lines = ['```mermaid', 'graph TD']
                    safe_sys = f'SYS{node_idx}'
                    lines.append(f'    {safe_sys}["{sys_name}"]')
                    styles = []
                    lines.append(f'    style {safe_sys} fill:#4A90D9,color:#fff')
                    for srv in srvs:
                        nid = f'S{node_idx}'
                        node_idx += 1
                        srv_name = srv.get('name', 'Unknown')
                        srv_ip = srv.get('ip', '')
                        metrics = srv.get('metrics', {})
                        cpu_val = metrics.get('cpu', {}).get('usage', 0)
                        mem_val = metrics.get('memory', {}).get('usage', 0)
                        label = srv_name
                        if srv_ip:
                            label += f'<br/>{srv_ip}'
                        label += f'<br/>CPU:{cpu_val}% 内存:{mem_val}%'
                        lines.append(f'    {safe_sys} --> {nid}["{label}"]')
                        status = srv.get('status', 'normal')
                        if status in ['critical', 'offline'] or (not srv.get('online', True) and srv.get('type') == 'agent'):
                            styles.append(f'    style {nid} fill:#FF6B6B,color:#fff')
                        elif status == 'warning' or cpu_val > 80 or mem_val > 85:
                            styles.append(f'    style {nid} fill:#FFD700')
                        else:
                            styles.append(f'    style {nid} fill:#90EE90')
                    lines.extend(styles)
                    lines.append('```')
                    topo_parts.append('\n'.join(lines))

                result = {'intent': 'topology', 'confidence': 0.95, 'response': '\n\n'.join(topo_parts)}

        elif is_report:
            # 报告生成 — 直接在聊天中显示报告内容
            generator = get_report_generator()
            if all_servers:
                server_data = []
                for srv in all_servers:
                    server_data.append({
                        'id': srv['id'],
                        'name': srv['name'],
                        'host': srv.get('ip', 'localhost'),
                        'status': srv.get('status', 'normal'),
                        'metrics': srv.get('metrics', {})
                    })

                report = generator.generate_real_server_report(
                    real_servers=server_data,
                    ai_analysis="通过智能问答生成的综合分析报告"
                )

                report_id = uuid.uuid4().hex[:8].upper()
                timestamp = get_beijing_time().strftime('%Y%m%d_%H%M%S')
                report_filename = f"chat_report_{report_id}_{timestamp}.md"
                report_path = Path('reports') / report_filename
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_content = report['content']
                with open(report_path, 'w', encoding='utf-8') as f:
                    f.write(report_content)
                _mirror_report_to_mysql(report_id, f'聊天生成报告 {timestamp}', 'chat',
                                        file_path=str(report_path), content=report_content)

                response_lines = [
                    "📋 **已生成运维报告**",
                    "",
                    f"📊 共分析 **{len(server_data)}** 台服务器",
                    f"✅ 报告已保存: `{report_filename}`",
                    "",
                    "---",
                    ""
                ]
                # 将报告内容直接嵌入聊天，超长时截取摘要
                if len(report_content) > 3000:
                    # 截取前3000字符作为摘要
                    response_lines.append(report_content[:3000])
                    response_lines.append("")
                    response_lines.append("---")
                    response_lines.append("⚠️ 报告内容较长，以上为摘要。完整报告可在「生成报告」页面下载。")
                else:
                    response_lines.append(report_content)

                result = {'intent': 'report_generate', 'confidence': 0.95, 'response': '\n'.join(response_lines)}
            else:
                result = {'intent': 'report_generate', 'confidence': 0.9, 'response': "📋 当前没有可分析的服务器数据。请先在「运行监控」中添加服务器。"}

        elif is_open_report:
            # 打开/查看已有报告 — 读取最近的报告文件并显示内容
            reports_dir = Path('reports')
            found_report = None
            if reports_dir.exists():
                report_files = sorted(reports_dir.glob('*.md'), key=lambda f: f.stat().st_mtime, reverse=True)
                # 如果用户指定了服务器IP或名称，尝试匹配
                if target_server:
                    srv_ip = target_server.get('ip', '')
                    srv_name = target_server.get('name', '')
                    for rf in report_files:
                        try:
                            content = rf.read_text(encoding='utf-8')
                            if srv_ip in content or srv_name in content:
                                found_report = (rf.name, content)
                                break
                        except:
                            continue
                # 没匹配到则用最近的报告
                if not found_report and report_files:
                    rf = report_files[0]
                    try:
                        found_report = (rf.name, rf.read_text(encoding='utf-8'))
                    except:
                        pass

            if found_report:
                fname, content = found_report
                response_lines = [
                    f"📋 **运维报告: `{fname}`**",
                    "",
                    "---",
                    ""
                ]
                if len(content) > 3000:
                    response_lines.append(content[:3000])
                    response_lines.append("")
                    response_lines.append("---")
                    response_lines.append("⚠️ 报告内容较长，以上为摘要。完整报告可在「生成报告」页面下载。")
                else:
                    response_lines.append(content)
                result = {'intent': 'open_report', 'confidence': 0.9, 'response': '\n'.join(response_lines)}
            else:
                result = {'intent': 'open_report', 'confidence': 0.8, 'response': "📋 暂无可查看的报告。请先说「生成报告」创建一份运维报告。"}

        elif is_analyze and not is_status:
            # 分析运行数据 - 按系统分组展示详细数据分析
            if all_servers:
                response_lines = ["📊 **运行数据分析报告**", ""]

                total_cpu = 0
                total_mem = 0
                warning_servers = []

                for srv in all_servers:
                    metrics = srv.get('metrics', {})
                    cpu_data = metrics.get('cpu', {})
                    cpu_val = cpu_data.get('current', cpu_data.get('percent', cpu_data.get('usage', 0)))
                    mem_data = metrics.get('memory', {})
                    mem_val = mem_data.get('current', mem_data.get('percent', mem_data.get('usage', 0)))
                    try:
                        total_cpu += float(cpu_val)
                        total_mem += float(mem_val)
                    except:
                        pass
                    if srv.get('anomalies') or srv.get('status') in ['warning', 'critical']:
                        warning_servers.append(srv)

                avg_cpu = total_cpu / len(all_servers) if all_servers else 0
                avg_mem = total_mem / len(all_servers) if all_servers else 0

                response_lines.append("📈 **整体资源使用情况：**")
                response_lines.append(f"- 服务器总数: {len(all_servers)} 台")
                response_lines.append(f"- 平均CPU使用率: {avg_cpu:.1f}%")
                response_lines.append(f"- 平均内存使用率: {avg_mem:.1f}%")
                response_lines.append("")

                # 按系统分组展示
                system_groups = {}
                for srv in all_servers:
                    sys_name = srv.get('system', 'default')
                    if sys_name == 'default':
                        sys_name = '默认分组'
                    if sys_name not in system_groups:
                        system_groups[sys_name] = []
                    system_groups[sys_name].append(srv)

                for sys_name, srvs in system_groups.items():
                    sys_cpu = sum(float(s.get('metrics', {}).get('cpu', {}).get('usage', 0)) for s in srvs)
                    sys_mem = sum(float(s.get('metrics', {}).get('memory', {}).get('usage', 0)) for s in srvs)
                    sys_avg_cpu = sys_cpu / len(srvs) if srvs else 0
                    sys_avg_mem = sys_mem / len(srvs) if srvs else 0
                    response_lines.append(f"**📁 {sys_name} ({len(srvs)}台) — 平均CPU: {sys_avg_cpu:.1f}%, 平均内存: {sys_avg_mem:.1f}%**")
                    for srv in srvs:
                        metrics = srv.get('metrics', {})
                        cpu_v = metrics.get('cpu', {}).get('usage', 0)
                        mem_v = metrics.get('memory', {}).get('usage', 0)
                        status_icon = '🟢' if srv.get('status') in ['normal', 'online'] or srv.get('online') else ('🟡' if srv.get('status') == 'warning' else '🔴')
                        response_lines.append(f"- {status_icon} {srv['name']} — CPU: {cpu_v}%, 内存: {mem_v}%")
                    response_lines.append("")

                if warning_servers:
                    response_lines.append(f"⚠️ **需要关注的服务器 ({len(warning_servers)}台)：**")
                    for srv in warning_servers[:5]:
                        anomalies = srv.get('anomalies', [])
                        response_lines.append(f"- {srv['name']}: {', '.join(anomalies)}")
                    response_lines.append("")
                else:
                    response_lines.append("✅ **所有服务器运行正常**")
                    response_lines.append("")

                response_lines.append("💡 您可以说「生成报告」获取完整的分析报告")

                result = {'intent': 'analyze_data', 'confidence': 0.9, 'response': '\n'.join(response_lines)}
            else:
                result = {'intent': 'analyze_data', 'confidence': 0.8, 'response': "📊 当前没有可分析的服务器数据。请先在「运行监控」中添加服务器。"}
        
        elif is_status or is_list:
            # 系统状态/服务器列表 — 按系统分组显示
            # 如果用户问的是"监控"相关，只显示 Agent 服务器（真实监控的）
            monitor_keywords = ['监控', '系统名', '哪些系统']
            is_monitor_query = any(kw in message for kw in monitor_keywords)
            display_servers = [s for s in all_servers if s['type'] == 'agent'] if is_monitor_query else all_servers

            if display_servers:
                response_lines = ["📊 **当前监控系统状态**" if is_monitor_query else "📊 **当前系统状态**", ""]

                # 按系统分组
                system_groups = {}
                for srv in display_servers:
                    sys_name = srv.get('system', 'default')
                    if sys_name == 'default':
                        sys_name = '默认分组'
                    if sys_name not in system_groups:
                        system_groups[sys_name] = []
                    system_groups[sys_name].append(srv)

                for sys_name, srvs in system_groups.items():
                    response_lines.append(f"**📁 {sys_name} ({len(srvs)}台)**")
                    for srv in srvs:
                        srv_type = 'Agent' if srv['type'] == 'agent' else '虚拟'
                        if srv['type'] == 'agent':
                            status_icon = '🟢' if srv.get('online') else '🔴'
                        else:
                            status = srv.get('status', 'normal')
                            status_icon = '🟢' if status == 'normal' else '🟡' if status == 'warning' else '🔴'
                        metrics = srv.get('metrics', {})
                        cpu = metrics.get('cpu', {})
                        cpu_val = cpu.get('usage', cpu.get('percent', '-'))
                        mem = metrics.get('memory', {})
                        mem_val = mem.get('usage', mem.get('percent', '-'))
                        ip_str = f" ({srv.get('ip')})" if srv.get('ip') else ''
                        response_lines.append(f"- {status_icon} **{srv['name']}**{ip_str} [{srv_type}] — CPU: {cpu_val}%, 内存: {mem_val}%")
                    response_lines.append("")

                normal_count = sum(1 for s in display_servers if s.get('status') in ['normal', 'online'] or s.get('online'))
                response_lines.append(f"📌 **汇总**: {normal_count}/{len(display_servers)} 台服务器正常运行")
                response_lines.append("")
                response_lines.append("💡 您可以说「分析数据」进行深度分析，或「生成报告」导出报告")

                result = {'intent': 'status_check', 'confidence': 0.9, 'response': '\n'.join(response_lines)}
            else:
                result = {'intent': 'status_check', 'confidence': 0.8, 'response': "📊 当前没有监控的服务器。\n\n请在「运行监控」中添加服务器后再查看状态。"}
        
        elif target_server:
            # 查询指定服务器
            srv = target_server
            response_lines = [
                f"📊 **服务器「{srv['name']}」详情**",
                "",
                f"🏷️ 类型: {'Agent服务器' if srv['type'] == 'agent' else '虚拟服务器'}"
            ]
            
            if srv['type'] == 'agent':
                response_lines.append(f"🌐 IP: {srv.get('ip', 'N/A')}")
                response_lines.append(f"📡 状态: {'🟢 在线' if srv.get('online') else '🔴 离线'}")
            else:
                status = srv.get('status', 'normal')
                status_text = '🟢 正常' if status == 'normal' else '🟡 警告' if status == 'warning' else '🔴 异常'
                response_lines.append(f"📡 状态: {status_text}")
            
            response_lines.append("")
            
            metrics = srv.get('metrics', {})
            if metrics:
                response_lines.append("**📈 实时指标：**")
                
                cpu = metrics.get('cpu', {})
                cpu_val = cpu.get('current', cpu.get('percent', cpu.get('usage', 'N/A')))
                response_lines.append(f"- CPU使用率: **{cpu_val}%**")
                
                mem = metrics.get('memory', {})
                mem_val = mem.get('current', mem.get('percent', mem.get('usage', 'N/A')))
                response_lines.append(f"- 内存使用率: **{mem_val}%**")
                
                io_data = metrics.get('io', metrics.get('disk_io', {}))
                if io_data:
                    io_val = io_data.get('current', io_data.get('percent', io_data.get('usage', 'N/A')))
                    response_lines.append(f"- IO利用率: **{io_val}%**")
            
            anomalies = srv.get('anomalies', [])
            if anomalies:
                response_lines.append("")
                response_lines.append("⚠️ **异常告警：**")
                for a in anomalies:
                    response_lines.append(f"- {a}")

                # 多方案对比：为第一个异常生成 Top-3 策略对比表
                repair_service = get_auto_repair_service()
                all_strategies = repair_service.get_all_strategies()
                # 映射异常描述到故障类型
                fault_type_map = {'CPU使用率偏高': 'CPU使用率偏高', 'CPU过载': 'CPU过载',
                                  '内存使用率偏高': '内存使用率偏高', '内存不足': '内存不足',
                                  '磁盘使用率偏高': '磁盘空间预警', '磁盘空间不足': '磁盘空间不足'}
                for a in anomalies:
                    ft = fault_type_map.get(a, '')
                    if ft and ft in all_strategies:
                        primary = all_strategies[ft]
                        category = primary.get('category', '')
                        candidates = []
                        for name, strat in all_strategies.items():
                            if strat.get('category') == category:
                                risk_map = {'auto': '低', 'diagnose': '中', 'manual': '高'}
                                candidates.append({
                                    'name': name, 'auto_fix': strat.get('auto_fix', False),
                                    'repair_type': strat.get('repair_type', 'manual'),
                                    'estimated_time': strat.get('estimated_time', 0),
                                    'steps': len(strat.get('operations', [])),
                                    'risk': risk_map.get(strat.get('repair_type', 'manual'), '高'),
                                    'is_primary': name == ft
                                })
                        candidates.sort(key=lambda x: (0 if x['is_primary'] else 1, x.get('estimated_time', 99)))
                        top3 = candidates[:3]
                        if top3:
                            response_lines.append("")
                            response_lines.append("🔧 **修复方案对比：**")
                            response_lines.append("")
                            response_lines.append("| 方案 | 类型 | 预估时间 | 风险 | 步骤数 | 自动修复 |")
                            response_lines.append("|------|------|---------|------|--------|---------|")
                            for c in top3:
                                star = '★ ' if c['is_primary'] else ''
                                auto = '✅' if c['auto_fix'] else '❌'
                                response_lines.append(f"| {star}{c['name']} | {c['repair_type']} | {c['estimated_time']}分钟 | {c['risk']} | {c['steps']}步 | {auto} |")
                        break  # 只对第一个异常生成对比

            result = {'intent': 'server_detail', 'confidence': 0.9, 'response': '\n'.join(response_lines)}
        
        else:
            # 默认：使用AI模型处理
            metrics = {}
            if all_servers:
                srv = all_servers[0]
                metrics = srv.get('metrics', {})
            # 动态获取补充数据
            extra_context = {}
            online_agents = [s for s in agent_servers if agent_status_map.get(s['id'], {}).get('success')]
            if online_agents:
                extra_context = fetch_extra_context_for_query(message, online_agents)
            # 从 SQLite 获取历史对话
            history_rows = db.execute('SELECT role, content FROM chat_messages WHERE session_id=? ORDER BY id DESC LIMIT 10', [session_id]).fetchall()
            history = [{'role': r['role'], 'content': r['content']} for r in reversed(history_rows)]
            result = assistant.process_query(message, metrics, all_servers=all_servers, history=history, extra_context=extra_context)

        response = result['response']
        model_name = '7B多模态' if get_runtime_config().model_type == 'llm' else 'ML+DL'

        # LLM 不可用时自动降级到本地小模型
        if '[LLM服务暂不可用]' in response or '[LLM不可用]' in response:
            print(f"[降级] LLM返回不可用标记，降级到本地模型。响应前100字: {response[:100]}")
            try:
                local_assistant = get_local_assistant()
                local_result = local_assistant.process_query(message, metrics)
                response = local_result['response'] + '\n\n_(当前使用本地小模型，大模型网关暂不可用)_'
                model_name = 'ML+DL(降级)'
            except Exception as e:
                print(f"[降级失败] {e}")  # 降级也失败则保持原响应

        # 保存到 SQLite
        db.execute('INSERT INTO chat_messages (session_id, role, content, model, created_at) VALUES (?,?,?,?,?)',
                   [session_id, 'user', message, '', now])
        db.execute('INSERT INTO chat_messages (session_id, role, content, model, created_at) VALUES (?,?,?,?,?)',
                   [session_id, 'assistant', response, model_name, now])
        # 更新会话标题和时间
        msg_count = db.execute('SELECT COUNT(*) as c FROM chat_messages WHERE session_id=?', [session_id]).fetchone()['c']
        title_for_mirror = None
        if msg_count <= 2:
            title = message[:20] + ('...' if len(message) > 20 else '')
            db.execute('UPDATE chat_sessions SET title=?, updated_at=? WHERE id=?', [title, now, session_id])
            title_for_mirror = title
        else:
            db.execute('UPDATE chat_sessions SET updated_at=? WHERE id=?', [now, session_id])
        db.commit()
        db.close()
        # 镜像到 MySQL（静默失败）
        _mirror_message_to_mysql(session_id, 'user', message, '')
        _mirror_message_to_mysql(session_id, 'assistant', response, model_name)
        if title_for_mirror:
            _mirror_session_to_mysql(session_id, title_for_mirror)
        
        return jsonify({
            'success': True,
            'response': response,
            'session_id': session_id,
            'model': model_name,
            'intent': result.get('intent', ''),
            'confidence': result.get('confidence', 0),
            'timestamp': now
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'聊天出错: {str(e)}'
        }), 500


@app.route('/api/chat/history', methods=['GET'])
def get_chat_history():
    """获取指定会话历史"""
    sid = request.args.get('session_id', '')
    if not sid:
        return jsonify({'success': True, 'history': []})
    db = get_db()
    rows = db.execute('SELECT role, content FROM chat_messages WHERE session_id=? ORDER BY id', [sid]).fetchall()
    db.close()
    return jsonify({'success': True, 'history': [{'role': r['role'], 'content': r['content']} for r in rows]})


@app.route('/api/chat/clear', methods=['POST'])
def clear_chat_history():
    """清空历史（前端会创建新会话）"""
    return jsonify({'success': True, 'message': '已清空'})


@app.route('/api/chat/sessions', methods=['GET'])
def get_chat_sessions():
    """获取所有会话列表（按更新时间降序）"""
    db = get_db()
    rows = db.execute('''
        SELECT s.id, s.title, s.created_at, s.updated_at,
               (SELECT COUNT(*) FROM chat_messages WHERE session_id=s.id) as message_count
        FROM chat_sessions s ORDER BY s.updated_at DESC LIMIT 30
    ''').fetchall()
    db.close()
    return jsonify({'success': True, 'sessions': [
        {'id': r['id'], 'title': r['title'], 'created_at': r['created_at'],
         'message_count': r['message_count']} for r in rows
    ]})


@app.route('/api/chat/session/new', methods=['POST'])
def new_chat_session():
    """创建新空会话"""
    db = get_db()
    sid = uuid.uuid4().hex[:8]
    now = get_beijing_time().isoformat()
    db.execute('INSERT INTO chat_sessions (id, title, created_at, updated_at) VALUES (?,?,?,?)',
               [sid, '新对话', now, now])
    db.commit()
    db.close()
    _mirror_session_to_mysql(sid, '新对话')
    return jsonify({'success': True, 'session': {'id': sid, 'title': '新对话'}})


@app.route('/api/chat/session/<session_id>/activate', methods=['POST'])
def activate_chat_session(session_id):
    """获取指定会话的消息"""
    db = get_db()
    session = db.execute('SELECT id, title FROM chat_sessions WHERE id=?', [session_id]).fetchone()
    if not session:
        db.close()
        return jsonify({'success': False, 'message': '会话不存在'}), 404
    rows = db.execute('SELECT role, content, model, created_at FROM chat_messages WHERE session_id=? ORDER BY id', [session_id]).fetchall()
    db.close()
    return jsonify({'success': True, 'messages': [
        {'role': r['role'], 'content': r['content'], 'model': r['model'] or ''} for r in rows
    ]})


@app.route('/api/chat/session/<session_id>', methods=['DELETE'])
def delete_chat_session(session_id):
    """删除会话及其消息"""
    db = get_db()
    db.execute('DELETE FROM chat_messages WHERE session_id=?', [session_id])
    db.execute('DELETE FROM chat_sessions WHERE id=?', [session_id])
    db.commit()
    db.close()
    return jsonify({'success': True})


# ==================== 多模态图片分析API（7B版新增） ====================

@app.route('/api/chat/image', methods=['POST'])
def chat_with_image():
    """多模态图片分析接口 - 7B版新增"""
    try:
        data = request.get_json() or {}
        image_base64 = data.get('image', '')
        question = data.get('message', '')
        mime_type = data.get('mime_type', 'image/png')

        if not image_base64:
            return jsonify({
                'success': False,
                'message': '请上传图片'
            }), 400

        if not question:
            question = '请分析这张图片的内容，识别其中的关键信息和异常情况。'

        assistant = get_intelligent_assistant()

        if hasattr(assistant, 'analyze_image'):
            result = assistant.analyze_image(
                image_base64=image_base64,
                question=question,
                mime_type=mime_type
            )
            response_text = result.get('response', '')
            # 检测是否为 fallback 响应（LLM 不可用）
            if '[LLM服务暂不可用]' in response_text:
                return jsonify({
                    'success': False,
                    'message': '大模型服务暂不可用，请检查模型网关连接后重试。'
                }), 503
            return jsonify({
                'success': True,
                'response': response_text,
                'model_type': result.get('model_type', 'llm_multimodal'),
                'intent': 'image_analysis'
            })
        else:
            return jsonify({
                'success': False,
                'message': '当前模型不支持图片分析，请切换到LLM多模态模式'
            }), 400

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'图片分析失败: {str(e)}'
        }), 500


@app.route('/api/model/info', methods=['GET'])
def get_model_info():
    """获取当前模型信息（含多模态能力标识）- 7B版新增"""
    try:
        runtime_config = get_runtime_config()
        config = runtime_config.get_current_config()

        from config import LLMConfig
        model_name = LLMConfig.QWEN_MODEL if runtime_config.llm_provider == 'qwen' else LLMConfig.LLM_MODEL
        is_multimodal = LLMConfig.MULTIMODAL_ENABLED and 'vl' in model_name.lower()

        return jsonify({
            'success': True,
            'model_info': {
                'model_type': config['model_type'],
                'provider': config['llm_provider'],
                'model_name': model_name,
                'multimodal': is_multimodal,
                'version': '5.25-7B',
                'capabilities': ['text_chat', 'fault_analysis', 'anomaly_detection',
                                'report_generation'] + (['image_analysis'] if is_multimodal else [])
            }
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


# ==================== 数据分析API ====================

@app.route('/api/analyze', methods=['POST'])
def analyze_data():
    """分析上传的数据"""
    try:
        data = request.get_json()
        content = data.get('content', '')
        server = data.get('server', '上传数据')
        data_type = data.get('data_type', 'auto')
        
        if not content:
            return jsonify({
                'success': False,
                'message': '数据内容不能为空'
            }), 400
        
        analyzer = get_data_analyzer()
        
        # 解析数据
        analysis_result = analyzer.auto_detect_and_parse(content)
        
        # 检测故障
        fault_detection = analyzer.detect_faults(analysis_result)
        
        # 存储故障数据供后续修复使用
        if fault_detection.get('has_fault'):
            store_uploaded_fault(server, analysis_result.get('type', 'unknown'), 
                               fault_detection, analysis_result)
        
        # 使用ML模型生成分析
        assistant = get_intelligent_assistant()
        
        # 构建metrics
        stats = analysis_result.get('statistics', {})
        metrics = {
            analysis_result.get('type', 'generic'): stats
        }
        
        ml_result = assistant.process_query(
            f"分析{analysis_result.get('type', '数据')}数据，检测到问题",
            metrics
        )
        
        return jsonify({
            'success': True,
            'analysis': analysis_result,
            'fault_detection': fault_detection,
            'ml_analysis': ml_result.get('response', ''),
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'分析出错: {str(e)}'
        }), 500


@app.route('/api/analyze/file', methods=['POST'])
def analyze_file():
    """分析上传的文件"""
    try:
        if 'file' not in request.files:
            return jsonify({
                'success': False,
                'message': '没有上传文件'
            }), 400
        
        file = request.files['file']
        server = request.form.get('server', '上传文件')
        
        content = file.read().decode('utf-8', errors='ignore')
        
        analyzer = get_data_analyzer()
        analysis_result = analyzer.auto_detect_and_parse(content)
        fault_detection = analyzer.detect_faults(analysis_result)
        
        if fault_detection.get('has_fault'):
            store_uploaded_fault(server, analysis_result.get('type', 'unknown'),
                               fault_detection, analysis_result)
        
        return jsonify({
            'success': True,
            'filename': file.filename,
            'analysis': analysis_result,
            'fault_detection': fault_detection,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'文件分析出错: {str(e)}'
        }), 500


# ==================== 系统状态API ====================

@app.route('/api/status', methods=['GET'])
def get_system_status():
    """获取系统状态"""
    try:
        analyzer = get_data_analyzer()
        status = analyzer.get_all_servers_status()
        
        return jsonify({
            'success': True,
            'data': status
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/status/summary', methods=['GET'])
def get_status_summary():
    """获取状态摘要"""
    try:
        analyzer = get_data_analyzer()
        status = analyzer.get_all_servers_status()
        
        return jsonify({
            'success': True,
            'summary': status.get('summary', {}),
            'timestamp': status.get('timestamp', '')
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


# ==================== 自动修复API ====================

@app.route('/api/repair/auto', methods=['POST'])
def auto_repair():
    """执行自动修复"""
    try:
        data = request.get_json() or {}
        server = data.get('server')
        fault_type = data.get('fault_type')
        
        repair_service = get_auto_repair_service()
        result = repair_service.analyze_and_repair(server, fault_type)
        
        return jsonify({
            'success': True,
            'result': result
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'修复出错: {str(e)}'
        }), 500


@app.route('/api/repair/ml-decision', methods=['POST'])
def ml_repair_decision():
    """ML/DL智能修复决策接口"""
    try:
        data = request.get_json() or {}
        fault_detection = data.get('fault_detection', {})
        
        # 如果没有传入fault_detection，尝试从上传的故障数据获取
        if not fault_detection:
            uploaded = get_uploaded_fault()
            fault_detection = uploaded.get('fault_detection', {})
        
        if not fault_detection:
            return jsonify({
                'success': False,
                'message': '未提供故障检测数据'
            }), 400
        
        repair_service = get_auto_repair_service()
        
        # 检查是否启用ML/DL
        if not repair_service.ml_dl_enabled:
            return jsonify({
                'success': False,
                'message': 'ML/DL模型未启用'
            }), 500
        
        # 转换故障检测数据格式
        # 支持多种输入格式
        if 'faults' in fault_detection and fault_detection['faults']:
            # 取第一个故障进行决策
            fault = fault_detection['faults'][0]
            decision_input = {
                'fault_type': fault.get('type', ''),
                'severity': fault.get('level', 'medium'),
                'details': {
                    'value': fault.get('value', 0)
                },
                'confidence': 0.9,
                'auto_fixable': fault_detection.get('status') != 'critical'
            }
        else:
            # 直接使用传入的格式
            decision_input = fault_detection
        
        # 获取ML/DL决策
        decision = repair_service.repair_decision_maker.decide(decision_input)
        
        return jsonify({
            'success': True,
            'input_fault': decision_input,
            'decision': decision,
            'model_info': repair_service.repair_decision_maker.get_model_info()
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'ML决策出错: {str(e)}'
        }), 500


@app.route('/api/repair/model-info', methods=['GET'])
def get_repair_model_info():
    """获取修复模型信息"""
    try:
        repair_service = get_auto_repair_service()
        
        if repair_service.ml_dl_enabled:
            model_info = repair_service.repair_decision_maker.get_model_info()
        else:
            model_info = {'ml_dl_enabled': False}
        
        return jsonify({
            'success': True,
            'ml_dl_enabled': repair_service.ml_dl_enabled,
            'model_info': model_info
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/repair/strategy', methods=['GET'])
def get_repair_strategy():
    """获取修复策略"""
    try:
        fault_type = request.args.get('fault_type', '')
        
        repair_service = get_auto_repair_service()
        strategy = repair_service.get_repair_strategy(fault_type)
        
        return jsonify({
            'success': True,
            'fault_type': fault_type,
            'strategy': strategy
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/repair/history', methods=['GET'])
def get_repair_history():
    """获取修复历史"""
    try:
        limit = int(request.args.get('limit', 10))
        
        repair_service = get_auto_repair_service()
        history = repair_service.get_repair_history(limit)
        
        return jsonify({
            'success': True,
            'history': history
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


# ==================== 虚拟服务器API ====================

@app.route('/api/virtual/servers', methods=['GET'])
def get_virtual_servers():
    """获取所有虚拟服务器状态"""
    try:
        cluster = get_virtual_cluster()
        servers = cluster.get_all_servers()
        
        return jsonify({
            'success': True,
            'servers': servers,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/virtual/server/<server_id>', methods=['GET'])
def get_virtual_server_status(server_id):
    """获取指定虚拟服务器状态"""
    try:
        server = get_virtual_server(server_id)
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        return jsonify({
            'success': True,
            'server': server.get_status()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/virtual/inject-fault', methods=['POST'])
def inject_fault_to_virtual():
    """向虚拟服务器注入故障（用于演示）"""
    try:
        data = request.get_json() or {}
        server_id = data.get('server_id', 'srv-001')
        fault_type = data.get('fault_type', 'cpu_overload')
        
        cluster = get_virtual_cluster()
        result = cluster.inject_fault_to_server(server_id, fault_type)
        
        return jsonify({
            'success': result.get('success', False),
            'result': result,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/virtual/execute-repair', methods=['POST'])
def execute_virtual_repair():
    """在虚拟服务器上执行修复操作"""
    try:
        data = request.get_json() or {}
        server_id = data.get('server_id', 'srv-001')
        action = data.get('action', 'status_check')
        params = data.get('params', {})
        
        cluster = get_virtual_cluster()
        result = cluster.execute_repair_on_server(server_id, action, params)
        
        return jsonify({
            'success': result.get('success', False),
            'result': result,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/virtual/auto-repair', methods=['POST'])
def virtual_auto_repair():
    """虚拟服务器自动修复 - 根据故障类型自动选择修复策略"""
    try:
        data = request.get_json() or {}
        server_id = data.get('server_id', 'srv-001')
        
        cluster = get_virtual_cluster()
        server = cluster.get_server(server_id)
        
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        status = server.get_status()
        repair_actions = []
        
        # 根据服务器状态决定修复操作
        if status['fault_injected']:
            fault_type = status['fault_type']
            
            if fault_type == 'cpu_overload':
                repair_actions = ['reduce_cpu_load']
            elif fault_type == 'memory_exhaustion':
                repair_actions = ['optimize_memory', 'clear_cache']
            elif fault_type == 'io_bottleneck':
                repair_actions = ['cleanup_disk']
            elif fault_type == 'service_down':
                repair_actions = ['restart_service']
            elif fault_type == 'network_issue':
                repair_actions = ['reset_network']
            else:
                repair_actions = ['status_check']
        else:
            # 检查资源使用情况
            resources = status['resources']
            if resources['cpu']['usage'] > 80:
                repair_actions.append('reduce_cpu_load')
            if resources['memory']['used'] / resources['memory']['total'] > 0.85:
                repair_actions.append('optimize_memory')
            if resources['disk']['io_util'] > 80:
                repair_actions.append('cleanup_disk')
            if not repair_actions:
                repair_actions = ['status_check']
        
        # 执行修复操作
        results = []
        for action in repair_actions:
            result = server.execute_repair(action, data.get('params', {}))
            results.append(result)
        
        return jsonify({
            'success': True,
            'server_id': server_id,
            'fault_type': status.get('fault_type'),
            'repair_actions': repair_actions,
            'results': results,
            'final_status': server.get_status(),
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/virtual/logs/<server_id>', methods=['GET'])
def get_virtual_server_logs(server_id):
    """获取虚拟服务器操作日志"""
    try:
        limit = int(request.args.get('limit', 20))
        server = get_virtual_server(server_id)
        
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        logs = server.get_operation_logs(limit)
        
        return jsonify({
            'success': True,
            'logs': logs
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/virtual/server/<server_id>/runtime-logs', methods=['GET'])
def get_server_runtime_logs(server_id):
    """获取服务器运行时日志（实时监控数据）"""
    try:
        limit = int(request.args.get('limit', 50))
        server = get_virtual_server(server_id)
        
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        logs = server.get_recent_logs(limit)
        
        return jsonify({
            'success': True,
            'server_id': server_id,
            'logs': logs,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/virtual/add-server', methods=['POST'])
def add_virtual_server():
    """添加服务器"""
    try:
        data = request.get_json() or {}
        ip = data.get('ip', '').strip()
        name = data.get('name', '').strip()
        ssh_key = data.get('ssh_key', '').strip() or None
        system = data.get('system', 'default').strip() or 'default'

        if not ip:
            return jsonify({
                'success': False,
                'message': 'IP地址不能为空'
            }), 400

        cluster = get_virtual_cluster()
        result = cluster.add_server(ip, name, ssh_key, system=system)
        # 镜像到 MySQL
        if result.get('success') and result.get('server'):
            srv = result['server']
            _mirror_server_to_mysql({
                'server_id': srv.get('id') or srv.get('server_id'),
                'server_name': srv.get('name'),
                'ip': srv.get('ip'),
                'type': 'virtual',
                'system': system,
                'status': srv.get('status', 'unknown'),
            })
        return jsonify(result)

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/virtual/remove-server/<server_id>', methods=['DELETE'])
def remove_virtual_server(server_id):
    """删除服务器"""
    try:
        cluster = get_virtual_cluster()
        result = cluster.remove_server(server_id)
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/virtual/server/<server_id>/metrics-history', methods=['GET'])
def get_server_metrics_history(server_id):
    """获取服务器历史监控数据（用于折线图展示）"""
    try:
        server = get_virtual_server(server_id)
        
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        history = server.get_metrics_history()
        
        return jsonify({
            'success': True,
            'server_id': server_id,
            'history': history,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/virtual/server/<server_id>/alerts', methods=['GET'])
def get_server_alerts(server_id):
    """获取服务器告警信息"""
    try:
        server = get_virtual_server(server_id)
        
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        status = server.get_status()
        alerts = status.get('alerts', [])
        
        return jsonify({
            'success': True,
            'server_id': server_id,
            'alerts': alerts,
            'alert_thresholds': server.alert_thresholds,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/virtual/server/<server_id>/alert-thresholds', methods=['PUT'])
def update_server_alert_thresholds(server_id):
    """更新服务器告警阈值"""
    try:
        data = request.get_json() or {}
        server = get_virtual_server(server_id)
        
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        # 更新告警阈值
        for key in ['cpu', 'memory', 'disk', 'io', 'connections']:
            if key in data:
                server.alert_thresholds[key] = float(data[key])
        
        return jsonify({
            'success': True,
            'message': '告警阈值已更新',
            'alert_thresholds': server.alert_thresholds
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/virtual/server/<server_id>/execute-script', methods=['POST'])
def execute_server_script(server_id):
    """执行服务器预设脚本"""
    try:
        data = request.get_json() or {}
        script_name = data.get('script_name')
        
        if not script_name:
            return jsonify({
                'success': False,
                'message': '请指定脚本名称'
            }), 400
        
        server = get_virtual_server(server_id)
        
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        result = server.execute_repair('execute_script', {'script_name': script_name})
        
        return jsonify({
            'success': True,
            'server_id': server_id,
            'script_name': script_name,
            'result': result,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/virtual/server/<server_id>/preset-scripts', methods=['GET'])
def get_server_preset_scripts(server_id):
    """获取服务器可用的预设脚本列表"""
    try:
        server = get_virtual_server(server_id)
        
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        scripts = []
        for name, path in server.preset_scripts.items():
            scripts.append({
                'name': name,
                'path': path,
                'description': {
                    'health_check': '健康检查脚本',
                    'backup': '备份脚本',
                    'cleanup': '清理脚本',
                    'restart_services': '服务重启脚本'
                }.get(name, '自定义脚本')
            })
        
        return jsonify({
            'success': True,
            'server_id': server_id,
            'scripts': scripts
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/virtual/server/<server_id>/network-status', methods=['GET'])
def get_server_network_status(server_id):
    """获取服务器网络状态（网卡、端口）"""
    try:
        server = get_virtual_server(server_id)
        
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        return jsonify({
            'success': True,
            'server_id': server_id,
            'network_interfaces': server.network_interfaces,
            'monitored_ports': server.monitored_ports,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


# 系统配置存储（用于自动/手动模式）
system_config = {
    'auto_repair_mode': 'manual',  # 'auto' 或 'manual'
    'auto_inspection_mode': 'manual',  # 'auto' 或 'manual'
    'auto_repair_interval': 300,  # 自动修复检查间隔（秒）
    'auto_inspection_interval': 600,  # 自动检修检查间隔（秒）
}


@app.route('/api/system/config', methods=['GET'])
def get_system_config():
    """获取系统配置"""
    return jsonify({
        'success': True,
        'config': system_config
    })


@app.route('/api/system/config', methods=['PUT'])
def update_system_config():
    """更新系统配置"""
    try:
        data = request.get_json() or {}
        
        for key in ['auto_repair_mode', 'auto_inspection_mode', 'auto_repair_interval', 'auto_inspection_interval']:
            if key in data:
                system_config[key] = data[key]
        
        return jsonify({
            'success': True,
            'message': '配置已更新',
            'config': system_config
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/virtual/faulty-servers', methods=['GET'])
def get_faulty_servers():
    """获取有故障的服务器列表"""
    try:
        cluster = get_virtual_cluster()
        faulty = cluster.get_faulty_servers()
        
        return jsonify({
            'success': True,
            'faulty_servers': faulty,
            'count': len(faulty),
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/fault/uploaded', methods=['GET'])
def get_uploaded_fault_api():
    """获取上传的故障数据"""
    try:
        fault_data = get_uploaded_fault()
        return jsonify({
            'success': True,
            'data': fault_data
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/fault/clear', methods=['POST'])
def clear_fault_data():
    """清除故障数据"""
    try:
        clear_uploaded_fault()
        return jsonify({
            'success': True,
            'message': '故障数据已清除'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


# ==================== 报告API ====================

@app.route('/api/report/generate', methods=['POST'])
def generate_report():
    """生成综合运维报告 - 使用用户添加的虚拟服务器"""
    try:
        data = request.get_json() or {}
        
        # 从虚拟服务器 + Agent服务器获取状态
        cluster = get_virtual_cluster()
        virtual_servers = cluster.get_all_servers()

        # 转换为分析数据格式
        servers = []
        # v5.7: 同时构建"按系统分析"所需的规范化服务器列表 + 巡检采集
        sys_servers = []
        inspect_map = {}

        # === Agent服务器（用户添加的真实服务器） ===
        try:
            agent_servers = agent_manager.list_servers()
            agent_status_map = agent_manager.get_all_servers_status_fast() if agent_servers else {}
            for srv in agent_servers:
                srv_status = agent_status_map.get(srv['id'], {'success': False})
                srv_name = srv.get('name') or srv.get('ip') or 'Unknown'
                is_online = srv_status.get('success', False)
                raw_data = srv_status.get('data', {}) if is_online else {}

                cpu_data = raw_data.get('cpu', {})
                mem_data = raw_data.get('memory', {})
                disk_data = raw_data.get('disk', {})

                cpu_val = cpu_data.get('percent', cpu_data.get('usage', 0))
                mem_val = mem_data.get('percent', mem_data.get('usage_percent', 0))
                if not mem_val and mem_data.get('total'):
                    mem_val = (mem_data.get('used', 0) / mem_data.get('total', 1) * 100)
                disk_val = disk_data.get('percent', disk_data.get('usage', 0))

                anomalies = []
                status = 'online' if is_online else 'offline'
                if is_online:
                    if cpu_val and float(cpu_val) > 80:
                        anomalies.append('CPU使用率偏高')
                        status = 'warning'
                    if mem_val and float(mem_val) > 85:
                        anomalies.append('内存使用率偏高')
                    if disk_val and float(disk_val) > 90:
                        anomalies.append('磁盘使用率偏高')

                servers.append({
                    'server': f"{srv_name} [Agent]",
                    'status': status,
                    'metrics': {
                        'cpu': {'current': round(float(cpu_val or 0), 1), 'avg': 0, 'max': 0},
                        'memory': {'current': round(float(mem_val or 0), 1), 'avg': 0, 'max': 0},
                        'io': {'current': round(float(disk_val or 0), 1), 'avg': 0, 'max': 0}
                    },
                    'anomalies': anomalies
                })
                # v5.7: 规范化服务器 + 在线节点拉巡检数据，供"按系统分析"
                sys_servers.append({
                    'id': srv['id'], 'name': srv_name, 'host': srv.get('ip'),
                    'system': srv.get('system', 'default'),
                    'status': 'online' if is_online else 'offline',
                    'metrics': {
                        'cpu': {'percent': round(float(cpu_val or 0), 1)},
                        'memory': {'percent': round(float(mem_val or 0), 1)},
                        'disk': {'percent': round(float(disk_val or 0), 1)},
                    }
                })
                if is_online:
                    try:
                        ins = agent_manager.inspect(srv['id'])
                        if ins.get('success') and ins.get('data'):
                            inspect_map[srv['id']] = ins['data']
                    except Exception:
                        pass
        except Exception as e:
            print(f"获取Agent服务器数据失败: {e}")

        # === 虚拟服务器 ===
        for srv in virtual_servers:
            resources = srv.get('resources', {})
            cpu_data = resources.get('cpu', {})
            mem_data = resources.get('memory', {})
            disk_data = resources.get('disk', {})
            
            # 计算CPU百分比（直接使用usage字段）
            cpu_usage = round(cpu_data.get('usage', 0), 1)
            
            # 计算内存百分比（used / total * 100）
            mem_total = mem_data.get('total', 1)
            mem_used = mem_data.get('used', 0)
            mem_usage = round((mem_used / mem_total) * 100, 1) if mem_total > 0 else 0
            
            # 计算磁盘IO百分比
            disk_io = round(disk_data.get('io_util', 0), 1)
            
            metrics = {
                'cpu': {'current': cpu_usage, 'avg': 0, 'max': 0},
                'memory': {'current': mem_usage, 'avg': 0, 'max': 0},
                'io': {'current': disk_io, 'avg': 0, 'max': 0}
            }
            anomalies = []
            status = srv.get('status', 'running')
            if srv.get('fault_injected'):
                anomalies.append(srv.get('fault_type', '故障'))
                status = 'warning'
            servers.append({
                'server': srv.get('server_name', srv.get('name', srv.get('server_id', 'unknown'))),
                'status': status,
                'metrics': metrics,
                'anomalies': anomalies
            })
            # v5.7: 虚拟服务器也纳入按系统分析（无 Agent 巡检数据，仅实时指标）
            sys_servers.append({
                'id': srv.get('server_id') or srv.get('id'),
                'name': srv.get('server_name', srv.get('name', 'unknown')),
                'host': srv.get('ip', ''),
                'system': srv.get('system', 'default'),
                'status': 'offline' if status in ('offline', 'unknown') else 'online',
                'metrics': {
                    'cpu': {'percent': cpu_usage},
                    'memory': {'percent': mem_usage},
                    'disk': {'percent': disk_io},
                }
            })

        analysis_data = {
            'timestamp': get_beijing_time().strftime('%Y-%m-%d %H:%M:%S'),
            'servers': servers,
            'summary': {
                'total': len(servers),
                'normal': sum(1 for s in servers if s['status'] in ['normal', 'running']),
                'warning': sum(1 for s in servers if s['status'] == 'warning'),
                'critical': sum(1 for s in servers if s['status'] == 'critical')
            }
        }
        
        repair_service = get_auto_repair_service()
        repair_history = repair_service.get_repair_history(5)
        
        uploaded_fault = get_uploaded_fault() or {}
        
        # 使用ML生成分析
        assistant = get_intelligent_assistant()
        ml_analysis = ""
        
        fault_detection = uploaded_fault.get('fault_detection') or {}
        if fault_detection.get('has_fault'):
            faults = fault_detection.get('faults', [])
            fault_names = [f['type'] for f in faults]
            ml_result = assistant.process_query(
                f"分析故障: {', '.join(fault_names)}",
                {}
            )
            ml_analysis = ml_result.get('response', '')
        
        generator = get_report_generator()
        report = generator.generate_fault_report(
            analysis_data=analysis_data,
            ai_analysis=ml_analysis,
            model_name='7B多模态' if get_runtime_config().model_type == 'llm' else 'ML+DL',
            repair_history=repair_history,
            uploaded_fault=uploaded_fault,
            system_servers=sys_servers,
            inspect_map=inspect_map,
            manual_inspection=_load_inspection_manual()
        )
        _mirror_report_to_mysql(
            report.get('id') or uuid.uuid4().hex[:8].upper(),
            report.get('title', '故障诊断报告'), 'fault',
            file_path=report.get('file_path'), content=report.get('content'))

        return jsonify({
            'success': True,
            'report': report
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'报告生成出错: {str(e)}'
        }), 500


@app.route('/api/report/virtual-servers', methods=['POST'])
def generate_virtual_server_report():
    """生成虚拟服务器监控报告"""
    try:
        data = request.get_json() or {}
        
        # 获取虚拟服务器集群
        cluster = get_virtual_cluster()
        servers = cluster.get_all_servers()
        
        # 使用ML分析
        assistant = get_intelligent_assistant()
        ai_analysis = ""
        
        # 检查是否有故障
        faulty_servers = [s for s in servers if s.get('fault_injected')]
        if faulty_servers:
            fault_types = list(set(s.get('fault_type') for s in faulty_servers))
            ml_result = assistant.process_query(
                f"分析服务器故障: {', '.join(fault_types)}",
                {}
            )
            ai_analysis = ml_result.get('response', '')
        
        # 生成报告
        generator = get_report_generator()
        report = generator.generate_virtual_server_report(
            virtual_servers=servers,
            ai_analysis=ai_analysis
        )
        _mirror_report_to_mysql(
            report.get('id') or uuid.uuid4().hex[:8].upper(),
            report.get('title', '虚拟服务器报告'), 'virtual',
            file_path=report.get('file_path'), content=report.get('content'))

        return jsonify({
            'success': True,
            'report': report
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'虚拟服务器报告生成出错: {str(e)}'
        }), 500



@app.route('/api/report/real-servers', methods=['POST'])
def generate_real_server_report():
    """生成真实服务器（Agent）监控报告"""
    try:
        data = request.get_json() or {}
        
        # 获取Agent服务器列表（用户添加的真实服务器）
        servers = agent_manager.list_servers()
        
        # 收集所有服务器的状态和指标
        server_data = []
        inspect_map = {}
        for server in servers:
            sid = server.get('id')
            server_info = {
                'id': sid,
                'name': server.get('name'),
                'host': server.get('ip'),
                'system': server.get('system', 'default'),  # v5.7: 按系统分组所需
                'status': server.get('status', 'unknown'),
                'metrics': {}
            }

            # 获取服务器最新状态
            try:
                status_result = agent_manager.get_server_status(sid)
                if status_result.get('success'):
                    server_info['status'] = 'online'
                    server_info['metrics'] = status_result.get('data', {})
                    # v5.7: 在线节点才拉巡检采集数据（离线不取，避免造假）
                    try:
                        ins = agent_manager.inspect(sid)
                        if ins.get('success') and ins.get('data'):
                            inspect_map[sid] = ins['data']
                    except Exception:
                        pass
                else:
                    server_info['status'] = 'offline'
            except:
                server_info['status'] = 'offline'

            server_data.append(server_info)
        # v5.7: 人工录入巡检项（证书/账号权限/审计日志/ISC登录/登录主页等）
        manual_inspection = _load_inspection_manual()
        
        # 使用ML分析
        assistant = get_intelligent_assistant()
        ai_analysis = ""
        
        # 检查是否有异常服务器
        abnormal_servers = [s for s in server_data if s.get('status') in ['warning', 'critical', 'offline']]
        if abnormal_servers:
            abnormal_info = ', '.join([f"{s['name']}({s['status']})" for s in abnormal_servers])
            ml_result = assistant.process_query(
                f"分析真实服务器状态异常: {abnormal_info}",
                {}
            )
            ai_analysis = ml_result.get('response', '')
        
        # 生成报告
        generator = get_report_generator()
        report = generator.generate_real_server_report(
            real_servers=server_data,
            ai_analysis=ai_analysis,
            inspect_map=inspect_map,
            manual_inspection=manual_inspection
        )
        _mirror_report_to_mysql(
            report.get('id') or uuid.uuid4().hex[:8].upper(),
            report.get('title', '真实服务器报告'), 'real',
            file_path=report.get('file_path'), content=report.get('content'))

        return jsonify({
            'success': True,
            'report': report
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'真实服务器报告生成出错: {str(e)}'
        }), 500

@app.route('/api/report/add-fault-record', methods=['POST'])
def add_fault_record():
    """添加故障记录到报告"""
    try:
        data = request.get_json() or {}
        generator = get_report_generator()
        generator.add_fault_record(data)
        
        return jsonify({
            'success': True,
            'message': '故障记录已添加'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/report/add-repair-record', methods=['POST'])
def add_repair_record():
    """添加修复记录到报告"""
    try:
        data = request.get_json() or {}
        generator = get_report_generator()
        generator.add_repair_record(data)
        
        return jsonify({
            'success': True,
            'message': '修复记录已添加'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/report/clear-history', methods=['POST'])
def clear_report_history():
    """清除报告历史记录"""
    try:
        generator = get_report_generator()
        generator.clear_history()
        
        return jsonify({
            'success': True,
            'message': '历史记录已清除'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/report/list', methods=['GET'])
def list_reports():
    """列出所有报告"""
    try:
        reports_dir = PathConfig.REPORTS_DIR
        reports = []
        
        if os.path.exists(reports_dir):
            for filename in os.listdir(reports_dir):
                if filename.endswith('.md'):
                    filepath = os.path.join(reports_dir, filename)
                    file_stat = os.stat(filepath)
                    reports.append({
                        'filename': filename,
                        'created_at': datetime.fromtimestamp(
                            os.path.getctime(filepath)
                        ).strftime('%Y-%m-%d %H:%M:%S'),
                        'size': file_stat.st_size
                    })
        
        reports.sort(key=lambda x: x['created_at'], reverse=True)
        
        return jsonify({
            'success': True,
            'reports': reports
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/report/<filename>', methods=['GET'])
def get_report(filename):
    """获取报告内容"""
    try:
        filepath = os.path.join(PathConfig.REPORTS_DIR, filename)
        
        if not os.path.exists(filepath):
            return jsonify({
                'success': False,
                'message': '报告不存在'
            }), 404
        
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return jsonify({
            'success': True,
            'filename': filename,
            'content': content
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


# ==================== 知识库API ====================

@app.route('/api/knowledge/search', methods=['GET'])
def search_knowledge():
    """搜索知识库"""
    try:
        query = request.args.get('q', '')

        kb = get_knowledge_base()
        result = kb.search(query)  # 返回 {query, total_results, results: [...]}

        return jsonify({
            'success': True,
            'results': result.get('results', []),
            'total_results': result.get('total_results', 0),
            'query': result.get('query', query)
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/diagnose', methods=['GET'])
def diagnose_system():
    """一键诊断端点 — 收集系统当前状态供故障排查。
    返回：版本、模型配置、服务器列表（脱敏）、知识库统计、可疑点自检。
    """
    from datetime import datetime
    import platform, traceback as tb
    report = {
        'success': True,
        'timestamp': datetime.now().isoformat(),
        'version': 'v5.60-7B',
    }
    try:
        runtime_config = get_runtime_config()
        report['model'] = {
            'model_type': getattr(runtime_config, 'model_type', None),
            'llm_provider': getattr(runtime_config, 'llm_provider', None),
        }
    except Exception as e:
        report['model'] = {'error': str(e)}

    # Agent 服务器（脱敏 token）
    try:
        agent_list = []
        for sid, srv in agent_manager.servers.items():
            agent_list.append({
                'server_id': sid,
                'server_name': srv.get('server_name'),
                'ip': srv.get('ip'),
                'port': srv.get('port'),
                'system': srv.get('system'),
                'status': srv.get('status'),
                'has_token': bool(srv.get('token')),
                'last_check': srv.get('last_check'),
            })
        systems = sorted({a['system'] or 'default' for a in agent_list})
        report['agents'] = {
            'count': len(agent_list),
            'systems': systems,
            'list': agent_list,
        }
    except Exception as e:
        report['agents'] = {'error': str(e), 'traceback': tb.format_exc()}

    # 虚拟服务器
    try:
        cluster = get_virtual_cluster()
        vlist = []
        for srv in cluster.servers.values():
            vlist.append({
                'server_id': srv.server_id,
                'server_name': srv.server_name,
                'ip': srv.ip,
                'system': getattr(srv, 'system', 'default'),
                'status': srv.status,
                'is_virtual': srv.is_virtual,
            })
        report['virtual_servers'] = {'count': len(vlist), 'list': vlist}
    except Exception as e:
        report['virtual_servers'] = {'error': str(e)}

    # 知识库
    try:
        kb = get_knowledge_base()
        sample = kb.search('CPU')
        report['knowledge_base'] = {
            'fault_patterns_count': len(kb.get_fault_patterns()),
            'solutions_count': len(kb.get_solutions()),
            'search_CPU_total': sample.get('total_results', 0),
            'search_CPU_top3': [
                {'type': r.get('type'), 'name': (r.get('data') or {}).get('name')}
                for r in sample.get('results', [])[:3]
            ],
        }
    except Exception as e:
        report['knowledge_base'] = {'error': str(e)}

    # 系统信息
    try:
        report['system_info'] = {
            'platform': platform.platform(),
            'python': platform.python_version(),
            'hostname': platform.node(),
        }
    except Exception as e:
        report['system_info'] = {'error': str(e)}

    return jsonify(report)


@app.route('/api/knowledge/patterns', methods=['GET'])
def get_fault_patterns():
    """获取故障模式"""
    try:
        kb = get_knowledge_base()
        patterns = kb.get_fault_patterns()

        return jsonify({
            'success': True,
            'patterns': patterns
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/knowledge/solutions', methods=['GET'])
def get_solutions():
    """获取解决方案"""
    try:
        kb = get_knowledge_base()
        solutions = kb.get_solutions()
        
        return jsonify({
            'success': True,
            'solutions': solutions
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/knowledge/cases', methods=['GET'])
def get_historical_cases():
    """获取历史案例"""
    try:
        import json as _json
        cases_file = os.path.join(os.path.dirname(__file__), 'knowledge_base', 'historical_cases.json')
        with open(cases_file, 'r', encoding='utf-8') as f:
            data = _json.load(f)
        return jsonify({
            'success': True,
            'cases': data.get('cases', [])
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


# ==================== 知识库管理API (CRUD) ====================

@app.route('/api/knowledge/patterns', methods=['POST'])
def add_fault_pattern():
    """新增故障模式"""
    try:
        data = request.get_json()
        if not data or not data.get('name'):
            return jsonify({'success': False, 'message': '故障模式名称不能为空'}), 400
        kb = get_knowledge_base()
        result = kb.add_pattern(data)
        return jsonify({'success': True, 'pattern': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/knowledge/patterns/<pattern_id>', methods=['PUT'])
def update_fault_pattern(pattern_id):
    """修改故障模式"""
    try:
        data = request.get_json()
        kb = get_knowledge_base()
        result = kb.update_pattern(pattern_id, data)
        if result:
            return jsonify({'success': True, 'pattern': result})
        return jsonify({'success': False, 'message': f'未找到故障模式: {pattern_id}'}), 404
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/knowledge/patterns/<pattern_id>', methods=['DELETE'])
def delete_fault_pattern(pattern_id):
    """删除故障模式"""
    try:
        kb = get_knowledge_base()
        if kb.delete_pattern(pattern_id):
            return jsonify({'success': True, 'message': f'已删除故障模式: {pattern_id}'})
        return jsonify({'success': False, 'message': f'未找到故障模式: {pattern_id}'}), 404
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/knowledge/solutions', methods=['POST'])
def add_solution():
    """新增处置方案"""
    try:
        data = request.get_json()
        if not data or not data.get('title'):
            return jsonify({'success': False, 'message': '方案标题不能为空'}), 400
        kb = get_knowledge_base()
        result = kb.add_solution(data)
        return jsonify({'success': True, 'solution': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/knowledge/solutions/<solution_id>', methods=['PUT'])
def update_solution(solution_id):
    """修改处置方案"""
    try:
        data = request.get_json()
        kb = get_knowledge_base()
        result = kb.update_solution(solution_id, data)
        if result:
            return jsonify({'success': True, 'solution': result})
        return jsonify({'success': False, 'message': f'未找到处置方案: {solution_id}'}), 404
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/knowledge/solutions/<solution_id>', methods=['DELETE'])
def delete_solution_item(solution_id):
    """删除处置方案"""
    try:
        kb = get_knowledge_base()
        if kb.delete_solution(solution_id):
            return jsonify({'success': True, 'message': f'已删除处置方案: {solution_id}'})
        return jsonify({'success': False, 'message': f'未找到处置方案: {solution_id}'}), 404
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/knowledge/cases', methods=['POST'])
def add_historical_case():
    """新增历史案例"""
    try:
        data = request.get_json()
        if not data or not data.get('title'):
            return jsonify({'success': False, 'message': '案例标题不能为空'}), 400
        kb = get_knowledge_base()
        result = kb.add_case(data)
        return jsonify({'success': True, 'case': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/knowledge/cases/<case_id>', methods=['PUT'])
def update_historical_case(case_id):
    """修改历史案例"""
    try:
        data = request.get_json()
        kb = get_knowledge_base()
        result = kb.update_case(case_id, data)
        if result:
            return jsonify({'success': True, 'case': result})
        return jsonify({'success': False, 'message': f'未找到案例: {case_id}'}), 404
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/knowledge/cases/<case_id>', methods=['DELETE'])
def delete_historical_case(case_id):
    """删除历史案例"""
    try:
        kb = get_knowledge_base()
        if kb.delete_case(case_id):
            return jsonify({'success': True, 'message': f'已删除案例: {case_id}'})
        return jsonify({'success': False, 'message': f'未找到案例: {case_id}'}), 404
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ==================== 运维操作API ====================

@app.route('/api/operation/execute', methods=['POST'])
def execute_operation():
    """执行运维操作"""
    try:
        data = request.get_json()
        server = data.get('server', '')
        operation = data.get('operation', '')
        dry_run = data.get('dry_run', True)
        
        if not server or not operation:
            return jsonify({
                'success': False,
                'message': '服务器和操作类型不能为空'
            }), 400
        
        op_service = get_operation_service()
        result = op_service.execute_operation(server, operation, dry_run=dry_run)
        
        return jsonify({
            'success': True,
            'result': result
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/operation/history', methods=['GET'])
def get_operation_history():
    """获取操作历史"""
    try:
        limit = int(request.args.get('limit', 20))
        
        op_service = get_operation_service()
        history = op_service.get_operation_history(limit)
        
        return jsonify({
            'success': True,
            'history': history
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


# ==================== 深度学习模型API ====================

@app.route('/api/dl/classify', methods=['POST'])
def dl_classify():
    """使用深度学习模型进行故障分类"""
    try:
        from models.dl_models import get_dl_manager
        
        data = request.get_json()
        features = data.get('features', [])
        
        if not features:
            return jsonify({
                'success': False,
                'message': '特征数据不能为空'
            }), 400
        
        dl_manager = get_dl_manager()
        result = dl_manager.classify_fault(features)
        
        return jsonify({
            'success': True,
            'result': result,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'DL分类出错: {str(e)}'
        }), 500


@app.route('/api/dl/anomaly', methods=['POST'])
def dl_anomaly():
    """使用AutoEncoder进行异常检测"""
    try:
        from models.dl_models import get_dl_manager
        
        data = request.get_json()
        features = data.get('features', [])
        
        if not features:
            return jsonify({
                'success': False,
                'message': '特征数据不能为空'
            }), 400
        
        dl_manager = get_dl_manager()
        result = dl_manager.detect_anomaly(features)
        
        return jsonify({
            'success': True,
            'result': result,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'异常检测出错: {str(e)}'
        }), 500


@app.route('/api/dl/trend', methods=['POST'])
def dl_trend():
    """使用LSTM进行趋势预测"""
    try:
        from models.dl_models import get_dl_manager
        
        data = request.get_json()
        time_series = data.get('time_series', [])
        
        if not time_series:
            return jsonify({
                'success': False,
                'message': '时序数据不能为空'
            }), 400
        
        dl_manager = get_dl_manager()
        result = dl_manager.analyze_trend(time_series)
        
        return jsonify({
            'success': True,
            'result': result,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'趋势分析出错: {str(e)}'
        }), 500


@app.route('/api/dl/text', methods=['POST'])
def dl_text_analyze():
    """使用MiniTransformer进行文本分析"""
    try:
        from models.dl_models import get_dl_manager
        
        data = request.get_json()
        text = data.get('text', '')
        
        if not text:
            return jsonify({
                'success': False,
                'message': '文本不能为空'
            }), 400
        
        dl_manager = get_dl_manager()
        result = dl_manager.analyze_text(text)
        
        return jsonify({
            'success': True,
            'result': result,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'文本分析出错: {str(e)}'
        }), 500


@app.route('/api/dl/explain', methods=['POST'])
def dl_explain():
    """使用注意力机制进行可解释预测"""
    try:
        from models.dl_models import get_dl_manager
        
        data = request.get_json()
        features = data.get('features', [])
        
        if not features:
            return jsonify({
                'success': False,
                'message': '特征数据不能为空'
            }), 400
        
        dl_manager = get_dl_manager()
        result = dl_manager.explain_prediction(features)
        
        return jsonify({
            'success': True,
            'result': result,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'可解释预测出错: {str(e)}'
        }), 500


@app.route('/api/dl/comprehensive', methods=['POST'])
def dl_comprehensive():
    """综合分析：结合多个DL模型"""
    try:
        from models.dl_models import get_dl_manager
        
        data = request.get_json()
        features = data.get('features', [])
        text = data.get('text', '')
        
        if not features:
            return jsonify({
                'success': False,
                'message': '特征数据不能为空'
            }), 400
        
        dl_manager = get_dl_manager()
        result = dl_manager.comprehensive_analysis(features, text)
        
        return jsonify({
            'success': True,
            'result': result,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'综合分析出错: {str(e)}'
        }), 500


# ==================== 端口和网卡监测API ====================

@app.route('/api/virtual/server/<server_id>/ports', methods=['GET'])
def get_server_ports(server_id):
    """获取服务器端口监控状态"""
    try:
        server = get_virtual_server(server_id)
        
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        ports = []
        for port, info in server.monitored_ports.items():
            ports.append({
                'port': port,
                'service': info.get('service', 'unknown'),
                'status': info.get('status', 'unknown'),
                'protocol': 'tcp'
            })
        
        return jsonify({
            'success': True,
            'server_id': server_id,
            'ports': ports,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/virtual/server/<server_id>/network-interfaces', methods=['GET'])
def get_server_network_interfaces(server_id):
    """获取服务器网卡状态"""
    try:
        server = get_virtual_server(server_id)
        
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        interfaces = []
        for name, info in server.network_interfaces.items():
            interfaces.append({
                'name': name,
                'status': info.get('status', 'unknown'),
                'ip': info.get('ip', ''),
                'mac': info.get('mac', '')
            })
        
        return jsonify({
            'success': True,
            'server_id': server_id,
            'interfaces': interfaces,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/virtual/server/<server_id>/check-port', methods=['POST'])
def check_server_port(server_id):
    """检查服务器端口连通性"""
    try:
        data = request.get_json() or {}
        port = data.get('port', 80)
        
        server = get_virtual_server(server_id)
        
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        result = server.execute_repair('check_port_connectivity', {'port': port})
        
        return jsonify({
            'success': True,
            'server_id': server_id,
            'port': port,
            'result': result,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/virtual/server/<server_id>/check-interface', methods=['POST'])
def check_server_interface(server_id):
    """检查服务器网卡状态"""
    try:
        data = request.get_json() or {}
        interface = data.get('interface', 'eth0')
        
        server = get_virtual_server(server_id)
        
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        result = server.execute_repair('check_network_interface', {'interface': interface})
        
        return jsonify({
            'success': True,
            'server_id': server_id,
            'interface': interface,
            'result': result,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/virtual/batch-repair', methods=['POST'])
def batch_repair():
    """批量修复多台服务器"""
    try:
        data = request.get_json() or {}
        server_ids = data.get('server_ids', [])
        action = data.get('action', 'status_check')
        params = data.get('params', {})
        
        if not server_ids:
            # 如果没有指定服务器，获取所有故障服务器
            cluster = get_virtual_cluster()
            faulty = cluster.get_faulty_servers()
            server_ids = [s['server_id'] for s in faulty]
        
        results = []
        for server_id in server_ids:
            server = get_virtual_server(server_id)
            if server:
                result = server.execute_repair(action, params)
                results.append(result)
        
        return jsonify({
            'success': True,
            'repaired_count': len(results),
            'results': results,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/dl/models', methods=['GET'])
def dl_model_info():
    """获取深度学习模型信息"""
    try:
        from models.dl_models import get_dl_manager
        
        dl_manager = get_dl_manager()
        info = dl_manager.get_model_info()
        
        return jsonify({
            'success': True,
            'model_info': info,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'获取模型信息出错: {str(e)}'
        }), 500


# ==================== 系统信息API ====================

@app.route('/api/info', methods=['GET'])
def get_system_info():
    """获取系统信息"""
    try:
        from models.dl_models import get_dl_manager
        dl_manager = get_dl_manager()
        dl_info = dl_manager.get_model_info()
        total_params = dl_info.get('total_parameters', 0)
    except:
        total_params = 0
    
    runtime_config = get_runtime_config()
    is_llm = runtime_config.model_type == 'llm'
    model_desc = '7B多模态 (内网API · 图文混合分析)' if is_llm else 'ML + DL (轻量级机器学习 + 深度学习)'

    return jsonify({
        'success': True,
        'info': {
            'name': '西藏电网智能运维平台',
            'version': '7B多模态版 v5.59',
            'model': model_desc,
            'features': [
                '基于规则的故障分类 (ML)',
                '统计学异常检测 (ML)',
                'TF-IDF文本分类 (ML)',
                '神经网络故障分类 (DL)',
                'LSTM时序预测 (DL)',
                'MiniTransformer文本分析 (DL)',
                'AutoEncoder异常检测 (DL)',
                '注意力机制可解释预测 (DL)',
                '自动修复建议'
            ],
            'dl_parameters': total_params,
            'timestamp': get_beijing_time().isoformat()
        }
    })


@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查"""
    return jsonify({
        'status': 'healthy',
        'timestamp': get_beijing_time().isoformat()
    })


# ==================== 真实服务器API ====================

@app.route('/api/real/servers', methods=['GET'])
def get_real_servers():
    """获取所有真实服务器状态"""
    try:
        manager = get_real_server_manager()
        servers = manager.get_all_servers()
        
        return jsonify({
            'success': True,
            'servers': servers,
            'count': len(servers),
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/real/server/<server_id>', methods=['GET'])
def get_real_server_status(server_id):
    """获取指定真实服务器状态"""
    try:
        server = get_real_server(server_id)
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        return jsonify({
            'success': True,
            'server': server.get_status()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/real/add-server', methods=['POST'])
def add_real_server():
    """添加真实服务器"""
    try:
        data = request.get_json() or {}
        ip = data.get('ip', '').strip()
        name = data.get('name', '').strip() or None
        ssh_key = data.get('ssh_key', '').strip() or None
        ssh_password = data.get('ssh_password', '').strip() or None
        ssh_user = data.get('ssh_user', 'root').strip()
        ssh_port = int(data.get('ssh_port', 22))
        
        if not ip:
            return jsonify({
                'success': False,
                'message': 'IP地址不能为空'
            }), 400
        
        manager = get_real_server_manager()
        result = manager.add_server(ip, name, ssh_key, ssh_password, ssh_user, ssh_port)
        # 镜像到 MySQL
        if result.get('success') and result.get('server'):
            srv = result['server']
            _mirror_server_to_mysql({
                'server_id': srv.get('id') or srv.get('server_id'),
                'server_name': srv.get('name'),
                'ip': srv.get('ip'),
                'port': srv.get('port', 22),
                'type': 'real',
                'system': srv.get('system', 'default'),
                'ssh_user': srv.get('ssh_user'),
                'ssh_port': srv.get('ssh_port'),
                'status': srv.get('status', 'unknown'),
            })
        return jsonify(result)

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/real/remove-server/<server_id>', methods=['DELETE'])
def remove_real_server(server_id):
    """删除真实服务器"""
    try:
        manager = get_real_server_manager()
        result = manager.remove_server(server_id)
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/real/test-connection', methods=['POST'])
def test_real_server_connection():
    """测试真实服务器连接（不添加）"""
    try:
        data = request.get_json() or {}
        ip = data.get('ip', '').strip()
        ssh_key = data.get('ssh_key', '').strip() or None
        ssh_password = data.get('ssh_password', '').strip() or None
        ssh_user = data.get('ssh_user', 'root').strip()
        ssh_port = int(data.get('ssh_port', 22))
        
        if not ip:
            return jsonify({
                'success': False,
                'message': 'IP地址不能为空'
            }), 400
        
        manager = get_real_server_manager()
        result = manager.test_server_connection(ip, ssh_key, ssh_password, ssh_user, ssh_port)
        
        return jsonify(result)
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/real/server/<server_id>/ping', methods=['POST'])
def ping_real_server(server_id):
    """Ping测试真实服务器"""
    try:
        server = get_real_server(server_id)
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        ping_ok, ping_msg = server.ping()
        
        return jsonify({
            'success': ping_ok,
            'message': ping_msg,
            'server_id': server_id,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/real/server/<server_id>/ssh-test', methods=['POST'])
def test_real_server_ssh(server_id):
    """测试真实服务器SSH连接"""
    try:
        server = get_real_server(server_id)
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        ssh_ok, ssh_msg = server.test_ssh_connection()
        
        return jsonify({
            'success': ssh_ok,
            'message': ssh_msg,
            'server_id': server_id,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/real/server/<server_id>/metrics', methods=['GET'])
def get_real_server_metrics(server_id):
    """获取真实服务器监控指标"""
    try:
        server = get_real_server(server_id)
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        metrics = server.get_system_metrics()
        
        return jsonify({
            'success': metrics.get('success', False),
            'server_id': server_id,
            'metrics': metrics,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/real/server/<server_id>/metrics-history', methods=['GET'])
def get_real_server_metrics_history(server_id):
    """获取真实服务器历史监控数据"""
    try:
        server = get_real_server(server_id)
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        history = server.get_metrics_history()
        
        return jsonify({
            'success': True,
            'server_id': server_id,
            'history': history,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/real/server/<server_id>/execute', methods=['POST'])
def execute_on_real_server(server_id):
    """在真实服务器上执行命令"""
    try:
        data = request.get_json() or {}
        command = data.get('command', '').strip()
        
        if not command:
            return jsonify({
                'success': False,
                'message': '命令不能为空'
            }), 400
        
        server = get_real_server(server_id)
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        result = server.execute_command(command)
        
        return jsonify({
            'success': result.get('success', False),
            'server_id': server_id,
            'result': result,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/real/server/<server_id>/repair', methods=['POST'])
def repair_real_server(server_id):
    """在真实服务器上执行修复操作"""
    try:
        data = request.get_json() or {}
        action = data.get('action', 'status_check')
        params = data.get('params', {})
        
        server = get_real_server(server_id)
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        result = server.execute_repair(action, params)
        
        return jsonify({
            'success': result.get('success', False),
            'server_id': server_id,
            'result': result,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/real/server/<server_id>/auto-repair', methods=['POST'])
def auto_repair_real_server(server_id):
    """对真实服务器执行自动检修"""
    try:
        server = get_real_server(server_id)
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        repair_results = []
        
        # Step 1: 状态检查
        status_result = server.execute_repair('status_check')
        repair_results.append(status_result)
        
        if not status_result.get('success'):
            return jsonify({
                'success': False,
                'message': '服务器连接失败，无法执行自动检修',
                'server_id': server_id,
                'results': repair_results,
                'timestamp': get_beijing_time().isoformat()
            })
        
        # Step 2: 获取系统指标
        metrics = server.get_system_metrics()
        
        # Step 3: 根据指标决定修复操作
        cpu_usage = metrics.get('cpu', {}).get('usage', 0)
        mem_usage = metrics.get('memory', {}).get('usage_percent', 0)
        disk_usage = 0
        try:
            disk_usage = float(str(metrics.get('disk', {}).get('usage_percent', 0)).replace('%', ''))
        except:
            pass
        
        repair_actions = []
        
        # CPU过高
        if cpu_usage > 85:
            repair_actions.append({
                'action': 'reduce_cpu_load',
                'reason': f'CPU使用率过高: {cpu_usage:.1f}%'
            })
        
        # 内存过高
        if mem_usage > 85:
            repair_actions.append({
                'action': 'optimize_memory',
                'reason': f'内存使用率过高: {mem_usage:.1f}%'
            })
        
        # 磁盘过高
        if disk_usage > 90:
            repair_actions.append({
                'action': 'cleanup_disk',
                'reason': f'磁盘使用率过高: {disk_usage:.1f}%'
            })
        
        # 执行修复操作
        for action_info in repair_actions:
            result = server.execute_repair(action_info['action'])
            result['reason'] = action_info['reason']
            repair_results.append(result)
        
        # 如果没有需要修复的问题
        if not repair_actions:
            repair_results.append({
                'action': 'no_action_needed',
                'success': True,
                'message': '服务器状态良好，无需修复',
                'metrics': {
                    'cpu': cpu_usage,
                    'memory': mem_usage,
                    'disk': disk_usage
                }
            })
        
        return jsonify({
            'success': True,
            'server_id': server_id,
            'metrics': metrics,
            'repair_actions': repair_actions,
            'results': repair_results,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/real/server/<server_id>/logs', methods=['GET'])
def get_real_server_logs(server_id):
    """获取真实服务器操作日志"""
    try:
        limit = int(request.args.get('limit', 20))
        server = get_real_server(server_id)
        
        if not server:
            return jsonify({
                'success': False,
                'message': f'服务器 {server_id} 不存在'
            }), 404
        
        logs = server.get_operation_logs(limit)
        
        return jsonify({
            'success': True,
            'server_id': server_id,
            'logs': logs
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/real/batch-repair', methods=['POST'])
def batch_repair_real_servers():
    """批量修复多台真实服务器"""
    try:
        data = request.get_json() or {}
        server_ids = data.get('server_ids', [])
        action = data.get('action', 'status_check')
        params = data.get('params', {})
        
        manager = get_real_server_manager()
        
        if not server_ids:
            # 如果没有指定，获取所有服务器
            server_ids = [s['server_id'] for s in manager.get_all_servers()]
        
        results = []
        for server_id in server_ids:
            server = manager.get_server(server_id)
            if server:
                result = server.execute_repair(action, params)
                results.append(result)
        
        return jsonify({
            'success': True,
            'repaired_count': len(results),
            'results': results,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


# 注意：/api/all/servers 路由在下方的 Agent服务器管理 部分定义（包含虚拟+网络监控+Agent）


# ==================== 脚本配置管理 ====================

@app.route('/api/scripts/config', methods=['GET'])
def get_scripts_config():
    """获取脚本配置"""
    try:
        config = load_scripts_config()
        return jsonify({
            'success': True,
            'config': config,
            'timestamp': get_beijing_time().isoformat()
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


_DANGEROUS_SCRIPT_PATTERNS = [
    # 客户的 rm（含删除 Agent 自身目录用于卸载）合法允许，只挡"宕机/丢盘"操作
    (r'rm\s+-rf?\s+/(\s|$)',                       '禁止 rm -rf / — 这会删整台机器'),
    (r'rm\s+-rf?\s+/\*',                           '禁止 rm -rf /* — 等同于删根目录'),
    (r'rm\s+-rf?\s+/(etc|var|usr|bin|sbin|boot|lib|lib64|root|dev|proc|sys|run)(\s|/|$)',
                                                    '禁止删除系统关键目录（/etc /var /usr /bin /boot /lib 等），会导致 Agent 服务器宕机'),
    (r'\b(shutdown|halt|poweroff|reboot)\b',       '禁止关机/重启命令 — 会让 Agent 服务器宕机失联'),
    (r'\binit\s+[06]\b',                           '禁止 init 0/6'),
    (r'\bsystemctl\s+(poweroff|halt|reboot)',      '禁止 systemctl 关机/重启'),
    (r'\bmkfs\.',                                  '禁止 mkfs 格式化磁盘'),
    (r'dd\s+if=\S+\s+of=/dev/[sh]d',               '禁止 dd 写物理磁盘 — 会损坏整盘'),
    (r':\(\)\s*\{\s*:\|:&?\s*\}\s*;:',             '禁止 fork 炸弹'),
]

def _scan_dangerous_script(scripts_payload):
    """扫描脚本配置 payload，发现危险命令返回 (script_name, reason)；安全则返回 None。"""
    import re
    for name, conf in (scripts_payload or {}).items():
        if not isinstance(conf, dict):
            continue
        candidate_texts = []
        if conf.get('use_custom_script') and conf.get('custom_script_path'):
            candidate_texts.append(conf['custom_script_path'])
        for c in (conf.get('commands') or []):
            candidate_texts.append(c)
        joined = '\n'.join(candidate_texts)
        for pat, reason in _DANGEROUS_SCRIPT_PATTERNS:
            if re.search(pat, joined, re.IGNORECASE | re.MULTILINE):
                return name, reason
    return None


@app.route('/api/scripts/config', methods=['POST'])
def update_scripts_config():
    """更新脚本配置"""
    try:
        data = request.json

        if not data:
            return jsonify({
                'success': False,
                'message': '请提供配置数据'
            }), 400

        # v5.57: 保存前扫描危险命令，阻止把"自杀"脚本写进 JSON
        if 'scripts' in data:
            hit = _scan_dangerous_script(data['scripts'])
            if hit:
                hit_name, hit_reason = hit
                return jsonify({
                    'success': False,
                    'blocked': True,
                    'message': f'脚本「{hit_name}」包含危险命令，已被拦截：{hit_reason}',
                    'hint': '请修改脚本去掉这条命令。常见安全替代：\n'
                            '• 想"清理临时文件"：rm -rf /tmp/myapp/* 而不是 rm -rf /\n'
                            '• 想"重启服务"：systemctl restart 服务名，不要写 reboot/shutdown\n'
                            '• 想"删除某个目录"：rm -rf 具体路径，不要删 /home 整体或 Agent 自己'
                }), 400

        # 加载现有配置
        config = load_scripts_config()

        # 更新配置
        if 'scripts' in data:
            if 'scripts' not in config:
                config['scripts'] = {}
            config['scripts'].update(data['scripts'])
        
        if 'service_commands' in data:
            if 'service_commands' not in config:
                config['service_commands'] = {}
            config['service_commands'].update(data['service_commands'])
        
        if 'global_settings' in data:
            if 'global_settings' not in config:
                config['global_settings'] = {}
            config['global_settings'].update(data['global_settings'])
        
        config['last_updated'] = get_beijing_time().strftime('%Y-%m-%d %H:%M:%S')
        
        if save_scripts_config(config):
            # 同步配置到所有Agent服务器
            try:
                from services.agent_manager import agent_manager
                sync_results = []
                for server_id, server in agent_manager.servers.items():
                    try:
                        import requests
                        url = f"http://{server['ip']}:{server['port']}/config"
                        headers = {'X-Agent-Token': server['token'], 'Content-Type': 'application/json'}
                        resp = requests.post(url, headers=headers, json={'scripts': data.get('scripts', {})}, timeout=10)
                        sync_results.append({'server': server_id, 'success': resp.status_code == 200})
                    except Exception as sync_err:
                        sync_results.append({'server': server_id, 'success': False, 'error': str(sync_err)})
            except Exception as e:
                pass  # 同步失败不影响保存
            # 生成同步结果消息
            synced = [r for r in sync_results if r.get('success')]
            sync_msg = f"脚本配置已更新，已同步到 {len(synced)}/{len(sync_results)} 个Agent服务器" if sync_results else "脚本配置已更新"
            return jsonify({
                'success': True,
                'message': sync_msg,
                'sync_results': sync_results,
                'timestamp': get_beijing_time().isoformat()
            })
        else:
            return jsonify({
                'success': False,
                'message': '保存配置失败'
            }), 500
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/scripts/<script_name>', methods=['GET'])
def get_script_detail(script_name):
    """获取单个脚本的详细配置"""
    try:
        config = load_scripts_config()
        scripts = config.get('scripts', {})
        
        if script_name in scripts:
            return jsonify({
                'success': True,
                'script_name': script_name,
                'config': scripts[script_name],
                'timestamp': get_beijing_time().isoformat()
            })
        else:
            return jsonify({
                'success': False,
                'message': f'脚本 {script_name} 未配置'
            }), 404
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/scripts/<script_name>', methods=['PUT'])
def update_script_detail(script_name):
    """更新单个脚本配置"""
    try:
        data = request.json
        
        if not data:
            return jsonify({
                'success': False,
                'message': '请提供脚本配置'
            }), 400
        
        config = load_scripts_config()
        if 'scripts' not in config:
            config['scripts'] = {}
        
        # 更新脚本配置
        if script_name not in config['scripts']:
            config['scripts'][script_name] = {}
        
        config['scripts'][script_name].update(data)
        config['last_updated'] = get_beijing_time().strftime('%Y-%m-%d %H:%M:%S')
        
        if save_scripts_config(config):
            return jsonify({
                'success': True,
                'message': f'脚本 {script_name} 配置已更新',
                'timestamp': get_beijing_time().isoformat()
            })
        else:
            return jsonify({
                'success': False,
                'message': '保存配置失败'
            }), 500
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@app.route('/api/services/commands', methods=['GET'])
def get_service_commands():
    """获取服务命令模板"""
    try:
        config = load_scripts_config()
        service_commands = config.get('service_commands', {})
        
        return jsonify({
            'success': True,
            'service_commands': service_commands,
            'timestamp': get_beijing_time().isoformat()
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


# ==================== 网络监控API ====================

@app.route('/api/monitor/servers', methods=['GET'])
def get_monitored_servers():
    """获取所有被监控服务器"""
    try:
        manager = get_network_monitor_manager()
        servers = manager.get_all_servers()
        
        return jsonify({
            'success': True,
            'count': len(servers),
            'servers': servers
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/monitor/add-server', methods=['POST'])
def add_monitored_server():
    """添加被监控服务器"""
    try:
        data = request.get_json() or {}
        ip = data.get('ip', '').strip()
        name = data.get('name', '').strip() or None
        ports = data.get('ports', [22, 80, 443, 3306, 8080])
        http_endpoints = data.get('http_endpoints', [])
        
        if not ip:
            return jsonify({'success': False, 'message': 'IP地址不能为空'}), 400
        
        manager = get_network_monitor_manager()
        result = manager.add_server(ip, name, ports, http_endpoints)
        # 镜像到 MySQL
        if result.get('success') and result.get('server'):
            srv = result['server']
            _mirror_server_to_mysql({
                'server_id': srv.get('id') or srv.get('server_id'),
                'server_name': srv.get('name'),
                'ip': srv.get('ip'),
                'type': 'monitor',
                'system': srv.get('system', 'default'),
                'status': srv.get('status', 'unknown'),
                'ports': ports,
                'http_endpoints': http_endpoints,
            })
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/monitor/remove-server/<server_id>', methods=['DELETE'])
def remove_monitored_server(server_id):
    """删除被监控服务器"""
    try:
        manager = get_network_monitor_manager()
        result = manager.remove_server(server_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/monitor/server/<server_id>', methods=['GET'])
def get_monitored_server_status(server_id):
    """获取单台服务器状态"""
    try:
        manager = get_network_monitor_manager()
        server = manager.get_server(server_id)
        
        if not server:
            return jsonify({'success': False, 'message': '服务器不存在'}), 404
        
        return jsonify({
            'success': True,
            'server': server.get_status()
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/monitor/server/<server_id>/check', methods=['POST'])
def check_monitored_server(server_id):
    """手动触发服务器检查"""
    try:
        manager = get_network_monitor_manager()
        result = manager.check_server(server_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/monitor/server/<server_id>/alerts', methods=['GET'])
def get_monitored_server_alerts(server_id):
    """获取服务器告警"""
    try:
        manager = get_network_monitor_manager()
        server = manager.get_server(server_id)
        
        if not server:
            return jsonify({'success': False, 'message': '服务器不存在'}), 404
        
        return jsonify({
            'success': True,
            'alerts': server.alerts
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/monitor/server/<server_id>/ports', methods=['PUT'])
def update_monitored_server_ports(server_id):
    """更新服务器监控端口"""
    try:
        data = request.get_json() or {}
        ports = data.get('ports', [])
        
        manager = get_network_monitor_manager()
        result = manager.update_server_ports(server_id, ports)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/monitor/server/<server_id>/http', methods=['PUT'])
def update_monitored_server_http(server_id):
    """更新服务器HTTP端点"""
    try:
        data = request.get_json() or {}
        endpoints = data.get('endpoints', [])
        
        manager = get_network_monitor_manager()
        result = manager.update_server_http(server_id, endpoints)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/monitor/test-connection', methods=['POST'])
def test_monitor_connection():
    """测试连接（不添加）"""
    try:
        data = request.get_json() or {}
        ip = data.get('ip', '').strip()
        ports = data.get('ports', [22, 80, 443])
        
        if not ip:
            return jsonify({'success': False, 'message': 'IP地址不能为空'}), 400
        
        manager = get_network_monitor_manager()
        result = manager.test_connection(ip, ports)
        
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/all/servers', methods=['GET'])
def get_all_servers_combined():
    """获取所有服务器（虚拟+网络监控+Agent）"""
    try:
        # 获取虚拟服务器
        cluster = get_virtual_cluster()
        virtual_servers = cluster.get_all_servers()
        
        # 获取网络监控服务器
        monitor_manager = get_network_monitor_manager()
        monitored_servers = monitor_manager.get_all_servers()
        
        # 获取Agent服务器
        agent_servers = agent_manager.list_servers()
        
        return jsonify({
            'success': True,
            'virtual_servers': virtual_servers,
            'virtual_count': len(virtual_servers),
            'monitored_servers': monitored_servers,
            'monitored_count': len(monitored_servers),
            'agent_servers': agent_servers,
            'real_servers': agent_servers,  # 兼容旧前端
            'agent_count': len(agent_servers),
            'total_count': len(virtual_servers) + len(monitored_servers) + len(agent_servers)
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500


# ==================== Agent服务器管理 ====================

@app.route('/api/agent/servers', methods=['GET'])
def get_agent_servers():
    """获取所有Agent服务器"""
    try:
        servers = agent_manager.list_servers()
        return jsonify({
            'success': True,
            'servers': servers,
            'count': len(servers)
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/agent/add-server', methods=['POST'])
def add_agent_server():
    """添加Agent服务器"""
    try:
        data = request.get_json() or {}
        ip = data.get('ip', '').strip()
        name = data.get('name', '').strip()
        port = int(data.get('port', 8089))
        token = data.get('token', 'CHANGE_ME_AGENT_TOKEN')
        system = data.get('system', 'default')

        if not ip:
            return jsonify({'success': False, 'message': 'IP地址不能为空'}), 400

        result = agent_manager.add_server(ip=ip, name=name, port=port, token=token, system=system)
        # 镜像到 MySQL
        if result.get('success') and result.get('server'):
            srv = result['server']
            _mirror_server_to_mysql({
                'server_id': srv.get('id') or srv.get('server_id'),
                'server_name': srv.get('name'),
                'ip': srv.get('ip'),
                'port': srv.get('port'),
                'type': 'agent',
                'system': srv.get('system', 'default'),
                'token': srv.get('token'),
                'status': srv.get('status', 'unknown'),
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/agent/remove-server/<server_id>', methods=['DELETE'])
def remove_agent_server(server_id):
    """移除Agent服务器"""
    try:
        result = agent_manager.remove_server(server_id)
        # v5.41: 同步从 MySQL 删除，避免客户库里残留已移除的 agent
        if result.get('success') and is_mysql_enabled():
            try:
                db = get_mysql_db()
                if db:
                    db.execute("DELETE FROM xzyw_servers WHERE server_id=%s", (server_id,), commit=True)
                    print(f'[Mirror] 已从 xzyw_servers 删除 server_id={server_id}', flush=True)
            except Exception as _e:
                print(f'[Mirror] 删除 MySQL 镜像失败 server_id={server_id}: {_e}', flush=True)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/agent/server/<server_id>', methods=['GET'])
def get_agent_server_detail(server_id):
    """获取Agent服务器详情"""
    try:
        server = agent_manager.get_server(server_id)
        if not server:
            return jsonify({'success': False, 'message': '服务器不存在'}), 404
        return jsonify({'success': True, 'server': server})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/agent/server/<server_id>/status', methods=['GET'])
def get_agent_server_status(server_id):
    """获取Agent服务器实时状态"""
    try:
        result = agent_manager.get_server_status(server_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/agent/server/<server_id>/metrics-history', methods=['GET'])
def get_agent_server_history(server_id):
    """获取Agent服务器历史监控数据（用于图表展示）"""
    try:
        minutes = request.args.get('minutes', 30, type=int)
        result = agent_manager.get_server_history(server_id, minutes)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500



@app.route('/api/agent/server/<server_id>/repair', methods=['POST'])
def agent_server_repair(server_id):
    """执行Agent服务器修复"""
    try:
        data = request.get_json() or {}
        action = data.get('action', 'auto')
        params = data.get('params', {})

        result = agent_manager.execute_repair(server_id, action=action, params=params)
        # 镜像修复记录到 MySQL
        status = 'success' if result.get('success') else 'failed'
        _mirror_repair_to_mysql(server_id, action, status,
                                result=str(result.get('message', ''))[:500],
                                triggered_by=data.get('triggered_by', 'manual'))
        return jsonify(result)
    except Exception as e:
        _mirror_repair_to_mysql(server_id, data.get('action', 'auto') if 'data' in dir() else 'auto',
                                'failed', result=str(e)[:500], triggered_by='manual')
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/agent/server/<server_id>/script', methods=['POST'])
def agent_server_script(server_id):
    """执行Agent服务器脚本"""
    try:
        data = request.get_json() or {}
        script_name = data.get('script_name', '')
        
        if not script_name:
            return jsonify({'success': False, 'message': '脚本名称不能为空'}), 400
        
        agent_result = agent_manager.execute_script(server_id, script_name)
        # 转换为前端期望的格式
        if agent_result.get("success"):
            return jsonify({
                "success": True,
                "result": {
                    "action": script_name,
                    "success": True,
                    "message": f"脚本 {script_name} 执行成功",
                    "duration": agent_result.get("duration", 0),
                    "steps": [{"step": i+1, "action": r.get("command", ""), "command": r.get("command", ""), "status": "completed" if r.get("success") else "failed", "message": r.get("output", r.get("error", ""))} for i, r in enumerate(agent_result.get("results", []))],
                    "output": [r.get("output", "") for r in agent_result.get("results", []) if r.get("output")]
                }
            })
        else:
            return jsonify(agent_result)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/agent/test-connection', methods=['POST'])
def test_agent_connection():
    """测试Agent连接"""
    try:
        data = request.get_json() or {}
        ip = data.get('ip', '').strip()
        port = int(data.get('port', 8089))
        token = data.get('token', 'CHANGE_ME_AGENT_TOKEN')
        
        if not ip:
            return jsonify({'success': False, 'message': 'IP地址不能为空'}), 400
        
        result = agent_manager.test_connection(ip, port, token)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/agent/server/<server_id>/auto-repair', methods=['POST'])
def agent_auto_repair(server_id):
    """Agent服务器自动修复"""
    try:
        result = agent_manager.auto_repair(server_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/agent/server/<server_id>/inject-fault', methods=['POST'])
def agent_inject_fault(server_id):
    """v5.50: 向真实 Agent 服务器注入故障（CPU/内存/IO 真实过载），用于"故障注入演示"。"""
    try:
        data = request.get_json() or {}
        fault_type = data.get('fault_type', 'cpu_overload')
        duration = int(data.get('duration', 180))
        intensity = data.get('intensity') or {}
        result = agent_manager.inject_fault(server_id, fault_type, duration, intensity)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/agent/server/<server_id>/detect-faults', methods=['GET'])
def detect_agent_faults(server_id):
    """检测Agent服务器故障"""
    try:
        result = agent_manager.detect_faults(server_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/agent/faulty-servers', methods=['GET'])
def get_agent_faulty_servers():
    """获取所有故障的Agent服务器"""
    try:
        faulty = agent_manager.get_faulty_servers()
        return jsonify({
            'success': True,
            'faulty_servers': faulty,
            'count': len(faulty),
            'timestamp': get_beijing_time().isoformat()
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/agent/server/<server_id>/smart-repair', methods=['POST'])
def agent_smart_repair(server_id):
    """Agent智能修复（先检测后修复）"""
    try:
        result = agent_manager.smart_auto_repair(server_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500




# ==================== 模型配置API ====================

@app.route('/api/model/config', methods=['GET'])
def get_model_config():
    """获取当前模型配置"""
    try:
        runtime_config = get_runtime_config()
        config = runtime_config.get_current_config()
        return jsonify({
            'success': True,
            'config': config
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/model/config', methods=['POST'])
def set_model_config():
    """设置模型配置"""
    try:
        data = request.get_json() or {}
        runtime_config = get_runtime_config()

        # 更新模型类型
        if 'model_type' in data:
            runtime_config.model_type = data['model_type']

        # 更新LLM提供商
        if 'llm_provider' in data:
            runtime_config.llm_provider = data['llm_provider']

        # 更新API密钥
        if 'api_key' in data and 'provider' in data:
            runtime_config.set_api_key(data['provider'], data['api_key'])

        return jsonify({
            'success': True,
            'message': '配置已更新',
            'config': runtime_config.get_current_config()
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/model/test', methods=['POST'])
def test_llm_connection():
    """测试LLM连接"""
    try:
        runtime_config = get_runtime_config()

        if runtime_config.model_type != 'llm':
            return jsonify({
                'success': False,
                'message': '当前使用本地模型，无需测试连接'
            })

        # 尝试导入并测试LLM客户端
        try:
            from models.llm_models import LLMClient
            config = runtime_config.get_active_llm_config()

            if not config.get('api_key'):
                return jsonify({
                    'success': False,
                    'message': f'未配置 {runtime_config.llm_provider} 的API密钥'
                })

            client = LLMClient()
            # 发送简单测试消息
            response = client.chat([{"role": "user", "content": "你好，请简短回复"}])

            if response and '[LLM服务暂不可用]' not in response and '[LLM不可用]' not in response:
                return jsonify({
                    'success': True,
                    'message': '连接成功',
                    'provider': runtime_config.llm_provider,
                    'model': config.get('model', ''),
                    'api_base': config.get('api_base', ''),
                    'response_preview': response[:100] + '...' if len(response) > 100 else response
                })
            else:
                return jsonify({
                    'success': False,
                    'message': 'LLM连接失败',
                    'detail': response,
                    'debug_cmd': f"curl -X POST {config.get('api_base', '')}/chat/completions -H 'Authorization: Bearer {config.get('api_key', '')[:8]}...' -H 'Content-Type: application/json' -d '{{\"model\":\"{config.get('model', '')}\",\"messages\":[{{\"role\":\"user\",\"content\":\"test\"}}]}}'"
                })
        except ImportError:
            return jsonify({
                'success': False,
                'message': 'LLM模块未安装'
            })
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'连接失败: {str(e)}'
            })

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ==================== 检修排程API ====================

@app.route('/api/maintenance/schedule', methods=['GET'])
def get_maintenance_schedule():
    """基于服务器健康状态和历史故障生成巡检排程"""
    try:
        # 收集所有服务器
        virtual_cluster = get_virtual_cluster()
        virtual_servers = virtual_cluster.get_all_servers()
        agent_servers = agent_manager.list_servers()
        agent_status_map = agent_manager.get_all_servers_status_fast() if agent_servers else {}

        # 读取修复历史统计故障频率
        repair_service = get_auto_repair_service()
        repair_history = repair_service.get_repair_history(100)
        fault_counts = {}
        for r in repair_history:
            srv_name = r.get('server', '')
            fault_counts[srv_name] = fault_counts.get(srv_name, 0) + 1

        schedule = []
        now = get_beijing_time()

        # Agent 服务器排程
        for srv in agent_servers:
            srv_name = srv.get('name', srv.get('ip', 'Unknown'))
            status_data = agent_status_map.get(srv['id'], {})
            raw = status_data.get('data', {}) if status_data.get('success') else {}
            cpu = raw.get('cpu', {}).get('usage', raw.get('cpu', {}).get('percent', 0)) or 0
            mem = raw.get('memory', {}).get('percent', 0) or 0
            faults = fault_counts.get(srv_name, 0)

            # 风险评估
            if faults >= 3 or cpu > 80 or mem > 85:
                risk, cycle, days = '高', '每日', 1
            elif faults >= 1 or cpu > 60 or mem > 70:
                risk, cycle, days = '中', '每周', 7
            else:
                risk, cycle, days = '低', '每月', 30

            next_check = (now + timedelta(days=days)).strftime('%Y-%m-%d')
            items = ['系统健康检查', 'CPU/内存/磁盘巡检', '日志异常扫描']
            if risk == '高':
                items += ['进程排查', '安全扫描']
            elif risk == '中':
                items += ['服务状态验证']

            schedule.append({
                'server': srv_name,
                'ip': srv.get('ip', ''),
                'system': srv.get('system', 'default'),
                'type': 'Agent',
                'risk': risk,
                'cycle': cycle,
                'next_check': next_check,
                'fault_count': faults,
                'items': items,
                'cpu': round(float(cpu), 1),
                'memory': round(float(mem), 1)
            })

        # 虚拟服务器排程
        for srv in virtual_servers:
            srv_name = srv.get('server_name', 'Unknown')
            resources = srv.get('resources', {})
            cpu = resources.get('cpu', {}).get('usage', 0)
            mem_used = resources.get('memory', {}).get('used', 0)
            mem_total = resources.get('memory', {}).get('total', 1)
            mem = (mem_used / mem_total * 100) if mem_total > 0 else 0
            faults = fault_counts.get(srv_name, 0)

            if faults >= 3 or cpu > 80 or mem > 85:
                risk, cycle, days = '高', '每日', 1
            elif faults >= 1 or cpu > 60 or mem > 70:
                risk, cycle, days = '中', '每周', 7
            else:
                risk, cycle, days = '低', '每月', 30

            next_check = (now + timedelta(days=days)).strftime('%Y-%m-%d')
            items = ['系统健康检查', 'CPU/内存/磁盘巡检']
            schedule.append({
                'server': srv_name,
                'ip': srv.get('ip', ''),
                'system': srv.get('system', 'default'),
                'type': '虚拟',
                'risk': risk,
                'cycle': cycle,
                'next_check': next_check,
                'fault_count': faults,
                'items': items,
                'cpu': round(cpu, 1),
                'memory': round(mem, 1)
            })

        # 按风险等级排序（高→中→低）
        risk_order = {'高': 0, '中': 1, '低': 2}
        schedule.sort(key=lambda x: risk_order.get(x['risk'], 9))

        return jsonify({'success': True, 'schedule': schedule, 'total': len(schedule)})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ==================== 策略对比API ====================

@app.route('/api/repair/compare-strategies', methods=['GET'])
def compare_repair_strategies():
    """返回指定故障类型的 Top-3 修复策略对比"""
    try:
        fault_type = request.args.get('fault_type', '')
        repair_service = get_auto_repair_service()
        all_strategies = repair_service.get_all_strategies()

        if not fault_type:
            return jsonify({'success': False, 'message': '请提供 fault_type 参数'}), 400

        primary = all_strategies.get(fault_type)
        if not primary:
            return jsonify({'success': False, 'message': f'未找到故障类型: {fault_type}'}), 404

        category = primary.get('category', '')

        # 收集同类别策略
        candidates = []
        for name, strat in all_strategies.items():
            if strat.get('category') == category:
                risk_map = {'auto': '低', 'diagnose': '中', 'manual': '高'}
                candidates.append({
                    'name': name,
                    'description': strat.get('description', ''),
                    'auto_fix': strat.get('auto_fix', False),
                    'repair_type': strat.get('repair_type', 'manual'),
                    'priority': strat.get('priority', 5),
                    'estimated_time': strat.get('estimated_time', 0),
                    'steps': len(strat.get('operations', [])),
                    'risk': risk_map.get(strat.get('repair_type', 'manual'), '高'),
                    'is_primary': name == fault_type
                })

        # 按优先级排序，主策略置顶
        candidates.sort(key=lambda x: (0 if x['is_primary'] else 1, x['priority']))
        top3 = candidates[:3]

        return jsonify({'success': True, 'fault_type': fault_type, 'category': category, 'strategies': top3})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ==================== v5.57: 智能告警聚合 + 根因 + 趋势预测 + 多方案对比 ====================

@app.route('/api/alerts/aggregated', methods=['GET'])
def api_alerts_aggregated():
    """聚合告警列表 — 时间窗内去重，按 (server, 类型) 聚合，附根因分析。

    对应需求 §3.2.1（告警/事件智能分析、根因）
    """
    try:
        # 实时扫描所有 Agent，发现指标超阈值 + 演示标记，全部 ingest 一遍
        agg = get_alert_aggregator()
        for server in agent_manager.list_servers():
            sid = server.get('id') or server.get('server_id')
            sname = server.get('name') or f"Agent-{server.get('ip', '')}"
            try:
                resp = agent_manager.get_server_status(sid)
                if not resp.get('success'):
                    agg.ingest(sid, sname, 'offline', None, 'Agent 离线/不可达', severity='critical')
                    continue
                d = resp.get('data') or {}
                fi = d.get('fault_injected') or {}
                cpu = (d.get('cpu') or {}).get('usage', 0)
                mem = (d.get('memory') or {}).get('percent', 0)
                disk = (d.get('disk') or {}).get('percent', 0)
                # 阈值告警 + 注入告警都收
                if cpu > 80:
                    agg.ingest(sid, sname, 'cpu_high', cpu, f'CPU={cpu:.1f}%',
                               severity='critical' if cpu > 95 else 'warning')
                if mem > 80:
                    agg.ingest(sid, sname, 'memory_high', mem, f'内存={mem:.1f}%',
                               severity='critical' if mem > 95 else 'warning')
                if disk > 85:
                    agg.ingest(sid, sname, 'disk_high', disk, f'磁盘={disk:.1f}%',
                               severity='critical' if disk > 95 else 'warning')
                workers = fi.get('active_workers') or []
                if workers:
                    types = ','.join(set(w.get('fault_type', '?') for w in workers))
                    agg.ingest(sid, sname, 'inject_demo', len(workers),
                               f'故障注入演示 workers 在跑：{types}', severity='critical')
                if fi.get('service_down'):
                    agg.ingest(sid, sname, 'service_down', 1, '服务停止演示标记', severity='critical')
                if fi.get('network_issue'):
                    agg.ingest(sid, sname, 'network_issue', 1, '网络异常演示标记', severity='critical')
            except Exception as e:
                agg.ingest(sid, sname, 'fetch_error', None, str(e)[:200], severity='warning')

        active = agg.list_active(max_age=300)
        # 给每个聚合告警附根因（按 server 分组取最新 status）
        result = []
        per_server_root = {}
        for a in active:
            sid = a['server_id']
            if sid not in per_server_root:
                try:
                    st = agent_manager.get_server_status(sid)
                    per_server_root[sid] = analyze_root_cause(st.get('data') or {})
                except Exception:
                    per_server_root[sid] = {'root_cause': '状态未知', 'severity': 'info'}
            a['root_cause_analysis'] = per_server_root[sid]
            result.append(a)

        return jsonify({
            'success': True,
            'total': len(result),
            'alerts': result,
            'window_seconds': 300,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/alerts/clear', methods=['POST'])
def api_alerts_clear():
    """清空聚合告警缓存（演示重置用）"""
    try:
        get_alert_aggregator().clear()
        return jsonify({'success': True, 'message': '告警缓存已清空'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/predict/trend/<server_id>', methods=['GET'])
def api_predict_trend(server_id):
    """设备健康度趋势预测 — 未来 24h 风险评分 + 预测曲线

    对应需求 §3.1.1（状态推演）+ §3.1.3（故障预测）
    """
    try:
        # 先拉一次最新 status 入历史
        st = agent_manager.get_server_status(server_id)
        if st.get('success') and st.get('data'):
            push_trend_metrics(server_id, st['data'])

        horizon = int(request.args.get('horizon', 24))
        result = predict_trend(server_id, horizon_hours=horizon)
        result['success'] = True
        result['server_id'] = server_id
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/repair/strategies', methods=['GET'])
def api_repair_strategies():
    """多方案对比 — 给定故障类型返回 2-3 个候选修复方案，每个附风险/恢复时间/副作用

    对应需求 §3.2.3（方案推演与对比）
    """
    try:
        fault_type = request.args.get('fault_type', '').strip()
        if not fault_type:
            return jsonify({
                'success': True,
                'supported_fault_types': get_supported_fault_types(),
                'message': '请通过 ?fault_type=xxx 指定故障类型'
            })
        strategies = get_strategies(fault_type)
        return jsonify({
            'success': True,
            'fault_type': fault_type,
            'strategies': strategies,
            'count': len(strategies),
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/agent/server/<server_id>/diagnose', methods=['GET'])
def api_agent_diagnose(server_id):
    """v5.57: 机器深度诊断 — 拉 agent /diagnose 看清谁吃了内存"""
    try:
        result = agent_manager.diagnose(server_id)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/sop/recommend/<server_id>', methods=['GET'])
def api_sop_recommend(server_id):
    """SOP 主动推荐 — 根据当前服务器状态推断故障类型并推荐 solutions.json 中的 SOP

    对应需求 §3.2.3（专家知识与 SOP 推荐）
    """
    try:
        st = agent_manager.get_server_status(server_id)
        if not st.get('success') or not st.get('data'):
            return jsonify({'success': True, 'recommendations': [], 'message': 'Agent 状态不可用'})
        data = st['data']
        # 根因分析推断 solution_id
        root = analyze_root_cause(data)
        # 拉知识库 solutions
        from services.knowledge_base import get_knowledge_base
        kb = get_knowledge_base()
        all_solutions = kb.get_solutions() if hasattr(kb, 'get_solutions') else []
        recommended = []
        # 优先推根因匹配的
        if root.get('recommended_solution_id'):
            for s in all_solutions:
                if s.get('id') == root['recommended_solution_id']:
                    recommended.append({**s, '_match_reason': f"根因匹配：{root.get('matched_rule', '')}"})
                    break
        # 再按当前指标补充推荐
        fi = data.get('fault_injected') or {}
        cpu = (data.get('cpu') or {}).get('usage', 0)
        mem = (data.get('memory') or {}).get('percent', 0)
        disk = (data.get('disk') or {}).get('percent', 0)
        for s in all_solutions:
            sid = s.get('id', '')
            if sid in [r.get('id') for r in recommended]:
                continue
            reason = None
            if sid == 'cpu_high' and cpu > 70:
                reason = f'CPU={cpu:.1f}%'
            elif sid == 'memory_leak' and mem > 70:
                reason = f'内存={mem:.1f}%'
            elif sid == 'disk_full' and disk > 80:
                reason = f'磁盘={disk:.1f}%'
            if reason:
                recommended.append({**s, '_match_reason': reason})
            if len(recommended) >= 3:
                break

        return jsonify({
            'success': True,
            'server_id': server_id,
            'root_cause': root,
            'recommendations': recommended,
            'current_metrics': {'cpu': cpu, 'memory': mem, 'disk': disk,
                                'fault_injected': fi},
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500


# ==================== 效果评估API ====================

@app.route('/api/dashboard/effectiveness', methods=['GET'])
def get_effectiveness_dashboard():
    """运维效能 KPI 统计"""
    try:
        repair_service = get_auto_repair_service()
        op_service = get_operation_service()
        repair_history = repair_service.get_repair_history(100)
        op_history = op_service.get_operation_history(100)

        # 总修复次数
        total_repairs = len(repair_history)

        # 修复成功率
        success_count = sum(1 for r in repair_history if r.get('result') == 'success' or r.get('status') == 'success' or r.get('auto_fixed', 0) > 0)
        failed_count = sum(1 for r in repair_history if r.get('result') == 'failed' or r.get('status') == 'failed')
        success_rate = round(success_count / total_repairs * 100, 1) if total_repairs > 0 else 0

        # 平均修复时间 (MTTR)
        times = [r.get('estimated_time', r.get('duration', 0)) for r in repair_history if r.get('estimated_time') or r.get('duration')]
        avg_mttr = round(sum(times) / len(times), 1) if times else 0

        # 故障类型分布 top-10
        fault_dist = {}
        for r in repair_history:
            ft = r.get('fault_type', r.get('type', '未知'))
            if isinstance(ft, list):
                for f in ft:
                    fault_dist[f] = fault_dist.get(f, 0) + 1
            else:
                fault_dist[ft] = fault_dist.get(ft, 0) + 1
        fault_top10 = sorted(fault_dist.items(), key=lambda x: x[1], reverse=True)[:10]

        # 操作类型频次 top-5
        op_freq = {}
        for o in op_history:
            op_type = o.get('operation', '未知')
            op_freq[op_type] = op_freq.get(op_type, 0) + 1
        op_top5 = sorted(op_freq.items(), key=lambda x: x[1], reverse=True)[:5]

        # 分类别 MTTR
        cat_mttr = {}
        cat_count = {}
        for r in repair_history:
            cat = r.get('category', '其他')
            t = r.get('estimated_time', r.get('duration', 0)) or 0
            cat_mttr[cat] = cat_mttr.get(cat, 0) + t
            cat_count[cat] = cat_count.get(cat, 0) + 1
        category_mttr = {k: round(cat_mttr[k] / cat_count[k], 1) for k in cat_mttr if cat_count[k] > 0}

        # 按天趋势（最近30天）
        from collections import defaultdict
        daily = defaultdict(lambda: {'repairs': 0, 'success': 0})
        for r in repair_history:
            ts = r.get('timestamp', '')
            if ts:
                day = ts[:10]
                daily[day]['repairs'] += 1
                if r.get('result') == 'success' or r.get('status') == 'success' or r.get('auto_fixed', 0) > 0:
                    daily[day]['success'] += 1
        trend = [{'date': k, 'repairs': v['repairs'], 'success': v['success']} for k, v in sorted(daily.items())[-30:]]

        return jsonify({
            'success': True,
            'kpi': {
                'total_repairs': total_repairs,
                'success_rate': success_rate,
                'avg_mttr': avg_mttr,
                'total_operations': len(op_history)
            },
            'fault_distribution': [{'name': k, 'value': v} for k, v in fault_top10],
            'operation_frequency': [{'name': k, 'value': v} for k, v in op_top5],
            'category_mttr': [{'name': k, 'value': v} for k, v in category_mttr.items()],
            'trend': trend
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ==================== 运维日志查看（v5.38） ====================

_APP_LOG_PATH = os.path.join(os.path.dirname(__file__), 'logs', 'app.log')


def _tail_lines(path: str, max_lines: int) -> list:
    """读文件末尾 max_lines 行（内存友好，不加载整个文件）"""
    if not os.path.exists(path):
        return []
    try:
        max_lines = max(1, min(int(max_lines), 5000))
    except Exception:
        max_lines = 200
    # 简单实现：按字节倒读。中等日志文件足够快。
    try:
        with open(path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            block = 8192
            data = b''
            pos = file_size
            lines_found = 0
            while pos > 0 and lines_found <= max_lines:
                read_size = min(block, pos)
                pos -= read_size
                f.seek(pos)
                chunk = f.read(read_size)
                data = chunk + data
                lines_found = data.count(b'\n')
            text = data.decode('utf-8', errors='replace')
            lines = text.splitlines()
            return lines[-max_lines:]
    except Exception as _e:
        return [f'[读取日志异常] {_e}']


@app.route('/api/system/logs', methods=['GET'])
def get_system_logs():
    """读平台主日志尾部。

    query: source=app|agent, lines=100..2000
    """
    source = request.args.get('source', 'app')
    lines_arg = request.args.get('lines', '200')
    if source == 'app':
        path = _APP_LOG_PATH
    else:
        return jsonify({'success': False, 'message': f'未知日志源: {source}'}), 400

    lines = _tail_lines(path, lines_arg)
    return jsonify({
        'success': True,
        'source': source,
        'path': path,
        'exists': os.path.exists(path),
        'size_bytes': os.path.getsize(path) if os.path.exists(path) else 0,
        'lines': lines,
        'count': len(lines),
    })


@app.route('/api/system/logs/download', methods=['GET'])
def download_system_logs():
    """下载完整 app.log（内网环境，不加鉴权；生产如外网需加 token）"""
    source = request.args.get('source', 'app')
    if source == 'app':
        path = _APP_LOG_PATH
    else:
        return jsonify({'success': False, 'message': f'未知日志源: {source}'}), 400
    if not os.path.exists(path):
        return jsonify({'success': False, 'message': '日志文件不存在'}), 404
    return send_file(path, as_attachment=True, download_name='app.log',
                     mimetype='text/plain')


# ==================== 启动应用 ====================

if __name__ == '__main__':
    # 确保必要目录存在
    os.makedirs(PathConfig.DATA_DIR, exist_ok=True)
    os.makedirs(PathConfig.RAW_DATA_DIR, exist_ok=True)
    os.makedirs(PathConfig.REPORTS_DIR, exist_ok=True)
    os.makedirs(PathConfig.LOGS_DIR, exist_ok=True)
    os.makedirs(PathConfig.KNOWLEDGE_BASE_DIR, exist_ok=True)
    
    print("=" * 50)
    print("西藏电网智能运维平台 - 7B多模态版")
    print("=" * 50)
    print(f"模型模式: {os.environ.get('MODEL_TYPE', 'llm')}")
    print(f"本地模型: ML+DL (规则分类、LSTM、Transformer)")
    print(f"大模型API: 内网7B多模态 (图文分析)")
    print(f"Agent支持: HTTP调用Agent执行修复")
    print(f"服务地址: http://{ServerConfig.HOST}:{ServerConfig.PORT}")
    print("=" * 50)
    
    app.run(
        host=ServerConfig.HOST,
        port=ServerConfig.PORT,
        debug=ServerConfig.DEBUG
    )
