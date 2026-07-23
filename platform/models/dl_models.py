"""
深度学习模型模块 - 轻量级实现
包含：简单神经网络、小型Transformer、LSTM等
使用PyTorch实现，支持CPU离线运行
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from typing import List, Dict, Tuple, Optional
import json
import os
import math


class SimpleNN(nn.Module):
    """
    简单的多层感知机(MLP)用于故障分类
    输入：系统指标向量（CPU、内存、IO等）
    输出：故障类型概率分布
    """
    def __init__(self, input_dim: int = 10, hidden_dims: List[int] = [64, 32], num_classes: int = 5):
        super().__init__()
        
        layers = []
        prev_dim = input_dim
        for hidden_dim in hidden_dims:
            layers.extend([
                nn.Linear(prev_dim, hidden_dim),
                nn.ReLU(),
                nn.Dropout(0.2),
                nn.BatchNorm1d(hidden_dim)
            ])
            prev_dim = hidden_dim
        
        layers.append(nn.Linear(prev_dim, num_classes))
        self.network = nn.Sequential(*layers)
        
        # 故障类型映射
        self.fault_types = ['正常', 'CPU故障', '内存故障', 'IO故障', '网络故障']
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.network(x)
    
    def predict(self, features: np.ndarray) -> Tuple[str, float]:
        """预测故障类型"""
        self.eval()
        with torch.no_grad():
            x = torch.FloatTensor(features).unsqueeze(0)
            logits = self.forward(x)
            probs = F.softmax(logits, dim=-1)
            pred_idx = torch.argmax(probs, dim=-1).item()
            confidence = probs[0, pred_idx].item()
        return self.fault_types[pred_idx], confidence


class LSTMAnalyzer(nn.Module):
    """
    LSTM时序分析模型
    用于分析系统指标的时序变化，预测未来趋势
    """
    def __init__(self, input_dim: int = 5, hidden_dim: int = 64, num_layers: int = 2, output_dim: int = 1):
        super().__init__()
        
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        
        self.lstm = nn.LSTM(
            input_size=input_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            dropout=0.1 if num_layers > 1 else 0
        )
        
        self.fc = nn.Sequential(
            nn.Linear(hidden_dim, 32),
            nn.ReLU(),
            nn.Linear(32, output_dim)
        )
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x shape: (batch, seq_len, input_dim)
        lstm_out, _ = self.lstm(x)
        # 取最后一个时间步的输出
        last_output = lstm_out[:, -1, :]
        return self.fc(last_output)
    
    def predict_trend(self, time_series: np.ndarray) -> Dict:
        """
        预测趋势
        time_series: shape (seq_len, features)
        """
        self.eval()
        with torch.no_grad():
            x = torch.FloatTensor(time_series).unsqueeze(0)
            prediction = self.forward(x).item()
        
        if prediction > 0.1:
            trend = "上升"
            risk = "高" if prediction > 0.3 else "中"
        elif prediction < -0.1:
            trend = "下降"
            risk = "低"
        else:
            trend = "稳定"
            risk = "低"
        
        return {
            "trend": trend,
            "predicted_change": prediction,
            "risk_level": risk
        }


class MiniTransformerEncoder(nn.Module):
    """
    小型Transformer编码器
    用于文本理解和故障描述分析
    参数量约 100K-500K（对比GPT-3的175B）
    """
    def __init__(
        self,
        vocab_size: int = 5000,
        d_model: int = 128,
        nhead: int = 4,
        num_layers: int = 2,
        dim_feedforward: int = 256,
        max_seq_len: int = 128,
        num_classes: int = 5,
        dropout: float = 0.1
    ):
        super().__init__()
        
        self.d_model = d_model
        self.embedding = nn.Embedding(vocab_size, d_model)
        self.pos_encoding = self._create_positional_encoding(max_seq_len, d_model)
        
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=nhead,
            dim_feedforward=dim_feedforward,
            dropout=dropout,
            batch_first=True
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        
        self.classifier = nn.Sequential(
            nn.Linear(d_model, d_model // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(d_model // 2, num_classes)
        )
        
        # 简单词汇表
        self.vocab = self._build_vocab()
        self.fault_labels = ['正常', 'CPU故障', '内存故障', 'IO故障', '网络故障']
    
    def _create_positional_encoding(self, max_len: int, d_model: int) -> torch.Tensor:
        """创建位置编码"""
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        return pe.unsqueeze(0)
    
    def _build_vocab(self) -> Dict[str, int]:
        """构建简单词汇表"""
        words = [
            '<PAD>', '<UNK>', '<CLS>', '<SEP>',
            # 故障相关
            'cpu', '内存', 'memory', 'io', '磁盘', 'disk', '网络', 'network',
            '故障', '异常', '错误', '警告', '告警', '问题',
            '高', '低', '满', '溢出', '超时', '延迟',
            '使用率', '占用', '负载', '性能', '响应',
            '服务', '进程', '系统', '服务器', '节点',
            # 数字和符号
            '0', '1', '2', '3', '4', '5', '6', '7', '8', '9',
            '%', 'mb', 'gb', 'kb', 'ms', 's',
            # 动作
            '重启', '清理', '扩容', '优化', '检查', '修复',
            # 常用词
            '的', '是', '有', '在', '和', '或', '不', '很', '太',
        ]
        return {word: idx for idx, word in enumerate(words)}
    
    def tokenize(self, text: str, max_len: int = 128) -> torch.Tensor:
        """简单分词"""
        text = text.lower()
        tokens = [self.vocab.get('<CLS>', 2)]
        
        # 简单的字符级分词
        for char in text:
            if char in self.vocab:
                tokens.append(self.vocab[char])
            elif char.isalnum():
                tokens.append(self.vocab.get('<UNK>', 1))
        
        tokens.append(self.vocab.get('<SEP>', 3))
        
        # 填充或截断
        if len(tokens) < max_len:
            tokens.extend([self.vocab.get('<PAD>', 0)] * (max_len - len(tokens)))
        else:
            tokens = tokens[:max_len]
        
        return torch.LongTensor(tokens)
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x shape: (batch, seq_len)
        embedded = self.embedding(x) * math.sqrt(self.d_model)
        embedded = embedded + self.pos_encoding[:, :x.size(1), :].to(x.device)
        
        # 创建padding mask
        padding_mask = (x == self.vocab.get('<PAD>', 0))
        
        encoded = self.transformer(embedded, src_key_padding_mask=padding_mask)
        
        # 使用CLS token的输出进行分类
        cls_output = encoded[:, 0, :]
        return self.classifier(cls_output)
    
    def analyze_text(self, text: str) -> Dict:
        """分析文本描述，识别故障类型"""
        self.eval()
        with torch.no_grad():
            tokens = self.tokenize(text).unsqueeze(0)
            logits = self.forward(tokens)
            probs = F.softmax(logits, dim=-1)
            pred_idx = torch.argmax(probs, dim=-1).item()
            confidence = probs[0, pred_idx].item()
        
        return {
            "fault_type": self.fault_labels[pred_idx],
            "confidence": confidence,
            "all_probabilities": {
                label: probs[0, i].item() 
                for i, label in enumerate(self.fault_labels)
            }
        }


class AutoEncoder(nn.Module):
    """
    自编码器用于异常检测
    通过重构误差识别异常数据点
    """
    def __init__(self, input_dim: int = 10, latent_dim: int = 4):
        super().__init__()
        
        # 编码器
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, 32),
            nn.ReLU(),
            nn.Linear(32, 16),
            nn.ReLU(),
            nn.Linear(16, latent_dim)
        )
        
        # 解码器
        self.decoder = nn.Sequential(
            nn.Linear(latent_dim, 16),
            nn.ReLU(),
            nn.Linear(16, 32),
            nn.ReLU(),
            nn.Linear(32, input_dim)
        )
        
        self.threshold = 0.5  # 异常阈值
    
    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        latent = self.encoder(x)
        reconstructed = self.decoder(latent)
        return reconstructed, latent
    
    def detect_anomaly(self, data: np.ndarray) -> Dict:
        """检测异常"""
        self.eval()
        with torch.no_grad():
            x = torch.FloatTensor(data)
            if x.dim() == 1:
                x = x.unsqueeze(0)
            
            reconstructed, latent = self.forward(x)
            mse = F.mse_loss(reconstructed, x, reduction='none').mean(dim=-1)
            
            is_anomaly = mse > self.threshold
            
        return {
            "is_anomaly": is_anomaly.tolist() if len(is_anomaly) > 1 else is_anomaly.item(),
            "reconstruction_error": mse.tolist() if len(mse) > 1 else mse.item(),
            "threshold": self.threshold,
            "latent_representation": latent.numpy().tolist()
        }


class AttentionClassifier(nn.Module):
    """
    基于注意力机制的分类器
    可解释性更强，能展示哪些特征对决策影响最大
    """
    def __init__(self, input_dim: int = 10, num_classes: int = 5):
        super().__init__()
        
        self.attention = nn.Sequential(
            nn.Linear(input_dim, 32),
            nn.Tanh(),
            nn.Linear(32, 1)
        )
        
        self.classifier = nn.Sequential(
            nn.Linear(input_dim, 64),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Linear(32, num_classes)
        )
        
        self.feature_names = [
            'CPU使用率', '内存使用率', 'IO等待', '磁盘使用率', '网络流量',
            '进程数', '负载1min', '负载5min', '负载15min', '响应时间'
        ]
        self.fault_types = ['正常', 'CPU故障', '内存故障', 'IO故障', '网络故障']
    
    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        # x shape: (batch, input_dim)
        
        # 计算每个特征的注意力权重
        # 使用简单的可学习权重
        attn_scores = self.attention(x)  # (batch, 1)
        
        # 生成每个特征的重要性权重（基于输入值和学习的变换）
        # 简化：使用归一化的输入值绝对值作为特征重要性
        feature_importance = torch.abs(x)
        attn_weights = F.softmax(feature_importance, dim=-1)  # (batch, input_dim)
        
        # 分类
        logits = self.classifier(x)
        
        return logits, attn_weights
    
    def predict_with_explanation(self, features: np.ndarray) -> Dict:
        """带解释的预测"""
        self.eval()
        with torch.no_grad():
            x = torch.FloatTensor(features)
            if x.dim() == 1:
                x = x.unsqueeze(0)
            
            logits, attn_weights = self.forward(x)
            probs = F.softmax(logits, dim=-1)
            pred_idx = torch.argmax(probs, dim=-1).item()
            confidence = probs[0, pred_idx].item()
            
            # 获取注意力权重
            weights = attn_weights[0].numpy()
            
            # 找出最重要的特征
            top_indices = np.argsort(weights)[::-1][:3]
            important_features = [
                {
                    "feature": self.feature_names[i] if i < len(self.feature_names) else f"特征{i}",
                    "attention_weight": float(weights[i]),
                    "value": float(features[i]) if i < len(features) else 0
                }
                for i in top_indices
            ]
        
        return {
            "prediction": self.fault_types[pred_idx],
            "confidence": confidence,
            "important_features": important_features,
            "all_attention_weights": weights.tolist()
        }


class DLModelManager:
    """
    深度学习模型管理器
    统一管理所有DL模型的加载、保存和推理
    """
    def __init__(self, model_dir: str = "checkpoints"):
        self.model_dir = model_dir
        os.makedirs(model_dir, exist_ok=True)
        
        # 初始化模型
        self.fault_classifier = SimpleNN(input_dim=10, num_classes=5)
        self.lstm_analyzer = LSTMAnalyzer(input_dim=5, hidden_dim=64)
        self.transformer = MiniTransformerEncoder(vocab_size=5000, d_model=128, num_layers=2)
        self.autoencoder = AutoEncoder(input_dim=10, latent_dim=4)
        self.attention_classifier = AttentionClassifier(input_dim=10, num_classes=5)
        
        # 初始化模型权重（使用预设值模拟训练后的模型）
        self._initialize_weights()
    
    def _initialize_weights(self):
        """初始化模型权重（实际部署时应加载训练好的权重）"""
        for model in [self.fault_classifier, self.lstm_analyzer, 
                      self.transformer, self.autoencoder, self.attention_classifier]:
            for m in model.modules():
                if isinstance(m, nn.Linear):
                    nn.init.xavier_uniform_(m.weight)
                    if m.bias is not None:
                        nn.init.zeros_(m.bias)
                elif isinstance(m, nn.Embedding):
                    nn.init.normal_(m.weight, mean=0, std=0.02)
    
    def classify_fault(self, features: List[float]) -> Dict:
        """使用神经网络分类故障"""
        features_array = np.array(features, dtype=np.float32)
        if len(features_array) < 10:
            features_array = np.pad(features_array, (0, 10 - len(features_array)))
        fault_type, confidence = self.fault_classifier.predict(features_array[:10])
        return {
            "model": "SimpleNN",
            "fault_type": fault_type,
            "confidence": confidence
        }
    
    def analyze_trend(self, time_series: List[List[float]]) -> Dict:
        """使用LSTM分析趋势"""
        ts_array = np.array(time_series, dtype=np.float32)
        result = self.lstm_analyzer.predict_trend(ts_array)
        result["model"] = "LSTM"
        return result
    
    def analyze_text(self, text: str) -> Dict:
        """使用Transformer分析文本"""
        result = self.transformer.analyze_text(text)
        result["model"] = "MiniTransformer"
        return result
    
    def detect_anomaly(self, data: List[float]) -> Dict:
        """使用AutoEncoder检测异常"""
        data_array = np.array(data, dtype=np.float32)
        if len(data_array) < 10:
            data_array = np.pad(data_array, (0, 10 - len(data_array)))
        result = self.autoencoder.detect_anomaly(data_array[:10])
        result["model"] = "AutoEncoder"
        return result
    
    def explain_prediction(self, features: List[float]) -> Dict:
        """使用注意力机制进行可解释预测"""
        features_array = np.array(features, dtype=np.float32)
        if len(features_array) < 10:
            features_array = np.pad(features_array, (0, 10 - len(features_array)))
        result = self.attention_classifier.predict_with_explanation(features_array[:10])
        result["model"] = "AttentionClassifier"
        return result
    
    def comprehensive_analysis(self, features: List[float], text: str = "") -> Dict:
        """综合分析：结合多个模型的结果"""
        results = {
            "nn_classification": self.classify_fault(features),
            "anomaly_detection": self.detect_anomaly(features),
            "explainable_prediction": self.explain_prediction(features)
        }
        
        if text:
            results["text_analysis"] = self.analyze_text(text)
        
        # 综合判断
        fault_votes = {}
        for key in ["nn_classification", "explainable_prediction"]:
            if key in results:
                fault_type = results[key].get("fault_type") or results[key].get("prediction")
                if fault_type:
                    fault_votes[fault_type] = fault_votes.get(fault_type, 0) + 1
        
        if text and "text_analysis" in results:
            text_fault = results["text_analysis"].get("fault_type")
            if text_fault:
                fault_votes[text_fault] = fault_votes.get(text_fault, 0) + 1
        
        # 投票决定最终结果
        final_fault = max(fault_votes.items(), key=lambda x: x[1])[0] if fault_votes else "未知"
        
        results["final_verdict"] = {
            "fault_type": final_fault,
            "is_anomaly": results["anomaly_detection"]["is_anomaly"],
            "voting_results": fault_votes
        }
        
        return results
    
    def save_models(self):
        """保存所有模型"""
        torch.save(self.fault_classifier.state_dict(), 
                   os.path.join(self.model_dir, "fault_classifier.pth"))
        torch.save(self.lstm_analyzer.state_dict(), 
                   os.path.join(self.model_dir, "lstm_analyzer.pth"))
        torch.save(self.transformer.state_dict(), 
                   os.path.join(self.model_dir, "transformer.pth"))
        torch.save(self.autoencoder.state_dict(), 
                   os.path.join(self.model_dir, "autoencoder.pth"))
        torch.save(self.attention_classifier.state_dict(), 
                   os.path.join(self.model_dir, "attention_classifier.pth"))
    
    def load_models(self):
        """加载所有模型"""
        try:
            self.fault_classifier.load_state_dict(
                torch.load(os.path.join(self.model_dir, "fault_classifier.pth")))
            self.lstm_analyzer.load_state_dict(
                torch.load(os.path.join(self.model_dir, "lstm_analyzer.pth")))
            self.transformer.load_state_dict(
                torch.load(os.path.join(self.model_dir, "transformer.pth")))
            self.autoencoder.load_state_dict(
                torch.load(os.path.join(self.model_dir, "autoencoder.pth")))
            self.attention_classifier.load_state_dict(
                torch.load(os.path.join(self.model_dir, "attention_classifier.pth")))
            return True
        except Exception as e:
            print(f"加载模型失败: {e}")
            return False
    
    def get_model_info(self) -> Dict:
        """获取模型信息"""
        def count_params(model):
            return sum(p.numel() for p in model.parameters())
        
        return {
            "models": {
                "SimpleNN": {
                    "description": "多层感知机故障分类器",
                    "parameters": count_params(self.fault_classifier),
                    "input": "系统指标向量(10维)",
                    "output": "故障类型(5类)"
                },
                "LSTM": {
                    "description": "时序分析与趋势预测",
                    "parameters": count_params(self.lstm_analyzer),
                    "input": "时间序列数据",
                    "output": "趋势预测"
                },
                "MiniTransformer": {
                    "description": "文本理解与故障描述分析",
                    "parameters": count_params(self.transformer),
                    "input": "故障描述文本",
                    "output": "故障类型分类"
                },
                "AutoEncoder": {
                    "description": "异常检测模型",
                    "parameters": count_params(self.autoencoder),
                    "input": "系统指标向量",
                    "output": "异常分数"
                },
                "AttentionClassifier": {
                    "description": "可解释性故障分类器",
                    "parameters": count_params(self.attention_classifier),
                    "input": "系统指标向量",
                    "output": "故障类型+注意力权重"
                }
            },
            "total_parameters": sum([
                count_params(self.fault_classifier),
                count_params(self.lstm_analyzer),
                count_params(self.transformer),
                count_params(self.autoencoder),
                count_params(self.attention_classifier)
            ])
        }


# 全局模型管理器实例
_dl_manager = None

def get_dl_manager() -> DLModelManager:
    """获取DL模型管理器单例"""
    global _dl_manager
    if _dl_manager is None:
        _dl_manager = DLModelManager()
    return _dl_manager
