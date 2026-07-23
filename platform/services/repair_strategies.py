# -*- coding: utf-8 -*-
"""
v5.57: 多方案对比推演
- 给定故障类型，返回 2-3 个候选修复方案
- 每个方案标注：修复 action / 风险等级 / 预计恢复时间 / 副作用 / 适用场景

对应需求文档 §3.2.3（智能辅助决策 - 方案推演与对比）
"""

from typing import Dict, List


# 故障类型 -> 候选方案列表（按推荐优先级排序）
STRATEGY_MATRIX = {
    'cpu_overload': [
        {
            'name': '降低 CPU 负载（推荐）',
            'action': 'fix_high_cpu',
            'risk': 'low',
            'eta_seconds': 5,
            'side_effects': '会 kill 注入演示子进程并对 find/updatedb/backup 类后台进程 renice 到优先级 19',
            'best_for': 'CPU 95%+ 持续高负载，非业务进程吃满',
        },
        {
            'name': '清理缓存释放（保守）',
            'action': 'clear_cache',
            'risk': 'low',
            'eta_seconds': 3,
            'side_effects': '只 sync + drop page cache，不杀进程；CPU 高如果由内存抖动间接导致才有效',
            'best_for': 'CPU 飙高伴随 swap 抖动',
        },
        {
            'name': '自动诊断（最稳妥）',
            'action': 'auto',
            'risk': 'low',
            'eta_seconds': 8,
            'side_effects': '一把清场：杀全部 stress 子进程 + drop_caches + 阈值修复',
            'best_for': '不确定根因，让系统自己决定',
        },
    ],
    'memory_exhaustion': [
        {
            'name': '清理内存释放缓存（推荐）',
            'action': 'optimize_memory',
            'risk': 'low',
            'eta_seconds': 4,
            'side_effects': 'kill 内存注入子进程 + drop_caches + swapoff/swapon',
            'best_for': '内存使用率 95%+，bytearray/缓存类占用',
        },
        {
            'name': '查杀僵尸进程（次选）',
            'action': 'kill_zombie_processes',
            'risk': 'low',
            'eta_seconds': 3,
            'side_effects': '只清 zombie，对真正占用内存的活进程无效',
            'best_for': 'ps 看到大量 defunct 的场景',
        },
        {
            'name': '自动诊断（最稳妥）',
            'action': 'auto',
            'risk': 'low',
            'eta_seconds': 8,
            'side_effects': '一把清场 + drop_caches',
            'best_for': '不确定内存被谁占用',
        },
    ],
    'io_bottleneck': [
        {
            'name': '清理磁盘 IO（推荐）',
            'action': 'cleanup_disk',
            'risk': 'low',
            'eta_seconds': 6,
            'side_effects': 'kill IO 注入子进程 + 删 /tmp 临时文件 + 清 journal + 清 yum/apt cache',
            'best_for': 'IO 95%+ 持续，临时文件/日志堆积',
        },
        {
            'name': '清理缓存（保守）',
            'action': 'clear_cache',
            'risk': 'low',
            'eta_seconds': 3,
            'side_effects': '只 drop page cache，不删文件',
            'best_for': 'IO 高但不想删任何文件',
        },
        {
            'name': '自动诊断',
            'action': 'auto',
            'risk': 'low',
            'eta_seconds': 8,
            'side_effects': '一把清场',
            'best_for': '不确定 IO 高的具体原因',
        },
    ],
    'service_down': [
        {
            'name': '重启服务（推荐）',
            'action': 'restart_service',
            'risk': 'medium',
            'eta_seconds': 10,
            'side_effects': '会中断该服务的当前请求（如果有真实服务），演示场景仅删标记文件',
            'best_for': '服务进程不在/端口不通',
        },
        {
            'name': '自动诊断',
            'action': 'auto',
            'risk': 'low',
            'eta_seconds': 8,
            'side_effects': '清演示标记 + 阈值检查',
            'best_for': '不确定具体故障类型',
        },
    ],
    'network_issue': [
        {
            'name': '重置网络（推荐）',
            'action': 'reset_network',
            'risk': 'medium',
            'eta_seconds': 8,
            'side_effects': '清演示标记 + restart NetworkManager（SLB 转发场景 SSH 会短暂断连几秒）',
            'best_for': '丢包/延迟/连接异常',
        },
        {
            'name': '网卡状态监测（保守）',
            'action': 'check_network_interface',
            'risk': 'low',
            'eta_seconds': 4,
            'side_effects': '只检查不动配置',
            'best_for': '想先了解状况再决定动作',
        },
        {
            'name': '自动诊断',
            'action': 'auto',
            'risk': 'low',
            'eta_seconds': 8,
            'side_effects': '清演示标记',
            'best_for': '不确定网络故障原因',
        },
    ],
}


def get_strategies(fault_type: str) -> List[Dict]:
    """返回某类故障的候选修复方案列表（按推荐优先级排序）。"""
    return STRATEGY_MATRIX.get(fault_type, [
        {
            'name': '自动诊断修复',
            'action': 'auto',
            'risk': 'low',
            'eta_seconds': 8,
            'side_effects': '一把清场：杀全部注入 + drop_caches + 按阈值修复',
            'best_for': '通用兜底方案',
        }
    ])


def get_supported_fault_types() -> List[str]:
    return list(STRATEGY_MATRIX.keys())
