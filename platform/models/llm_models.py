# -*- coding: utf-8 -*-
"""
7B多模态版 - 智能运维模型
基于内网大模型网关（OpenAI兼容接口）
支持文本对话 + 图片分析（监控截图、巡检记录等）

所有接口与ml_models.py保持一致，可无缝切换
"""

import os
import json
import math
import time
import requests
from typing import Dict, List, Any, Optional, Tuple
from collections import Counter
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import LLMConfig, KnowledgeBaseConfig


class LLMClient:
    """
    大模型API客户端
    支持OpenAI兼容接口
    """

    def __init__(self):
        self.config = LLMConfig.get_active_config()
        self.api_base = self.config['api_base']
        self.api_key = self.config['api_key']
        self.model = self.config['model']
        self.timeout = LLMConfig.LLM_TIMEOUT
        self.max_tokens = LLMConfig.LLM_MAX_TOKENS
        self.temperature = LLMConfig.LLM_TEMPERATURE

        # 缓存
        self._cache = {}
        self._cache_ttl = 300  # 5分钟缓存

    def _get_cache_key(self, messages: List[Dict]) -> str:
        """生成缓存键"""
        return hash(json.dumps(messages, ensure_ascii=False))

    def chat(self, messages: List[Dict], temperature: float = None,
             max_tokens: int = None, use_cache: bool = True) -> str:
        """
        调用LLM API

        Args:
            messages: 消息列表 [{"role": "system/user/assistant", "content": "..."}]
            temperature: 温度参数
            max_tokens: 最大token数
            use_cache: 是否使用缓存

        Returns:
            LLM响应文本
        """
        if not self.api_key:
            return self._fallback_response(messages)

        # 检查缓存
        cache_key = self._get_cache_key(messages)
        if use_cache and cache_key in self._cache:
            cached_time, cached_response = self._cache[cache_key]
            if time.time() - cached_time < self._cache_ttl:
                return cached_response

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            data = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature or self.temperature,
                "max_tokens": max_tokens or self.max_tokens
            }

            url = f"{self.api_base}/chat/completions"
            response = requests.post(
                url,
                headers=headers,
                json=data,
                timeout=self.timeout
            )

            if response.status_code == 200:
                result = response.json()
                content = result['choices'][0]['message']['content']
                # 缓存结果
                self._cache[cache_key] = (time.time(), content)
                return content
            elif response.status_code in (401, 403):
                detail = f"API密钥认证失败(HTTP {response.status_code})"
                print(f"LLM API认证错误: {response.status_code} - {response.text[:200]}")
                return self._fallback_response(messages, detail)
            elif response.status_code == 429:
                detail = "请求频率超限，请稍后重试"
                print(f"LLM API限流: {response.text[:200]}")
                return self._fallback_response(messages, detail)
            else:
                detail = f"网关返回HTTP {response.status_code}"
                print(f"LLM API错误: {response.status_code} - {response.text[:500]}")
                return self._fallback_response(messages, detail)

        except requests.exceptions.ConnectionError as e:
            detail = f"无法连接大模型网关({self.api_base.split('/')[2]})"
            print(f"LLM连接失败: {e}")
            return self._fallback_response(messages, detail)
        except requests.exceptions.Timeout:
            detail = f"大模型网关响应超时({self.timeout}秒)"
            print(f"LLM API超时: {self.timeout}s")
            return self._fallback_response(messages, detail)
        except Exception as e:
            detail = f"调用异常: {type(e).__name__}: {str(e)[:100]}"
            print(f"LLM API调用失败: {e} | URL: {url}")
            return self._fallback_response(messages, detail)

    def chat_with_image(self, image_base64: str, question: str,
                         mime_type: str = "image/png",
                         temperature: float = None,
                         max_tokens: int = None) -> str:
        """
        多模态对话 - 图片+文本分析

        Args:
            image_base64: 图片的Base64编码字符串
            question: 用户问题/分析指令
            mime_type: 图片MIME类型（image/png, image/jpeg等）
            temperature: 温度参数
            max_tokens: 最大token数

        Returns:
            LLM多模态分析结果
        """
        messages = [
            {
                "role": "system",
                "content": "你是电网智能运维分析工具。请直接分析图片内容并输出结果，不要有任何问候、自我介绍或额外说明。只输出分析内容本身。"
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{image_base64}"
                        }
                    },
                    {
                        "type": "text",
                        "text": question
                    }
                ]
            }
        ]

        return self.chat(messages, temperature=temperature, max_tokens=max_tokens or 2000, use_cache=False)

    def _fallback_response(self, messages: List[Dict], error_detail: str = "") -> str:
        """API不可用时的回退响应，附带具体错误原因"""
        last_message = messages[-1].get('content', '') if messages else ""
        if isinstance(last_message, list):
            last_message = next((item['text'] for item in last_message if item.get('type') == 'text'), '')
        base_msg = f"[LLM服务暂不可用] 收到您的问题：{str(last_message)[:50]}"
        if error_detail:
            return f"{base_msg}\n\n错误原因：{error_detail}\n\n排查建议：请在服务器终端运行以下命令测试网络连通性：\ncurl -X POST {self.api_base}/chat/completions -H 'Authorization: Bearer {self.api_key[:8]}...' -H 'Content-Type: application/json' -d '{{\"model\":\"{self.model}\",\"messages\":[{{\"role\":\"user\",\"content\":\"test\"}}]}}'"
        return f"{base_msg}... 请检查API配置或稍后重试。"


class LLMFaultClassifier:
    """
    基于大模型的故障分类器
    接口与FaultClassifier完全一致
    """

    def __init__(self, llm_client: LLMClient = None):
        self.llm_client = llm_client or LLMClient()
        self.thresholds = KnowledgeBaseConfig.THRESHOLDS
        self.system_prompt = """你是一个专业的服务器运维故障分类专家。
根据提供的服务器监控指标，分析可能存在的故障类型。

故障类型包括：
- CPU过载 (CPU>=90%)
- CPU使用率偏高 (70%<=CPU<90%)
- 内存不足 (内存>=90%)
- 内存使用率偏高 (75%<=内存<90%)
- IO瓶颈 (IO>=400)
- IO利用率偏高 (200<=IO<400)
- IOWait过高 (IOWait>=40%)
- IOWait偏高 (20%<=IOWait<40%)

请返回JSON格式的分析结果，格式如下：
{
    "faults": [
        {
            "type": "故障类型名称",
            "severity": "critical/warning",
            "category": "CPU/内存/磁盘IO",
            "confidence": 0.0-1.0,
            "reason": "判断依据"
        }
    ],
    "summary": "整体分析总结"
}

只返回JSON，不要有其他内容。"""

    def classify(self, metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        分类故障类型 - 使用LLM分析

        Args:
            metrics: 监控指标数据

        Returns:
            检测到的故障列表
        """
        # 构建用户消息
        user_message = f"""请分析以下服务器监控指标：

CPU: 当前 {metrics.get('cpu', {}).get('current', 0)}%, 平均 {metrics.get('cpu', {}).get('avg', 0)}%, 最大 {metrics.get('cpu', {}).get('max', 0)}%
内存: 当前 {metrics.get('memory', {}).get('current', 0)}%, 平均 {metrics.get('memory', {}).get('avg', 0)}%, 最大 {metrics.get('memory', {}).get('max', 0)}%
IO: 当前 {metrics.get('io', {}).get('current', 0)}, 平均 {metrics.get('io', {}).get('avg', 0)}
IOWait: 平均 {metrics.get('iowait', {}).get('avg', 0)}%
磁盘: 当前 {metrics.get('disk', {}).get('current', 0)}%

请识别所有存在的故障问题。"""

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message}
        ]

        response = self.llm_client.chat(messages, temperature=0.3)

        # 解析LLM响应
        try:
            # 尝试从响应中提取JSON
            json_str = self._extract_json(response)
            result = json.loads(json_str)
            faults = result.get('faults', [])

            # 标准化输出格式
            return [{
                'type': f.get('type', '未知故障'),
                'severity': f.get('severity', 'warning'),
                'category': f.get('category', '其他'),
                'confidence': float(f.get('confidence', 0.7))
            } for f in faults]
        except (json.JSONDecodeError, KeyError) as e:
            print(f"LLM响应解析失败: {e}, 响应: {response[:200]}")
            # 回退到规则检测
            return self._rule_based_classify(metrics)

    def _extract_json(self, text: str) -> str:
        """从文本中提取JSON"""
        # 尝试找到JSON部分
        start_idx = text.find('{')
        end_idx = text.rfind('}') + 1
        if start_idx != -1 and end_idx > start_idx:
            return text[start_idx:end_idx]
        return text

    def _rule_based_classify(self, metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
        """规则回退分类"""
        detected_faults = []

        # CPU检测
        cpu_current = metrics.get('cpu', {}).get('current', 0)
        if cpu_current >= 90:
            detected_faults.append({
                'type': 'CPU过载',
                'severity': 'critical',
                'category': 'CPU',
                'confidence': 0.9
            })
        elif cpu_current >= 70:
            detected_faults.append({
                'type': 'CPU使用率偏高',
                'severity': 'warning',
                'category': 'CPU',
                'confidence': 0.8
            })

        # 内存检测
        mem_current = metrics.get('memory', {}).get('current', 0)
        if mem_current >= 90:
            detected_faults.append({
                'type': '内存不足',
                'severity': 'critical',
                'category': '内存',
                'confidence': 0.9
            })
        elif mem_current >= 75:
            detected_faults.append({
                'type': '内存使用率偏高',
                'severity': 'warning',
                'category': '内存',
                'confidence': 0.8
            })

        # IO检测
        io_current = metrics.get('io', {}).get('current', 0)
        if io_current >= 400:
            detected_faults.append({
                'type': 'IO瓶颈',
                'severity': 'critical',
                'category': '磁盘IO',
                'confidence': 0.9
            })
        elif io_current >= 200:
            detected_faults.append({
                'type': 'IO利用率偏高',
                'severity': 'warning',
                'category': '磁盘IO',
                'confidence': 0.8
            })

        return detected_faults


class LLMAnomalyDetector:
    """
    基于大模型的异常检测器
    接口与AnomalyDetector完全一致
    """

    def __init__(self, llm_client: LLMClient = None):
        self.llm_client = llm_client or LLMClient()
        self.z_score_threshold = 2.5
        self.system_prompt = """你是一个专业的时序数据异常检测专家。
请分析提供的时序数据，识别其中的异常点。

分析方法：
1. 计算数据的均值和标准差
2. 使用Z-score方法检测异常（|Z| > 2.5为异常）
3. 使用IQR方法辅助检测
4. 分析数据趋势

请返回JSON格式：
{
    "anomalies": [
        {"index": 0, "time": "时间", "value": 数值, "z_score": 数值, "deviation": "high/low"}
    ],
    "statistics": {
        "mean": 数值, "std": 数值, "min": 数值, "max": 数值,
        "q1": 数值, "q3": 数值, "iqr": 数值,
        "lower_bound": 数值, "upper_bound": 数值
    },
    "trend": "increasing/decreasing/stable",
    "analysis": "简要分析说明"
}

只返回JSON。"""

    def detect(self, data_points: List[Dict], metric_key: str = 'usage') -> Dict[str, Any]:
        """
        检测时序数据中的异常点 - 使用LLM分析

        Args:
            data_points: 数据点列表
            metric_key: 要检测的指标键名

        Returns:
            异常检测结果
        """
        if not data_points or len(data_points) < 3:
            return {'anomalies': [], 'statistics': {}}

        # 先用统计方法计算基础数据
        values = [p.get(metric_key, 0) for p in data_points]
        stats = self._calculate_statistics(values)

        # 如果数据量较小，直接使用统计方法
        if len(data_points) <= 10:
            return self._statistical_detect(data_points, values, stats)

        # 使用LLM进行深度分析
        user_message = f"""请分析以下时序数据的异常情况：

数据点数量: {len(data_points)}
指标名称: {metric_key}

统计信息:
- 均值: {stats['mean']:.2f}
- 标准差: {stats['std']:.2f}
- 最小值: {stats['min']:.2f}
- 最大值: {stats['max']:.2f}

最近20个数据点：
{self._format_data_points(data_points[-20:], metric_key)}

请识别异常点并分析趋势。"""

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message}
        ]

        response = self.llm_client.chat(messages, temperature=0.2)

        try:
            json_str = self._extract_json(response)
            result = json.loads(json_str)

            # 合并LLM分析结果和统计数据
            return {
                'anomalies': result.get('anomalies', []),
                'statistics': {**stats, **result.get('statistics', {})},
                'trend': result.get('trend', self._detect_trend(values)),
                'llm_analysis': result.get('analysis', '')
            }
        except (json.JSONDecodeError, KeyError):
            return self._statistical_detect(data_points, values, stats)

    def _calculate_statistics(self, values: List[float]) -> Dict[str, float]:
        """计算统计量"""
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        std = math.sqrt(variance) if variance > 0 else 0.001

        sorted_values = sorted(values)
        n = len(sorted_values)
        q1 = sorted_values[n // 4]
        q3 = sorted_values[3 * n // 4]
        iqr = q3 - q1

        return {
            'mean': round(mean, 2),
            'std': round(std, 2),
            'min': round(min(values), 2),
            'max': round(max(values), 2),
            'q1': round(q1, 2),
            'q3': round(q3, 2),
            'iqr': round(iqr, 2),
            'lower_bound': round(q1 - 1.5 * iqr, 2),
            'upper_bound': round(q3 + 1.5 * iqr, 2)
        }

    def _statistical_detect(self, data_points: List[Dict], values: List[float],
                           stats: Dict) -> Dict[str, Any]:
        """统计方法检测异常"""
        anomalies = []
        mean, std = stats['mean'], stats['std']

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

        return {
            'anomalies': anomalies,
            'statistics': stats,
            'trend': self._detect_trend(values)
        }

    def _detect_trend(self, values: List[float]) -> str:
        """检测趋势"""
        if len(values) < 5:
            return 'stable'

        n = len(values)
        x_mean = (n - 1) / 2
        y_mean = sum(values) / n

        numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        denominator = sum((i - x_mean) ** 2 for i in range(n))

        slope = numerator / denominator if denominator != 0 else 0
        threshold = y_mean * 0.01

        if slope > threshold:
            return 'increasing'
        elif slope < -threshold:
            return 'decreasing'
        return 'stable'

    def _format_data_points(self, points: List[Dict], key: str) -> str:
        """格式化数据点"""
        lines = []
        for p in points:
            time_str = p.get('time', 'N/A')
            value = p.get(key, 0)
            lines.append(f"  {time_str}: {value}")
        return '\n'.join(lines)

    def _extract_json(self, text: str) -> str:
        """从文本中提取JSON"""
        start_idx = text.find('{')
        end_idx = text.rfind('}') + 1
        if start_idx != -1 and end_idx > start_idx:
            return text[start_idx:end_idx]
        return text


class LLMTextClassifier:
    """
    基于大模型的文本分类器
    接口与TextClassifier完全一致
    """

    def __init__(self, llm_client: LLMClient = None):
        self.llm_client = llm_client or LLMClient()
        self.system_prompt = """你是一个专业的运维系统意图识别专家。
请分析用户输入，识别其意图类别。

可能的意图类别：
1. fault_analysis - 故障分析（分析问题、诊断故障、检测异常）
2. repair_request - 修复请求（修复、重启、恢复服务）
3. report_generate - 报告生成（生成报告、汇总、导出）
4. knowledge_query - 知识查询（什么是、怎么、如何、为什么）
5. status_check - 状态查询（系统状态、监控、运行情况）
6. greeting - 问候（你好、帮忙）
7. general - 其他一般性问题

请返回JSON格式：
{
    "intent": "意图类别",
    "confidence": 0.0-1.0,
    "matched_keywords": ["匹配的关键词"],
    "reason": "判断理由"
}

只返回JSON。"""

    def classify(self, text: str) -> Dict[str, Any]:
        """
        分类用户意图 - 使用LLM

        Args:
            text: 用户输入文本

        Returns:
            意图分类结果
        """
        if not text or not text.strip():
            return {
                'intent': 'general',
                'confidence': 0.3,
                'matched_words': [],
                'all_scores': {}
            }

        # 快速处理简单问候
        text_lower = text.lower().strip()
        greeting_words = ['你好', '您好', 'hello', 'hi', '嗨', '在吗']
        if any(text_lower == g or (text_lower.startswith(g) and len(text_lower) < 10)
               for g in greeting_words):
            return {
                'intent': 'greeting',
                'confidence': 0.95,
                'matched_words': [text_lower],
                'all_scores': {}
            }

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"请分析用户意图：{text}"}
        ]

        response = self.llm_client.chat(messages, temperature=0.2)

        try:
            json_str = self._extract_json(response)
            result = json.loads(json_str)
            return {
                'intent': result.get('intent', 'general'),
                'confidence': float(result.get('confidence', 0.7)),
                'matched_words': result.get('matched_keywords', []),
                'all_scores': {},
                'llm_reason': result.get('reason', '')
            }
        except (json.JSONDecodeError, KeyError):
            # 回退到关键词匹配
            return self._keyword_classify(text)

    def _keyword_classify(self, text: str) -> Dict[str, Any]:
        """关键词回退分类"""
        text_lower = text.lower()

        intent_keywords = {
            'fault_analysis': ['故障', '异常', '错误', '问题', '分析', '诊断', 'cpu', '内存', 'io'],
            'repair_request': ['修复', '修理', '重启', '恢复', '处理'],
            'report_generate': ['报告', '报表', '汇总', '生成'],
            'knowledge_query': ['什么是', '怎么', '如何', '为什么'],
            'status_check': ['状态', '监控', '运行'],
            'greeting': ['你好', '您好', '帮忙']
        }

        for intent, keywords in intent_keywords.items():
            matched = [kw for kw in keywords if kw in text_lower]
            if matched:
                return {
                    'intent': intent,
                    'confidence': 0.7,
                    'matched_words': matched,
                    'all_scores': {}
                }

        return {
            'intent': 'general',
            'confidence': 0.5,
            'matched_words': [],
            'all_scores': {}
        }

    def _extract_json(self, text: str) -> str:
        """从文本中提取JSON"""
        start_idx = text.find('{')
        end_idx = text.rfind('}') + 1
        if start_idx != -1 and end_idx > start_idx:
            return text[start_idx:end_idx]
        return text


class LLMResponseGenerator:
    """
    基于大模型的响应生成器
    替代MiniTransformer，接口兼容
    """

    def __init__(self, llm_client: LLMClient = None):
        self.llm_client = llm_client or LLMClient()
        self.system_prompt = """你是西藏电网智能运维助手，一个专业、友好的AI运维专家。

你的职责：
1. 分析服务器故障和异常
2. 提供运维建议和解决方案
3. 帮助生成运维报告
4. 解答运维相关知识问题

回复要求：
- 使用简洁专业的中文
- 适当使用emoji增加可读性
- 提供具体可行的建议
- 结构化输出，使用markdown格式"""

    def generate_response(self, intent: str, context: Dict[str, Any]) -> str:
        """
        根据意图和上下文生成响应 - 使用LLM

        Args:
            intent: 用户意图
            context: 上下文信息

        Returns:
            生成的响应文本
        """
        # 构建提示
        intent_prompts = {
            'fault_analysis': "用户请求故障分析，请根据以下信息提供专业的故障诊断和建议：",
            'repair_request': "用户请求执行修复操作，请说明操作步骤和注意事项：",
            'report_generate': "用户请求生成报告，请汇总以下信息生成运维报告摘要：",
            'knowledge_query': "用户咨询运维知识，请详细解答以下问题：",
            'status_check': "用户查询系统状态，请根据以下信息说明当前状态：",
            'greeting': "用户发来问候，请友好地自我介绍并说明你能提供的帮助：",
            'image_analysis': "用户请求分析图片内容，请直接描述图片中的关键信息和分析结果：",
            'default': "请根据以下上下文信息，为用户提供帮助："
        }

        prompt = intent_prompts.get(intent, intent_prompts['default'])

        user_message = f"{prompt}\n\n上下文信息：\n{json.dumps(context, ensure_ascii=False, indent=2)}"

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message}
        ]

        return self.llm_client.chat(messages, temperature=0.7)


class LLMIntelligentAssistant:
    """
    基于大模型的智能运维助手
    接口与IntelligentAssistant完全一致
    """

    def __init__(self):
        # LLM客户端
        self.llm_client = LLMClient()

        # LLM组件
        self.fault_classifier = LLMFaultClassifier(self.llm_client)
        self.anomaly_detector = LLMAnomalyDetector(self.llm_client)
        self.text_classifier = LLMTextClassifier(self.llm_client)
        self.response_generator = LLMResponseGenerator(self.llm_client)

        # 知识库
        self.knowledge_base = self._load_knowledge_base()

        # 系统提示词
        self.system_prompt = """你是西藏电网智能运维助手，一个专业的AI运维专家。

核心能力：
1. 🔍 故障诊断 - 分析服务器故障原因，提供解决方案
2. 📊 数据分析 - 分析监控数据，检测异常趋势
3. 🔧 自动修复 - 推荐和执行修复操作
4. 📋 报告生成 - 生成专业的运维报告
5. 📚 知识问答 - 解答运维相关问题
6. 🗺️ 拓扑图生成 - 根据监控的服务器生成系统拓扑图

回复风格：
- 专业、简洁、实用
- 使用适量emoji增加可读性
- 提供结构化的建议
- 使用Markdown格式

【拓扑图能力】拓扑图由系统自动生成，你不需要自己生成 mermaid 代码。当用户要求生成拓扑图时，回复"正在为您生成系统拓扑图..."即可，系统会自动处理。"""

        print("✓ LLM智能助手已加载 (7B多模态版本 - 支持图片分析)")

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

    def process_query(self, query: str, metrics: Dict = None, all_servers: list = None, history: list = None, extra_context: dict = None) -> Dict[str, Any]:
        """
        处理用户查询 - 使用LLM

        Args:
            query: 用户输入
            metrics: 可选的监控指标数据

        Returns:
            处理结果
        """
        # 1. 快速处理简单问候
        greeting_keywords = ['你好', '您好', 'hello', 'hi', '嗨', '在吗']
        query_lower = (query or '').lower().strip()
        is_simple_greeting = any(
            query_lower == kw or (query_lower.startswith(kw) and len(query_lower) < 10)
            for kw in greeting_keywords
        )

        if is_simple_greeting:
            return {
                'intent': 'greeting',
                'confidence': 0.95,
                'response': self._generate_greeting_response(),
                'context': {},
                'model_type': 'llm'
            }

        # 2. 使用LLM进行综合处理
        response = self._llm_process_query(query, metrics, all_servers=all_servers, history=history, extra_context=extra_context)

        return {
            'intent': response.get('intent', 'general'),
            'confidence': response.get('confidence', 0.8),
            'response': response.get('response', ''),
            'context': response.get('context', {}),
            'model_type': 'llm'
        }

    def _llm_process_query(self, query: str, metrics: Dict = None, all_servers: list = None, history: list = None, extra_context: dict = None) -> Dict[str, Any]:
        """使用LLM综合处理查询"""

        # 构建系统提示词（包含实际服务器数据）
        system_content = self.system_prompt + "\n\n"
        system_content += "【重要规则】你只能基于下面提供的实际监控数据回答。不要编造服务器名称、系统名称或数据。如果没有相关数据，直接说'暂无相关数据'。\n\n"

        # 注入服务器列表
        if all_servers:
            agent_servers = [s for s in all_servers if s.get('type') == 'agent']
            virtual_servers = [s for s in all_servers if s.get('type') == 'virtual']

            system_content += f"【当前监控状态】共监控 {len(all_servers)} 台服务器"
            if agent_servers:
                system_content += f"（Agent服务器 {len(agent_servers)} 台"
            if virtual_servers:
                system_content += f"，虚拟服务器 {len(virtual_servers)} 台"
            system_content += "）\n\n"

            for i, srv in enumerate(all_servers, 1):
                srv_type = 'Agent' if srv.get('type') == 'agent' else '虚拟'
                srv_name = srv.get('name', 'Unknown')
                srv_ip = srv.get('ip', '')
                srv_status = srv.get('status', 'unknown')
                m = srv.get('metrics', {})
                cpu = m.get('cpu', {}).get('usage', 'N/A')
                mem = m.get('memory', {}).get('usage', 'N/A')
                disk = m.get('disk', {}).get('usage', 'N/A')
                anomalies = srv.get('anomalies', [])

                srv_system = srv.get('system', 'default')
                if srv_system == 'default':
                    srv_system = '默认分组'
                line = f"{i}. [{srv_type}] {srv_name}"
                if srv_ip:
                    line += f" ({srv_ip})"
                line += f" | 所属系统:{srv_system} | 状态:{srv_status} | CPU:{cpu}% 内存:{mem}% 磁盘:{disk}%"
                if anomalies:
                    line += f" | ⚠️ {', '.join(anomalies)}"
                system_content += line + "\n"
        else:
            system_content += "【当前监控状态】暂无已添加的服务器。用户可在「运行监控」页面添加服务器。\n"

        # 注入动态获取的补充数据
        if extra_context:
            # v5.59: 增加 knowledge 标签 + list 分支，让真 RAG 检索结果拼进 prompt
            label_map = {
                'services': '服务状态', 'logs': '最新日志', 'port': '端口信息',
                'network': '网卡状态', 'load': '负载信息',
                'knowledge': '知识库检索结果',
            }
            for key, data in extra_context.items():
                label = label_map.get(key, key)
                system_content += f"\n\n【补充数据 - {label}】\n"
                # knowledge 是 list of dict（RAG 命中条目），其余仍是 {server_name: data}
                if isinstance(data, list):
                    for idx, item in enumerate(data, 1):
                        if isinstance(item, dict):
                            kv = ', '.join(f'{k}: {v}' for k, v in item.items())
                            system_content += f"  {idx}. {kv}\n"
                        else:
                            system_content += f"  {idx}. {str(item)[:500]}\n"
                elif isinstance(data, dict):
                    for srv_name, srv_data in data.items():
                        if isinstance(srv_data, dict):
                            items = ', '.join(f'{k}: {v}' for k, v in srv_data.items())
                            system_content += f"服务器 {srv_name}: {items}\n"
                        else:
                            system_content += f"服务器 {srv_name}: {str(srv_data)[:2000]}\n"

        system_content += "\n请基于以上实际数据回答用户问题。用专业但友好的语气。"

        # 构建消息列表
        messages = [{"role": "system", "content": system_content}]

        # 注入对话历史（最近10条）
        if history:
            recent = history[-10:]
            for msg in recent:
                if msg.get('role') in ('user', 'assistant'):
                    messages.append({"role": msg['role'], "content": msg['content']})

        # 当前用户问题
        user_message = query
        if metrics:
            user_message += f"\n\n（当前查看的服务器指标：CPU {metrics.get('cpu', {}).get('usage', 'N/A')}%, 内存 {metrics.get('memory', {}).get('usage', 'N/A')}%）"

        messages.append({"role": "user", "content": user_message})

        response_text = self.llm_client.chat(messages, temperature=0.7)

        # 意图分类
        intent_result = self.text_classifier.classify(query)

        return {
            'intent': intent_result['intent'],
            'confidence': intent_result['confidence'],
            'response': response_text,
            'context': {'metrics': metrics} if metrics else {}
        }

    def _generate_greeting_response(self) -> str:
        """生成问候响应"""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": "用户向你打招呼，请友好地介绍自己并说明你能提供的帮助服务。"}
        ]

        response = self.llm_client.chat(messages, temperature=0.8)

        # 如果LLM不可用，返回默认问候
        if "[LLM服务暂不可用]" in response:
            return ("您好！我是西藏电网智能运维助手 🤖\n\n"
                    "我可以帮您完成以下任务：\n"
                    "📊 **分析数据** - 上传运维数据进行智能分析\n"
                    "📋 **生成报告** - 生成故障诊断报告\n"
                    "🔍 **知识库搜索** - 查询运维知识和解决方案\n"
                    "🔧 **自动修复** - 执行常见问题的自动修复\n"
                    "📈 **系统状态** - 查看当前系统运行状态\n\n"
                    "请问有什么可以帮您的？")
        return response

    def analyze_image(self, image_base64: str, question: str = "",
                       mime_type: str = "image/png") -> Dict[str, Any]:
        """
        分析上传的图片（监控截图、巡检记录、设备照片等）

        Args:
            image_base64: 图片Base64编码
            question: 用户问题（可选，默认自动分析）
            mime_type: 图片MIME类型

        Returns:
            分析结果字典
        """
        if not question:
            question = ("请分析这张图片的内容。如果是监控截图，请识别指标数据和异常情况；"
                       "如果是巡检记录，请提取关键信息；如果是设备照片，请识别设备状态。"
                       "请给出专业的运维分析建议。")

        response = self.llm_client.chat_with_image(
            image_base64=image_base64,
            question=question,
            mime_type=mime_type
        )

        return {
            'response': response,
            'intent': 'image_analysis',
            'confidence': 0.9,
            'model_type': 'llm_multimodal',
            'context': {'has_image': True, 'mime_type': mime_type}
        }

    def analyze_fault(self, metrics: Dict[str, Any], query: str = "") -> str:
        """
        综合故障分析 - 使用LLM

        Args:
            metrics: 监控指标
            query: 用户查询

        Returns:
            分析结果
        """
        # 使用LLM故障分类器
        faults = self.fault_classifier.classify(metrics)

        # 构建分析请求
        fault_info = json.dumps(faults, ensure_ascii=False, indent=2) if faults else "未检测到明显故障"

        user_message = f"""请为以下服务器状态生成专业的故障诊断报告：

**当前指标：**
- CPU: {metrics.get('cpu', {}).get('current', 0)}% (平均: {metrics.get('cpu', {}).get('avg', 0)}%)
- 内存: {metrics.get('memory', {}).get('current', 0)}% (平均: {metrics.get('memory', {}).get('avg', 0)}%)
- IO: {metrics.get('io', {}).get('current', 0)} (平均: {metrics.get('io', {}).get('avg', 0)})
- IOWait: {metrics.get('iowait', {}).get('avg', 0)}%

**检测到的故障：**
{fault_info}

**用户关注点：**
{query if query else '无特殊关注点'}

请生成包含以下内容的诊断报告：
1. 🔍 故障诊断结果
2. ⚠️ 风险评估
3. 💡 处理建议
4. 📋 下一步操作"""

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message}
        ]

        return self.llm_client.chat(messages, temperature=0.5)


# 单例模式
_llm_assistant_instance = None


def get_llm_intelligent_assistant() -> LLMIntelligentAssistant:
    """获取LLM智能助手实例"""
    global _llm_assistant_instance
    if _llm_assistant_instance is None:
        _llm_assistant_instance = LLMIntelligentAssistant()
    return _llm_assistant_instance


# 导出类 - 与ml_models保持一致的命名
__all__ = [
    'LLMClient',
    'LLMFaultClassifier',
    'LLMAnomalyDetector',
    'LLMTextClassifier',
    'LLMResponseGenerator',
    'LLMIntelligentAssistant',
    'get_llm_intelligent_assistant',
    # 别名 - 方便切换
    'LLMFaultClassifier as FaultClassifier',
    'LLMAnomalyDetector as AnomalyDetector',
    'LLMTextClassifier as TextClassifier',
    'LLMResponseGenerator as MiniTransformer',
    'LLMIntelligentAssistant as IntelligentAssistant',
    'get_llm_intelligent_assistant as get_intelligent_assistant'
]
