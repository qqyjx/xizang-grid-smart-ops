#!/bin/bash
#===============================================================================
# 西藏电网智能运维 Agent（7B多模态版）- 纯离线一键部署脚本 v6.0-7B
# 强制使用包内 Miniconda，不再复用系统 Python
# 所有依赖装在 xizang-agent-7B/miniconda3/ 内，与系统完全隔离
# 删除整个目录即彻底卸载，不会动任何系统包
#===============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo -e "${CYAN}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║   西藏电网智能运维 Agent - 7B多模态版部署 v6.0-7B             ║${NC}"
echo -e "${CYAN}║   独立 Miniconda 环境，与系统 Python 完全隔离                  ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# [1/4] 准备独立 Python 环境（强制使用包内 Miniconda）
echo -e "[1/4] ${YELLOW}准备 Python 环境（独立 Miniconda，与系统隔离）...${NC}"
CONDA_DIR="$SCRIPT_DIR/miniconda3"

if [ ! -f "$CONDA_DIR/bin/python" ]; then
    INSTALLER="$SCRIPT_DIR/python_installer/Miniconda3-latest-Linux-x86_64.sh"
    if [ ! -f "$INSTALLER" ]; then
        echo -e "      ${RED}✗ 找不到 Miniconda 安装包${NC}"
        echo -e "      ${RED}  请确认 python_installer/Miniconda3-latest-Linux-x86_64.sh 存在${NC}"
        exit 1
    fi

    # 检查磁盘空间（Miniconda 解压需要约 500MB）
    AVAIL_MB=$(df -m "$SCRIPT_DIR" 2>/dev/null | awk 'NR==2{print $4}')
    if [ -n "$AVAIL_MB" ] && [ "$AVAIL_MB" -lt 600 ] 2>/dev/null; then
        echo -e "      ${RED}✗ 磁盘空间不足：剩余 ${AVAIL_MB}MB，需要至少 600MB${NC}"
        exit 1
    fi

    echo -e "      ${YELLOW}→ 首次部署，正在安装独立 Miniconda（约30秒）...${NC}"
    mkdir -p logs
    bash "$INSTALLER" -b -p "$CONDA_DIR" > logs/miniconda_install.log 2>&1
    if [ ! -f "$CONDA_DIR/bin/python" ]; then
        echo -e "      ${RED}✗ Miniconda 安装失败，错误信息：${NC}"
        tail -15 logs/miniconda_install.log 2>/dev/null
        echo ""
        echo -e "      ${YELLOW}完整日志: logs/miniconda_install.log${NC}"
        exit 1
    fi
fi

PYTHON_CMD="$CONDA_DIR/bin/python"
PIP_CMD="$CONDA_DIR/bin/pip"
PYTHON_VER=$("$PYTHON_CMD" --version 2>&1)
echo -e "      ${GREEN}✓ 独立 Python: ${PYTHON_VER}${NC}"
echo -e "      ${GREEN}✓ 隔离路径: ${CONDA_DIR}${NC}"
echo -e "      ${GREEN}✓ 不会动系统 Python 任何包${NC}"

# [2/4] 检查 pip（Miniconda 自带，正常无需安装）
echo -e "[2/4] ${YELLOW}检查 pip...${NC}"
if "$PIP_CMD" --version &>/dev/null; then
    echo -e "      ${GREEN}✓ pip 已就绪${NC}"
else
    echo -e "      ${RED}✗ Miniconda 自带 pip 异常，请检查安装${NC}"
    exit 1
fi

# [3/4] 安装依赖到独立环境
echo -e "[3/4] ${YELLOW}安装 Python 依赖到独立环境...${NC}"

if "$PYTHON_CMD" -c "import flask, flask_cors, psutil, requests" 2>/dev/null; then
    echo -e "      ${GREEN}✓ 依赖已安装${NC}"
else
    echo -e "      ${YELLOW}→ 批量安装离线包（pip 自动解析依赖，独立环境）...${NC}"
    "$PIP_CMD" install --no-index --find-links="$SCRIPT_DIR/packages" \
        setuptools wheel typing_extensions \
        markupsafe itsdangerous werkzeug click blinker jinja2 \
        flask flask-cors \
        certifi charset-normalizer idna urllib3 requests \
        psutil -q 2>&1 | tail -5

    if "$PYTHON_CMD" -c "import flask, flask_cors, psutil, requests" 2>/dev/null; then
        echo -e "      ${GREEN}✓ 依赖批量安装成功（隔离环境）${NC}"
    else
        echo -e "      ${YELLOW}→ 批量失败，逐个 wheel 兜底安装...${NC}"
        for whl in "$SCRIPT_DIR/packages"/*.whl; do
            "$PIP_CMD" install --no-index --find-links="$SCRIPT_DIR/packages" "$whl" -q 2>/dev/null || true
        done
        if "$PYTHON_CMD" -c "import flask, flask_cors, psutil, requests" 2>/dev/null; then
            echo -e "      ${GREEN}✓ 依赖逐个安装成功（隔离环境）${NC}"
        else
            echo -e "      ${RED}✗ 依赖安装失败${NC}"
            echo -e "      ${YELLOW}已安装的包:${NC}"
            "$PIP_CMD" list 2>/dev/null | head -30
            exit 1
        fi
    fi
fi

# [4/4] 启动服务（用独立 Python）
echo -e "[4/4] ${YELLOW}启动Agent服务...${NC}"
mkdir -p logs

# 停止旧进程 — v6.0: PID 文件 + 进程名 + 端口三重兜底(rm -rf 重装/PID丢失时仍能杀旧进程,
# 避免 :8089 被占导致新 Agent 起来就停)
APORT=${AGENT_PORT:-8089}
if [ -f ".agent.pid" ]; then
    OLD_PID=$(cat .agent.pid 2>/dev/null)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo -e "      ${YELLOW}→ 停止旧进程 (PID: $OLD_PID)${NC}"
        kill "$OLD_PID" 2>/dev/null || true; sleep 1; kill -9 "$OLD_PID" 2>/dev/null || true
    fi
    rm -f .agent.pid
fi
AG_PIDS=$(pgrep -f "python.*agent\.py" 2>/dev/null || true)
if [ -n "$AG_PIDS" ]; then
    for p in $AG_PIDS; do kill "$p" 2>/dev/null || true; done
    sleep 1; for p in $AG_PIDS; do kill -9 "$p" 2>/dev/null || true; done
fi
AGP=""
if command -v lsof >/dev/null 2>&1; then AGP=$(lsof -ti :$APORT 2>/dev/null | head -1)
elif command -v fuser >/dev/null 2>&1; then AGP=$(fuser ${APORT}/tcp 2>/dev/null | tr -d ' ')
elif command -v ss >/dev/null 2>&1; then AGP=$(ss -ltnp 2>/dev/null | grep ":$APORT " | grep -oP 'pid=\K[0-9]+' | head -1); fi
if [ -n "$AGP" ]; then
    echo -e "      ${YELLOW}→ 端口 $APORT 仍被占用 (PID: $AGP), 强制释放...${NC}"
    kill "$AGP" 2>/dev/null || true; sleep 1; kill -9 "$AGP" 2>/dev/null || true
fi

# 启动（强制使用独立 Miniconda Python）
nohup "$PYTHON_CMD" agent.py > logs/agent.log 2>&1 &
NEW_PID=$!
echo $NEW_PID > .agent.pid
sleep 2

if kill -0 "$NEW_PID" 2>/dev/null; then
    echo -e "      ${GREEN}✓ Agent启动成功 (PID: $NEW_PID)${NC}"
else
    echo -e "      ${RED}✗ Agent启动失败${NC}"
    tail -20 logs/agent.log 2>/dev/null
    exit 1
fi

LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")
AGENT_PORT=${AGENT_PORT:-8089}

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    ✅ Agent部署成功!                           ║${NC}"
echo -e "${GREEN}╠════════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║  🌐 Agent地址: http://${LOCAL_IP}:${AGENT_PORT}${NC}"
echo -e "${GREEN}║  📋 常用命令:${NC}"
echo -e "${GREEN}║     停止: ./stop.sh     查看日志: tail -f logs/agent.log${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}验证命令:${NC}"
echo -e "  curl http://${LOCAL_IP}:${AGENT_PORT}/health"
echo ""
