#!/bin/bash
#===============================================================================
# 西藏电网智能运维 Agent - 一键诊断脚本
# 用法: chmod +x diagnose.sh && ./diagnose.sh
# 输出: diagnose_report_<时间戳>.txt  ← 发回给开发人员即可
#===============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
REPORT="diagnose_report_${TIMESTAMP}.txt"

w() { echo "$@" >> "$REPORT"; }
section() { w ""; w "========== $1 =========="; }
cmd() { w ">>> $1"; eval "$1" >> "$REPORT" 2>&1; w ""; }

> "$REPORT"

w "西藏电网智能运维 Agent - 诊断报告"
w "生成时间: $(date '+%Y-%m-%d %H:%M:%S')"
w "服务器: $(hostname)"
w "IP: $(hostname -I 2>/dev/null | awk '{print $1}')"

#--- 1. 系统环境 ---
section "1. 系统环境"
cmd "uname -a"
cmd "cat /etc/os-release 2>/dev/null | head -5"
cmd "free -h"
cmd "df -h /"

#--- 2. Python 环境 ---
section "2. Python 环境"
CONDA_DIR="$SCRIPT_DIR/miniconda3"
if [ -f "$CONDA_DIR/bin/python" ]; then
    PYTHON="$CONDA_DIR/bin/python"
    w "Python来源: 包内Miniconda"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
    w "Python来源: 系统python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
    w "Python来源: 系统python"
else
    w "Python: 未找到!"
    PYTHON=""
fi

if [ -n "$PYTHON" ]; then
    cmd "$PYTHON --version"
    for pkg in flask flask_cors psutil requests; do
        result=$($PYTHON -c "import $pkg; print(f'$pkg: OK ({$pkg.__version__})')" 2>&1)
        w "  $result"
    done
fi

#--- 3. 服务状态 ---
section "3. Agent服务状态"
AGENT_PORT=${AGENT_PORT:-8089}
cmd "netstat -tlnp 2>/dev/null | grep $AGENT_PORT || ss -tlnp | grep $AGENT_PORT"

if [ -f ".agent.pid" ]; then
    PID=$(cat .agent.pid)
    w "PID文件: .agent.pid → $PID"
    if kill -0 "$PID" 2>/dev/null; then
        w "进程状态: 运行中"
        cmd "ps -p $PID -o pid,ppid,user,%cpu,%mem,etime,cmd"
    else
        w "进程状态: 已停止"
    fi
else
    w "PID文件: 不存在"
fi

# 本地API测试
w "--- 本地API测试 ---"
for endpoint in "/health" "/status"; do
    w "  GET http://localhost:${AGENT_PORT}${endpoint}"
    if [ "$endpoint" = "/status" ]; then
        result=$(curl -s --connect-timeout 3 --max-time 5 -H "X-Agent-Token: CHANGE_ME_AGENT_TOKEN" "http://localhost:${AGENT_PORT}${endpoint}" 2>&1)
    else
        result=$(curl -s --connect-timeout 3 --max-time 5 "http://localhost:${AGENT_PORT}${endpoint}" 2>&1)
    fi
    w "    → ${result:0:300}"
    w ""
done

#--- 4. 与平台连通性 ---
section "4. 与平台服务器连通性"
w "从本机测试平台端口5001（如果知道平台IP可手动测试）:"
w "  示例: curl http://<平台IP>:5001/api/health"

#--- 5. 日志 ---
section "5. 最近日志 (logs/agent.log 最后50行)"
if [ -f "logs/agent.log" ]; then
    tail -50 logs/agent.log >> "$REPORT" 2>&1
else
    w "logs/agent.log 不存在"
fi

#--- 6. 文件完整性 ---
section "6. 文件完整性"
for f in agent.py deploy.sh stop.sh requirements.txt; do
    if [ -f "$f" ]; then
        size=$(stat -c%s "$f" 2>/dev/null || stat -f%z "$f" 2>/dev/null)
        w "  ✓ $f (${size}B)"
    else
        w "  ✗ $f 缺失!"
    fi
done

w "--- packages/ ---"
ls -lh packages/*.whl 2>/dev/null >> "$REPORT" || w "  (空)"
w ""
w "--- python_installer/ ---"
ls -lh python_installer/ 2>/dev/null >> "$REPORT" || w "  (空)"

#--- 完成 ---
w ""
w "========== 诊断完成 =========="
w "请将此文件发回开发人员: $REPORT"

echo ""
echo "============================================"
echo "  诊断完成！报告已保存到:"
echo "  $SCRIPT_DIR/$REPORT"
echo ""
echo "  请将此文件发回开发人员"
echo "============================================"
echo ""
