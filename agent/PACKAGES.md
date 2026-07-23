# Agent Python 包清单与系统隔离说明

## v5.18 起：完全隔离，不动系统

从 **v5.18** 开始，Agent 部署脚本（`deploy.sh`）强制使用包内 `miniconda3/` 独立 Python 环境，所有 Python 依赖都装在：

```
xizang-agent-7B/miniconda3/lib/python3.x/site-packages/
```

**与系统 Python（`/usr/bin/python3`、`/usr/lib/python3.x/`）完全隔离**。

### 卸载方法

直接删除 `xizang-agent-7B/` 整个目录即可彻底卸载，**不会影响**系统上任何 Python 包或其他业务。

```bash
./stop.sh
cd ..
rm -rf xizang-agent-7B/
```

---

## Agent 用到的所有 Python 包

下列包都装在独立 Miniconda 环境中（v5.18+），不会污染系统：

| 包名 | 用途 | 是否必需 |
|------|------|---------|
| **flask** | HTTP Web 框架，提供 Agent REST API | 必需 |
| **flask-cors** | 跨域请求支持，平台调 Agent 用 | 必需 |
| **werkzeug** | Flask 底层 WSGI 工具 | 必需 |
| **jinja2** | Flask 模板引擎依赖 | 必需 |
| **markupsafe** | jinja2 依赖 | 必需 |
| **itsdangerous** | Flask session 签名 | 必需 |
| **click** | Flask CLI 依赖 | 必需 |
| **blinker** | Flask 信号系统依赖 | 必需 |
| **psutil** | 系统指标采集（CPU/内存/磁盘/网络） | 必需 |
| **requests** | HTTP 客户端，用于 /screenshot 路由转发图片到平台 | 必需 |
| **urllib3** | requests 依赖 | 必需 |
| **certifi** | requests 依赖（SSL 证书） | 必需 |
| **charset-normalizer** | requests 依赖（字符编码检测） | 必需 |
| **idna** | requests 依赖（国际化域名） | 必需 |
| **typing_extensions** | 类型注解后向兼容 | 必需 |
| **zipp** | importlib_metadata 依赖 | 必需 |
| **importlib_metadata** | Python 元数据 API | 必需 |
| **setuptools** | wheel 安装基础工具 | 必需 |
| **wheel** | wheel 包格式支持 | 必需 |

所有包都以 `.whl` 文件预打包在 `packages/` 目录中，部署时离线安装，**全程不需要联网**。

---

## v5.17 及之前版本的"系统污染"问题

### 问题描述

v5.17 及之前版本的 `deploy.sh` 在 `[1/4] 检查Python环境` 阶段，**优先复用系统 Python**：

```bash
# v5.17 旧逻辑（已废弃）
elif command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"   # ← 用了系统 python3
```

后续 `pip install` 会写入系统 site-packages（如 `/usr/lib/python3.6/site-packages/`），**会先卸载系统原有的版本再装新版本**。

### 实际观察到的污染

客户在 <内网IP> 部署时截图显示：

```
Attempting uninstall: jinja2
  Found existing installation: Jinja2 2.11.2
  Uninstalling Jinja2-2.11.2:
    Successfully uninstalled Jinja2-2.11.2
Successfully installed jinja2-3.1.6

Attempting uninstall: requests
  Found existing installation: requests 2.24.0
  Uninstalling requests-2.24.0:
    Successfully uninstalled requests-2.24.0
Successfully installed requests-2.31.0
```

理论上 v5.17 的 INSTALL_ORDER 中**任何一个包**都可能发生类似替换，包括上面"用到的所有 Python 包"列表里的全部 19 个包。

### 影响范围

- 如果该服务器上**只跑 Agent**，没有其他 Python 业务：**无影响**，新版本兼容性更好。
- 如果该服务器上**还跑其他 Python 服务**，且这些服务依赖被替换的版本：**可能出问题**，比如某个旧 Web 服务依赖 `Flask 1.x`、`Jinja2 2.x`，会因为 API 不兼容而报错。

### 怎么修复

**方案 A：从备份镜像还原**（最干净）— 如果你有这台服务器的快照/镜像，直接回滚是最安全的。

**方案 B：用 rollback.sh 卸载 + yum/apt 重装**

```bash
# 1. 进入 Agent 目录
cd xizang-agent-7B

# 2. 跑 rollback.sh — 它会列出当前版本号、停止 Agent、卸载这些包
./rollback.sh
# （根据提示选 y 确认）

# 3. 用系统包管理器重装业务需要的版本
#    CentOS/RHEL:
sudo yum install python3-jinja2 python3-requests python3-flask
#    Ubuntu/Debian:
sudo apt install python3-jinja2 python3-requests python3-flask

# 4. 重新部署 v5.18+ Agent（这次会装到独立 miniconda3/，不动系统）
./deploy.sh
```

### rollback.sh 的局限

- pip 没有"恢复到上一版本"的功能。rollback.sh 只能**卸载**，无法**精确恢复**到 deploy 之前的版本号。
- 卸载之后必须用 yum/apt 或客户原有部署方式装回去。
- 如果客户在 v5.17 deploy 之前没记录原版本号，rollback.sh 也只能列出"现在的版本（即 v5.17 装的新版）"，看不到原来的旧版号。这是 pip 的固有限制。

---

## 验证当前 Agent 是否使用独立环境

```bash
# 看 Agent 进程的 Python 路径
ps aux | grep agent.py | grep -v grep

# 应该看到类似：
# root  2817318  ... /opt/xizang-agent-7B/miniconda3/bin/python agent.py
#                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#                    路径包含 miniconda3 = 独立环境，✓ 隔离

# 如果看到的是 /usr/bin/python 或 /usr/local/bin/python3 这种系统路径，
# 说明用的还是 v5.17 旧版部署，需要 rollback + 重新部署 v5.18
```

---

## 版本历史

| 版本 | 日期 | 系统隔离 |
|------|------|---------|
| v5.18-7B | 2026-04-08 | ✅ 强制独立 Miniconda，完全隔离 |
| v5.17-7B 及以下 | ~ 2026-04-07 | ❌ 优先复用系统 Python，可能污染系统包 |
