# 西藏电网智能运维 Agent - 7B多模态版 v5.18

## 概述

部署在被监控服务器上的轻量探针，配合 `xizang-offline-7B` 平台端进行指标采集、命令执行和截图转发。

**Agent 本身不运行 7B 模型**，仅做指标采集和数据转发。AI 分析能力集中在平台端。

## ⚠️ v5.18 重要变更：完全系统隔离

从 v5.18 起，Agent 部署强制使用包内独立 Miniconda 环境（`xizang-agent-7B/miniconda3/`）。

| 项目 | v5.17 及之前 | v5.18+ |
|------|-------------|--------|
| Python 来源 | 优先复用系统 `python3` | 强制使用包内 Miniconda |
| 依赖安装位置 | 系统 site-packages | `xizang-agent-7B/miniconda3/lib/...` |
| 是否动系统包 | **会**（卸载替换 jinja2/requests/Flask 等） | **不会**（完全隔离） |
| 卸载方式 | 需手动处理系统包 | `rm -rf xizang-agent-7B/` 即可 |

详见 [PACKAGES.md](PACKAGES.md) — 包含完整包清单、污染问题说明、回滚方案。

如果你之前用 v5.17 部署过这台服务器，**强烈建议**先跑 `./rollback.sh` 卸载之前装到系统的包，再重新部署 v5.18。

## 快速部署

```bash
# 解压
tar -xzf xizang-agent-7B-v5.18.tar.gz
cd xizang-agent-7B

# 一键部署（约 1-2 分钟）
chmod +x deploy.sh && ./deploy.sh

# 验证
curl http://localhost:8089/health
```

部署脚本会自动：
1. 安装包内 Miniconda 到 `./miniconda3/`（首次约30秒）
2. 离线安装 19 个 Python 依赖包到 `./miniconda3/lib/...`（不动系统）
3. 启动 Agent 服务（端口 8089）

**全程不需要联网**，**全程不会动系统任何 Python 包**。

## 接口列表

### 无需认证

```bash
GET /health
curl http://localhost:8089/health
```

### 需要 Token (`X-Agent-Token: CHANGE_ME_AGENT_TOKEN`)

| 接口 | 方法 | 说明 |
|------|------|------|
| `/status` | GET | 系统状态（CPU/内存/磁盘/网络） |
| `/repair` | POST | 执行修复动作 |
| `/script` | POST | 执行预定义脚本 |
| `/screenshot` | POST | 转发截图给平台多模态分析 |
| `/logs/latest` | GET | 获取最新 Agent 日志 |
| `/config` | GET/POST | 读取/更新 Agent 配置 |

### 示例

```bash
# 系统状态
curl -H "X-Agent-Token: CHANGE_ME_AGENT_TOKEN" \
     http://localhost:8089/status

# 自动修复
curl -X POST -H "X-Agent-Token: CHANGE_ME_AGENT_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"action": "auto"}' \
     http://localhost:8089/repair

# 截图转发分析
curl -X POST -H "X-Agent-Token: CHANGE_ME_AGENT_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "image": "<base64编码的图片>",
       "question": "分析这张监控截图",
       "platform_url": "http://平台IP:5001",
       "mime_type": "image/png"
     }' \
     http://localhost:8089/screenshot

# 获取最新日志
curl "http://localhost:8089/logs/latest?token=CHANGE_ME_AGENT_TOKEN&lines=100"
```

## 常用命令

```bash
./deploy.sh              # 部署/重启
./stop.sh                # 停止服务
./rollback.sh            # ⚠️ 仅对从 v5.17 升级的机器，卸载系统污染包
tail -f logs/agent.log   # 查看实时日志
```

## 验证 Agent 是否使用独立环境

```bash
ps aux | grep agent.py | grep -v grep
```

正确输出应包含 `xizang-agent-7B/miniconda3/bin/python` 路径。如果看到的是 `/usr/bin/python` 或 `/usr/local/bin/python3` 这种系统路径，说明用的还是 v5.17 旧版部署，建议跑 `./rollback.sh` 后重新部署 v5.18。

## 目录结构

```
xizang-agent-7B/
├── agent.py                     # Agent 主程序 v5.18-7B
├── deploy.sh                    # 一键部署 v5.18-7B
├── stop.sh                      # 停止服务
├── rollback.sh                  # v5.17 系统污染回滚脚本
├── PACKAGES.md                  # 包清单与隔离说明
├── README.md                    # 本文件
├── python_installer/            # Miniconda 安装包
│   └── Miniconda3-latest-Linux-x86_64.sh
├── packages/                    # Python 离线 wheel 包
├── miniconda3/                  # 部署后生成的独立 Python 环境
└── logs/                        # 日志目录
```

## 技术规格

| 项目 | 值 |
|------|-----|
| 端口 | 8089 |
| Token | `CHANGE_ME_AGENT_TOKEN` |
| 内存占用 | ~50MB（空闲） |
| CPU 占用 | <1%（空闲） |
| 部署包大小 | ~155MB（含 Miniconda） |
| 支持系统 | Linux x86_64（CentOS/RHEL/Ubuntu/Debian） |

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v5.18-7B | 2026-04-08 | **强制独立 Miniconda，解决系统包污染** + rollback.sh |
| v5.17-7B 及以下 | ~ 2026-04-07 | ❌ 优先复用系统 Python，可能污染系统包 |
