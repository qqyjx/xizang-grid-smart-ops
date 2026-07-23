# -*- coding: utf-8 -*-
"""
报告生成服务 - 支持虚拟服务器监控和故障修复报告
v2.3: 支持虚拟服务器实时状态报告和详细修复操作记录
"""

import os
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
import uuid
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import ReportConfig, PathConfig

# 北京时间
BEIJING_TZ = timezone(timedelta(hours=8))

# ==================== v5.8: 巡检 17 项统一数据源 ====================
# 单一事实源：报告"按系统分析"的行 + 巡检项人工录入表单都从这里派生，保证两边一致。
# auto: Agent 能否程序化采集；非 None 表示有自动采集逻辑，采到则 🟢自动，采不到可人工录入。
# desc: 该巡检点的"含义/可巡检什么内容"，在报告与录入表单都展示（客户要求逐点说明）。
INSPECTION_DIMS = [
    {'key': 'slb_ports', 'auto': 'ports', 'label': 'SLB / 代理服务端口',
     'desc': '检查负载均衡(SLB)及各代理服务对外监听的端口是否正常开放、有无异常占用或缺失。',
     'pending': '未探测到监听端口，请人工核查 SLB/代理对外端口与状态。',
     'ph': '可留空（自动采集监听端口）；未采到时填，如：SLB 监听 80/443，代理 8080 正常'},
    {'key': 'redis', 'auto': 'redis', 'label': '缓存 Redis',
     'desc': '检查 Redis 缓存服务是否运行、端口(6379)是否可达、有无连接异常或内存告警。',
     'pending': '未探测到 Redis 端口(6379)，如使用云缓存/非标端口请在此填写实例地址与状态。',
     'ph': '如：使用云 Redis 10.x.x.x:6379，主从正常，命中率 95%'},
    {'key': 'database', 'auto': 'db', 'label': '云上/云下各类数据库（磁盘·内存·CPU）',
     'desc': '检查 MySQL/PostgreSQL/Oracle 等数据库的磁盘、内存、CPU 使用率及连接是否正常。',
     'pending': '未探测到本机数据库端口，请人工填写数据库类型/地址/资源使用情况。',
     'ph': '如：MySQL <内网IP>，CPU 30%/内存 60%/磁盘 55%，连接数正常'},
    {'key': 'scheduled_task', 'auto': 'cron', 'label': '定时任务',
     'desc': '检查 crontab/cron.d 及 systemd 定时器中配置的计划任务数量与执行情况。',
     'pending': '暂无采集数据（节点离线/采集超时），请人工填写关键定时任务清单与执行情况。',
     'ph': '如：每日 2:00 备份、每 5 分钟同步，均正常执行'},
    {'key': 'mq', 'auto': 'mq', 'label': '消息队列',
     'desc': '检查 RabbitMQ/Kafka 等消息中间件是否运行、端口是否可达、队列有无积压。',
     'pending': '未探测到 MQ 端口(5672/9092)，如使用云 MQ 请在此填写实例与积压情况。',
     'ph': '如：RabbitMQ 集群正常，无积压；或：未使用消息队列'},
    {'key': 'service_cpu_mem', 'auto': 'proc_mem', 'label': '服务的内存 / CPU 使用情况',
     'desc': '检查各业务服务进程占用的内存与 CPU，识别资源占用过高的进程。',
     'pending': '暂无进程级采集数据（节点离线/采集超时），请人工填写关键服务资源占用。',
     'ph': '如：核心服务内存 2G/CPU 15%，无异常高占用进程'},
    {'key': 'cert_status', 'auto': None, 'label': '各类账号 / 证书有无过期',
     'desc': '检查系统账号、SSL 证书、各类密钥/令牌是否临近或已经过期。',
     'pending': '待录入（运维核查后填写）。',
     'ph': '如：全部有效，最近到期 SSL 证书 2026-12-01'},
    {'key': 'middleware', 'auto': 'middleware', 'label': '使用的中间件',
     'desc': '盘点服务器上部署的中间件(Nginx/Tomcat/Redis/MQ/Nacos 等)及其运行状态。',
     'pending': '未探测到常见中间件端口，请人工填写所用中间件清单与版本。',
     'ph': '如：Nginx 1.20、Tomcat 9、Redis 6，均正常'},
    {'key': 'nacos', 'auto': 'nacos', 'label': 'Nacos 注册/配置中心',
     'desc': '检查 Nacos 注册中心/配置中心是否运行、端口(8848)是否可达、服务注册是否正常。',
     'pending': '未探测到 Nacos 端口(8848)，如使用请在此填写地址与注册/配置情况。',
     'ph': '如：Nacos 10.x.x.x:8848，已注册 12 个服务，配置正常'},
    {'key': 'isc_login_limit', 'auto': None, 'label': 'ISC 账号登录次数 5 次限制',
     'desc': '检查 ISC 统一认证是否启用"连续登录失败 N 次锁定"的账号安全策略。',
     'pending': '待录入（运维核查后填写）。',
     'ph': '如：已启用，连续 5 次失败锁定 30 分钟'},
    {'key': 'thread_count', 'auto': 'threads', 'label': '线程数',
     'desc': '检查系统/各服务的线程总数，识别线程泄漏或线程数异常偏高。',
     'pending': '暂无采集数据，请人工填写关键服务线程数。',
     'ph': '如：核心服务线程 200 左右，无持续增长'},
    {'key': 'jdbc', 'auto': 'jdbc', 'label': 'JDBC / 数据库连接数',
     'desc': '检查应用到数据库的连接数(含连接池)，识别连接泄漏或连接耗尽风险。',
     'pending': '暂无采集数据，请人工填写应用 JDBC 连接数/连接池配置。',
     'ph': '如：连接池上限 50，当前 12，无泄漏'},
    {'key': 'account_permission', 'auto': None, 'label': '系统账号权限分配',
     'desc': '检查系统各账号的角色与权限分配是否遵循最小授权原则。',
     'pending': '待录入（运维核查后填写）。',
     'ph': '如：按角色最小授权，管理员 3 个/只读 12 个'},
    {'key': 'audit_log', 'auto': None, 'label': '系统审计日志',
     'desc': '检查审计日志是否开启、保留周期、是否定期归档。',
     'pending': '待录入（运维核查后填写）。',
     'ph': '如：已开启，保留 180 天，每日归档'},
    {'key': 'login_homepage', 'auto': None, 'label': '系统登录地址 / 主页面',
     'desc': '记录系统登录入口地址，确认门户主页面可正常访问。',
     'pending': '待录入（运维核查后填写）。',
     'ph': '如：https://10.x.x.x/portal 访问正常'},
    {'key': 'process', 'auto': 'process', 'label': '服务进程',
     'desc': '检查关键业务进程是否存活、数量是否正常、有无异常退出或僵尸进程。',
     'pending': '暂无采集数据，请人工填写关键服务进程存活情况。',
     'ph': '如：核心 6 个服务进程均存活，无僵尸进程'},
    {'key': 'cloud_server', 'auto': 'cloud', 'label': '云服务器运行状态、内存、CPU 使用率',
     'desc': '检查云服务器整体在线状态及 CPU/内存/磁盘使用率是否在正常范围。',
     'pending': '暂无采集数据，请人工填写服务器在线与资源使用情况。',
     'ph': '如：在线，CPU 20%/内存 50%/磁盘 45%'},
]
# 末尾补充说明（非编号巡检项）
INSPECTION_REMARK = {'key': 'remark', 'label': '补充说明（选填）',
                     'desc': '该系统其它需要在报告中体现的人工核查结论。',
                     'ph': '该系统其它需在报告中体现的人工核查结论'}


class ReportGenerator:
    """报告生成服务类 - 支持虚拟服务器监控报告"""
    
    def __init__(self):
        self.output_dir = ReportConfig.REPORT_OUTPUT_DIR
        os.makedirs(self.output_dir, exist_ok=True)
        
        # 故障历史和修复记录存储
        self.fault_history = []
        self.repair_records = []
    
    def add_fault_record(self, fault_record: Dict):
        """添加故障记录"""
        self.fault_history.append(fault_record)
        # 保留最近50条
        if len(self.fault_history) > 50:
            self.fault_history = self.fault_history[-50:]
    
    def add_repair_record(self, repair_record: Dict):
        """添加修复记录"""
        self.repair_records.append(repair_record)
        # 保留最近50条
        if len(self.repair_records) > 50:
            self.repair_records = self.repair_records[-50:]
    
    def clear_history(self):
        """清除历史记录"""
        self.fault_history = []
        self.repair_records = []
    
    def get_fault_history(self, limit: int = 10) -> List[Dict]:
        """获取故障历史"""
        return self.fault_history[-limit:] if self.fault_history else []
    
    def get_repair_records(self, limit: int = 10) -> List[Dict]:
        """获取修复记录"""
        return self.repair_records[-limit:] if self.repair_records else []
    
    def generate_virtual_server_report(self, virtual_servers: List[Dict], 
                                        ai_analysis: str = "") -> Dict[str, Any]:
        """
        生成虚拟服务器监控报告
        - 无故障时：生成日常监控报告
        - 有故障/修复记录时：生成故障详情和修复报告
        """
        report_id = str(uuid.uuid4())[:8].upper()
        timestamp = datetime.now(BEIJING_TZ)
        timestamp_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
        
        # 判断是否有故障历史
        has_fault_history = len(self.fault_history) > 0 or len(self.repair_records) > 0
        
        # 检查当前是否有故障
        current_faults = [s for s in virtual_servers if s.get('fault_injected')]
        
        # 根据情况生成不同类型的报告
        if has_fault_history or current_faults:
            report_type = "故障诊断与修复报告"
        else:
            report_type = "日常运行监控报告"
        
        content = self._build_virtual_server_report(
            virtual_servers=virtual_servers,
            current_faults=current_faults,
            ai_analysis=ai_analysis,
            report_type=report_type,
            report_id=report_id,
            timestamp=timestamp
        )
        
        # 保存报告
        filename = f'virtual_server_report_{report_id}_{timestamp.strftime("%Y%m%d_%H%M%S")}.md'
        filepath = os.path.join(self.output_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return {
            'success': True,
            'report_id': report_id,
            'filename': filename,
            'file_path': filepath,
            'content': content,
            'report_type': report_type,
            'timestamp': timestamp_str,
            'has_fault_history': has_fault_history,
            'current_fault_count': len(current_faults),
            'summary': {
                'total_servers': len(virtual_servers),
                'running_count': sum(1 for s in virtual_servers if s.get('status') == 'running'),
                'fault_count': len(current_faults),
                'repair_count': len(self.repair_records)
            }
        }
    
    def _build_virtual_server_report(self, virtual_servers: List[Dict],
                                      current_faults: List[Dict],
                                      ai_analysis: str,
                                      report_type: str,
                                      report_id: str,
                                      timestamp: datetime) -> str:
        """构建虚拟服务器报告内容"""
        lines = []
        
        # 报告头部
        lines.append(f"# 🖥️ 西藏电网智能运维 - {report_type}")
        lines.append("")
        lines.append("## 报告信息")
        lines.append("")
        lines.append("| 项目 | 内容 |")
        lines.append("|:-----|:-----|")
        lines.append(f"| 报告编号 | {report_id} |")
        lines.append(f"| 生成时间 | {timestamp.strftime('%Y-%m-%d %H:%M:%S')} |")
        lines.append(f"| 报告类型 | {report_type} |")
        lines.append(f"| 服务器数量 | {len(virtual_servers)} |")
        lines.append(f"| 故障数量 | {len(current_faults)} |")
        lines.append(f"| 修复记录 | {len(self.repair_records)} 条 |")
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # 服务器状态概览
        lines.append("## 📊 服务器状态概览")
        lines.append("")
        lines.append("| 服务器名称 | IP地址 | 状态 | CPU | 内存 | 磁盘IO | 网络延迟 | 故障状态 |")
        lines.append("|:---------|:------|:-----|:---:|:----:|:-----:|:-------:|:-------:|")
        
        for srv in virtual_servers:
            resources = srv.get('resources', {})
            cpu = resources.get('cpu', {}).get('usage', 0)
            mem_used = resources.get('memory', {}).get('used', 0)
            mem_total = resources.get('memory', {}).get('total', 32)
            mem_pct = (mem_used / mem_total * 100) if mem_total > 0 else 0
            io_util = resources.get('disk', {}).get('io_util', 0)
            latency = resources.get('network', {}).get('latency', 0)
            
            status_emoji = "🟢" if srv.get('status') == 'running' else "🔴"
            fault_status = f"⚠️ {self._get_fault_name(srv.get('fault_type'))}" if srv.get('fault_injected') else "✅ 正常"
            
            lines.append(f"| {srv.get('server_name', 'N/A')} | {srv.get('ip', 'N/A')} | {status_emoji} {srv.get('status', 'N/A')} | {cpu:.1f}% | {mem_pct:.1f}% | {io_util:.1f}% | {latency:.0f}ms | {fault_status} |")
        
        lines.append("")
        
        # 各服务器详细状态
        lines.append("## 📋 各服务器详细状态")
        lines.append("")
        
        for srv in virtual_servers:
            lines.append(f"### 🖥️ {srv.get('server_name', 'N/A')} ({srv.get('ip', 'N/A')})")
            lines.append("")
            
            resources = srv.get('resources', {})
            
            # CPU信息
            cpu_info = resources.get('cpu', {})
            cpu_status = "⚠️ 高负载" if cpu_info.get('usage', 0) > 80 else "✅ 正常"
            lines.append(f"**CPU状态:** {cpu_status}")
            lines.append(f"- 使用率: {cpu_info.get('usage', 0):.1f}%")
            lines.append(f"- 核心数: {cpu_info.get('cores', 0)}")
            lines.append(f"- 频率: {cpu_info.get('frequency', 0):.1f} GHz")
            lines.append(f"- 温度: {cpu_info.get('temperature', 0):.1f}°C")
            lines.append("")
            
            # 内存信息
            mem_info = resources.get('memory', {})
            mem_pct = (mem_info.get('used', 0) / mem_info.get('total', 32) * 100) if mem_info.get('total', 32) > 0 else 0
            mem_status = "⚠️ 高占用" if mem_pct > 85 else "✅ 正常"
            lines.append(f"**内存状态:** {mem_status}")
            lines.append(f"- 总量: {mem_info.get('total', 0):.0f} GB")
            lines.append(f"- 已用: {mem_info.get('used', 0):.1f} GB ({mem_pct:.1f}%)")
            lines.append(f"- 缓存: {mem_info.get('cached', 0):.1f} GB")
            lines.append(f"- Swap使用: {mem_info.get('swap_used', 0):.1f} GB")
            lines.append("")
            
            # 磁盘信息
            disk_info = resources.get('disk', {})
            io_status = "⚠️ IO繁忙" if disk_info.get('io_util', 0) > 80 else "✅ 正常"
            lines.append(f"**磁盘状态:** {io_status}")
            lines.append(f"- 总量: {disk_info.get('total', 0):.0f} GB")
            lines.append(f"- 已用: {disk_info.get('used', 0):.1f} GB")
            lines.append(f"- IO利用率: {disk_info.get('io_util', 0):.1f}%")
            lines.append(f"- 读取速度: {disk_info.get('read_speed', 0):.1f} MB/s")
            lines.append(f"- 写入速度: {disk_info.get('write_speed', 0):.1f} MB/s")
            lines.append("")
            
            # 网络信息
            net_info = resources.get('network', {})
            net_status = "⚠️ 高延迟" if net_info.get('latency', 0) > 100 else "✅ 正常"
            lines.append(f"**网络状态:** {net_status}")
            lines.append(f"- 延迟: {net_info.get('latency', 0):.1f} ms")
            lines.append(f"- 连接数: {net_info.get('connections', 0)}")
            lines.append(f"- 接收: {net_info.get('rx_bytes', 0) / 1024 / 1024:.2f} MB")
            lines.append(f"- 发送: {net_info.get('tx_bytes', 0) / 1024 / 1024:.2f} MB")
            lines.append("")
            
            # 服务状态
            services = srv.get('services', {})
            if services:
                lines.append(f"**服务状态:**")
                lines.append("")
                lines.append("| 服务名 | 状态 | PID | 端口 | 内存占用 |")
                lines.append("|:------|:-----|:----|:-----|:--------|")
                for svc_name, svc_info in services.items():
                    svc_status = "🟢 运行中" if svc_info.get('status') == 'running' else "🔴 已停止"
                    lines.append(f"| {svc_name} | {svc_status} | {svc_info.get('pid', 'N/A')} | {svc_info.get('port', 'N/A')} | {svc_info.get('memory', 0)} MB |")
                lines.append("")
            
            lines.append("---")
            lines.append("")
        
        # 故障历史与修复记录（如果有）
        if self.fault_history or self.repair_records:
            lines.append("## ⚠️ 故障历史与修复记录")
            lines.append("")
            
            if self.fault_history:
                lines.append("### 🔴 故障注入记录")
                lines.append("")
                for i, fault in enumerate(self.fault_history[-10:], 1):  # 最近10条
                    lines.append(f"**故障 #{i}**")
                    lines.append(f"- ⏰ 时间: {fault.get('timestamp', 'N/A')}")
                    lines.append(f"- 🖥️ 服务器: {fault.get('server_name', 'N/A')} ({fault.get('ip', 'N/A')})")
                    lines.append(f"- 🔴 故障类型: {self._get_fault_name(fault.get('fault_type', 'N/A'))}")
                    if fault.get('changes'):
                        lines.append(f"- 📊 影响:")
                        for change in fault.get('changes', []):
                            lines.append(f"  - {change}")
                    lines.append("")
            
            if self.repair_records:
                lines.append("### 🔧 修复操作详情")
                lines.append("")
                for i, repair in enumerate(self.repair_records[-10:], 1):  # 最近10条
                    action_name = self._get_action_name(repair.get('action', 'N/A'))
                    result_emoji = "✅" if repair.get('success') else "❌"
                    
                    lines.append(f"**修复操作 #{i}: {action_name}** {result_emoji}")
                    lines.append("")
                    lines.append(f"| 项目 | 内容 |")
                    lines.append(f"|:-----|:-----|")
                    lines.append(f"| 目标服务器 | {repair.get('server', 'N/A')} ({repair.get('ip', 'N/A')}) |")
                    lines.append(f"| 操作类型 | {action_name} |")
                    lines.append(f"| 开始时间 | {repair.get('start_time', 'N/A')} |")
                    lines.append(f"| 结束时间 | {repair.get('end_time', 'N/A')} |")
                    lines.append(f"| 执行耗时 | {repair.get('duration', 0):.2f} 秒 |")
                    lines.append(f"| 执行结果 | {result_emoji} {'成功' if repair.get('success') else '失败'} |")
                    lines.append("")
                    
                    # 详细执行步骤
                    steps = repair.get('steps', [])
                    if steps:
                        lines.append(f"**执行步骤:**")
                        lines.append("")
                        for step in steps:
                            step_status = "✅" if step.get('status') == 'completed' else "❌"
                            lines.append(f"{step.get('step', 0)}. {step_status} **{step.get('action', 'N/A')}**")
                            if step.get('command'):
                                lines.append(f"   - 命令: `{step.get('command')}`")
                            lines.append(f"   - 结果: {step.get('message', '')}")
                        lines.append("")
                    
                    # 命令输出
                    output = repair.get('output', [])
                    if output:
                        lines.append(f"**终端输出:**")
                        lines.append("")
                        lines.append("```bash")
                        for out in output[:15]:  # 限制输出行数
                            lines.append(out)
                        if len(output) > 15:
                            lines.append(f"... (共 {len(output)} 行输出)")
                        lines.append("```")
                        lines.append("")
                    
                    # 修复效果
                    changes = repair.get('changes', [])
                    if changes:
                        lines.append(f"**修复效果:**")
                        lines.append("")
                        for change in changes:
                            lines.append(f"- ✅ {change}")
                        lines.append("")
                    
                    lines.append("---")
                    lines.append("")
        
        # 当前故障状态
        if current_faults:
            lines.append("## 🚨 当前故障状态")
            lines.append("")
            lines.append("⚠️ **以下服务器当前存在故障，需要处理：**")
            lines.append("")
            for srv in current_faults:
                fault_name = self._get_fault_name(srv.get('fault_type', '未知'))
                lines.append(f"- **{srv.get('server_name', 'N/A')}** ({srv.get('ip', 'N/A')})")
                lines.append(f"  - 故障类型: {fault_name}")
                lines.append(f"  - 建议操作: {self._get_repair_suggestion(srv.get('fault_type'))}")
            lines.append("")
        
        # AI分析（如果有）
        if ai_analysis:
            lines.append("## 🤖 智能分析")
            lines.append("")
            lines.append(ai_analysis)
            lines.append("")
        
        # 报告结论
        lines.append("## 📝 报告结论")
        lines.append("")
        
        if current_faults:
            lines.append(f"### ⚠️ 系统存在 {len(current_faults)} 个待处理故障")
            lines.append("")
            lines.append("建议立即进行修复操作，确保系统稳定运行。")
            lines.append("")
            lines.append("**建议操作优先级：**")
            lines.append("")
            for i, srv in enumerate(current_faults, 1):
                lines.append(f"{i}. {srv.get('server_name')} - {self._get_repair_suggestion(srv.get('fault_type'))}")
        elif self.fault_history or self.repair_records:
            lines.append("### ✅ 故障已全部修复，系统运行正常")
            lines.append("")
            lines.append(f"本报告周期内共记录 {len(self.fault_history)} 个故障，执行 {len(self.repair_records)} 次修复操作。")
            lines.append("")
            lines.append("**修复统计：**")
            success_count = sum(1 for r in self.repair_records if r.get('success'))
            fail_count = len(self.repair_records) - success_count
            lines.append(f"- 成功: {success_count} 次")
            lines.append(f"- 失败: {fail_count} 次")
            if self.repair_records:
                total_duration = sum(r.get('duration', 0) for r in self.repair_records)
                lines.append(f"- 总耗时: {total_duration:.2f} 秒")
        else:
            lines.append("### ✅ 系统运行正常，无异常情况")
            lines.append("")
            lines.append("所有服务器资源使用正常，服务运行稳定。")
            lines.append("")
            lines.append("**监控建议：**")
            lines.append("- 保持日常巡检频率")
            lines.append("- 关注资源使用趋势")
            lines.append("- 定期检查日志异常")
        
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(f"*报告由西藏电网智能运维平台自动生成 | {timestamp.strftime('%Y-%m-%d %H:%M:%S')}*")
        
        return '\n'.join(lines)
    
    def _get_fault_name(self, fault_type: str) -> str:
        """获取故障类型中文名称"""
        fault_names = {
            'cpu_overload': 'CPU过载',
            'memory_exhaustion': '内存耗尽',
            'io_bottleneck': 'IO瓶颈',
            'service_down': '服务停止',
            'network_issue': '网络异常'
        }
        return fault_names.get(fault_type, fault_type or '未知')
    
    def _get_action_name(self, action: str) -> str:
        """获取操作类型中文名称"""
        action_names = {
            'restart_service': '重启服务',
            'restart_server': '重启服务器',
            'clear_cache': '清理缓存',
            'kill_process': '终止进程',
            'cleanup_disk': '清理磁盘',
            'reset_network': '重置网络',
            'optimize_memory': '优化内存',
            'reduce_cpu_load': '降低CPU负载',
            'status_check': '状态检查'
        }
        return action_names.get(action, action or '未知操作')
    
    def _get_repair_suggestion(self, fault_type: str) -> str:
        """获取修复建议"""
        suggestions = {
            'cpu_overload': '执行"降低CPU负载"或"终止高CPU进程"',
            'memory_exhaustion': '执行"优化内存"或"清理缓存"',
            'io_bottleneck': '执行"清理磁盘"',
            'service_down': '执行"重启服务"',
            'network_issue': '执行"重置网络"'
        }
        return suggestions.get(fault_type, '检查服务器状态')

    def generate_real_server_report(self, real_servers: List[Dict],
                                    ai_analysis: str = "",
                                    inspect_map: Dict = None,
                                    manual_inspection: Dict = None) -> Dict[str, Any]:
        """
        生成真实服务器（Agent）监控报告
        - 监控通过Agent连接的真实服务器状态
        - v5.7: 按系统分组，每系统输出 17 项巡检深度分析（Agent /inspect 真采 + 人工录入合并）
        """
        report_id = str(uuid.uuid4())[:8].upper()
        timestamp = datetime.now(BEIJING_TZ)
        timestamp_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
        
        # 检查服务器状态
        online_count = sum(1 for s in real_servers if s.get('status') not in ['offline', 'unknown'])
        offline_count = sum(1 for s in real_servers if s.get('status') in ['offline', 'unknown'])
        warning_count = sum(1 for s in real_servers if s.get('status') == 'warning')
        critical_count = sum(1 for s in real_servers if s.get('status') == 'critical')
        
        if critical_count > 0:
            report_type = "紧急告警报告"
        elif warning_count > 0:
            report_type = "预警监控报告"
        else:
            report_type = "日常运行监控报告"
        
        content = self._build_real_server_report(
            real_servers=real_servers,
            ai_analysis=ai_analysis,
            report_type=report_type,
            report_id=report_id,
            timestamp=timestamp,
            inspect_map=inspect_map or {},
            manual_inspection=manual_inspection or {}
        )
        
        # 保存报告
        filename = f'real_server_report_{report_id}_{timestamp.strftime("%Y%m%d_%H%M%S")}.md'
        filepath = os.path.join(self.output_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return {
            'success': True,
            'report_id': report_id,
            'filename': filename,
            'file_path': filepath,
            'content': content,
            'report_type': report_type,
            'timestamp': timestamp_str,
            'summary': {
                'total_servers': len(real_servers),
                'online_count': online_count,
                'offline_count': offline_count,
                'warning_count': warning_count,
                'critical_count': critical_count
            }
        }
    
    def _build_real_server_report(self, real_servers: List[Dict],
                                   ai_analysis: str,
                                   report_type: str,
                                   report_id: str,
                                   timestamp: datetime,
                                   inspect_map: Dict = None,
                                   manual_inspection: Dict = None) -> str:
        """构建真实服务器报告内容"""
        lines = []
        
        # 报告头部
        lines.append(f"# 🖥️ 西藏电网智能运维 - 真实服务器{report_type}")
        lines.append("")
        lines.append("## 报告信息")
        lines.append("")
        lines.append("| 项目 | 内容 |")
        lines.append("|:-----|:-----|")
        lines.append(f"| 报告编号 | {report_id} |")
        lines.append(f"| 生成时间 | {timestamp.strftime('%Y-%m-%d %H:%M:%S')} |")
        lines.append(f"| 报告类型 | 真实服务器{report_type} |")
        lines.append(f"| 监控服务器数量 | {len(real_servers)} |")
        
        online_count = sum(1 for s in real_servers if s.get('status') not in ['offline', 'unknown'])
        offline_count = len(real_servers) - online_count
        lines.append(f"| 在线服务器 | {online_count} |")
        lines.append(f"| 离线服务器 | {offline_count} |")
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # 服务器状态概览
        lines.append("## 📊 真实服务器状态概览")
        lines.append("")
        
        if not real_servers:
            lines.append("> ⚠️ **暂无已添加的真实服务器**")
            lines.append(">")
            lines.append("> 请在「运行监控」->「真实服务器」中添加部署了Agent的服务器")
            lines.append("")
        else:
            lines.append("| 服务器名称 | IP地址 | 状态 | CPU | 内存 | 磁盘 | 最后更新 |")
            lines.append("|:---------|:------|:-----|:---:|:----:|:----:|:-------:|")
            
            for srv in real_servers:
                name = srv.get('name', '未知')
                host = srv.get('host', '未知')
                status = srv.get('status', 'unknown')
                
                status_icon = {
                    'normal': '🟢 正常',
                    'running': '🟢 运行中',
                    'warning': '🟡 警告',
                    'critical': '🔴 严重',
                    'offline': '⚫ 离线',
                    'unknown': '⚪ 未知'
                }.get(status, '⚪ 未知')
                
                metrics = srv.get('metrics', {})
                cpu = metrics.get('cpu', {})
                memory = metrics.get('memory', {})
                disk = metrics.get('disk', {})
                
                # 兼容多种字段名: usage, percent, usage_percent, current
                cpu_usage = cpu.get('usage', cpu.get('percent', cpu.get('current', 0)))
                
                # 内存: 优先使用percent/usage_percent，否则计算 used/total*100
                mem_usage = memory.get('percent', memory.get('usage_percent', memory.get('usage', 0)))
                if mem_usage == 0 and memory.get('total', 0) > 0:
                    mem_usage = (memory.get('used', 0) / memory.get('total', 1)) * 100
                mem_usage = round(float(mem_usage), 1) if mem_usage else 0
                
                # 磁盘: 优先使用percent/usage_percent
                disk_usage = disk.get('percent', disk.get('usage_percent', disk.get('usage', 0)))
                try:
                    disk_usage = float(str(disk_usage).replace('%', ''))
                except:
                    disk_usage = 0
                disk_usage = round(disk_usage, 1)
                
                last_update = srv.get('last_update', '-')
                if last_update and len(last_update) > 16:
                    last_update = last_update[11:16]  # 只显示时:分
                
                lines.append(f"| {name} | {host} | {status_icon} | {cpu_usage:.1f}% | {mem_usage:.1f}% | {disk_usage:.1f}% | {last_update} |")
            
            lines.append("")

        # v5.7: 按系统深度分析（17 项巡检）—— 领导要求的核心章节
        lines.append(self._build_system_analysis(real_servers, inspect_map, manual_inspection))

        # AI分析（如果有）
        if ai_analysis:
            lines.append("## 🤖 AI智能分析")
            lines.append("")
            lines.append(ai_analysis)
            lines.append("")

        # 运维建议
        lines.append("## 💡 运维建议")
        lines.append("")
        
        warning_servers = [s for s in real_servers if s.get('status') == 'warning']
        critical_servers = [s for s in real_servers if s.get('status') == 'critical']
        offline_servers = [s for s in real_servers if s.get('status') in ['offline', 'unknown']]
        
        if critical_servers:
            lines.append("### 🔴 紧急处理")
            for srv in critical_servers:
                lines.append(f"- **{srv.get('name')}**: 服务器状态严重异常，需立即排查")
            lines.append("")
        
        if warning_servers:
            lines.append("### 🟡 需要关注")
            for srv in warning_servers:
                lines.append(f"- **{srv.get('name')}**: 服务器指标超出正常范围，建议检查")
            lines.append("")
        
        if offline_servers:
            lines.append("### ⚫ 离线服务器")
            for srv in offline_servers:
                lines.append(f"- **{srv.get('name')}** ({srv.get('host')}): 无法连接，请检查Agent服务是否正常运行")
            lines.append("")
        
        if not warning_servers and not critical_servers and not offline_servers:
            lines.append("✅ 所有真实服务器运行正常，无需特别处理。")
            lines.append("")
        
        # 报告尾部
        lines.append("---")
        lines.append("")
        lines.append(f"*报告由西藏电网智能运维平台自动生成*")
        lines.append(f"*生成时间: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}*")
        
        return '\n'.join(lines)

    # ===== v5.7: 按系统 17 项巡检深度分析 =====

    def _build_system_analysis(self, servers: List[Dict], inspect_map: Dict = None,
                               manual: Dict = None) -> str:
        """按系统分组的 17 项巡检深度分析（领导要求）。
        每系统覆盖附件全部 17 项；能自动采集的用 Agent /inspect 真实数据，不可采集的用人工录入值，
        都没有则标注"待录入"，保证每项都在报告中出现——绝不编造数值。"""
        inspect_map = inspect_map or {}
        manual = manual or {}
        lines = ["## 🗂️ 按系统深度分析（对照巡检要点 17 项）", ""]
        lines.append("> 来源说明：`🟢自动` = Agent 实时采集；`✍️人工` = 运维核查录入；"
                     "`⚪待录入` = 尚未填报，请在「报告与效能评估 → 系统巡检项录入」补充。")
        lines.append("")

        # v5.8: 过滤"默认分组/空系统"——与前端 isHiddenSystem 一致（客户："默认分组之前就去掉了"）
        def _hidden_system(name):
            n = (name or '').strip()
            return n in ('', 'default', '默认分组')

        groups = {}
        for s in servers:
            sysname = (s.get('system') or '').strip()
            if _hidden_system(sysname):
                continue  # 默认分组/未分组不进按系统分析
            groups.setdefault(sysname, []).append(s)

        if not groups:
            lines.append("> 暂无已分组的系统。请在「运行监控」给服务器设置所属系统（如：人工智能平台 / 智能运维平台）后再生成按系统分析。")
            lines.append("")
            return '\n'.join(lines)

        for sysname, srvs in groups.items():
            mv = manual.get(sysname, {}) if isinstance(manual, dict) else {}
            agg = self._agg_inspect(srvs, inspect_map)
            online = [s for s in srvs if s.get('status') not in ('offline', 'unknown')]
            lines.append(f"### 🖥️ 系统：{sysname}")
            lines.append("")
            lines.append(f"- 服务器数量：**{len(srvs)}** 台（在线 {len(online)} / 离线 {len(srvs) - len(online)}）")
            node_names = '、'.join((s.get('name') or s.get('host') or '?') for s in srvs)
            lines.append(f"- 节点清单：{node_names}")
            if not online:
                lines.append("- ⚠️ 该系统全部节点离线，自动采集项暂无实时数据，已如实标注；人工录入项不受影响。")
            lines.append("")
            lines.append("| # | 巡检要点 | 来源 | 结论 / 数值 |")
            lines.append("|:--:|:-------|:----:|:-----------|")
            for i, (item, desc, src, val) in enumerate(self._system_dim_rows(srvs, online, agg, mv), 1):
                val = (val or '').replace('\n', ' ')
                # 巡检要点下挂一行"含义说明"（客户要求逐点说明可巡检内容）
                item_cell = f"**{item}**<br><span style=\"color:#6b7280;font-size:12px\">📖 {desc}</span>"
                lines.append(f"| {i} | {item_cell} | {src} | {val} |")
            lines.append("")
            # v5.8: 系统级补充说明（人工录入的 remark）
            remark = (mv.get('remark') or '').strip()
            if remark:
                lines.append(f"> 📝 **补充说明**：{remark}")
                lines.append("")
            # 该系统服务进程 Top（真实采集时附明细，丰富报告内容）
            if agg['top_proc']:
                lines.append(f"<details><summary>📋 {sysname} 服务进程明细（按内存占用 Top）</summary>")
                lines.append("")
                lines.append("| 节点 | 进程 | 用户 | 内存% | 线程 |")
                lines.append("|:----|:----|:----|:----:|:---:|")
                for host, tp in agg['top_proc'][:12]:
                    lines.append(f"| {host} | {tp.get('name', '')} | {tp.get('user', '')} | "
                                 f"{tp.get('mem_pct', 0)}% | {tp.get('threads', 0)} |")
                lines.append("")
                lines.append("</details>")
                lines.append("")
        return '\n'.join(lines)

    def _agg_inspect(self, srvs: List[Dict], inspect_map: Dict) -> Dict:
        """聚合一个系统内各服务器的 /inspect 采集数据。"""
        agg = {'thread_total': 0, 'established': 0, 'process_count': 0, 'cron': 0,
               'timers': 0, 'listen_ports': set(), 'services': {}, 'top_proc': [], 'has_data': False}
        for s in srvs:
            ins = inspect_map.get(s.get('id')) or {}
            if not ins:
                continue
            agg['has_data'] = True
            agg['thread_total'] += ins.get('thread_total', 0) or 0
            agg['established'] += ins.get('established_count', 0) or 0
            agg['process_count'] += ins.get('process_count', 0) or 0
            agg['cron'] += ins.get('cron_jobs', 0) or 0
            agg['timers'] += ins.get('systemd_timers', 0) or 0
            for p in ins.get('listen_ports', []) or []:
                agg['listen_ports'].add(p)
            for d in ins.get('detected_services', []) or []:
                agg['services'][d.get('service', '?')] = d.get('port')
            host = s.get('name') or s.get('host') or '?'
            for tp in (ins.get('top_processes', []) or [])[:5]:
                agg['top_proc'].append((host, tp))
        return agg

    def _system_dim_rows(self, srvs: List[Dict], online: List[Dict], agg: Dict, mv: Dict):
        """v5.8: 数据驱动生成 17 项巡检行：(巡检要点, 含义说明, 来源, 结论/数值)。
        三级取值：① Agent 自动采到 → 🟢自动；② 采不到但运维已人工录入 → ✍️人工；③ 都没有 → ⚪待录入。
        每个自动项采不到时也允许人工录入（客户要求：所有待录入项都有录入位置），绝不编造。"""
        A, M, W = '🟢自动', '✍️人工', '⚪待录入'

        def avg(metric):
            vals = []
            for s in online:
                mm = (s.get('metrics', {}) or {}).get(metric, {}) or {}
                v = mm.get('percent', mm.get('usage'))
                if v is not None:
                    try:
                        vals.append(float(str(v).rstrip('%')))
                    except Exception:
                        pass
            return (sum(vals) / len(vals)) if vals else None

        cpu, mem, disk = avg('cpu'), avg('memory'), avg('disk')

        def fmt(x):
            return f"{x:.1f}%" if x is not None else "—"

        svc = agg['services']

        def pick(*kw):
            return '、'.join(f"{k}(:{p})" for k, p in svc.items() if any(x in k for x in kw))

        redis_s = pick('Redis')
        db_s = pick('MySQL', 'PostgreSQL', 'Oracle', 'MongoDB', '数据库')
        mq_s = pick('RabbitMQ', 'Kafka', '消息队列')
        nacos_s = pick('Nacos')
        mw_s = '、'.join(f"{k}(:{p})" for k, p in svc.items()) if svc else ''
        ports = sorted(agg['listen_ports'])
        ports_str = '、'.join(str(p) for p in ports[:25]) + ('…' if len(ports) > 25 else '')
        has_auto = agg['has_data']
        metrics_str = (f"系统均值 CPU {fmt(cpu)} / 内存 {fmt(mem)} / 磁盘 {fmt(disk)}"
                       if cpu is not None else None)

        # 每个巡检项的自动采集值（None 表示该项本轮没采到 → 可由人工录入兜底）
        auto = {
            'slb_ports': (f"监听端口：{ports_str}" if ports else None),
            'redis': (f"检测到 {redis_s} 在运行" if redis_s else None),
            'database': (((f"检测到数据库：{db_s}；" if db_s else "") + (metrics_str or ""))
                         if (db_s or metrics_str) else None),
            'scheduled_task': (f"crontab/cron.d {agg['cron']} 项，systemd timers {agg['timers']} 项" if has_auto else None),
            'mq': (f"检测到 {mq_s} 在运行" if mq_s else None),
            'service_cpu_mem': (f"采集到 {agg['process_count']} 个进程，详见下方进程明细" if agg['top_proc'] else None),
            'middleware': (f"按端口探测到：{mw_s}" if mw_s else None),
            'nacos': (f"检测到 {nacos_s} 在运行" if nacos_s else None),
            'thread_count': (f"系统在线节点线程合计 {agg['thread_total']}" if has_auto else None),
            'jdbc': (f"已建立 TCP 连接合计 {agg['established']}（含 JDBC，精确连接数建议结合应用监控）" if has_auto else None),
            'process': (f"在线节点进程合计 {agg['process_count']} 个" if agg['top_proc'] else None),
            'cloud_server': (f"在线 {len(online)}/{len(srvs)} 台" + ("；" + metrics_str if metrics_str else "（实时指标暂无）")),
            # 纯人工项（auto=None）不在此 dict
        }

        rows = []
        for dim in INSPECTION_DIMS:
            key = dim['key']
            av = auto.get(key)  # 自动采集值（纯人工项为 None）
            mvv = (mv.get(key) or '').strip()
            if av:
                src = A
                val = av + (f"；运维补充：{mvv}" if mvv else "")
            elif mvv:
                src, val = M, mvv
            else:
                src, val = W, dim['pending']
            rows.append((dim['label'], dim['desc'], src, val))
        return rows

    # ===== 兼容原有接口 =====
    
    def generate_fault_report(
        self,
        analysis_data: Dict[str, Any],
        ai_analysis: str = "",
        model_name: str = "ML-Mini",
        repair_history: list = None,
        uploaded_fault: Dict[str, Any] = None,
        system_servers: List[Dict] = None,
        inspect_map: Dict = None,
        manual_inspection: Dict = None
    ) -> Dict[str, Any]:
        """生成故障检测报告（兼容原有接口）。v5.7: 追加按系统 17 项巡检深度分析。"""
        report_id = str(uuid.uuid4())[:8].upper()
        timestamp = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")

        # 构建各部分内容
        uploaded_section = self._build_uploaded_fault_section(uploaded_fault)
        system_overview = self._build_system_overview(analysis_data)
        fault_diagnosis = self._build_fault_diagnosis(analysis_data)
        recommendations = self._build_recommendations(analysis_data)
        repair_section = self._build_repair_history_section(repair_history)
        # v5.7: 按系统深度分析（领导要求，每系统覆盖 17 项）
        system_analysis = self._build_system_analysis(
            system_servers or [], inspect_map or {}, manual_inspection or {}
        ) if system_servers else ""
        
        report_content = f"""
# 西藏电网智能运维故障分析报告

## 报告信息
| 项目 | 内容 |
|------|------|
| 报告编号 | {report_id} |
| 生成时间 | {timestamp} |
| 分析引擎 | {model_name} |

---
{uploaded_section}
## 1. 系统状态概览

{system_overview}

---

## 2. 故障诊断结果

{fault_diagnosis}

---

## 3. {model_name}分析

{ai_analysis if ai_analysis else "系统运行正常，未检测到明显异常。"}

---

## 4. 自动修复记录

{repair_section}

---

## 5. 处置建议

{recommendations}

---

{system_analysis}

## 6. 检修计划建议

### 优先级排序
1. **紧急处理**: 状态为"critical"的服务器需立即处理
2. **计划维护**: 状态为"warning"的服务器需安排维护窗口
3. **日常巡检**: 正常服务器保持常规监控

### 建议维护时间
- 建议在业务低峰期(凌晨2:00-6:00)进行维护操作
- 重启操作前需确认业务影响范围

---

*报告由西藏电网智能运维平台(Mini版)自动生成*
"""
        
        # 保存报告
        report_filename = f"report_{report_id}_{datetime.now(BEIJING_TZ).strftime('%Y%m%d_%H%M%S')}.md"
        report_path = os.path.join(self.output_dir, report_filename)
        
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report_content)
        
        return {
            "report_id": report_id,
            "timestamp": timestamp,
            "model_name": model_name,
            "content": report_content,
            "file_path": report_path,
            "summary": {
                "total_servers": len(analysis_data.get("servers", [])),
                "critical_count": sum(1 for s in analysis_data.get("servers", []) if s.get("status") == "critical"),
                "warning_count": sum(1 for s in analysis_data.get("servers", []) if s.get("status") == "warning"),
                "normal_count": sum(1 for s in analysis_data.get("servers", []) if s.get("status") == "normal")
            }
        }
    
    def _build_system_overview(self, analysis_data: Dict[str, Any]) -> str:
        """构建系统概览"""
        servers = analysis_data.get("servers", [])
        
        if not servers:
            return "暂无服务器数据"
        
        overview = "### 服务器状态汇总\n\n"
        overview += "| 服务器 | 状态 | CPU(%) | 内存(%) | 异常数 |\n"
        overview += "|--------|------|--------|---------|--------|\n"
        
        for server in servers:
            status_emoji = {"normal": "🟢", "warning": "🟡", "critical": "🔴"}.get(server.get("status", "unknown"), "⚪")
            cpu = server.get("metrics", {}).get("cpu", {}).get("current", 0)
            mem = server.get("metrics", {}).get("memory", {}).get("current", 0)
            anomaly_count = len(server.get("anomalies", []))
            
            overview += f"| {server.get('server', 'Unknown')} | {status_emoji} {server.get('status', 'unknown')} | {cpu} | {mem} | {anomaly_count} |\n"
        
        return overview
    
    def _build_fault_diagnosis(self, analysis_data: Dict[str, Any]) -> str:
        """构建故障诊断结果"""
        servers = analysis_data.get("servers", [])
        
        diagnosis = ""
        has_issues = False
        
        for server in servers:
            anomalies = server.get("anomalies", [])
            if anomalies:
                has_issues = True
                diagnosis += f"### {server.get('server', 'Unknown')}\n\n"
                for anomaly in anomalies:
                    diagnosis += f"- ⚠️ {anomaly}\n"
                diagnosis += "\n"
        
        if not has_issues:
            diagnosis = "✅ 所有服务器运行正常，未检测到明显异常。\n"
        
        return diagnosis
    
    def _build_recommendations(self, analysis_data: Dict[str, Any]) -> str:
        """构建处置建议"""
        recommendations = "### 自动生成建议\n\n"
        
        servers = analysis_data.get("servers", [])
        critical_servers = [s for s in servers if s.get("status") == "critical"]
        warning_servers = [s for s in servers if s.get("status") == "warning"]
        
        if critical_servers:
            recommendations += "**紧急处理项:**\n"
            for server in critical_servers:
                recommendations += f"- 服务器 `{server.get('server')}` 需要立即检查\n"
            recommendations += "\n"
        
        if warning_servers:
            recommendations += "**需关注项:**\n"
            for server in warning_servers:
                recommendations += f"- 服务器 `{server.get('server')}` 需要关注\n"
            recommendations += "\n"
        
        if not critical_servers and not warning_servers:
            recommendations += "- 系统运行正常，建议保持日常监控\n"
        
        return recommendations
    
    def _build_uploaded_fault_section(self, uploaded_fault: Dict[str, Any] = None) -> str:
        """构建上传数据分析部分"""
        if not uploaded_fault:
            return ""
        
        section = "\n## 📤 用户上传数据分析\n\n"
        
        server = uploaded_fault.get('server', '上传数据')
        data_type = uploaded_fault.get('data_type', '未知类型')
        fault_detection = uploaded_fault.get('fault_detection') or {}
        
        section += f"**数据来源**: {server}\n\n"
        section += f"**数据类型**: {data_type}\n\n"
        
        if fault_detection and fault_detection.get('has_fault'):
            status = fault_detection.get('status', 'warning')
            status_emoji = "🔴" if status == 'critical' else "🟡"
            section += f"**故障状态**: {status_emoji} {status.upper()}\n\n"
            
            faults = fault_detection.get('faults', [])
            if faults:
                section += "### 检测到的故障\n\n"
                for i, fault in enumerate(faults, 1):
                    level = fault.get('level', 'warning')
                    level_emoji = "🔴" if level == 'critical' else "🟡"
                    section += f"{i}. {level_emoji} **{fault.get('type', '未知')}**: {fault.get('message', '')}\n"
                section += "\n"
        
        section += "---\n"
        return section
    
    def _build_repair_history_section(self, repair_history: list = None) -> str:
        """构建修复历史部分"""
        if not repair_history:
            return "暂无自动修复记录。\n"
        
        section = "### 最近修复记录\n\n"
        section += "| 时间 | 故障类型 | 状态 |\n"
        section += "|------|----------|------|\n"
        
        for record in repair_history[:5]:
            repairs = record.get('repairs', [])
            for repair in repairs:
                fault_type = repair.get('fault_type', 'Unknown')
                status = repair.get('status', 'unknown')
                status_emoji = "✅" if status == 'success' else "⚠️"
                start_time = repair.get('start_time', '')[:16]
                section += f"| {start_time} | {fault_type} | {status_emoji} {status} |\n"
        
        return section


# 单例模式
_generator_instance = None

def get_report_generator() -> ReportGenerator:
    """获取报告生成器实例"""
    global _generator_instance
    if _generator_instance is None:
        _generator_instance = ReportGenerator()
    return _generator_instance
