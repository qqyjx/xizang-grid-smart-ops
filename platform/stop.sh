#!/bin/bash
#===============================================================================
# 西藏离线运维系统 - 停止服务脚本
#===============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.app.pid"
PORT=5001

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

stopped=false

# 方法1：通过PID文件停止
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo -e "${GREEN}正在停止服务 (PID: $PID)...${NC}"
        kill "$PID"
        sleep 2
        if kill -0 "$PID" 2>/dev/null; then
            echo -e "${RED}服务未响应，强制终止...${NC}"
            kill -9 "$PID" 2>/dev/null
        fi
        stopped=true
    fi
    rm -f "$PID_FILE"
fi

# 方法2：通过端口查找并停止（v6.0: lsof 在精简 CentOS 可能未装，加 fuser/ss 兜底）
PORT_PID=""
if command -v lsof >/dev/null 2>&1; then
    PORT_PID=$(lsof -ti :$PORT 2>/dev/null | head -1)
elif command -v fuser >/dev/null 2>&1; then
    PORT_PID=$(fuser ${PORT}/tcp 2>/dev/null | tr -d ' ')
elif command -v ss >/dev/null 2>&1; then
    PORT_PID=$(ss -ltnp 2>/dev/null | grep ":$PORT " | grep -oP 'pid=\K[0-9]+' | head -1)
fi
if [ -n "$PORT_PID" ]; then
    echo -e "${YELLOW}发现端口 $PORT 被占用 (PID: $PORT_PID)，正在停止...${NC}"
    kill $PORT_PID 2>/dev/null
    sleep 1
    if kill -0 $PORT_PID 2>/dev/null; then
        kill -9 $PORT_PID 2>/dev/null
    fi
    stopped=true
fi

# 方法3：通过进程名查找
PIDS=$(pgrep -f "python.*app.py" 2>/dev/null | grep -v $$ || true)
if [ -n "$PIDS" ]; then
    echo -e "${YELLOW}发现相关进程，正在停止: $PIDS${NC}"
    for pid in $PIDS; do
        kill $pid 2>/dev/null
    done
    sleep 1
    stopped=true
fi

if [ "$stopped" = true ]; then
    echo -e "${GREEN}✓ 服务已停止${NC}"
else
    echo -e "${YELLOW}服务未运行${NC}"
fi
