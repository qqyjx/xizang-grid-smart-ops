# -*- coding: utf-8 -*-
"""
v5.57: 设备健康度趋势预测
- 基于历史 N 分钟指标的简单线性回归 + 移动平均
- 输出未来 24h 的 CPU/内存/磁盘 预测点 + "风险评分"

对应需求文档 §3.1.1（状态推演与预测）和 §3.1.3（故障预测）

工程化策略：演示环境不强求高精度，只需"能给出趋势曲线 + 风险评分"
"""

from typing import Dict, List
from collections import deque


# 进程内简易历史缓存。生产场景应改为 MySQL 时序存储。
# key=server_id -> deque[{ts, cpu, mem, disk}]
_history = {}
_MAX_HISTORY = 720  # 720 个采样点 = 1h（5s/点）


def push_metrics(server_id: str, metrics: Dict):
    """每次平台拉到 agent /status 时调用。"""
    if server_id not in _history:
        _history[server_id] = deque(maxlen=_MAX_HISTORY)
    cpu = (metrics.get('cpu') or {}).get('usage', 0)
    mem = (metrics.get('memory') or {}).get('percent', 0)
    disk = (metrics.get('disk') or {}).get('percent', 0)
    import time
    _history[server_id].append({
        'ts': time.time(),
        'cpu': cpu, 'mem': mem, 'disk': disk
    })


def _linear_fit(values: List[float]):
    """对一维序列做最小二乘线性拟合，返回 (slope, intercept)。"""
    n = len(values)
    if n < 2:
        return 0.0, values[0] if values else 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (values[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    slope = num / den if den != 0 else 0.0
    intercept = y_mean - slope * x_mean
    return slope, intercept


def predict_trend(server_id: str, horizon_hours: int = 24) -> Dict:
    """基于当前历史预测未来 horizon_hours 小时的曲线 + 风险评分。

    返回 {
      'history_points': [{ts, cpu, mem, disk}],
      'forecast_points': [{ts, cpu, mem, disk}],
      'risk_score': 0-100,
      'risk_level': 'low|warning|critical',
      'risk_reasons': [...]
    }
    """
    import time
    if server_id not in _history or len(_history[server_id]) < 5:
        return {
            'history_points': [],
            'forecast_points': [],
            'risk_score': 0,
            'risk_level': 'unknown',
            'risk_reasons': ['历史数据不足（至少需要 5 个采样点，约 25 秒）'],
        }

    points = list(_history[server_id])
    cpus = [p['cpu'] for p in points]
    mems = [p['mem'] for p in points]
    disks = [p['disk'] for p in points]

    # v5.59: 按真实时间外推（旧版 step_per_hour=720 写死，与实际稀疏采样不符致 24h 曲线撞 0/100）
    # 用相邻点 ts 差中位数估每小时采样点数；首小时即接近边界的退化场景由 [0,100] 钳位兜底
    cpu_slope, cpu_intercept = _linear_fit(cpus)
    mem_slope, mem_intercept = _linear_fit(mems)
    disk_slope, disk_intercept = _linear_fit(disks)

    base_ts = points[-1]['ts']
    n_now = len(points) - 1
    # 计算相邻点 ts 差的中位数（秒），推出每小时实际采样点数
    if len(points) >= 2:
        dts = sorted(points[i]['ts'] - points[i - 1]['ts'] for i in range(1, len(points)))
        median_dt = dts[len(dts) // 2]
        if median_dt <= 0:
            median_dt = 5.0  # 兜底：5s/点
    else:
        median_dt = 5.0
    pts_per_hour = max(1.0, 3600.0 / median_dt)

    forecast = []
    for h in range(1, horizon_hours + 1):
        idx = n_now + h * pts_per_hour
        cpu_pred = max(0, min(100, cpu_slope * idx + cpu_intercept))
        mem_pred = max(0, min(100, mem_slope * idx + mem_intercept))
        disk_pred = max(0, min(100, disk_slope * idx + disk_intercept))
        forecast.append({
            'ts': base_ts + h * 3600,
            'cpu': round(cpu_pred, 1),
            'mem': round(mem_pred, 1),
            'disk': round(disk_pred, 1),
            'hour_offset': h,
        })

    # 风险评分：max(预测 24h 内任意时刻 CPU/Mem/Disk 是否超阈值)
    max_cpu = max(p['cpu'] for p in forecast)
    max_mem = max(p['mem'] for p in forecast)
    max_disk = max(p['disk'] for p in forecast)

    risk_reasons = []
    score = 0
    if max_cpu > 90:
        risk_reasons.append(f'24h 内 CPU 预测峰值 {max_cpu:.0f}% 可能超 90%'); score = max(score, 80)
    elif max_cpu > 70:
        risk_reasons.append(f'24h 内 CPU 预测峰值 {max_cpu:.0f}% 偏高'); score = max(score, 50)
    if max_mem > 90:
        risk_reasons.append(f'24h 内内存预测峰值 {max_mem:.0f}% 可能超 90%'); score = max(score, 85)
    elif max_mem > 70:
        risk_reasons.append(f'24h 内内存预测峰值 {max_mem:.0f}% 偏高'); score = max(score, 50)
    if max_disk > 90:
        risk_reasons.append(f'24h 内磁盘预测峰值 {max_disk:.0f}% 可能超 90%'); score = max(score, 90)
    elif max_disk > 80:
        risk_reasons.append(f'24h 内磁盘预测峰值 {max_disk:.0f}% 偏高'); score = max(score, 55)

    if score == 0:
        risk_reasons.append('未来 24h 各项指标预计平稳，无明显风险')
        level = 'low'
    elif score < 60:
        level = 'warning'
    else:
        level = 'critical'

    # 抽样输出历史点（不全送，最多 60 点）
    if len(points) > 60:
        step = len(points) // 60
        history_out = points[::step][-60:]
    else:
        history_out = points

    return {
        'history_points': [
            {'ts': p['ts'], 'cpu': round(p['cpu'], 1),
             'mem': round(p['mem'], 1), 'disk': round(p['disk'], 1)}
            for p in history_out
        ],
        'forecast_points': forecast,
        'risk_score': score,
        'risk_level': level,
        'risk_reasons': risk_reasons,
        'trend': {
            'cpu_slope_per_sample': round(cpu_slope, 4),
            'mem_slope_per_sample': round(mem_slope, 4),
            'disk_slope_per_sample': round(disk_slope, 4),
        }
    }
