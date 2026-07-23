#!/usr/bin/env python3
"""独立 MySQL 建表脚本 — v5.38 新增

用途：客户现场部署后若 app 启动时 MySQL 连接失败导致未建表，
      可手工跑本脚本补建，避免必须重启整个服务。

用法：
    cd /path/to/xizang-offline-7B
    export DB_HOST=<内网IP>
    export DB_USER=xz_gmdmxzdjx
    export DB_NAME=gmdmxzdjx
    export DB_PASSWORD=xxx        # 或脚本会提示输入；或读 ~/.xizang_db_password
    python init_mysql_schema.py

成功输出：[Schema] 初始化完成 9/9 个语句
然后 Navicat / mysql 客户端 SHOW TABLES; 应看到 9 张 xzyw_* 表。
"""

import os
import sys
import getpass


def main() -> int:
    # 默认值与 db/client.py init_db_from_env 保持一致
    host = os.environ.get('DB_HOST', '<内网IP>')
    port = int(os.environ.get('DB_PORT', '3306'))
    user = os.environ.get('DB_USER', 'xz_gmdmxzdjx')
    database = os.environ.get('DB_NAME', 'gmdmxzdjx')
    password = os.environ.get('DB_PASSWORD', '')

    # v5.39: 优先读 ~/.xizang_db_password（deploy.sh 已保存过的密码），免交互
    if not password:
        pwd_file = os.path.expanduser('~/.xizang_db_password')
        if os.path.exists(pwd_file):
            try:
                with open(pwd_file, 'r') as _f:
                    password = _f.read().strip()
                if password:
                    print(f'[Init] 从 {pwd_file} 读取密码（chmod 600 管理）', flush=True)
            except Exception as _e:
                print(f'[Init] 读取 {pwd_file} 失败: {_e}', flush=True)

    if not password:
        try:
            password = getpass.getpass(f'请输入 MySQL 用户 {user}@{host} 的密码: ')
        except (EOFError, KeyboardInterrupt):
            print('\n[Abort] 未提供密码', flush=True)
            return 2

    print(f'[Init] 目标: {host}:{port}/{database} user={user}', flush=True)

    try:
        from db.client import DBClient
        from db.schema import init_schema
    except ImportError as e:
        print(f'[Init] 模块导入失败: {e}（请确认在 xizang-offline-7B 目录下执行）', flush=True)
        return 3

    client = DBClient(host, port, user, password, database)
    if not client.is_available(force_check=True):
        print(f'[Init] 连接失败，请检查 host/port/user/password/network，并确认 {database} 库已由 DBA 创建', flush=True)
        return 4

    ok = init_schema(client)
    if ok:
        rows = client.query("SHOW TABLES")
        table_names = [list(r.values())[0] for r in rows]
        xzyw_tables = [t for t in table_names if t.startswith('xzyw_')]
        print(f'[Init] ✓ 已创建 xzyw_* 表 {len(xzyw_tables)} 张: {", ".join(xzyw_tables)}', flush=True)
        return 0
    else:
        print('[Init] ✗ 部分表建表失败，查看上方输出定位具体语句', flush=True)
        return 5


if __name__ == '__main__':
    sys.exit(main())
