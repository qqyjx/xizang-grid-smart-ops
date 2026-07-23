# -*- coding: utf-8 -*-
"""数据库持久层 — v5.36

设计原则：
- MySQL 可选：若环境变量 DB_ENABLED=true 且能连接，启用 MySQL；否则回退 JSON/SQLite
- 双写兼容：JSON 文件作为离线备份，MySQL 作为主存储（如启用）
- 轻量依赖：只用 PyMySQL（纯 Python，无需编译）
"""

from .client import DBClient, get_db, is_db_enabled, init_db_from_env
from .schema import init_schema

__all__ = ['DBClient', 'get_db', 'is_db_enabled', 'init_db_from_env', 'init_schema']
