# -*- coding: utf-8 -*-
"""Schema 初始化 — 从 schema.sql 执行建表语句"""

import os
import logging

logger = logging.getLogger('db.schema')

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), 'schema.sql')


def _split_statements(sql: str):
    """按 ; 分割 SQL 语句，忽略注释行"""
    stmts = []
    buf = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('--'):
            continue
        buf.append(line)
        if stripped.endswith(';'):
            stmt = '\n'.join(buf).strip()
            if stmt.rstrip(';').strip():
                stmts.append(stmt.rstrip(';').strip())
            buf = []
    if buf:
        tail = '\n'.join(buf).strip()
        if tail:
            stmts.append(tail.rstrip(';').strip())
    return stmts


def init_schema(db_client) -> bool:
    """在 MySQL 上执行 schema.sql，幂等"""
    if db_client is None:
        print('[Schema] 跳过：db_client 为 None（MySQL 未启用或连接失败）', flush=True)
        return False
    if not os.path.exists(_SCHEMA_PATH):
        msg = f'[Schema] 找不到 schema.sql: {_SCHEMA_PATH}'
        print(msg, flush=True)
        logger.error(msg)
        return False

    try:
        with open(_SCHEMA_PATH, 'r', encoding='utf-8') as f:
            sql = f.read()

        stmts = _split_statements(sql)
        ok_count = 0
        for stmt in stmts:
            rows = db_client.execute(stmt, commit=True)
            if rows >= 0:
                ok_count += 1
            else:
                # v5.38: 建表失败 print 到 stdout 便于客户 tail app.log 定位
                print(f'[Schema] 建表失败: {stmt[:120]}', flush=True)
                logger.warning(f'[Schema] 建表失败: {stmt[:80]}')

        print(f'[Schema] 初始化完成 {ok_count}/{len(stmts)} 个语句', flush=True)
        logger.info(f'[Schema] 初始化完成 {ok_count}/{len(stmts)} 个语句')
        return ok_count == len(stmts)
    except Exception as e:
        msg = f'[Schema] 初始化异常: {e}'
        print(msg, flush=True)
        logger.error(msg)
        return False
