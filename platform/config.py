# -*- coding: utf-8 -*-
"""
西藏电网智能运维平台 - 7B多模态版配置文件
默认使用内网大模型网关（OpenAI兼容接口）
支持图片+文本混合分析（监控截图、巡检记录等）
"""

import os

# 基础路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class LLMConfig:
    """大模型API配置"""

    # 模型类型选择: 'llm' 使用内网7B多模态模型（默认）, 'local' 使用本地小模型
    MODEL_TYPE = os.environ.get('MODEL_TYPE', 'llm')

    # 多模态能力标志
    MULTIMODAL_ENABLED = True

    # 内网7B多模态大模型API（OpenAI兼容接口）
    QWEN_API_BASE = os.environ.get('QWEN_API_BASE', 'http://<内网IP>:80/xlm-gateway-pscr-c/sfm-api-gateway/gateway/compatible-mode/v1')
    QWEN_API_KEY = os.environ.get('QWEN_API_KEY', ''  # 必须通过环境变量提供)
    QWEN_MODEL = os.environ.get('QWEN_MODEL', 'qwen2-vl-7b-instruct')

    # 请求配置
    LLM_TIMEOUT = int(os.environ.get('LLM_TIMEOUT', '120'))
    LLM_MAX_TOKENS = int(os.environ.get('LLM_MAX_TOKENS', '2000'))
    LLM_TEMPERATURE = float(os.environ.get('LLM_TEMPERATURE', '0.7'))

    @classmethod
    def get_active_config(cls):
        """获取当前激活的LLM配置 - 优先使用运行时配置"""
        # 尝试使用运行时配置
        try:
            runtime_config = get_runtime_config()
            return runtime_config.get_active_llm_config()
        except Exception:
            pass

        # 回退到内网7B多模态API
        return {
            'api_base': cls.QWEN_API_BASE,
            'api_key': cls.QWEN_API_KEY,
            'model': cls.QWEN_MODEL
        }


class ModelConfig:
    """模型配置"""
    
    # 模型保存路径
    MODEL_DIR = os.path.join(BASE_DIR, 'ml_models')
    
    # 故障分类模型配置
    FAULT_CLASSIFIER_PATH = os.path.join(MODEL_DIR, 'fault_classifier.pkl')
    
    # 异常检测模型配置
    ANOMALY_DETECTOR_PATH = os.path.join(MODEL_DIR, 'anomaly_detector.pkl')
    
    # 文本分类模型配置 (轻量级Transformer)
    TEXT_MODEL_PATH = os.path.join(MODEL_DIR, 'text_model')
    TEXT_MODEL_MAX_LENGTH = 256
    TEXT_MODEL_HIDDEN_SIZE = 128
    TEXT_MODEL_NUM_LAYERS = 2
    TEXT_MODEL_NUM_HEADS = 4
    
    # 设备配置
    DEVICE = 'cpu'  # 轻量级模型只用CPU


class ServerConfig:
    """服务器配置"""
    HOST = '0.0.0.0'
    PORT = 5001  # 可根据防火墙情况修改端口
    DEBUG = False


class DatabaseConfig:
    """MySQL 数据库配置 (v5.36 新增)

    启用方式：设置环境变量 DB_ENABLED=true
    连接信息默认指向客户内网 MySQL，可通过环境变量覆盖
    未启用或连接失败时自动降级到 JSON/SQLite 存储
    """
    ENABLED = os.environ.get('DB_ENABLED', 'false').lower() in ('1', 'true', 'yes', 'on')

    HOST = os.environ.get('DB_HOST', '<内网IP>')
    PORT = int(os.environ.get('DB_PORT', '3306'))
    USER = os.environ.get('DB_USER', 'yw')
    PASSWORD = os.environ.get('DB_PASSWORD', '')  # 必须通过环境变量提供
    NAME = os.environ.get('DB_NAME', 'gmdmxzdjx')
    CHARSET = 'utf8mb4'
    TIMEOUT = int(os.environ.get('DB_TIMEOUT', '5'))

    # 表前缀（避免和客户其他表冲突）
    TABLE_PREFIX = 'xzyw_'

    @classmethod
    def as_dict(cls):
        return {
            'enabled': cls.ENABLED,
            'host': cls.HOST,
            'port': cls.PORT,
            'user': cls.USER,
            'database': cls.NAME,
            'has_password': bool(cls.PASSWORD)
        }


class OperationConfig:
    """运维操作配置"""
    
    # 服务器列表
    SERVERS = {
        'xizang-master': {'ip': '192.168.1.1', 'role': 'master'},
        'xizang-worker01': {'ip': '192.168.1.101', 'role': 'worker'},
        'xizang-worker02': {'ip': '192.168.1.102', 'role': 'worker'},
    }
    
    # 操作模式: 'simulate' 模拟执行
    OPERATION_MODE = 'simulate'
    
    # 允许的操作
    ALLOWED_OPERATIONS = ['restart', 'status_check', 'log_collect', 'process_check', 
                          'memory_check', 'cache_clear', 'io_check', 'hardware_check']
    
    # 操作超时时间（秒）
    OPERATION_TIMEOUT = 60


class PathConfig:
    """路径配置"""
    DATA_DIR = os.path.join(BASE_DIR, 'data')
    RAW_DATA_DIR = os.path.join(BASE_DIR, 'data', 'raw')
    REPORTS_DIR = os.path.join(BASE_DIR, 'reports')
    LOGS_DIR = os.path.join(BASE_DIR, 'operation_logs')
    KNOWLEDGE_BASE_DIR = os.path.join(BASE_DIR, 'knowledge_base')


class KnowledgeBaseConfig:
    """知识库配置"""
    
    # 阈值配置
    THRESHOLDS = {
        'cpu': {'warning': 70, 'critical': 90},
        'memory': {'warning': 75, 'critical': 90},
        'io': {'warning': 200, 'critical': 400},
        'iowait': {'warning': 20, 'critical': 40}
    }


class ReportConfig:
    """报告配置"""
    REPORT_OUTPUT_DIR = os.path.join(BASE_DIR, 'reports')


# 导出常用路径变量
KNOWLEDGE_BASE_DIR = PathConfig.KNOWLEDGE_BASE_DIR


class RuntimeConfig:
    """运行时配置 - 支持动态切换模型类型和LLM提供商"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_config()
        return cls._instance

    def _init_config(self):
        """初始化配置"""
        self._model_type = LLMConfig.MODEL_TYPE  # 'local' 或 'llm'
        self._llm_provider = os.environ.get('LLM_PROVIDER', 'qwen')
        self._api_keys = {
            'qwen': LLMConfig.QWEN_API_KEY
        }

    @property
    def model_type(self):
        return self._model_type

    @model_type.setter
    def model_type(self, value):
        if value in ['local', 'llm']:
            self._model_type = value

    @property
    def llm_provider(self):
        return self._llm_provider

    @llm_provider.setter
    def llm_provider(self, value):
        if value in ['qwen']:
            self._llm_provider = value

    def set_api_key(self, provider, api_key):
        """设置API密钥"""
        if provider in self._api_keys:
            self._api_keys[provider] = api_key

    def get_api_key(self, provider=None):
        """获取API密钥"""
        if provider is None:
            provider = self._llm_provider
        return self._api_keys.get(provider, '')

    def get_current_config(self):
        """获取当前配置"""
        provider = self._llm_provider
        has_key = bool(self._api_keys.get(provider, ''))

        return {
            'model_type': self._model_type,
            'llm_provider': self._llm_provider,
            'has_api_key': has_key,
            'multimodal_enabled': LLMConfig.MULTIMODAL_ENABLED,
            'available_providers': [
                {'id': 'qwen', 'name': '内网大模型网关', 'models': [
                    'qwen2-vl-7b-instruct'
                ]}
            ]
        }

    def get_active_llm_config(self):
        """获取当前激活的LLM配置（内网7B多模态API）"""
        return {
            'api_base': LLMConfig.QWEN_API_BASE,
            'api_key': self._api_keys.get('qwen', ''),
            'model': LLMConfig.QWEN_MODEL
        }


def get_runtime_config():
    """获取运行时配置单例"""
    return RuntimeConfig()
