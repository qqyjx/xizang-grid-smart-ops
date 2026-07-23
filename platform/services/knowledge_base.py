# -*- coding: utf-8 -*-
"""
运维知识库服务
"""

import os
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import KNOWLEDGE_BASE_DIR

# 北京时间
BEIJING_TZ = timezone(timedelta(hours=8))


class KnowledgeBase:
    """运维知识库服务类"""
    
    def __init__(self):
        self.kb_dir = KNOWLEDGE_BASE_DIR
        os.makedirs(self.kb_dir, exist_ok=True)
        
        self.fault_patterns_file = os.path.join(self.kb_dir, 'fault_patterns.json')
        self.cases_file = os.path.join(self.kb_dir, 'historical_cases.json')
        self.solutions_file = os.path.join(self.kb_dir, 'solutions.json')
        
        self._init_knowledge_base()
    
    def _init_knowledge_base(self):
        """初始化知识库数据"""
        if not os.path.exists(self.fault_patterns_file):
            fault_patterns = {
                "patterns": [
                    {
                        "id": "FP001",
                        "name": "CPU过载",
                        "description": "CPU使用率持续超过90%",
                        "symptoms": ["系统响应缓慢", "进程卡顿", "任务队列积压"],
                        "possible_causes": ["计算密集型任务", "死循环", "资源泄漏", "并发请求过高"],
                        "severity": "high"
                    },
                    {
                        "id": "FP002",
                        "name": "内存溢出",
                        "description": "内存使用率持续超过90%或OOM",
                        "symptoms": ["服务崩溃", "OOM Killer触发", "Swap使用率高"],
                        "possible_causes": ["内存泄漏", "缓存未释放", "大数据处理", "配置不当"],
                        "severity": "high"
                    },
                    {
                        "id": "FP003",
                        "name": "磁盘IO瓶颈",
                        "description": "磁盘IO等待时间过长",
                        "symptoms": ["读写缓慢", "iowait高", "日志写入延迟"],
                        "possible_causes": ["磁盘空间不足", "日志过多", "数据库查询慢", "磁盘故障"],
                        "severity": "medium"
                    },
                    {
                        "id": "FP004",
                        "name": "网络连接异常",
                        "description": "网络连接失败或超时",
                        "symptoms": ["连接超时", "请求失败", "TCP重传率高"],
                        "possible_causes": ["网络拥塞", "防火墙配置", "DNS解析失败"],
                        "severity": "high"
                    },
                    {
                        "id": "FP005",
                        "name": "服务启动失败",
                        "description": "服务进程无法正常启动",
                        "symptoms": ["端口未监听", "进程不存在", "健康检查失败"],
                        "possible_causes": ["依赖服务未启动", "端口冲突", "配置错误"],
                        "severity": "high"
                    }
                ]
            }
            with open(self.fault_patterns_file, 'w', encoding='utf-8') as f:
                json.dump(fault_patterns, f, ensure_ascii=False, indent=2)
        
        if not os.path.exists(self.solutions_file):
            solutions = {
                "solutions": [
                    {
                        "id": "SOL001",
                        "fault_pattern": "FP001",
                        "title": "CPU过载处理方案",
                        "steps": [
                            "使用top/htop定位高CPU进程",
                            "分析进程是否异常",
                            "终止异常进程或优化代码",
                            "评估是否需要扩容"
                        ],
                        "auto_executable": False
                    },
                    {
                        "id": "SOL002",
                        "fault_pattern": "FP002",
                        "title": "内存溢出处理方案",
                        "steps": [
                            "使用free -h查看内存",
                            "清理系统缓存",
                            "重启内存泄漏服务",
                            "增加swap或物理内存"
                        ],
                        "auto_executable": True
                    },
                    {
                        "id": "SOL003",
                        "fault_pattern": "FP003",
                        "title": "IO瓶颈处理方案",
                        "steps": [
                            "使用iotop定位高IO进程",
                            "检查磁盘空间",
                            "优化IO密集型操作",
                            "考虑使用SSD"
                        ],
                        "auto_executable": False
                    }
                ]
            }
            with open(self.solutions_file, 'w', encoding='utf-8') as f:
                json.dump(solutions, f, ensure_ascii=False, indent=2)
    
    def get_fault_patterns(self) -> List[Dict]:
        """获取所有故障模式"""
        try:
            with open(self.fault_patterns_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('patterns', [])
        except:
            return []
    
    def get_solutions(self) -> List[Dict]:
        """获取所有解决方案"""
        try:
            with open(self.solutions_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                solutions = data.get('solutions', [])
                # 兼容旧格式：solutions 为 dict 时转为 list
                if isinstance(solutions, dict):
                    return [{'id': k, **(v if isinstance(v, dict) else {})} for k, v in solutions.items()]
                return solutions
        except:
            return []
    
    def search(self, query: str) -> Dict[str, Any]:
        """搜索知识库"""
        results = []
        query_lower = query.lower()
        
        # 搜索故障模式
        for pattern in self.get_fault_patterns():
            score = 0
            if query_lower in pattern.get('name', '').lower():
                score += 10
            if query_lower in pattern.get('description', '').lower():
                score += 5
            for symptom in pattern.get('symptoms', []):
                if query_lower in symptom.lower():
                    score += 3
            if score > 0:
                results.append({
                    'type': 'fault_pattern',
                    'data': pattern,
                    'score': score
                })
        
        # 搜索解决方案
        for solution in self.get_solutions():
            score = 0
            # 兼容旧格式：title 或 name 字段
            sol_title = solution.get('title', '') or solution.get('name', '')
            if query_lower in sol_title.lower():
                score += 10
            for step in solution.get('steps', []):
                if query_lower in step.lower():
                    score += 2
            if score > 0:
                results.append({
                    'type': 'solution',
                    'data': solution,
                    'score': score
                })

        # v5.59: 搜索历史案例（此前漏查，16 条案例对 CPU/内存/磁盘/OOM 关键词命中却不进结果）
        for case in self.get_cases():
            score = 0
            if query_lower in (case.get('title', '') or '').lower():
                score += 10
            if query_lower in (case.get('fault_type', '') or '').lower():
                score += 6
            if query_lower in (case.get('description', '') or '').lower():
                score += 5
            if query_lower in (case.get('root_cause', '') or '').lower():
                score += 4
            if query_lower in (case.get('resolution', '') or '').lower():
                score += 3
            if score > 0:
                results.append({
                    'type': 'historical_case',
                    'data': case,
                    'score': score
                })

        results.sort(key=lambda x: x['score'], reverse=True)
        
        return {
            'query': query,
            'total_results': len(results),
            'results': results[:10]
        }
    
    def get_solution_for_fault(self, fault_type: str) -> Optional[Dict]:
        """根据故障类型获取解决方案"""
        fault_type_lower = fault_type.lower()

        for solution in self.get_solutions():
            if fault_type_lower in solution.get('title', '').lower():
                return solution

        # 返回通用建议
        return {
            'title': f'{fault_type}处理建议',
            'steps': [
                '检查系统日志',
                '分析相关指标',
                '联系运维人员处理'
            ],
            'auto_executable': False
        }

    # ========== 故障模式 CRUD ==========

    def _save_patterns(self, patterns: List[Dict]):
        """保存故障模式到文件"""
        with open(self.fault_patterns_file, 'w', encoding='utf-8') as f:
            json.dump({"patterns": patterns}, f, ensure_ascii=False, indent=2)

    def add_pattern(self, data: Dict) -> Dict:
        """新增故障模式"""
        patterns = self.get_fault_patterns()
        if not data.get('id'):
            max_num = 0
            for p in patterns:
                pid = p.get('id', '')
                if pid.startswith('FP') and pid[2:].isdigit():
                    max_num = max(max_num, int(pid[2:]))
            data['id'] = f'FP{max_num + 1:03d}'
        data['created_at'] = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')
        patterns.append(data)
        self._save_patterns(patterns)
        return data

    def update_pattern(self, pattern_id: str, data: Dict) -> Optional[Dict]:
        """修改故障模式"""
        patterns = self.get_fault_patterns()
        for i, p in enumerate(patterns):
            if p.get('id') == pattern_id:
                data['id'] = pattern_id
                data['updated_at'] = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')
                patterns[i] = data
                self._save_patterns(patterns)
                return data
        return None

    def delete_pattern(self, pattern_id: str) -> bool:
        """删除故障模式"""
        patterns = self.get_fault_patterns()
        new_patterns = [p for p in patterns if p.get('id') != pattern_id]
        if len(new_patterns) == len(patterns):
            return False
        self._save_patterns(new_patterns)
        return True

    # ========== 处置方案 CRUD ==========

    def _save_solutions(self, solutions: List[Dict]):
        """保存处置方案到文件"""
        with open(self.solutions_file, 'w', encoding='utf-8') as f:
            json.dump({"solutions": solutions}, f, ensure_ascii=False, indent=2)

    def add_solution(self, data: Dict) -> Dict:
        """新增处置方案"""
        solutions = self.get_solutions()
        if not data.get('id'):
            max_num = 0
            for s in solutions:
                sid = s.get('id', '')
                if sid.startswith('SOL') and sid[3:].isdigit():
                    max_num = max(max_num, int(sid[3:]))
            data['id'] = f'SOL{max_num + 1:03d}'
        data['created_at'] = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')
        solutions.append(data)
        self._save_solutions(solutions)
        return data

    def update_solution(self, solution_id: str, data: Dict) -> Optional[Dict]:
        """修改处置方案"""
        solutions = self.get_solutions()
        for i, s in enumerate(solutions):
            if s.get('id') == solution_id:
                data['id'] = solution_id
                data['updated_at'] = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')
                solutions[i] = data
                self._save_solutions(solutions)
                return data
        return None

    def delete_solution(self, solution_id: str) -> bool:
        """删除处置方案"""
        solutions = self.get_solutions()
        new_solutions = [s for s in solutions if s.get('id') != solution_id]
        if len(new_solutions) == len(solutions):
            return False
        self._save_solutions(new_solutions)
        return True

    # ========== 历史案例 CRUD ==========

    def get_cases(self) -> List[Dict]:
        """获取所有历史案例"""
        try:
            with open(self.cases_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('cases', [])
        except:
            return []

    def _save_cases(self, cases: List[Dict]):
        """保存历史案例到文件"""
        with open(self.cases_file, 'w', encoding='utf-8') as f:
            json.dump({"cases": cases}, f, ensure_ascii=False, indent=2)

    def add_case(self, data: Dict) -> Dict:
        """新增历史案例"""
        cases = self.get_cases()
        if not data.get('id'):
            max_num = 0
            for c in cases:
                cid = c.get('id', '')
                if cid.startswith('CASE') and cid[4:].isdigit():
                    max_num = max(max_num, int(cid[4:]))
            data['id'] = f'CASE{max_num + 1:03d}'
        if not data.get('time'):
            data['time'] = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')
        data['created_at'] = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')
        cases.append(data)
        self._save_cases(cases)
        return data

    def update_case(self, case_id: str, data: Dict) -> Optional[Dict]:
        """修改历史案例"""
        cases = self.get_cases()
        for i, c in enumerate(cases):
            if c.get('id') == case_id:
                data['id'] = case_id
                data['updated_at'] = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')
                cases[i] = data
                self._save_cases(cases)
                return data
        return None

    def delete_case(self, case_id: str) -> bool:
        """删除历史案例"""
        cases = self.get_cases()
        new_cases = [c for c in cases if c.get('id') != case_id]
        if len(new_cases) == len(cases):
            return False
        self._save_cases(new_cases)
        return True


# 单例模式
_kb_instance = None

def get_knowledge_base() -> KnowledgeBase:
    """获取知识库实例"""
    global _kb_instance
    if _kb_instance is None:
        _kb_instance = KnowledgeBase()
    return _kb_instance
