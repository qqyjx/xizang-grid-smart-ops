# -*- coding: utf-8 -*-
"""
v5.57: 智能告警聚合 + 根因分析
- 时间窗内去重 / 关联 / 聚合同源同类型告警
- 基于 agent /status 透出的 fault_injected + 指标 + 知识库做根因推断
- 输出根因结论 + 关联事件 + 严重度

对应需求文档 §3.2.1（告警/事件智能分析）
"""

import time
from collections import defaultdict
from typing import Dict, List


# 简单去重窗口（秒）。同 (server_id, fault_type) 在窗口内只算一条
DEDUP_WINDOW = 60


class AlertAggregator:
    """告警聚合器 — 进程内 in-memory，重启后丢失但不影响业务（演示场景）"""

    def __init__(self):
        # key=(server_id, alert_type) -> {'first': ts, 'last': ts, 'count': int, 'samples': [...]}
        self._buckets: Dict = defaultdict(lambda: {'first': 0, 'last': 0, 'count': 0, 'samples': []})

    def ingest(self, server_id: str, server_name: str, alert_type: str,
               metric_value, message: str, severity: str = 'warning',
               extra: Dict = None) -> Dict:
        """收一条告警，自动按 (server_id, alert_type) 去重聚合。"""
        now = time.time()
        key = (server_id, alert_type)
        b = self._buckets[key]
        if b['count'] == 0 or (now - b['last']) > DEDUP_WINDOW:
            # 新窗口
            b['first'] = now
            b['samples'] = []
        b['last'] = now
        b['count'] += 1
        sample = {
            'ts': now,
            'metric_value': metric_value,
            'message': message,
            'extra': extra or {}
        }
        if len(b['samples']) < 5:
            b['samples'].append(sample)

        return {
            'server_id': server_id,
            'server_name': server_name,
            'alert_type': alert_type,
            'severity': severity,
            'first_seen': b['first'],
            'last_seen': b['last'],
            'duration': round(b['last'] - b['first'], 1),
            'count': b['count'],
            'latest_sample': sample,
        }

    def list_active(self, max_age=300) -> List[Dict]:
        """列出 max_age 秒内活跃告警，按严重度+次数排序。"""
        now = time.time()
        out = []
        for (sid, atype), b in list(self._buckets.items()):
            if now - b['last'] > max_age:
                continue
            out.append({
                'server_id': sid,
                'alert_type': atype,
                'first_seen': b['first'],
                'last_seen': b['last'],
                'duration': round(b['last'] - b['first'], 1),
                'count': b['count'],
                'samples': b['samples'][-3:],
            })
        out.sort(key=lambda x: (-x['count'], -x['last_seen']))
        return out

    def clear(self):
        self._buckets.clear()


_aggregator = AlertAggregator()


def get_aggregator() -> AlertAggregator:
    return _aggregator


# ==================== 根因分析 ====================

# 同一服务器上多个指标同时异常 → 推断根因
ROOT_CAUSE_RULES = [
    # 优先级从高到低
    {
        'name': '故障注入演示',
        'condition': lambda s: bool((s.get('fault_injected') or {}).get('active_workers') or
                                    (s.get('fault_injected') or {}).get('service_down') or
                                    (s.get('fault_injected') or {}).get('network_issue')),
        'root_cause': '当前为故障注入演示状态，所有指标异常均由演示工具触发',
        'recommended_solution_id': None,  # 走 /repair auto
        'severity': 'critical',
    },
    {
        'name': '内存耗尽连锁',
        'condition': lambda s: s.get('memory', {}).get('percent', 0) > 90 and s.get('cpu', {}).get('usage', 0) > 80,
        'root_cause': '内存高 + CPU 高 — 大概率是内存压力触发 swap 抖动或频繁 GC，是内存问题为主，CPU 是连锁现象',
        'recommended_solution_id': 'memory_leak',
        'severity': 'critical',
    },
    {
        'name': '磁盘满连锁',
        'condition': lambda s: s.get('disk', {}).get('percent', 0) > 90,
        'root_cause': '磁盘空间不足 — 优先清理磁盘，否则会引发服务写入失败、日志丢失',
        'recommended_solution_id': 'disk_full',
        'severity': 'critical',
    },
    {
        'name': 'CPU 高负载',
        'condition': lambda s: s.get('cpu', {}).get('usage', 0) > 80,
        'root_cause': 'CPU 持续高负载 — 通常是高并发请求、死循环或定时任务叠加',
        'recommended_solution_id': 'cpu_high',
        'severity': 'warning',
    },
    {
        'name': '内存压力',
        'condition': lambda s: s.get('memory', {}).get('percent', 0) > 80,
        'root_cause': '内存压力较大 — 检查进程是否存在泄漏或缓存未设上限',
        'recommended_solution_id': 'memory_leak',
        'severity': 'warning',
    },
    {
        'name': '连接数偏高',
        'condition': lambda s: (s.get('network') or {}).get('connections', 0) > 500,
        'root_cause': '连接数偏高 — 检查连接是否正确关闭，或后端服务响应是否变慢',
        'recommended_solution_id': 'high_connections',
        'severity': 'warning',
    },
]


def analyze_root_cause(status_data: Dict) -> Dict:
    """根据 agent /status 数据 + 规则推断根因。返回 {root_cause, severity, recommended_solution_id, matched_rule}。"""
    if not status_data:
        return {'root_cause': '无状态数据', 'severity': 'info', 'recommended_solution_id': None}

    for rule in ROOT_CAUSE_RULES:
        try:
            if rule['condition'](status_data):
                return {
                    'root_cause': rule['root_cause'],
                    'severity': rule['severity'],
                    'recommended_solution_id': rule.get('recommended_solution_id'),
                    'matched_rule': rule['name'],
                }
        except Exception:
            continue

    return {
        'root_cause': '指标在正常范围内，未发现明显故障',
        'severity': 'info',
        'recommended_solution_id': None,
        'matched_rule': None,
    }
