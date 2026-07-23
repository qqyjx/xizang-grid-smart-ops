# -*- coding: utf-8 -*-
"""
模型模块 - 7B多模态版
默认使用内网大模型网关（llm模式）
支持图片+文本混合分析

通过环境变量 MODEL_TYPE 控制，默认使用 llm 模式
"""

import os

# 获取模型类型配置（7B版默认llm）
MODEL_TYPE = os.environ.get('MODEL_TYPE', 'llm').lower()

print(f"[Models] 当前模型模式: {MODEL_TYPE} (7B多模态版)")

if MODEL_TYPE == 'llm':
    # 使用大模型API版本
    print("[Models] 加载大模型API版本...")

    from .llm_models import (
        LLMFaultClassifier as FaultClassifier,
        LLMAnomalyDetector as AnomalyDetector,
        LLMTextClassifier as TextClassifier,
        LLMResponseGenerator as MiniTransformer,
        LLMIntelligentAssistant as IntelligentAssistant,
        get_llm_intelligent_assistant as get_intelligent_assistant,
        LLMClient
    )

    # 尝试加载DL模型（兼容性）
    try:
        from .dl_models import (
            SimpleNN,
            LSTMAnalyzer,
            MiniTransformerEncoder,
            AutoEncoder,
            AttentionClassifier,
            DLModelManager,
            get_dl_manager
        )
    except ImportError:
        # DL模型不可用时使用空实现
        SimpleNN = None
        LSTMAnalyzer = None
        MiniTransformerEncoder = None
        AutoEncoder = None
        AttentionClassifier = None
        DLModelManager = None
        get_dl_manager = lambda: None

    __all__ = [
        # 主要模型（LLM版本）
        'FaultClassifier',
        'AnomalyDetector',
        'TextClassifier',
        'MiniTransformer',
        'IntelligentAssistant',
        'get_intelligent_assistant',
        'LLMClient',
        # DL模型（可选）
        'SimpleNN',
        'LSTMAnalyzer',
        'MiniTransformerEncoder',
        'AutoEncoder',
        'AttentionClassifier',
        'DLModelManager',
        'get_dl_manager'
    ]

else:
    # 使用本地小模型版本（默认）
    print("[Models] 加载本地小模型版本...")

    from .ml_models import (
        FaultClassifier,
        AnomalyDetector,
        TextClassifier,
        MiniTransformer,
        IntelligentAssistant,
        get_intelligent_assistant
    )

    from .dl_models import (
        SimpleNN,
        LSTMAnalyzer,
        MiniTransformerEncoder,
        AutoEncoder,
        AttentionClassifier,
        DLModelManager,
        get_dl_manager
    )

    __all__ = [
        # ML模型
        'FaultClassifier',
        'AnomalyDetector',
        'TextClassifier',
        'MiniTransformer',
        'IntelligentAssistant',
        'get_intelligent_assistant',
        # DL模型
        'SimpleNN',
        'LSTMAnalyzer',
        'MiniTransformerEncoder',
        'AutoEncoder',
        'AttentionClassifier',
        'DLModelManager',
        'get_dl_manager'
    ]


# 导出LLM模型供直接使用
try:
    from .llm_models import (
        LLMClient,
        LLMFaultClassifier,
        LLMAnomalyDetector,
        LLMTextClassifier,
        LLMResponseGenerator,
        LLMIntelligentAssistant,
        get_llm_intelligent_assistant
    )
    __all__.extend([
        'LLMClient',
        'LLMFaultClassifier',
        'LLMAnomalyDetector',
        'LLMTextClassifier',
        'LLMResponseGenerator',
        'LLMIntelligentAssistant',
        'get_llm_intelligent_assistant'
    ])
except ImportError as e:
    print(f"[Models] LLM模型导入失败（不影响本地模型）: {e}")


def get_model_info() -> dict:
    """获取当前模型信息（7B多模态版）"""
    is_multimodal = MODEL_TYPE == 'llm'
    return {
        'model_type': MODEL_TYPE,
        'model_name': '7B多模态大模型 (内网网关)' if MODEL_TYPE == 'llm' else 'Local ML/DL',
        'description': '7B多模态版（支持图片分析）' if MODEL_TYPE == 'llm' else '本地小模型版本（离线可用）',
        'multimodal': is_multimodal,
        'version': '5.29-7B',
        'features': {
            'fault_classification': True,
            'anomaly_detection': True,
            'text_classification': True,
            'response_generation': True,
            'image_analysis': is_multimodal,
            'deep_learning': MODEL_TYPE == 'local'
        }
    }
