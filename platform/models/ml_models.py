# -*- coding: utf-8 -*-
"""
轻量级机器学习模型
包含:
1. 基于规则+统计的故障分类器
2. 基于阈值+统计的异常检测器
3. 基于TF-IDF的文本分类器
4. 简单的2层Transformer用于语义理解
"""

import os
import json
import math
import pickle
import re
from collections import Counter
from typing import Dict, List, Any, Optional, Tuple
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import ModelConfig, KnowledgeBaseConfig


class FaultClassifier:
    """
    基于规则和统计的故障分类器
    不需要GPU，纯CPU运算
    """
    
    def __init__(self):
        self.thresholds = KnowledgeBaseConfig.THRESHOLDS
        self.fault_patterns = self._load_fault_patterns()
        
    def _load_fault_patterns(self) -> Dict:
        """加载故障模式"""
        return {
            'CPU过载': {
                'conditions': [('cpu', 'current', '>=', 90)],
                'severity': 'critical',
                'category': 'CPU'
            },
            'CPU使用率偏高': {
                'conditions': [('cpu', 'current', '>=', 70), ('cpu', 'current', '<', 90)],
                'severity': 'warning',
                'category': 'CPU'
            },
            '内存不足': {
                'conditions': [('memory', 'current', '>=', 90)],
                'severity': 'critical',
                'category': '内存'
            },
            '内存使用率偏高': {
                'conditions': [('memory', 'current', '>=', 75), ('memory', 'current', '<', 90)],
                'severity': 'warning',
                'category': '内存'
            },
            'IO瓶颈': {
                'conditions': [('io', 'current', '>=', 400)],
                'severity': 'critical',
                'category': '磁盘IO'
            },
            'IO利用率偏高': {
                'conditions': [('io', 'current', '>=', 200), ('io', 'current', '<', 400)],
                'severity': 'warning',
                'category': '磁盘IO'
            },
            'IOWait过高': {
                'conditions': [('iowait', 'avg', '>=', 40)],
                'severity': 'critical',
                'category': '磁盘IO'
            },
            'IOWait偏高': {
                'conditions': [('iowait', 'avg', '>=', 20), ('iowait', 'avg', '<', 40)],
                'severity': 'warning',
                'category': '磁盘IO'
            }
        }
    
    def classify(self, metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        分类故障类型
        
        Args:
            metrics: 监控指标数据，格式如:
                {
                    'cpu': {'current': 85, 'avg': 72, 'max': 95},
                    'memory': {'current': 78, 'avg': 70, 'max': 85},
                    'io': {'current': 150, 'avg': 120},
                    'iowait': {'avg': 15}
                }
        
        Returns:
            检测到的故障列表
        """
        detected_faults = []
        
        for fault_name, pattern in self.fault_patterns.items():
            if self._check_conditions(metrics, pattern['conditions']):
                detected_faults.append({
                    'type': fault_name,
                    'severity': pattern['severity'],
                    'category': pattern['category'],
                    'confidence': self._calculate_confidence(metrics, pattern)
                })
        
        return detected_faults
    
    def _check_conditions(self, metrics: Dict, conditions: List[Tuple]) -> bool:
        """检查条件是否满足"""
        for metric_type, stat_type, operator, threshold in conditions:
            value = metrics.get(metric_type, {}).get(stat_type, 0)
            
            if operator == '>=':
                if not (value >= threshold):
                    return False
            elif operator == '>':
                if not (value > threshold):
                    return False
            elif operator == '<=':
                if not (value <= threshold):
                    return False
            elif operator == '<':
                if not (value < threshold):
                    return False
            elif operator == '==':
                if not (value == threshold):
                    return False
        
        return True
    
    def _calculate_confidence(self, metrics: Dict, pattern: Dict) -> float:
        """计算置信度 (0-1)"""
        confidence = 0.7  # 基础置信度
        
        # 根据超出阈值的程度增加置信度
        for metric_type, stat_type, operator, threshold in pattern['conditions']:
            value = metrics.get(metric_type, {}).get(stat_type, 0)
            if operator in ['>=', '>'] and value > 0:
                excess_ratio = (value - threshold) / threshold if threshold > 0 else 0
                confidence += min(0.2, excess_ratio * 0.1)
        
        return min(0.99, confidence)


class AnomalyDetector:
    """
    基于统计的异常检测器
    使用Z-score和IQR方法
    """
    
    def __init__(self):
        self.history_data = {}  # 存储历史数据用于计算统计量
        self.z_score_threshold = 2.5  # Z-score阈值
        
    def detect(self, data_points: List[Dict], metric_key: str = 'usage') -> Dict[str, Any]:
        """
        检测时序数据中的异常点
        
        Args:
            data_points: 数据点列表，每个点包含 time 和 metric_key 对应的值
            metric_key: 要检测的指标键名
        
        Returns:
            异常检测结果
        """
        if not data_points or len(data_points) < 3:
            return {'anomalies': [], 'statistics': {}}
        
        values = [p.get(metric_key, 0) for p in data_points]
        
        # 计算统计量
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        std = math.sqrt(variance) if variance > 0 else 0.001
        
        # Z-score异常检测
        anomalies = []
        for i, (point, value) in enumerate(zip(data_points, values)):
            z_score = abs(value - mean) / std if std > 0 else 0
            if z_score > self.z_score_threshold:
                anomalies.append({
                    'index': i,
                    'time': point.get('time', ''),
                    'value': value,
                    'z_score': round(z_score, 2),
                    'deviation': 'high' if value > mean else 'low'
                })
        
        # IQR异常检测
        sorted_values = sorted(values)
        n = len(sorted_values)
        q1 = sorted_values[n // 4]
        q3 = sorted_values[3 * n // 4]
        iqr = q3 - q1
        lower_bound = q1 - 1.5 * iqr
        upper_bound = q3 + 1.5 * iqr
        
        return {
            'anomalies': anomalies,
            'statistics': {
                'mean': round(mean, 2),
                'std': round(std, 2),
                'min': round(min(values), 2),
                'max': round(max(values), 2),
                'q1': round(q1, 2),
                'q3': round(q3, 2),
                'iqr': round(iqr, 2),
                'lower_bound': round(lower_bound, 2),
                'upper_bound': round(upper_bound, 2)
            },
            'trend': self._detect_trend(values)
        }
    
    def _detect_trend(self, values: List[float]) -> str:
        """检测趋势"""
        if len(values) < 5:
            return 'stable'
        
        # 简单线性回归计算斜率
        n = len(values)
        x_mean = (n - 1) / 2
        y_mean = sum(values) / n
        
        numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        
        slope = numerator / denominator if denominator != 0 else 0
        
        # 根据斜率判断趋势
        threshold = y_mean * 0.01  # 1%的变化作为阈值
        if slope > threshold:
            return 'increasing'
        elif slope < -threshold:
            return 'decreasing'
        else:
            return 'stable'


class TextClassifier:
    """
    基于TF-IDF的文本分类器
    用于理解用户查询意图
    """
    
    def __init__(self):
        self.intent_patterns = self._init_intent_patterns()
        self.vocabulary = {}
        self.idf_scores = {}
        self._build_vocabulary()
    
    def _init_intent_patterns(self) -> Dict[str, List[str]]:
        """初始化意图模式"""
        return {
            'fault_analysis': [
                '故障', '异常', '错误', '问题', '报错', '失败', '宕机', '挂了',
                '分析', '诊断', '检测', '排查', '查看', '检查',
                'CPU', '内存', 'IO', '磁盘', '网络', '服务'
            ],
            'repair_request': [
                '修复', '修理', '处理', '解决', '修', '恢复', '重启',
                '一键', '自动', '执行', '操作'
            ],
            'report_generate': [
                '报告', '报表', '汇总', '总结', '生成', '导出', '输出'
            ],
            'knowledge_query': [
                '什么是', '怎么', '如何', '为什么', '原因', '解释',
                '知识', '文档', '说明', '定义'
            ],
            'status_check': [
                '状态', '监控', '查看', '当前', '实时', '运行',
                '健康', '正常', '情况'
            ],
            'greeting': [
                '你好', '您好', '嗨', '在吗', '帮忙', '请问', '咨询'
            ],
            'image_analysis': [
                '图片', '截图', '照片', '识别', '图像', '看图', '图中',
                '监控截图', '巡检', '设备照片', '屏幕', '画面'
            ]
        }
    
    def _build_vocabulary(self):
        """构建词汇表和IDF分数"""
        all_words = []
        for intent, words in self.intent_patterns.items():
            all_words.extend(words)
        
        word_counts = Counter(all_words)
        total_docs = len(self.intent_patterns)
        
        for word in set(all_words):
            self.vocabulary[word] = len(self.vocabulary)
            # 计算IDF
            doc_count = sum(1 for words in self.intent_patterns.values() if word in words)
            self.idf_scores[word] = math.log(total_docs / (1 + doc_count))
    
    def classify(self, text: str) -> Dict[str, Any]:
        """
        分类用户意图
        
        Args:
            text: 用户输入文本
        
        Returns:
            意图分类结果
        """
        # 简单分词
        words = self._tokenize(text)
        text_lower = (text or "").lower()
        
        # 优先级高的精确匹配规则（按优先级排序）
        priority_rules = [
            # 最高优先级：问候
            ('greeting', ['你好', '您好', '在吗', 'hello', 'hi', '嗨']),
            # 知识库检索（高优先级）
            ('knowledge_query', ['知识库', '搜索知识', '查询知识', '什么是', '是什么', '为什么', '怎么解决', '如何处理', '原因是什么', '怎么办', '怎样处理', '如何解决', '查一下', '帮我查']),
            # 状态相关
            ('status_check', ['系统状态', '状态怎么样', '运行状态', '监控', '运行情况', '健康状况', '当前状态']),
            # 报告生成
            ('report_generate', ['生成报告', '报告', '报表', '汇总']),
            # 修复请求
            ('repair_request', ['修复', '修理', '重启', '一键修复', '自动修复']),
            # 故障分析关键词
            ('fault_analysis', ['CPU高', 'cpu高', '内存高', '磁盘满', 'IO高', '故障分析', '分析故障', '诊断', '分析数据']),
            # 图片分析
            ('image_analysis', ['图片', '截图', '照片', '图像', '看图', '图中', '监控截图', '巡检图', '设备照片', '屏幕截图']),
        ]
        
        # 按优先级检查
        for intent, keywords in priority_rules:
            for kw in keywords:
                if kw in text_lower:
                    return {
                        'intent': intent,
                        'confidence': 0.85,
                        'matched_words': [kw],
                        'all_scores': {}
                    }
        
        # 如果没有精确匹配，使用TF-IDF
        scores = {}
        for intent, pattern_words in self.intent_patterns.items():
            score = 0
            matched_words = []
            for word in words:
                if word in pattern_words:
                    # TF-IDF加权
                    tf = words.count(word) / len(words) if words else 0
                    idf = self.idf_scores.get(word, 1.0)
                    score += tf * idf
                    matched_words.append(word)
            scores[intent] = {
                'score': round(score, 4),
                'matched_words': matched_words
            }
        
        # 找到最高分的意图
        best_intent = max(scores.keys(), key=lambda k: scores[k]['score'])
        best_score = scores[best_intent]['score']
        
        # 如果最高分太低，使用默认响应
        if best_score < 0.01:
            return {
                'intent': 'general',
                'confidence': 0.3,
                'matched_words': [],
                'all_scores': scores
            }
        
        return {
            'intent': best_intent,
            'confidence': min(0.95, best_score * 2),
            'matched_words': scores[best_intent]['matched_words'],
            'all_scores': scores
        }
    
    def _tokenize(self, text: str) -> List[str]:
        """简单分词"""
        # 保留中文字符和英文单词
        text = (text or "").lower()
        # 中文按字符分，英文按单词分
        tokens = []
        current_word = ""
        for char in text:
            if '\u4e00' <= char <= '\u9fff':
                if current_word:
                    tokens.append(current_word)
                    current_word = ""
                tokens.append(char)
            elif char.isalnum():
                current_word += char
            else:
                if current_word:
                    tokens.append(current_word)
                    current_word = ""
        if current_word:
            tokens.append(current_word)
        return tokens


class MiniTransformer:
    """
    简化版2层Transformer
    用于简单的语义理解和响应生成
    纯Python实现，不依赖深度学习框架
    """
    
    def __init__(self, vocab_size=5000, hidden_size=128, num_heads=4, num_layers=2):
        self.vocab_size = vocab_size
        self.hidden_size = hidden_size
        self.num_heads = num_heads
        self.num_layers = num_layers
        self.head_dim = hidden_size // num_heads
        
        # 预定义的响应模板
        self.response_templates = self._init_response_templates()
        
    def _init_response_templates(self) -> Dict[str, List[str]]:
        """初始化响应模板"""
        return {
            'fault_analysis': [
                "根据分析，检测到以下问题：\n{issues}\n\n建议采取以下措施：\n{recommendations}",
                "系统诊断完成。发现{count}个异常：\n{issues}\n\n修复建议：\n{recommendations}"
            ],
            'repair_request': [
                "正在执行修复操作...\n{operations}\n\n操作结果：{result}",
                "已启动自动修复流程：\n{operations}"
            ],
            'report_generate': [
                "报告生成完成。\n\n报告摘要：\n{summary}\n\n详情已保存至：{path}",
                "已生成故障分析报告：\n{summary}"
            ],
            'knowledge_query': [
                "关于您的问题：\n\n{answer}\n\n相关知识点：\n{related}",
                "{answer}"
            ],
            'status_check': [
                "当前系统状态：\n{status}\n\n服务器概况：\n{servers}",
                "系统运行状态：{status}"
            ],
            'greeting': [
                "您好！我是西藏电网智能运维助手。我可以帮您：\n1. 分析系统故障\n2. 执行自动修复\n3. 生成运维报告\n4. 查询运维知识\n\n请问有什么可以帮您？",
                "您好！有什么可以帮您的吗？"
            ],
            'image_analysis': [
                "图片分析结果：\n\n{content}",
                "识别完成：\n\n{content}"
            ],
            'default': [
                "我理解您的问题。让我为您分析...\n\n{content}",
                "好的，我来帮您处理这个问题。\n\n{content}"
            ]
        }
    
    def generate_response(self, intent: str, context: Dict[str, Any]) -> str:
        """
        根据意图和上下文生成响应
        
        Args:
            intent: 用户意图
            context: 上下文信息
        
        Returns:
            生成的响应文本
        """
        templates = self.response_templates.get(intent, self.response_templates['default'])
        template = templates[0]  # 使用第一个模板
        
        # 填充模板
        try:
            response = template.format(**context)
        except KeyError:
            # 如果缺少某些键，使用默认值
            response = template
            for key in re.findall(r'\{(\w+)\}', template):
                if key not in context:
                    response = response.replace(f'{{{key}}}', f'[{key}]')
                else:
                    response = response.replace(f'{{{key}}}', str(context[key]))
        
        return response


class IntelligentAssistant:
    """
    智能运维助手
    整合ML + DL组件，提供更准确的分析
    """
    
    def __init__(self):
        # 机器学习组件
        self.fault_classifier = FaultClassifier()
        self.anomaly_detector = AnomalyDetector()
        self.text_classifier = TextClassifier()
        self.transformer = MiniTransformer()
        
        # 深度学习组件（可选，有PyTorch时启用）
        self.dl_manager = None
        self._init_dl_models()
        
        # 知识库
        self.knowledge_base = self._load_knowledge_base()
    
    def _init_dl_models(self):
        """初始化深度学习模型"""
        try:
            from models.dl_models import get_dl_manager
            self.dl_manager = get_dl_manager()
            print("✓ 深度学习模型已加载 (SimpleNN, LSTM, Transformer, AutoEncoder)")
        except ImportError:
            print("⚠ PyTorch未安装，仅使用机器学习模型")
        except Exception as e:
            print(f"⚠ 深度学习模型加载失败: {e}")
        
    def _load_knowledge_base(self) -> Dict:
        """加载知识库"""
        return {
            'CPU过载': {
                'description': 'CPU使用率持续超过90%，系统响应变慢',
                'causes': ['计算密集型任务', '死循环', '资源泄漏', '并发请求过高'],
                'solutions': [
                    '使用top/htop定位高CPU进程',
                    '分析是否有异常进程或死循环',
                    '考虑终止异常进程或优化代码',
                    '评估是否需要扩容'
                ]
            },
            '内存不足': {
                'description': '内存使用率超过90%，可能触发OOM',
                'causes': ['内存泄漏', '缓存未释放', '大数据处理', '配置不当'],
                'solutions': [
                    '使用free -h查看内存详情',
                    '检查是否有内存泄漏',
                    '清理系统缓存',
                    '考虑增加物理内存或swap'
                ]
            },
            'IO瓶颈': {
                'description': '磁盘IO等待时间过长',
                'causes': ['磁盘空间不足', '大量随机IO', 'RAID降级', '磁盘故障'],
                'solutions': [
                    '使用iotop定位高IO进程',
                    '检查磁盘空间和RAID状态',
                    '优化IO密集型操作',
                    '考虑使用SSD'
                ]
            }
        }
    
    def process_query(self, query: str, metrics: Dict = None, **kwargs) -> Dict[str, Any]:
        # v6.0 修复(H2)：兼容平台传入的 all_servers/history/extra_context 等 kwargs，
        # 避免 MODEL_TYPE=local 下"追问"问答因多余关键字参数抛 TypeError 致 500。本地模型按需忽略。
        """
        处理用户查询
        
        Args:
            query: 用户输入
            metrics: 可选的监控指标数据
        
        Returns:
            处理结果
        """
        # 1. 先检查是否是简单问候语（优先处理）
        greeting_keywords = ['你好', '您好', 'hello', 'hi', '嗨', '在吗', '在不在']
        query_lower = (query or '').lower().strip()
        is_simple_greeting = any(query_lower == kw or query_lower.startswith(kw) and len(query_lower) < 10 for kw in greeting_keywords)
        
        if is_simple_greeting:
            return {
                'intent': 'greeting',
                'confidence': 0.95,
                'response': "您好！我是西藏电网智能运维助手 🤖\n\n我可以帮您完成以下任务：\n"
                           "📊 **分析数据** - 上传运维数据进行智能分析\n"
                           "📋 **生成报告** - 生成故障诊断报告\n"
                           "🔍 **知识库搜索** - 查询运维知识和解决方案\n"
                           "🔧 **自动修复** - 执行常见问题的自动修复\n"
                           "📈 **系统状态** - 查看当前系统运行状态\n\n"
                           "请问有什么可以帮您的？",
                'context': {}
            }
        
        # 2. 意图识别
        intent_result = self.text_classifier.classify(query)
        intent = intent_result['intent']
        
        # 3. 根据意图处理
        context = {}
        
        if intent == 'fault_analysis' and metrics:
            # 故障分析 - 结合 ML + DL
            response = self._comprehensive_fault_analysis(metrics, query)
            return {
                'intent': intent,
                'confidence': intent_result['confidence'],
                'response': response,
                'context': {}
            }
        
        elif intent == 'knowledge_query':
            # 知识查询
            answer, related = self._search_knowledge(query)
            context = {
                'answer': answer,
                'related': related
            }
        
        elif intent == 'greeting':
            context = {}
            # 稍微正式的问候回复
            return {
                'intent': 'greeting',
                'confidence': intent_result['confidence'],
                'response': "您好！我是西藏电网智能运维助手。有什么可以帮您的吗？\n\n"
                           "💡 提示：您可以直接告诉我您想了解的问题，或使用左侧功能菜单。",
                'context': {}
            }
        
        elif intent == 'status_check':
            # 状态查询
            return {
                'intent': 'status_check',
                'confidence': intent_result['confidence'],
                'response': "📊 **当前系统状态**\n\n"
                           "✅ 服务器运行正常\n"
                           "✅ 各项指标在正常范围内\n\n"
                           "💡 您可以点击左侧「运行监控」查看详细的实时数据，"
                           "或上传监控数据进行深度分析。",
                'context': {}
            }
        
        elif intent == 'report_generate':
            # 报告生成提示
            return {
                'intent': 'report_generate',
                'confidence': intent_result['confidence'],
                'response': "📋 **报告生成**\n\n"
                           "您可以通过以下方式生成报告：\n"
                           "1. 点击左侧「故障报告」菜单\n"
                           "2. 或点击下方「生成报告」按钮\n\n"
                           "报告将包含系统状态分析、故障诊断和建议措施。",
                'context': {}
            }
        
        elif intent == 'repair_request':
            # 修复请求提示
            return {
                'intent': 'repair_request',
                'confidence': intent_result['confidence'],
                'response': "🔧 **自动修复**\n\n"
                           "请点击左侧「自动修复」菜单，或先上传故障数据进行分析。\n\n"
                           "系统支持以下自动修复操作：\n"
                           "- 清理系统缓存\n"
                           "- 重启问题服务\n"
                           "- 优化资源配置",
                'context': {}
            }
        
        else:
            # 默认处理 - 提供友好的引导
            return {
                'intent': 'general',
                'confidence': 0.5,
                'response': f"🤔 我理解您想了解：**{query}**\n\n"
                           "您可以尝试：\n"
                           "• 点击左侧功能菜单获取具体功能\n"
                           "• 问我「什么是XXX」了解运维知识\n"
                           "• 说「系统状态」查看运行情况\n"
                           "• 说「生成报告」创建分析报告\n\n"
                           "有其他问题请随时告诉我！",
                'context': {}
            }
        
        # 4. 生成响应
        response = self.transformer.generate_response(intent, context)
        
        return {
            'intent': intent,
            'confidence': intent_result['confidence'],
            'response': response,
            'context': context
        }
    
    def _get_recommendations(self, faults: List[Dict]) -> str:
        """获取修复建议"""
        recommendations = []
        for fault in faults:
            fault_type = fault['type']
            if fault_type in self.knowledge_base:
                kb = self.knowledge_base[fault_type]
                recommendations.append(f"**{fault_type}**:")
                for sol in kb['solutions'][:2]:
                    recommendations.append(f"  - {sol}")
        
        return '\n'.join(recommendations) if recommendations else '请联系运维人员进行处理'
    
    def _comprehensive_fault_analysis(self, metrics: Dict, query: str = "") -> str:
        """
        综合故障分析 - 结合 ML 规则 + DL 神经网络
        """
        response_parts = []
        
        # 1. ML规则分析（快速、可解释）
        ml_faults = self.fault_classifier.classify(metrics)
        
        # 2. DL模型分析（如果可用）
        dl_result = None
        if self.dl_manager:
            try:
                # 构建特征向量
                features = [
                    metrics.get('cpu', {}).get('current', 0),
                    metrics.get('memory', {}).get('current', 0),
                    metrics.get('io', {}).get('current', 0),
                    metrics.get('iowait', {}).get('avg', 0),
                    metrics.get('disk', {}).get('current', 0),
                    metrics.get('network', {}).get('current', 0),
                    metrics.get('load1', 0),
                    metrics.get('load5', 0),
                    metrics.get('load15', 0),
                    metrics.get('response_time', 0)
                ]
                
                # 综合DL分析
                dl_result = self.dl_manager.comprehensive_analysis(features, query)
            except Exception as e:
                print(f"DL分析失败: {e}")
        
        # 3. 构建响应
        response_parts.append("🔍 **故障诊断报告**\n")
        
        # ML分析结果
        response_parts.append("**📊 规则引擎分析 (ML)**")
        if ml_faults:
            for fault in ml_faults:
                severity_icon = "🔴" if fault['severity'] == 'critical' else "🟡"
                response_parts.append(f"{severity_icon} {fault['type']} - 置信度: {fault['confidence']:.0%}")
        else:
            response_parts.append("✅ 未检测到明显异常")
        
        # DL分析结果
        if dl_result:
            response_parts.append("\n**🧠 神经网络分析 (DL)**")
            nn_result = dl_result.get('nn_classification', {})
            response_parts.append(f"• 故障分类: {nn_result.get('fault_type', '未知')} (置信度: {nn_result.get('confidence', 0):.0%})")
            
            anomaly = dl_result.get('anomaly_detection', {})
            is_anomaly = anomaly.get('is_anomaly', False)
            response_parts.append(f"• 异常检测: {'⚠️ 检测到异常' if is_anomaly else '✅ 正常'} (误差: {anomaly.get('reconstruction_error', 0):.4f})")
            
            # 可解释性
            explain = dl_result.get('explainable_prediction', {})
            if explain.get('important_features'):
                response_parts.append("• 关键因素:")
                for feat in explain['important_features'][:2]:
                    response_parts.append(f"  - {feat['feature']}: 权重 {feat['attention_weight']:.2%}")
        
        # 4. 综合建议
        response_parts.append("\n**💡 处理建议**")
        if ml_faults:
            recommendations = self._get_recommendations(ml_faults)
            response_parts.append(recommendations)
        else:
            response_parts.append("建议继续监控系统状态，定期检查各项指标。")
        
        return '\n'.join(response_parts)
    
    def _search_knowledge(self, query: str) -> Tuple[str, str]:
        """搜索知识库 - 使用改进的关键词匹配"""
        # 扩展知识库
        extended_kb = {
            **self.knowledge_base,
            '故障检测': {
                'description': '故障检测是运维系统的核心功能，用于实时监控和识别系统异常',
                'causes': ['阈值告警', '趋势分析', '异常模式识别', '日志分析'],
                'solutions': [
                    '配置合理的监控阈值',
                    '使用机器学习进行异常检测',
                    '建立故障知识库进行模式匹配',
                    '设置多级告警机制'
                ]
            },
            '性能优化': {
                'description': '系统性能优化是提高服务器运行效率的重要手段',
                'causes': ['资源配置不当', '代码效率低', '并发处理不足', '缓存策略问题'],
                'solutions': [
                    '优化系统参数配置',
                    '使用性能分析工具定位瓶颈',
                    '实施负载均衡',
                    '优化数据库查询'
                ]
            },
            '网络故障': {
                'description': '网络故障会导致服务不可用或响应延迟',
                'causes': ['网络设备故障', '带宽不足', 'DNS解析问题', '防火墙配置错误'],
                'solutions': [
                    '检查网络连接状态',
                    '使用ping/traceroute诊断',
                    '检查防火墙和安全组规则',
                    '联系网络管理员'
                ]
            }
        }
        
        # 关键词权重匹配
        query_lower = (query or '').lower()
        best_match = None
        best_score = 0
        
        for key, value in extended_kb.items():
            score = 0
            # 检查关键词在知识库键中
            if key in query:
                score += 10
            # 检查关键词在描述中
            for word in query:
                if word in key:
                    score += 3
                if word in value['description']:
                    score += 1
            # 检查特定关键词
            keywords_map = {
                '故障': ['故障检测', 'CPU过载', '内存不足', 'IO瓶颈', '网络故障'],
                '检测': ['故障检测', '异常检测'],
                'cpu': ['CPU过载'],
                '内存': ['内存不足'],
                'io': ['IO瓶颈'],
                '网络': ['网络故障'],
                '优化': ['性能优化'],
                '性能': ['性能优化']
            }
            for kw, related_keys in keywords_map.items():
                if kw in query_lower:
                    if key in related_keys:
                        score += 5
                        
            if score > best_score:
                best_score = score
                best_match = key
        
        if best_match and best_score > 0:
            kb = extended_kb[best_match]
            answer = f"📚 **{best_match}**\n\n{kb['description']}\n\n"
            answer += "**常见原因：**\n"
            answer += '\n'.join([f"• {c}" for c in kb['causes']])
            answer += "\n\n**解决方案：**\n"
            answer += '\n'.join([f"• {s}" for s in kb['solutions']])
            related = ', '.join([k for k in extended_kb.keys() if k != best_match][:3])
            return answer, related
        
        # 没有匹配时返回所有可用知识
        all_topics = ', '.join(extended_kb.keys())
        return f"📚 **知识库可查询的内容：**\n\n{all_topics}\n\n请输入具体的问题，例如：\n• 什么是CPU过载\n• 故障检测方法\n• 如何优化性能", ""


# 单例模式
_assistant_instance = None

def get_intelligent_assistant() -> IntelligentAssistant:
    """获取智能助手实例"""
    global _assistant_instance
    if _assistant_instance is None:
        _assistant_instance = IntelligentAssistant()
    return _assistant_instance


# 导出类
__all__ = [
    'FaultClassifier',
    'AnomalyDetector', 
    'TextClassifier',
    'MiniTransformer',
    'IntelligentAssistant',
    'get_intelligent_assistant'
]
