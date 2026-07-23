# -*- coding: utf-8 -*-
"""
服务模块
"""

from .data_analyzer import DataAnalyzer, get_data_analyzer
from .knowledge_base import KnowledgeBase, get_knowledge_base
from .operations import OperationService, get_operation_service
from .auto_repair import AutoRepairService, get_auto_repair_service, store_uploaded_fault, get_uploaded_fault, clear_uploaded_fault
from .report_generator import ReportGenerator, get_report_generator

__all__ = [
    'DataAnalyzer', 'get_data_analyzer',
    'KnowledgeBase', 'get_knowledge_base',
    'OperationService', 'get_operation_service',
    'AutoRepairService', 'get_auto_repair_service',
    'store_uploaded_fault', 'get_uploaded_fault', 'clear_uploaded_fault',
    'ReportGenerator', 'get_report_generator'
]
