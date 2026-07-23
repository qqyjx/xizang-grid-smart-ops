#!/bin/bash
#===============================================================================
# 西藏电网智能运维 Agent - 停止脚本
#===============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

AGENT_PORT=${AGENT_PORT:-8089}

# 方法1: 通过PID文件
if [ -f ".agent.pid" ]; then
    PID=$(cat .agent.pid)
    if kill -0 "$PID" 2>/dev/null; then
        echo "正在停止Agent (PID: $PID)..."
        kill "$PID" 2>/dev/null
        sleep 2
        if ! kill -0 "$PID" 2>/dev/null; then
            echo "✓ Agent已停止"
            rm -f .agent.pid
            exit 0
        fi
        kill -9 "$PID" 2>/dev/null
        rm -f .agent.pid
        echo "✓ Agent已强制停止"
        exit 0
    fi
    rm -f .agent.pid
fi

# 方法2: 通过端口
PORT_PID=$(lsof -ti :$AGENT_PORT 2>/dev/null)
if [ -n "$PORT_PID" ]; then
    echo "正在停止占用端口${AGENT_PORT}的进程 (PID: $PORT_PID)..."
    kill "$PORT_PID" 2>/dev/null
    sleep 2
    echo "✓ Agent已停止"
    exit 0
fi

# 方法3: 通过进程名
AGENT_PID=$(pgrep -f "python.*agent.py" 2>/dev/null)
if [ -n "$AGENT_PID" ]; then
    echo "正在停止Agent进程 (PID: $AGENT_PID)..."
    kill $AGENT_PID 2>/dev/null
    sleep 2
    echo "✓ Agent已停止"
    exit 0
fi

echo "Agent未在运行"
