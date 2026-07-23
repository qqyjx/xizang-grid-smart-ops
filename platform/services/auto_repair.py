# -*- coding: utf-8 -*-
"""
自动修复服务
用于检测运行监控数据中的故障并自动执行修复操作
完整版 - 包含大模型生成的详细修复策略
"""

import os
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PathConfig
from services.data_analyzer import get_data_analyzer
from services.operations import get_operation_service

# 北京时间
BEIJING_TZ = timezone(timedelta(hours=8))

# 上传故障数据的文件存储路径
_UPLOADED_FAULT_FILE = os.path.join(PathConfig.LOGS_DIR, 'uploaded_fault.json')


def _get_empty_fault_data():
    """返回空的故障数据结构"""
    return {
        'data': None,
        'fault_detection': None,
        'upload_time': None,
        'server': None,
        'data_type': None
    }


def store_uploaded_fault(server: str, data_type: str, fault_detection: Dict, raw_analysis: Dict = None):
    """存储上传的故障数据，供自动修复模块使用"""
    try:
        os.makedirs(os.path.dirname(_UPLOADED_FAULT_FILE), exist_ok=True)
        
        fault_data = {
            'data': raw_analysis,
            'fault_detection': fault_detection,
            'upload_time': datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S'),
            'server': server,
            'data_type': data_type
        }
        
        with open(_UPLOADED_FAULT_FILE, 'w', encoding='utf-8') as f:
            json.dump(fault_data, f, ensure_ascii=False, indent=2)
            
        print(f"[AutoRepair] 已存储上传故障数据: {server} - {data_type}")
    except Exception as e:
        print(f"[AutoRepair] 存储上传故障数据失败: {e}")


def get_uploaded_fault() -> Dict:
    """获取最近上传的故障数据"""
    try:
        if os.path.exists(_UPLOADED_FAULT_FILE):
            with open(_UPLOADED_FAULT_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if data and data.get('fault_detection'):
                    return data
        return _get_empty_fault_data()
    except Exception as e:
        print(f"[AutoRepair] 读取上传故障数据失败: {e}")
        return _get_empty_fault_data()


def clear_uploaded_fault():
    """清除上传的故障数据"""
    try:
        if os.path.exists(_UPLOADED_FAULT_FILE):
            os.remove(_UPLOADED_FAULT_FILE)
            print("[AutoRepair] 已清除上传故障数据")
    except Exception as e:
        print(f"[AutoRepair] 清除上传故障数据失败: {e}")


# 导入ML/DL修复决策模型
try:
    from models.repair_decision_model import get_repair_decision_maker, predict_repair_strategy, get_repair_model_info
    ML_DL_AVAILABLE = True
    print("[AutoRepair] ML/DL修复决策模型已加载")
except ImportError as e:
    ML_DL_AVAILABLE = False
    print(f"[AutoRepair] ML/DL修复决策模型不可用: {e}")


class AutoRepairService:
    """自动修复服务类 - 完整版（集成ML/DL决策）"""
    
    def __init__(self):
        self.data_analyzer = get_data_analyzer()
        self.operation_service = get_operation_service()
        self.repair_log_file = os.path.join(PathConfig.LOGS_DIR, 'auto_repair_history.json')
        
        # ML/DL决策器
        self.ml_dl_enabled = ML_DL_AVAILABLE
        if self.ml_dl_enabled:
            self.repair_decision_maker = get_repair_decision_maker()
            print("[AutoRepair] 已启用ML/DL智能修复决策")
        
        # ==================== 完整修复策略配置 ====================
        # auto_fix: True=可自动修复, False=需人工处理
        # repair_type: 'auto'=自动修复, 'diagnose'=仅诊断, 'manual'=需人工操作
        # priority: 1-5, 1最高优先级
        # estimated_time: 预估修复时间（分钟）
        self.repair_strategies = {
            # ==================== CPU相关故障 ====================
            'CPU过载': {
                'operations': ['status_check', 'process_check', 'log_collect'],
                'description': '检查服务状态、分析高CPU进程并收集日志',
                'auto_fix': False,
                'repair_type': 'diagnose',
                'priority': 1,
                'estimated_time': 5,
                'category': 'CPU',
                'manual_action': '需要人工排查高CPU进程，建议操作：\n1. 使用 top/htop 定位高CPU进程\n2. 分析进程是否异常（死循环、内存泄漏等）\n3. 考虑终止异常进程：kill -9 <PID>\n4. 如为业务进程，考虑优化代码或增加服务器资源\n5. 检查是否有恶意程序或挖矿木马',
                'root_causes': ['进程死循环', '代码效率低', '并发请求过多', '恶意程序', '系统服务异常'],
                'prevention': '定期代码审查、设置进程资源限制、部署监控告警'
            },
            'CPU使用率偏高': {
                'operations': ['status_check', 'process_check'],
                'description': '检查服务状态和进程列表',
                'auto_fix': False,
                'repair_type': 'diagnose',
                'priority': 2,
                'estimated_time': 3,
                'category': 'CPU',
                'manual_action': '需要持续监控，建议操作：\n1. 观察CPU使用趋势，判断是否为峰值\n2. 如持续偏高，排查占用CPU的进程\n3. 评估是否需要扩容或优化',
                'root_causes': ['业务高峰期', '后台任务执行', '配置不当'],
                'prevention': '设置合理的告警阈值、优化业务逻辑'
            },
            'CPU温度过高': {
                'operations': ['status_check', 'hardware_check'],
                'description': '检查CPU温度和散热状态',
                'auto_fix': False,
                'repair_type': 'manual',
                'priority': 1,
                'estimated_time': 30,
                'category': 'CPU',
                'manual_action': '需要人工检查硬件，建议操作：\n1. 检查服务器散热风扇是否正常运转\n2. 清理机箱灰尘\n3. 检查CPU硅脂是否需要更换\n4. 检查机房空调是否正常\n5. 必要时降低CPU频率或负载',
                'root_causes': ['散热故障', '灰尘积累', '环境温度高', '超频运行'],
                'prevention': '定期清洁、监控温度、保持机房恒温'
            },
            
            # ==================== 内存相关故障 ====================
            '内存不足': {
                'operations': ['status_check', 'memory_check', 'cache_clear', 'restart'],
                'description': '检查内存状态、清理缓存后重启服务释放内存',
                'auto_fix': True,
                'repair_type': 'auto',
                'priority': 1,
                'estimated_time': 5,
                'category': '内存',
                'manual_action': None,
                'root_causes': ['内存泄漏', '缓存未释放', '进程异常', '配置不当'],
                'prevention': '定期重启服务、配置合理的内存限制、监控内存趋势'
            },
            '内存使用率偏高': {
                'operations': ['status_check', 'memory_check'],
                'description': '检查内存使用详情',
                'auto_fix': False,
                'repair_type': 'diagnose',
                'priority': 2,
                'estimated_time': 3,
                'category': '内存',
                'manual_action': '需要人工监控，建议操作：\n1. 使用 free -h 查看内存详情\n2. 使用 ps aux --sort=-%mem 查看内存占用进程\n3. 检查是否有内存泄漏\n4. 考虑增加swap或物理内存',
                'root_causes': ['缓存积累', '进程内存占用大', '配置不当'],
                'prevention': '设置内存告警、定期清理缓存'
            },
            '内存泄漏': {
                'operations': ['status_check', 'memory_check', 'log_collect', 'restart'],
                'description': '检测内存泄漏并尝试重启服务',
                'auto_fix': True,
                'repair_type': 'auto',
                'priority': 1,
                'estimated_time': 10,
                'category': '内存',
                'manual_action': None,
                'root_causes': ['代码bug', '资源未释放', '第三方库问题'],
                'prevention': '代码审查、使用内存分析工具、定期重启'
            },
            'OOM Killer触发': {
                'operations': ['status_check', 'log_collect', 'restart'],
                'description': '分析OOM日志并重启受影响服务',
                'auto_fix': True,
                'repair_type': 'auto',
                'priority': 1,
                'estimated_time': 5,
                'category': '内存',
                'manual_action': None,
                'root_causes': ['内存不足', '进程内存超限', '系统配置不当'],
                'prevention': '增加内存、设置合理的内存限制、配置swap'
            },
            
            # ==================== 磁盘IO相关故障 ====================
            'IO瓶颈': {
                'operations': ['status_check', 'io_check', 'log_collect'],
                'description': '检查IO状态并收集相关日志',
                'auto_fix': False,
                'repair_type': 'diagnose',
                'priority': 1,
                'estimated_time': 10,
                'category': '磁盘IO',
                'manual_action': '需要人工优化IO，建议操作：\n1. 使用 iotop 定位高IO进程\n2. 使用 iostat -x 1 分析磁盘性能\n3. 检查是否有大量随机读写\n4. 考虑使用SSD或优化存储架构\n5. 检查RAID状态是否正常',
                'root_causes': ['大量随机IO', '磁盘性能不足', 'RAID降级', '文件系统碎片'],
                'prevention': '使用高性能存储、优化IO模式、定期整理碎片'
            },
            'IO利用率偏高': {
                'operations': ['status_check', 'io_check'],
                'description': '检查IO使用情况',
                'auto_fix': False,
                'repair_type': 'diagnose',
                'priority': 2,
                'estimated_time': 5,
                'category': '磁盘IO',
                'manual_action': '需要人工监控，建议操作：\n1. 观察IO趋势，判断是否为临时高峰\n2. 分析是读IO还是写IO为主\n3. 评估是否需要优化存储或扩容',
                'root_causes': ['备份任务', '日志写入', '数据同步', '业务高峰'],
                'prevention': '错峰执行IO密集任务、优化日志策略'
            },
            'IO等待时间过长': {
                'operations': ['status_check', 'io_check', 'disk_check'],
                'description': '检查磁盘响应时间和队列深度',
                'auto_fix': False,
                'repair_type': 'diagnose',
                'priority': 1,
                'estimated_time': 10,
                'category': '磁盘IO',
                'manual_action': '需要人工排查，建议操作：\n1. 检查磁盘健康状态：smartctl -a /dev/sdX\n2. 检查IO调度器设置\n3. 分析是否有IO密集型进程\n4. 考虑升级存储设备',
                'root_causes': ['磁盘老化', '队列拥塞', '控制器瓶颈'],
                'prevention': '定期检查磁盘健康、使用NVMe SSD'
            },
            'IOWait过高': {
                'operations': ['status_check', 'io_check', 'process_check'],
                'description': '检查IO等待和相关进程',
                'auto_fix': False,
                'repair_type': 'diagnose',
                'priority': 1,
                'estimated_time': 10,
                'category': '磁盘IO',
                'manual_action': '需要人工排查，建议操作：\n1. 使用 iostat 查看磁盘IO状态\n2. 使用 iotop 定位高IO进程\n3. 检查是否有大文件读写操作\n4. 优化存储或增加缓存',
                'root_causes': ['磁盘性能瓶颈', '大量IO操作', '存储故障'],
                'prevention': '使用SSD、增加内存缓存、优化IO操作'
            },
            
            # ==================== 磁盘空间相关故障 ====================
            '磁盘空间不足': {
                'operations': ['status_check', 'disk_check', 'log_cleanup', 'temp_cleanup'],
                'description': '检查磁盘空间并清理日志和临时文件',
                'auto_fix': True,
                'repair_type': 'auto',
                'priority': 1,
                'estimated_time': 10,
                'category': '磁盘空间',
                'manual_action': None,
                'root_causes': ['日志堆积', '临时文件未清理', '数据增长', '备份文件过多'],
                'prevention': '配置日志轮转、定期清理、监控磁盘使用'
            },
            '磁盘空间预警': {
                'operations': ['status_check', 'disk_check'],
                'description': '检查各分区磁盘使用情况',
                'auto_fix': False,
                'repair_type': 'diagnose',
                'priority': 2,
                'estimated_time': 5,
                'category': '磁盘空间',
                'manual_action': '需要人工处理，建议操作：\n1. 使用 df -h 查看各分区使用情况\n2. 使用 du -sh /* 定位大文件目录\n3. 清理不需要的文件\n4. 考虑扩容或迁移数据',
                'root_causes': ['数据增长', '日志未轮转', '备份策略不当'],
                'prevention': '设置磁盘告警、配置自动清理策略'
            },
            'inode耗尽': {
                'operations': ['status_check', 'disk_check', 'temp_cleanup'],
                'description': '检查inode使用并清理小文件',
                'auto_fix': True,
                'repair_type': 'auto',
                'priority': 1,
                'estimated_time': 15,
                'category': '磁盘空间',
                'manual_action': None,
                'root_causes': ['大量小文件', '会话文件堆积', '缓存文件过多'],
                'prevention': '定期清理、优化文件存储策略'
            },
            
            # ==================== 网络相关故障 ====================
            '网络连接超时': {
                'operations': ['status_check', 'network_check', 'connectivity_test'],
                'description': '检查网络连接状态',
                'auto_fix': False,
                'repair_type': 'diagnose',
                'priority': 1,
                'estimated_time': 5,
                'category': '网络',
                'manual_action': '需要人工排查，建议操作：\n1. ping 目标地址测试连通性\n2. traceroute 分析网络路径\n3. 检查防火墙规则\n4. 检查DNS解析\n5. 联系网络管理员',
                'root_causes': ['网络拥塞', '路由问题', '防火墙阻断', 'DNS故障'],
                'prevention': '配置网络监控、设置合理的超时时间'
            },
            '网络带宽饱和': {
                'operations': ['status_check', 'network_check', 'traffic_analysis'],
                'description': '分析网络流量和带宽使用',
                'auto_fix': False,
                'repair_type': 'diagnose',
                'priority': 1,
                'estimated_time': 10,
                'category': '网络',
                'manual_action': '需要人工优化，建议操作：\n1. 使用 iftop/nethogs 分析流量\n2. 检查是否有异常流量\n3. 优化带宽分配\n4. 考虑扩容带宽或CDN加速',
                'root_causes': ['流量攻击', '大文件传输', '业务高峰', '配置不当'],
                'prevention': '配置流量限制、使用CDN、部署DDoS防护'
            },
            '网络丢包': {
                'operations': ['status_check', 'network_check', 'log_collect'],
                'description': '检测网络丢包并收集日志',
                'auto_fix': False,
                'repair_type': 'diagnose',
                'priority': 1,
                'estimated_time': 10,
                'category': '网络',
                'manual_action': '需要人工排查，建议操作：\n1. 检查网卡状态和错误计数\n2. 检查交换机端口状态\n3. 测试不同网络路径\n4. 更换网线或网卡',
                'root_causes': ['网卡故障', '线缆问题', '交换机故障', '拥塞丢包'],
                'prevention': '冗余网络、定期检查网络设备'
            },
            '端口连接数过多': {
                'operations': ['status_check', 'connection_check', 'restart'],
                'description': '检查连接数并重启服务释放连接',
                'auto_fix': True,
                'repair_type': 'auto',
                'priority': 2,
                'estimated_time': 5,
                'category': '网络',
                'manual_action': None,
                'root_causes': ['连接泄漏', '攻击', '并发过高', '超时配置不当'],
                'prevention': '配置连接池、设置超时、限流保护'
            },
            
            # ==================== 服务相关故障 ====================
            '服务停止': {
                'operations': ['status_check', 'log_collect', 'restart'],
                'description': '检查服务状态并尝试重启',
                'auto_fix': True,
                'repair_type': 'auto',
                'priority': 1,
                'estimated_time': 3,
                'category': '服务',
                'manual_action': None,
                'root_causes': ['进程崩溃', 'OOM', '手动停止', '依赖服务故障'],
                'prevention': '配置进程守护、健康检查、自动重启'
            },
            '服务响应慢': {
                'operations': ['status_check', 'process_check', 'log_collect'],
                'description': '分析服务响应延迟原因',
                'auto_fix': False,
                'repair_type': 'diagnose',
                'priority': 2,
                'estimated_time': 10,
                'category': '服务',
                'manual_action': '需要人工优化，建议操作：\n1. 检查服务日志定位慢请求\n2. 分析数据库查询是否有慢SQL\n3. 检查外部依赖服务响应时间\n4. 考虑增加服务实例或优化代码',
                'root_causes': ['代码效率低', '数据库慢查询', '外部依赖慢', '资源不足'],
                'prevention': '性能测试、慢日志监控、设置超时'
            },
            '服务异常重启': {
                'operations': ['status_check', 'log_collect', 'core_dump_check'],
                'description': '分析服务异常重启原因',
                'auto_fix': False,
                'repair_type': 'diagnose',
                'priority': 1,
                'estimated_time': 15,
                'category': '服务',
                'manual_action': '需要人工分析，建议操作：\n1. 检查服务日志和系统日志\n2. 分析core dump文件\n3. 检查是否有OOM\n4. 检查依赖服务状态\n5. 回滚最近的代码变更',
                'root_causes': ['代码bug', '内存溢出', '依赖故障', '配置错误'],
                'prevention': '完善测试、灰度发布、配置健康检查'
            },
            '服务健康检查失败': {
                'operations': ['status_check', 'health_check', 'restart'],
                'description': '检查服务健康状态并尝试恢复',
                'auto_fix': True,
                'repair_type': 'auto',
                'priority': 1,
                'estimated_time': 5,
                'category': '服务',
                'manual_action': None,
                'root_causes': ['服务假死', '端口未监听', '依赖异常'],
                'prevention': '配置合理的健康检查、自动重启策略'
            },
            
            # ==================== 数据库相关故障 ====================
            '数据库连接池耗尽': {
                'operations': ['status_check', 'connection_check', 'db_restart'],
                'description': '检查数据库连接并重启连接池',
                'auto_fix': True,
                'repair_type': 'auto',
                'priority': 1,
                'estimated_time': 5,
                'category': '数据库',
                'manual_action': None,
                'root_causes': ['连接泄漏', '并发过高', '配置不当', '慢查询阻塞'],
                'prevention': '配置连接池监控、设置合理的连接数'
            },
            '数据库慢查询': {
                'operations': ['status_check', 'db_check', 'log_collect'],
                'description': '分析慢查询日志',
                'auto_fix': False,
                'repair_type': 'diagnose',
                'priority': 2,
                'estimated_time': 15,
                'category': '数据库',
                'manual_action': '需要人工优化，建议操作：\n1. 分析慢查询日志\n2. 使用EXPLAIN分析执行计划\n3. 添加必要的索引\n4. 优化SQL语句\n5. 考虑读写分离或分库分表',
                'root_causes': ['缺少索引', 'SQL不优化', '数据量大', '锁竞争'],
                'prevention': '定期分析慢查询、建立索引规范'
            },
            '数据库死锁': {
                'operations': ['status_check', 'db_check', 'deadlock_analysis'],
                'description': '检测并分析数据库死锁',
                'auto_fix': False,
                'repair_type': 'diagnose',
                'priority': 1,
                'estimated_time': 20,
                'category': '数据库',
                'manual_action': '需要人工处理，建议操作：\n1. 分析死锁日志\n2. 找出死锁的SQL语句\n3. 优化事务顺序\n4. 减小事务粒度\n5. 必要时kill阻塞会话',
                'root_causes': ['事务顺序不一致', '长事务', '索引不当'],
                'prevention': '统一事务顺序、减小事务范围、添加合适索引'
            },
            '数据库主从延迟': {
                'operations': ['status_check', 'db_check', 'replication_check'],
                'description': '检查数据库主从复制状态',
                'auto_fix': False,
                'repair_type': 'diagnose',
                'priority': 2,
                'estimated_time': 10,
                'category': '数据库',
                'manual_action': '需要人工处理，建议操作：\n1. 检查从库复制状态\n2. 分析是否有大事务\n3. 检查网络延迟\n4. 考虑并行复制\n5. 评估是否需要重建从库',
                'root_causes': ['大事务', '网络延迟', '从库性能不足', '锁等待'],
                'prevention': '拆分大事务、优化网络、提升从库配置'
            },
            
            # ==================== 安全相关故障 ====================
            '异常登录尝试': {
                'operations': ['status_check', 'security_check', 'log_collect'],
                'description': '检测异常登录行为',
                'auto_fix': False,
                'repair_type': 'diagnose',
                'priority': 1,
                'estimated_time': 10,
                'category': '安全',
                'manual_action': '需要人工处理，建议操作：\n1. 分析登录日志，确认攻击来源IP\n2. 将可疑IP加入黑名单\n3. 检查是否有账号被破解\n4. 强制重置可疑账号密码\n5. 启用多因素认证',
                'root_causes': ['暴力破解', '弱密码', '凭证泄露'],
                'prevention': '强密码策略、登录限流、多因素认证'
            },
            '可疑进程': {
                'operations': ['status_check', 'process_check', 'security_check'],
                'description': '检测可疑进程活动',
                'auto_fix': False,
                'repair_type': 'manual',
                'priority': 1,
                'estimated_time': 30,
                'category': '安全',
                'manual_action': '需要人工处理，建议操作：\n1. 使用 ps aux 检查可疑进程\n2. 分析进程来源和行为\n3. 检查是否有挖矿、后门程序\n4. 终止可疑进程\n5. 全面安全扫描\n6. 必要时重装系统',
                'root_causes': ['入侵', '恶意软件', '内部威胁'],
                'prevention': '定期安全扫描、最小权限原则、入侵检测'
            },
            '证书即将过期': {
                'operations': ['status_check', 'cert_check'],
                'description': '检查SSL证书有效期',
                'auto_fix': False,
                'repair_type': 'manual',
                'priority': 2,
                'estimated_time': 30,
                'category': '安全',
                'manual_action': '需要人工更新证书，建议操作：\n1. 确认证书过期时间\n2. 申请新证书或续期\n3. 部署新证书\n4. 验证证书配置正确\n5. 配置证书自动续期',
                'root_causes': ['证书管理疏忽', '自动续期失败'],
                'prevention': '配置证书监控、使用自动续期工具'
            },
            
            # ==================== 系统相关故障 ====================
            '系统负载过高': {
                'operations': ['status_check', 'process_check', 'log_collect'],
                'description': '分析系统整体负载',
                'auto_fix': False,
                'repair_type': 'diagnose',
                'priority': 1,
                'estimated_time': 10,
                'category': '系统',
                'manual_action': '需要人工排查，建议操作：\n1. 使用 uptime 查看负载趋势\n2. 使用 vmstat 分析系统状态\n3. 定位高负载原因（CPU/IO/内存）\n4. 采取针对性措施',
                'root_causes': ['资源竞争', '配置不当', '业务高峰', '异常进程'],
                'prevention': '合理规划资源、配置自动扩容'
            },
            '系统时间不同步': {
                'operations': ['status_check', 'time_sync'],
                'description': '检查并同步系统时间',
                'auto_fix': True,
                'repair_type': 'auto',
                'priority': 2,
                'estimated_time': 2,
                'category': '系统',
                'manual_action': None,
                'root_causes': ['NTP配置错误', '时钟漂移', '网络隔离'],
                'prevention': '配置NTP服务、定期检查时间同步'
            },
            '文件描述符耗尽': {
                'operations': ['status_check', 'fd_check', 'restart'],
                'description': '检查文件描述符使用并重启服务',
                'auto_fix': True,
                'repair_type': 'auto',
                'priority': 1,
                'estimated_time': 5,
                'category': '系统',
                'manual_action': None,
                'root_causes': ['连接泄漏', '文件未关闭', '配置过小'],
                'prevention': '增加fd限制、检查代码资源释放'
            },
            '僵尸进程过多': {
                'operations': ['status_check', 'zombie_cleanup'],
                'description': '清理僵尸进程',
                'auto_fix': True,
                'repair_type': 'auto',
                'priority': 2,
                'estimated_time': 3,
                'category': '系统',
                'manual_action': None,
                'root_causes': ['父进程未回收', '信号处理不当'],
                'prevention': '修复父进程代码、配置进程监控'
            },
            'Swap使用过高': {
                'operations': ['status_check', 'memory_check', 'swap_clear'],
                'description': '检查Swap使用并尝试释放',
                'auto_fix': True,
                'repair_type': 'auto',
                'priority': 2,
                'estimated_time': 5,
                'category': '系统',
                'manual_action': None,
                'root_causes': ['内存不足', '配置不当'],
                'prevention': '增加物理内存、调整swappiness'
            },
            
            # ==================== 日志相关故障 ====================
            '日志增长过快': {
                'operations': ['status_check', 'log_check', 'log_cleanup', 'log_rotate'],
                'description': '检查日志并执行轮转清理',
                'auto_fix': True,
                'repair_type': 'auto',
                'priority': 2,
                'estimated_time': 5,
                'category': '日志',
                'manual_action': None,
                'root_causes': ['日志级别过低', '异常循环日志', '未配置轮转'],
                'prevention': '配置日志轮转、调整日志级别'
            },
            '错误日志激增': {
                'operations': ['status_check', 'log_collect', 'log_analysis'],
                'description': '分析错误日志原因',
                'auto_fix': False,
                'repair_type': 'diagnose',
                'priority': 1,
                'estimated_time': 15,
                'category': '日志',
                'manual_action': '需要人工分析，建议操作：\n1. 统计错误类型和频率\n2. 定位错误根因\n3. 修复代码或配置问题\n4. 临时调整日志级别降低噪音',
                'root_causes': ['代码bug', '配置错误', '依赖故障', '数据异常'],
                'prevention': '完善错误处理、配置日志告警'
            }
        }
        
        # 确保日志目录存在
        os.makedirs(os.path.dirname(self.repair_log_file), exist_ok=True)
    
    def _get_beijing_time(self) -> str:
        """获取北京时间字符串"""
        return datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')
    
    def detect_faults_in_data(self, server_name: str, data_type: str, analysis_result: Dict) -> Dict[str, Any]:
        """
        检测上传数据中的故障
        Args:
            server_name: 服务器名称
            data_type: 数据类型 (cpu/memory/io)
            analysis_result: data_analyzer的分析结果
        Returns:
            故障检测结果
        """
        detection_result = {
            'detection_time': self._get_beijing_time(),
            'server': server_name,
            'data_type': data_type,
            'has_fault': False,
            'faults': [],
            'status': 'normal',
            'auto_repair_available': False,
            'recommended_actions': []
        }
        
        try:
            stats = analysis_result.get('statistics', {})
            
            # 检测CPU故障
            if data_type == 'cpu':
                current_cpu = stats.get('current', 0) or stats.get('avg_usage', 0) or stats.get('max_usage', 0) or stats.get('max', 0) or stats.get('avg', 0)
                
                if current_cpu > 90:
                    detection_result['faults'].append({
                        'type': 'CPU过载',
                        'level': 'critical',
                        'value': round(current_cpu, 2),
                        'threshold': 90,
                        'message': f'CPU使用率 {current_cpu:.2f}% 超过 90%，需要立即处理！'
                    })
                    detection_result['status'] = 'critical'
                    detection_result['auto_repair_available'] = True
                    detection_result['recommended_actions'].extend(['检查高CPU进程', '收集系统日志'])
                elif current_cpu > 70:
                    detection_result['faults'].append({
                        'type': 'CPU使用率偏高',
                        'level': 'warning',
                        'value': round(current_cpu, 2),
                        'threshold': 70,
                        'message': f'CPU使用率 {current_cpu:.2f}% 超过 70%，建议关注'
                    })
                    detection_result['status'] = 'warning'
                    detection_result['auto_repair_available'] = True
                    detection_result['recommended_actions'].append('监控CPU趋势')
            
            # 检测内存故障
            elif data_type == 'memory':
                mem_used = stats.get('used_percent_avg', 0) or stats.get('current', 0) or stats.get('current_used_percent', 0) or stats.get('max_used_percent', 0) or stats.get('max', 0) or stats.get('avg', 0)
                
                if mem_used > 90:
                    detection_result['faults'].append({
                        'type': '内存不足',
                        'level': 'critical',
                        'value': round(mem_used, 2),
                        'threshold': 90,
                        'message': f'内存使用率 {mem_used:.2f}% 超过 90%，需要立即处理！'
                    })
                    detection_result['status'] = 'critical'
                    detection_result['auto_repair_available'] = True
                    detection_result['recommended_actions'].extend(['检查内存占用进程', '考虑重启服务释放内存'])
                elif mem_used > 80:
                    detection_result['faults'].append({
                        'type': '内存使用率偏高',
                        'level': 'warning',
                        'value': round(mem_used, 2),
                        'threshold': 80,
                        'message': f'内存使用率 {mem_used:.2f}% 超过 80%，建议关注'
                    })
                    detection_result['status'] = 'warning'
                    detection_result['auto_repair_available'] = True
                    detection_result['recommended_actions'].append('监控内存趋势')
            
            # 检测IO故障
            elif data_type == 'io':
                io_util = stats.get('util_avg', 0) or stats.get('current', 0) or stats.get('current_util', 0) or stats.get('max_util', 0) or stats.get('max', 0) or stats.get('avg', 0)
                
                if io_util > 90:
                    detection_result['faults'].append({
                        'type': 'IO瓶颈',
                        'level': 'critical',
                        'value': round(io_util, 2),
                        'threshold': 90,
                        'message': f'IO利用率 {io_util:.2f}% 超过 90%，需要立即处理！'
                    })
                    detection_result['status'] = 'critical'
                    detection_result['auto_repair_available'] = True
                    detection_result['recommended_actions'].extend(['检查IO密集进程', '分析磁盘性能'])
                elif io_util > 70:
                    detection_result['faults'].append({
                        'type': 'IO利用率偏高',
                        'level': 'warning',
                        'value': round(io_util, 2),
                        'threshold': 70,
                        'message': f'IO利用率 {io_util:.2f}% 超过 70%，建议关注'
                    })
                    detection_result['status'] = 'warning'
                    detection_result['auto_repair_available'] = True
                    detection_result['recommended_actions'].append('监控IO趋势')
            
            detection_result['has_fault'] = len(detection_result['faults']) > 0
            
        except Exception as e:
            detection_result['error'] = str(e)
        
        return detection_result
    
    def analyze_and_repair(self, server: str = None, fault_type: str = None) -> Dict[str, Any]:
        """分析并执行修复（基于上传的故障数据）"""
        timestamp = self._get_beijing_time()
        
        result = {
            'timestamp': timestamp,
            'server': server,
            'repairs': [],
            'diagnoses': [],
            'manual_actions': [],
            'summary': {
                'total_faults': 0,
                'auto_fixed': 0,
                'diagnosed': 0,
                'manual_required': 0
            }
        }
        
        # 获取上传的故障数据
        uploaded = get_uploaded_fault()
        fault_detection = uploaded.get('fault_detection', {})
        
        if not fault_detection or not fault_detection.get('has_fault'):
            result['message'] = '未检测到需要修复的故障'
            return result
        
        faults = fault_detection.get('faults', [])
        result['summary']['total_faults'] = len(faults)
        result['server'] = server or uploaded.get('server', 'unknown')
        
        for fault in faults:
            fault_name = fault.get('type', '')
            strategy = self.repair_strategies.get(fault_name, {})
            
            repair_result = {
                'fault_type': fault_name,
                'severity': fault.get('level', 'warning'),
                'message': fault.get('message', ''),
                'server': result['server'],
                'status': 'pending',
                'operations': [],
                'start_time': timestamp
            }
            
            if strategy:
                repair_result['strategy'] = strategy.get('description', '')
                repair_result['category'] = strategy.get('category', '')
                repair_result['priority'] = strategy.get('priority', 3)
                repair_result['estimated_time'] = strategy.get('estimated_time', 5)
                
                # 执行修复/诊断操作
                for op in strategy.get('operations', []):
                    op_result = self.operation_service.execute_operation(
                        server_name=result['server'],
                        operation=op,
                        dry_run=not strategy.get('auto_fix', False)
                    )
                    repair_result['operations'].append({
                        'operation': op,
                        'result': op_result.get('status', 'unknown'),
                        'message': op_result.get('message', '')
                    })
                
                if strategy.get('auto_fix'):
                    repair_result['status'] = 'auto_fixed'
                    result['summary']['auto_fixed'] += 1
                    result['repairs'].append(repair_result)
                else:
                    repair_result['status'] = 'diagnosed'
                    repair_result['manual_action'] = strategy.get('manual_action', '需要人工处理')
                    repair_result['root_causes'] = strategy.get('root_causes', [])
                    repair_result['prevention'] = strategy.get('prevention', '')
                    result['summary']['diagnosed'] += 1
                    result['diagnoses'].append(repair_result)
                    
                    if strategy.get('manual_action'):
                        result['manual_actions'].append({
                            'fault_type': fault_name,
                            'server': result['server'],
                            'action': strategy.get('manual_action'),
                            'priority': strategy.get('priority', 3)
                        })
                        result['summary']['manual_required'] += 1
            else:
                repair_result['status'] = 'unknown_fault'
                repair_result['message'] = f'未知故障类型: {fault_name}，需要人工分析'
                result['summary']['manual_required'] += 1
                result['diagnoses'].append(repair_result)
            
            repair_result['end_time'] = self._get_beijing_time()
        
        # 保存修复记录
        self._save_repair_history(result)
        
        return result
    
    def auto_repair_uploaded_data(self, server_name: str, fault_detection: Dict, dry_run: bool = True) -> Dict[str, Any]:
        """
        对上传数据中检测到的故障执行自动修复
        集成ML/DL智能决策
        """
        repair_result = {
            'start_time': self._get_beijing_time(),
            'server': server_name,
            'dry_run': dry_run,
            'repairs': [],
            'diagnoses': [],
            'manual_actions': [],
            'ml_dl_decisions': [],  # ML/DL决策记录
            'success': True
        }
        
        if not fault_detection.get('has_fault'):
            repair_result['message'] = '未检测到需要修复的故障'
            return repair_result
        
        for fault in fault_detection.get('faults', []):
            fault_type = fault.get('type', '')
            strategy = self.repair_strategies.get(fault_type)
            
            # ==================== ML/DL智能决策 ====================
            ml_dl_decision = None
            if self.ml_dl_enabled:
                try:
                    # 构建故障检测信息
                    fault_info = {
                        'fault_type': fault_type,
                        'severity': fault.get('level', 'medium'),
                        'details': fault.get('details', {}),
                        'confidence': fault.get('confidence', 0.8),
                        'auto_fixable': strategy.get('auto_fix', False) if strategy else False
                    }
                    
                    # 获取ML/DL决策
                    ml_dl_decision = self.repair_decision_maker.decide(fault_info)
                    repair_result['ml_dl_decisions'].append({
                        'fault_type': fault_type,
                        'decision': ml_dl_decision
                    })
                    
                    print(f"[AutoRepair] ML/DL决策: {fault_type} -> {ml_dl_decision.get('best_strategy')}")
                    print(f"  预测成功率: {ml_dl_decision.get('predicted_success_rate', 0):.2%}")
                    print(f"  置信度: {ml_dl_decision.get('confidence', 0):.2%}")
                    print(f"  模型共识: {'是' if ml_dl_decision.get('consensus') else '否'}")
                except Exception as e:
                    print(f"[AutoRepair] ML/DL决策异常: {e}")
            
            if not strategy:
                # 尝试使用ML/DL推荐的策略
                if ml_dl_decision and ml_dl_decision.get('best_operations'):
                    strategy = {
                        'operations': ml_dl_decision.get('best_operations', ['status_check']),
                        'description': f"ML/DL推荐策略: {ml_dl_decision.get('best_strategy')}",
                        'auto_fix': ml_dl_decision.get('auto_fixable', False),
                        'category': '智能诊断',
                        'ml_dl_predicted': True
                    }
                else:
                    continue
            
            repair_record = {
                'fault_type': fault_type,
                'fault_level': fault.get('level'),
                'fault_message': fault.get('message'),
                'strategy': strategy.get('description'),
                'category': strategy.get('category'),
                'operations': [],
                'status': 'pending',
                'ml_dl_prediction': ml_dl_decision.get('predicted_success_rate') if ml_dl_decision else None,
                'ml_dl_model': ml_dl_decision.get('model_type') if ml_dl_decision else None
            }
            
            # 执行修复操作
            all_success = True
            for operation in strategy.get('operations', []):
                op_result = self.operation_service.execute_operation(
                    server_name=server_name,
                    operation=operation,
                    params={},
                    dry_run=dry_run or not strategy.get('auto_fix', False)
                )
                
                repair_record['operations'].append({
                    'operation': operation,
                    'result': op_result.get('message', ''),
                    'success': op_result.get('success', False) or op_result.get('status') == 'simulated'
                })
                
                if not (op_result.get('success', False) or op_result.get('status') == 'simulated'):
                    all_success = False
            
            if strategy.get('auto_fix'):
                repair_record['status'] = 'success' if all_success else 'partial'
                repair_result['repairs'].append(repair_record)
            else:
                repair_record['status'] = 'diagnosed'
                repair_record['manual_action'] = strategy.get('manual_action')
                repair_record['root_causes'] = strategy.get('root_causes', [])
                repair_record['prevention'] = strategy.get('prevention', '')
                repair_result['diagnoses'].append(repair_record)
                
                if strategy.get('manual_action'):
                    repair_result['manual_actions'].append({
                        'fault_type': fault_type,
                        'action': strategy.get('manual_action'),
                        'priority': strategy.get('priority', 3)
                    })
            
            if not all_success:
                repair_result['success'] = False
        
        repair_result['end_time'] = self._get_beijing_time()
        repair_result['total_repairs'] = len(repair_result['repairs'])
        repair_result['total_diagnoses'] = len(repair_result['diagnoses'])
        repair_result['ml_dl_enabled'] = self.ml_dl_enabled
        
        return repair_result
    
    def get_repair_strategy(self, fault_type: str) -> Dict:
        """获取修复策略"""
        return self.repair_strategies.get(fault_type, {})
    
    def get_all_strategies(self) -> Dict:
        """获取所有修复策略"""
        return self.repair_strategies
    
    def get_strategies_by_category(self, category: str) -> Dict:
        """按类别获取修复策略"""
        return {k: v for k, v in self.repair_strategies.items() if v.get('category') == category}
    
    def _save_repair_history(self, repair_result: Dict[str, Any]):
        """保存修复历史"""
        try:
            history = []
            if os.path.exists(self.repair_log_file):
                with open(self.repair_log_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)
            
            history.append(repair_result)
            history = history[-100:]  # 只保留最近100条
            
            with open(self.repair_log_file, 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存修复历史失败: {e}")
    
    def get_repair_history(self, limit: int = 20) -> List[Dict]:
        """获取修复历史"""
        try:
            if os.path.exists(self.repair_log_file):
                with open(self.repair_log_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)
                return history[-limit:][::-1]
        except Exception as e:
            print(f"读取修复历史失败: {e}")
        return []


# 单例模式
_auto_repair_service = None


def get_auto_repair_service() -> AutoRepairService:
    """获取自动修复服务实例"""
    global _auto_repair_service
    if _auto_repair_service is None:
        _auto_repair_service = AutoRepairService()
    return _auto_repair_service
