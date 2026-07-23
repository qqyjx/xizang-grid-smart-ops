# -*- coding: utf-8 -*-
"""MySQL 客户端 — 连接池化、重试、自动降级

v5.36 首次引入。所有数据库读写都走这里。
"""

import os
import time
import logging
import threading
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

logger = logging.getLogger('db')

try:
    import pymysql
    from pymysql.cursors import DictCursor
    PYMYSQL_AVAILABLE = True
except ImportError:
    PYMYSQL_AVAILABLE = False
    pymysql = None
    DictCursor = None


class DBClient:
    """轻量 MySQL 客户端 — 每次请求独立连接，失败自动降级"""

    def __init__(self, host: str, port: int, user: str, password: str,
                 database: str, charset: str = 'utf8mb4', timeout: int = 5):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.charset = charset
        self.timeout = timeout
        self._available = None  # None=未测试 True=可用 False=不可用
        self._last_check = 0
        self._lock = threading.Lock()

    def _get_connection(self):
        """创建新连接"""
        if not PYMYSQL_AVAILABLE:
            raise RuntimeError('PyMySQL 未安装')
        return pymysql.connect(
            host=self.host, port=self.port,
            user=self.user, password=self.password,
            database=self.database, charset=self.charset,
            connect_timeout=self.timeout,
            read_timeout=self.timeout * 2,
            write_timeout=self.timeout * 2,
            cursorclass=DictCursor,
            autocommit=False
        )

    def is_available(self, force_check: bool = False) -> bool:
        """检查数据库是否可用（带缓存，避免频繁 ping）"""
        if not PYMYSQL_AVAILABLE:
            return False
        now = time.time()
        with self._lock:
            # 缓存 30 秒
            if not force_check and self._available is not None and (now - self._last_check) < 30:
                return self._available
            try:
                conn = self._get_connection()
                conn.ping(reconnect=False)
                conn.close()
                self._available = True
                # v5.38: print 到 stdout（nohup 捕获入 app.log），方便客户现场 tail 查看
                print(f'[DB] MySQL 连接正常: {self.host}:{self.port}/{self.database} user={self.user}', flush=True)
                logger.info(f'[DB] MySQL 连接正常: {self.host}:{self.port}/{self.database}')
            except Exception as e:
                self._available = False
                # v5.38: 失败原因 print 到 stdout，暴露 host/port/user/db 便于定位
                print(f'[DB] MySQL 连接失败: {self.host}:{self.port}/{self.database} user={self.user} err={e}', flush=True)
                logger.warning(f'[DB] MySQL 连接失败: {e}')
            self._last_check = now
            return self._available

    @contextmanager
    def connection(self):
        """上下文管理器：自动关闭连接"""
        conn = None
        try:
            conn = self._get_connection()
            yield conn
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def execute(self, sql: str, params: Optional[tuple] = None, commit: bool = True) -> int:
        """执行 INSERT/UPDATE/DELETE，返回影响行数"""
        try:
            with self.connection() as conn:
                with conn.cursor() as cur:
                    rows = cur.execute(sql, params or ())
                    if commit:
                        conn.commit()
                    return rows
        except Exception as e:
            logger.error(f'[DB] execute 失败: {e} | SQL: {sql[:200]}')
            return -1

    def execute_many(self, sql: str, params_list: List[tuple]) -> int:
        """批量执行"""
        if not params_list:
            return 0
        try:
            with self.connection() as conn:
                with conn.cursor() as cur:
                    rows = cur.executemany(sql, params_list)
                    conn.commit()
                    return rows
        except Exception as e:
            logger.error(f'[DB] execute_many 失败: {e}')
            return -1

    def query(self, sql: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """SELECT 查询，返回 dict 列表"""
        try:
            with self.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params or ())
                    return list(cur.fetchall())
        except Exception as e:
            logger.error(f'[DB] query 失败: {e} | SQL: {sql[:200]}')
            return []

    def query_one(self, sql: str, params: Optional[tuple] = None) -> Optional[Dict[str, Any]]:
        """SELECT 单行"""
        try:
            with self.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params or ())
                    return cur.fetchone()
        except Exception as e:
            logger.error(f'[DB] query_one 失败: {e}')
            return None

    def insert(self, table: str, data: Dict[str, Any]) -> int:
        """便捷插入，返回 lastrowid"""
        cols = list(data.keys())
        placeholders = ', '.join(['%s'] * len(cols))
        col_list = ', '.join(f'`{c}`' for c in cols)
        sql = f'INSERT INTO `{table}` ({col_list}) VALUES ({placeholders})'
        try:
            with self.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(data[c] for c in cols))
                    conn.commit()
                    return cur.lastrowid
        except Exception as e:
            logger.error(f'[DB] insert 失败: {e} | table: {table}')
            return -1

    def upsert(self, table: str, data: Dict[str, Any], key_cols: List[str]) -> int:
        """INSERT ... ON DUPLICATE KEY UPDATE"""
        cols = list(data.keys())
        placeholders = ', '.join(['%s'] * len(cols))
        col_list = ', '.join(f'`{c}`' for c in cols)
        update_list = ', '.join(f'`{c}`=VALUES(`{c}`)' for c in cols if c not in key_cols)
        sql = f'INSERT INTO `{table}` ({col_list}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {update_list}'
        try:
            with self.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(data[c] for c in cols))
                    conn.commit()
                    return cur.rowcount
        except Exception as e:
            logger.error(f'[DB] upsert 失败: {e}')
            return -1


# ========== 单例 ==========

_db_instance: Optional[DBClient] = None
_db_enabled: bool = False


def _read_env_bool(key: str, default: bool = False) -> bool:
    v = os.environ.get(key, '')
    return v.lower() in ('1', 'true', 'yes', 'on') if v else default


def init_db_from_env() -> Optional[DBClient]:
    """从环境变量初始化 DB 单例。未启用或依赖缺失时返回 None。"""
    global _db_instance, _db_enabled

    enabled = _read_env_bool('DB_ENABLED', False)
    if not enabled:
        _db_enabled = False
        print('[DB] 未启用（DB_ENABLED 未设置），使用 JSON/SQLite 存储', flush=True)
        logger.info('[DB] 未启用（DB_ENABLED 未设置），使用 JSON/SQLite 存储')
        return None

    if not PYMYSQL_AVAILABLE:
        _db_enabled = False
        print('[DB] DB_ENABLED=true 但 PyMySQL 未安装，降级到 JSON/SQLite', flush=True)
        logger.warning('[DB] DB_ENABLED=true 但 PyMySQL 未安装，降级到 JSON/SQLite')
        return None

    host = os.environ.get('DB_HOST', '<内网IP>')
    port = int(os.environ.get('DB_PORT', '3306'))
    user = os.environ.get('DB_USER', 'xz_gmdmxzdjx')
    password = os.environ.get('DB_PASSWORD', '')
    database = os.environ.get('DB_NAME', 'gmdmxzdjx')

    # v5.38: 密码空时显式警告（常见漏配场景）
    if not password:
        print(f'[DB] WARNING: DB_PASSWORD 环境变量为空，MySQL 连接将失败；'
              f'请 export DB_PASSWORD=xxx 后重启', flush=True)

    client = DBClient(host, port, user, password, database)
    if client.is_available(force_check=True):
        _db_instance = client
        _db_enabled = True
        print(f'[DB] MySQL 已启用: {host}:{port}/{database} user={user}', flush=True)
        logger.info(f'[DB] MySQL 已启用: {host}:{port}/{database}')
        return client
    else:
        _db_enabled = False
        print(f'[DB] MySQL 连接失败 host={host} port={port} user={user} db={database}，降级到 JSON/SQLite', flush=True)
        logger.warning(f'[DB] MySQL 连接失败，降级到 JSON/SQLite')
        return None


def get_db() -> Optional[DBClient]:
    """获取 DB 单例"""
    return _db_instance


def is_db_enabled() -> bool:
    """MySQL 是否启用且可用"""
    return _db_enabled and _db_instance is not None and _db_instance.is_available()
