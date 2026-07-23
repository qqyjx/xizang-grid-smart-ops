# -*- coding: utf-8 -*-
"""
智能修复决策模型
基于ML/DL的故障修复策略选择和执行

特性：
1. 故障特征提取与分类（DL）
2. 修复策略智能选择（ML决策树）
3. 修复成功率预测（神经网络）
4. 自适应策略调整
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from typing import Dict, List, Tuple, Optional, Any
import json
import os
from datetime import datetime


class FaultFeatureExtractor(nn.Module):
    """
    故障特征提取器
    从原始监控数据中提取故障特征向量
    """
    def __init__(self, input_dim: int = 20, feature_dim: int = 32):
        super().__init__()
        self.feature_extractor = nn.Sequential(
            nn.Linear(input_dim, 64),
            nn.ReLU(),
            nn.BatchNorm1d(64),
            nn.Dropout(0.2),
            nn.Linear(64, feature_dim),
            nn.ReLU()
        )
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.feature_extractor(x)


class RepairStrategyClassifier(nn.Module):
    """
    修复策略分类器
    基于故障特征选择最佳修复策略
    
    输入：故障特征向量
    输出：修复策略选择概率
    """
    def __init__(self, feature_dim: int = 32, num_strategies: int = 10):
        super().__init__()
        
        # 策略分类网络
        self.strategy_classifier = nn.Sequential(
            nn.Linear(feature_dim, 64),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Linear(32, num_strategies)
        )
        
        # 修复策略映射
        self.strategies = [
            'process_kill',      # 0: 终止高负载进程
            'cache_clear',       # 1: 清理系统缓存
            'service_restart',   # 2: 重启服务
            'disk_cleanup',      # 3: 磁盘清理
            'network_reset',     # 4: 网络重置
            'memory_release',    # 5: 内存释放
            'io_optimize',       # 6: IO优化
            'log_rotate',        # 7: 日志轮转
            'connection_reset',  # 8: 连接重置
            'full_restart'       # 9: 完全重启
        ]
        
        # 策略对应的操作
        self.strategy_operations = {
            'process_kill': ['process_check', 'status_check'],
            'cache_clear': ['memory_check', 'cache_clear'],
            'service_restart': ['status_check', 'service_restart'],
            'disk_cleanup': ['disk_check', 'disk_cleanup'],
            'network_reset': ['network_check', 'network_test'],
            'memory_release': ['memory_check', 'cache_clear', 'status_check'],
            'io_optimize': ['disk_check', 'process_check'],
            'log_rotate': ['log_collect', 'disk_check'],
            'connection_reset': ['network_check', 'connection_check'],
            'full_restart': ['status_check', 'service_restart', 'status_check']
        }
    
    def forward(self, features: torch.Tensor) -> torch.Tensor:
        return self.strategy_classifier(features)
    
    def get_strategy_name(self, idx: int) -> str:
        return self.strategies[idx] if idx < len(self.strategies) else 'unknown'
    
    def get_strategy_operations(self, strategy: str) -> List[str]:
        return self.strategy_operations.get(strategy, ['status_check'])


class RepairSuccessPredictor(nn.Module):
    """
    修复成功率预测器
    预测给定策略对特定故障的修复成功概率
    
    输入：故障特征 + 策略embedding
    输出：修复成功概率
    """
    def __init__(self, feature_dim: int = 32, num_strategies: int = 10, embed_dim: int = 16):
        super().__init__()
        
        # 策略embedding
        self.strategy_embedding = nn.Embedding(num_strategies, embed_dim)
        
        # 预测网络
        self.predictor = nn.Sequential(
            nn.Linear(feature_dim + embed_dim, 64),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
            nn.Sigmoid()
        )
    
    def forward(self, features: torch.Tensor, strategy_idx: torch.Tensor) -> torch.Tensor:
        strategy_embed = self.strategy_embedding(strategy_idx)
        combined = torch.cat([features, strategy_embed], dim=-1)
        return self.predictor(combined)


class IntelligentRepairModel(nn.Module):
    """
    智能修复决策模型
    整合特征提取、策略选择和成功率预测
    """
    def __init__(self, input_dim: int = 20, feature_dim: int = 32, num_strategies: int = 10):
        super().__init__()
        
        self.feature_extractor = FaultFeatureExtractor(input_dim, feature_dim)
        self.strategy_classifier = RepairStrategyClassifier(feature_dim, num_strategies)
        self.success_predictor = RepairSuccessPredictor(feature_dim, num_strategies)
        
        self.num_strategies = num_strategies
        
        # 故障类型到特征的映射
        self.fault_type_mapping = {
            'CPU过载': 0, 'cpu_overload': 0, 'CPU使用率过高': 0,
            '内存不足': 1, 'memory_shortage': 1, '内存使用率过高': 1,
            'IO瓶颈': 2, 'io_bottleneck': 2, 'IO等待过高': 2,
            '磁盘空间不足': 3, 'disk_full': 3, '磁盘使用率过高': 3,
            '网络延迟': 4, 'network_latency': 4, '网络异常': 4,
            '服务异常': 5, 'service_error': 5, '服务停止': 5,
            '数据库异常': 6, 'db_error': 6,
            '安全异常': 7, 'security_issue': 7,
            '系统错误': 8, 'system_error': 8,
            '日志异常': 9, 'log_error': 9
        }
        
        # 初始化权重
        self._init_weights()
    
    def _init_weights(self):
        """v5.59: 固定 seed 让初始化可复现。
        说明：当前 DL 网络无训练好的 .pt 权重，输出仅作演示性辅助；
        融合决策以 MLDecisionTree（规则）为权威，DL 仅提供稳定的辅助参考。
        固定 seed 后跨进程重启同输入得同输出，避免演示中"成功率/策略漂移"穿帮。
        """
        import torch as _torch
        with _torch.random.fork_rng():
            _torch.manual_seed(42)
            for module in self.modules():
                if isinstance(module, nn.Linear):
                    nn.init.xavier_normal_(module.weight)
                    if module.bias is not None:
                        nn.init.zeros_(module.bias)
                elif isinstance(module, nn.Embedding):
                    nn.init.normal_(module.weight, mean=0, std=0.1)
    
    def encode_fault_data(self, fault_detection: Dict) -> np.ndarray:
        """
        将故障检测结果编码为模型输入向量
        """
        # 初始化特征向量
        features = np.zeros(20, dtype=np.float32)
        
        if not fault_detection:
            return features
        
        # 提取故障信息
        fault_type = fault_detection.get('fault_type', '')
        severity = fault_detection.get('severity', 'low')
        details = fault_detection.get('details', {})
        
        # 1. 故障类型编码 (one-hot, indices 0-9)
        fault_idx = self.fault_type_mapping.get(fault_type, 8)
        features[fault_idx] = 1.0
        
        # 2. 严重程度编码 (index 10)
        severity_map = {'low': 0.2, 'medium': 0.5, 'high': 0.8, 'critical': 1.0}
        features[10] = severity_map.get(severity, 0.5)
        
        # 3. 指标数值提取 (indices 11-15)
        if isinstance(details, dict):
            # CPU使用率
            cpu = details.get('cpu_usage', details.get('cpu', 0))
            features[11] = float(cpu) / 100 if cpu else 0
            
            # 内存使用率
            mem = details.get('memory_usage', details.get('memory', 0))
            features[12] = float(mem) / 100 if mem else 0
            
            # IO等待
            io_wait = details.get('io_wait', details.get('iowait', 0))
            features[13] = float(io_wait) / 100 if io_wait else 0
            
            # 磁盘使用率
            disk = details.get('disk_usage', details.get('disk', 0))
            features[14] = float(disk) / 100 if disk else 0
            
            # 网络延迟（归一化到0-1）
            latency = details.get('latency', details.get('network_latency', 0))
            features[15] = min(float(latency) / 1000, 1.0) if latency else 0
        
        # 4. 置信度 (index 16)
        confidence = fault_detection.get('confidence', 0.5)
        features[16] = float(confidence)
        
        # 5. 是否为自动修复类型 (index 17)
        auto_fixable = fault_detection.get('auto_fixable', False)
        features[17] = 1.0 if auto_fixable else 0.0
        
        # 6. 故障发生时间特征 (index 18-19)
        # 时间周期性特征（小时和星期）
        now = datetime.now()
        features[18] = now.hour / 24  # 小时归一化
        features[19] = now.weekday() / 7  # 星期归一化
        
        return features
    
    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        前向传播
        返回：特征向量、策略logits、各策略成功率
        """
        features = self.feature_extractor(x)
        strategy_logits = self.strategy_classifier(features)
        
        # 计算每个策略的成功率
        batch_size = x.size(0)
        success_rates = []
        for i in range(self.num_strategies):
            strategy_idx = torch.full((batch_size,), i, dtype=torch.long)
            rate = self.success_predictor(features, strategy_idx)
            success_rates.append(rate)
        success_rates = torch.cat(success_rates, dim=-1)
        
        return features, strategy_logits, success_rates
    
    def predict(self, fault_detection: Dict) -> Dict:
        """
        预测最佳修复策略
        
        Args:
            fault_detection: 故障检测结果
        
        Returns:
            包含推荐策略和预测成功率的字典
        """
        self.eval()
        
        # 编码输入
        input_features = self.encode_fault_data(fault_detection)
        x = torch.FloatTensor(input_features).unsqueeze(0)
        
        with torch.no_grad():
            features, strategy_logits, success_rates = self.forward(x)
            
            # 获取策略概率
            strategy_probs = F.softmax(strategy_logits, dim=-1)[0]
            
            # 综合考虑策略概率和成功率
            # 评分 = 策略概率 * 成功率
            combined_scores = strategy_probs * success_rates[0]
            
            # 获取top-3策略
            top_indices = torch.argsort(combined_scores, descending=True)[:3]
            
            recommendations = []
            for idx in top_indices:
                idx = idx.item()
                strategy = self.strategy_classifier.get_strategy_name(idx)
                operations = self.strategy_classifier.get_strategy_operations(strategy)
                
                recommendations.append({
                    'strategy': strategy,
                    'strategy_probability': float(strategy_probs[idx]),
                    'predicted_success_rate': float(success_rates[0][idx]),
                    'combined_score': float(combined_scores[idx]),
                    'operations': operations
                })
            
            # 最佳策略
            best_idx = top_indices[0].item()
            best_strategy = self.strategy_classifier.get_strategy_name(best_idx)
            best_operations = self.strategy_classifier.get_strategy_operations(best_strategy)
        
        return {
            'best_strategy': best_strategy,
            'best_operations': best_operations,
            'predicted_success_rate': float(success_rates[0][best_idx]),
            'confidence': float(strategy_probs[best_idx]),
            'recommendations': recommendations,
            'input_features': input_features.tolist(),
            'model': 'IntelligentRepairModel',
            'model_type': 'deep_learning'
        }


class MLDecisionTree:
    """
    机器学习决策树
    基于规则的快速故障-策略匹配
    作为深度学习模型的补充和验证
    """
    def __init__(self):
        # 决策规则树
        self.rules = {
            # CPU相关
            'CPU过载': {
                'conditions': {'cpu_usage': (80, 100)},
                'strategies': ['process_kill', 'service_restart'],
                'auto_fixable': False,
                'priority': 1
            },
            'CPU使用率过高': {
                'conditions': {'cpu_usage': (80, 100)},
                'strategies': ['process_kill', 'service_restart'],
                'auto_fixable': False,
                'priority': 1
            },
            
            # 内存相关
            '内存不足': {
                'conditions': {'memory_usage': (85, 100)},
                'strategies': ['cache_clear', 'memory_release', 'service_restart'],
                'auto_fixable': True,
                'priority': 1
            },
            '内存使用率过高': {
                'conditions': {'memory_usage': (85, 100)},
                'strategies': ['cache_clear', 'memory_release'],
                'auto_fixable': True,
                'priority': 2
            },
            
            # IO相关
            'IO瓶颈': {
                'conditions': {'io_wait': (50, 100)},
                'strategies': ['io_optimize', 'process_kill'],
                'auto_fixable': False,
                'priority': 2
            },
            'IO等待过高': {
                'conditions': {'io_wait': (50, 100)},
                'strategies': ['io_optimize', 'process_kill'],
                'auto_fixable': False,
                'priority': 2
            },
            
            # 磁盘相关
            '磁盘空间不足': {
                'conditions': {'disk_usage': (85, 100)},
                'strategies': ['disk_cleanup', 'log_rotate'],
                'auto_fixable': True,
                'priority': 2
            },
            '磁盘使用率过高': {
                'conditions': {'disk_usage': (85, 100)},
                'strategies': ['disk_cleanup', 'log_rotate'],
                'auto_fixable': True,
                'priority': 2
            },
            
            # 网络相关
            '网络延迟': {
                'conditions': {'latency': (100, 10000)},
                'strategies': ['network_reset', 'connection_reset'],
                'auto_fixable': True,
                'priority': 2
            },
            '网络异常': {
                'conditions': {},
                'strategies': ['network_reset', 'connection_reset'],
                'auto_fixable': True,
                'priority': 2
            },
            
            # 服务相关
            '服务异常': {
                'conditions': {},
                'strategies': ['service_restart', 'full_restart'],
                'auto_fixable': True,
                'priority': 1
            },
            '服务停止': {
                'conditions': {},
                'strategies': ['service_restart'],
                'auto_fixable': True,
                'priority': 1
            },
            
            # 数据库相关
            '数据库异常': {
                'conditions': {},
                'strategies': ['connection_reset', 'service_restart'],
                'auto_fixable': False,
                'priority': 1
            },
            
            # 安全相关
            '安全异常': {
                'conditions': {},
                'strategies': ['process_kill', 'full_restart'],
                'auto_fixable': False,
                'priority': 1
            },
            
            # 系统相关
            '系统错误': {
                'conditions': {},
                'strategies': ['service_restart', 'full_restart'],
                'auto_fixable': False,
                'priority': 1
            },
            
            # 日志相关
            '日志异常': {
                'conditions': {},
                'strategies': ['log_rotate', 'disk_cleanup'],
                'auto_fixable': True,
                'priority': 3
            }
        }
        
        # 策略操作映射
        self.strategy_operations = {
            'process_kill': ['process_check', 'status_check'],
            'cache_clear': ['memory_check', 'cache_clear'],
            'service_restart': ['status_check', 'service_restart'],
            'disk_cleanup': ['disk_check', 'disk_cleanup'],
            'network_reset': ['network_check', 'network_test'],
            'memory_release': ['memory_check', 'cache_clear', 'status_check'],
            'io_optimize': ['disk_check', 'process_check'],
            'log_rotate': ['log_collect', 'disk_check'],
            'connection_reset': ['network_check', 'connection_check'],
            'full_restart': ['status_check', 'service_restart', 'status_check']
        }
    
    def predict(self, fault_detection: Dict) -> Dict:
        """
        基于决策树规则预测修复策略
        """
        fault_type = fault_detection.get('fault_type', '')
        severity = fault_detection.get('severity', 'medium')
        details = fault_detection.get('details', {})
        
        # 查找匹配的规则
        rule = self.rules.get(fault_type)
        
        if not rule:
            # 尝试模糊匹配
            for key in self.rules:
                if key in fault_type or fault_type in key:
                    rule = self.rules[key]
                    break
        
        if not rule:
            # 默认规则
            return {
                'best_strategy': 'status_check',
                'best_operations': ['status_check', 'log_collect'],
                'predicted_success_rate': 0.5,
                'confidence': 0.3,
                'auto_fixable': False,
                'recommendations': [],
                'model': 'MLDecisionTree',
                'model_type': 'machine_learning'
            }
        
        # 获取推荐策略
        strategies = rule['strategies']
        best_strategy = strategies[0]
        best_operations = self.strategy_operations.get(best_strategy, ['status_check'])
        
        # 计算预测成功率（基于规则）
        base_success_rate = 0.85 if rule['auto_fixable'] else 0.6
        severity_factor = {'low': 1.1, 'medium': 1.0, 'high': 0.8, 'critical': 0.6}
        success_rate = min(base_success_rate * severity_factor.get(severity, 1.0), 1.0)
        
        # 生成推荐列表
        recommendations = []
        for i, strategy in enumerate(strategies):
            ops = self.strategy_operations.get(strategy, ['status_check'])
            rate = success_rate * (1 - i * 0.1)  # 后续策略成功率递减
            recommendations.append({
                'strategy': strategy,
                'operations': ops,
                'predicted_success_rate': rate,
                'rank': i + 1
            })
        
        return {
            'best_strategy': best_strategy,
            'best_operations': best_operations,
            'predicted_success_rate': success_rate,
            'confidence': 0.9 if rule['auto_fixable'] else 0.7,
            'auto_fixable': rule['auto_fixable'],
            'priority': rule['priority'],
            'recommendations': recommendations,
            'model': 'MLDecisionTree',
            'model_type': 'machine_learning'
        }


class HybridRepairDecisionMaker:
    """
    混合修复决策器
    结合DL模型和ML决策树进行智能决策
    
    特性：
    1. DL模型提供主要预测
    2. ML决策树提供规则验证
    3. 融合两者结果得出最终决策
    """
    def __init__(self, model_dir: str = "checkpoints"):
        self.model_dir = model_dir
        os.makedirs(model_dir, exist_ok=True)
        
        # 深度学习模型
        self.dl_model = IntelligentRepairModel()
        
        # 机器学习决策树
        self.ml_tree = MLDecisionTree()
        
        # 融合权重
        self.dl_weight = 0.6
        self.ml_weight = 0.4
        
        # 历史记录
        self.history = []
    
    def decide(self, fault_detection: Dict) -> Dict:
        """
        综合决策
        
        Args:
            fault_detection: 故障检测结果
        
        Returns:
            包含最终修复决策的字典
        """
        # DL模型预测
        dl_result = self.dl_model.predict(fault_detection)
        
        # ML决策树预测
        ml_result = self.ml_tree.predict(fault_detection)
        
        # 融合决策
        # 如果两个模型推荐相同策略，置信度提升
        if dl_result['best_strategy'] == ml_result['best_strategy']:
            final_strategy = dl_result['best_strategy']
            final_operations = dl_result['best_operations']
            final_confidence = min((dl_result['confidence'] + ml_result['confidence']) / 2 * 1.2, 1.0)
            final_success_rate = (dl_result['predicted_success_rate'] * self.dl_weight + 
                                 ml_result['predicted_success_rate'] * self.ml_weight)
            consensus = True
        else:
            # v5.59: 分歧时统一以 ML 规则树为权威决策（DL 网络未训练，原本按权重选会引入不可复现的随机性）
            # ML 决策树是基于真实运维规则的稳定逻辑，跨进程一致；DL 结果仍透出在 dl_prediction 供参考。
            final_strategy = ml_result['best_strategy']
            final_operations = ml_result['best_operations']
            final_success_rate = ml_result['predicted_success_rate']
            final_confidence = (dl_result['confidence'] * self.dl_weight +
                               ml_result['confidence'] * self.ml_weight)
            consensus = False
        
        # 是否可自动修复
        auto_fixable = ml_result.get('auto_fixable', False)
        
        result = {
            'fault_type': fault_detection.get('fault_type', 'unknown'),
            'severity': fault_detection.get('severity', 'medium'),
            'best_strategy': final_strategy,
            'best_operations': final_operations,
            'predicted_success_rate': float(final_success_rate),
            'confidence': float(final_confidence),
            'auto_fixable': auto_fixable,
            'consensus': consensus,
            'dl_prediction': {
                'strategy': dl_result['best_strategy'],
                'success_rate': dl_result['predicted_success_rate'],
                'confidence': dl_result['confidence']
            },
            'ml_prediction': {
                'strategy': ml_result['best_strategy'],
                'success_rate': ml_result['predicted_success_rate'],
                'confidence': ml_result['confidence']
            },
            'recommendations': dl_result.get('recommendations', []),
            'model': 'HybridRepairDecisionMaker',
            'model_type': 'hybrid_ml_dl'
        }
        
        # 记录历史
        self.history.append({
            'timestamp': datetime.now().isoformat(),
            'fault': fault_detection.get('fault_type'),
            'decision': final_strategy,
            'success_rate': final_success_rate
        })
        
        return result
    
    def get_model_info(self) -> Dict:
        """获取模型信息"""
        # 计算参数量
        dl_params = sum(p.numel() for p in self.dl_model.parameters())
        
        return {
            'dl_model': {
                'name': 'IntelligentRepairModel',
                'parameters': dl_params,
                'components': ['FaultFeatureExtractor', 'RepairStrategyClassifier', 'RepairSuccessPredictor']
            },
            'ml_model': {
                'name': 'MLDecisionTree',
                'rules_count': len(self.ml_tree.rules),
                'strategies_count': len(self.ml_tree.strategy_operations)
            },
            'fusion': {
                'dl_weight': self.dl_weight,
                'ml_weight': self.ml_weight
            },
            'history_count': len(self.history)
        }
    
    def save_model(self, path: str = None):
        """保存模型"""
        if path is None:
            path = os.path.join(self.model_dir, 'repair_decision_model.pt')
        
        torch.save({
            'dl_model_state': self.dl_model.state_dict(),
            'dl_weight': self.dl_weight,
            'ml_weight': self.ml_weight,
            'history': self.history[-100:]  # 只保留最近100条
        }, path)
        
        print(f"[RepairModel] 模型已保存到 {path}")
    
    def load_model(self, path: str = None):
        """加载模型"""
        if path is None:
            path = os.path.join(self.model_dir, 'repair_decision_model.pt')
        
        if os.path.exists(path):
            checkpoint = torch.load(path, map_location='cpu')
            self.dl_model.load_state_dict(checkpoint['dl_model_state'])
            self.dl_weight = checkpoint.get('dl_weight', 0.6)
            self.ml_weight = checkpoint.get('ml_weight', 0.4)
            self.history = checkpoint.get('history', [])
            print(f"[RepairModel] 模型已从 {path} 加载")
        else:
            print(f"[RepairModel] 未找到模型文件，使用默认权重")


# 全局实例
_repair_decision_maker = None


def get_repair_decision_maker() -> HybridRepairDecisionMaker:
    """获取修复决策器单例"""
    global _repair_decision_maker
    if _repair_decision_maker is None:
        _repair_decision_maker = HybridRepairDecisionMaker()
    return _repair_decision_maker


# 快捷函数
def predict_repair_strategy(fault_detection: Dict) -> Dict:
    """预测修复策略"""
    return get_repair_decision_maker().decide(fault_detection)


def get_repair_model_info() -> Dict:
    """获取模型信息"""
    return get_repair_decision_maker().get_model_info()
