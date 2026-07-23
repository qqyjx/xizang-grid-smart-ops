#!/bin/bash
#===============================================================================
# Agent v5.17 及之前版本 系统包污染回滚脚本
#
# 适用场景：
#   - 服务器之前用 v5.17 或更早的 deploy.sh 部署过 Agent
#   - 那些版本会优先用系统 Python，导致 pip install 把系统原有的
#     jinja2 / Flask / requests / urllib3 / psutil 等包卸载替换为新版本
#   - 如果服务器上还有其他业务依赖这些旧版本，可能受到影响
#
# 本脚本作用：
#   1. 列出当前系统 Python 中相关包的版本号
#   2. 停止 Agent 服务
#   3. 卸载新装的所有包（pip uninstall）
#   4. 提示客户用 yum/apt 重装业务依赖的旧版本
#
# ⚠️ 重要：rollback.sh 不能"魔法恢复"原版本——pip 没有 downgrade-to-previous
#         功能。最干净的恢复方式是从备份镜像还原，或从同型号未污染的服务器复制。
#         本脚本的价值是把"环境状态"清空，让客户用系统包管理器重新装回去。
#
# v5.18 之后的部署完全不需要本脚本——新版 deploy.sh 强制用包内 Miniconda，
# 所有依赖装在 xizang-agent-7B/miniconda3/ 内，与系统 Python 完全隔离。
#===============================================================================

set -u

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo -e "${CYAN}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║   Agent v5.17 系统包污染 — 回滚脚本                            ║${NC}"
echo -e "${CYAN}║   作用：卸载之前 deploy.sh 装到系统 Python 的所有包             ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# v5.17 INSTALL_ORDER 中所有可能动到的包
POLLUTED_PACKAGES=(
    "Flask"
    "Flask-Cors"
    "Jinja2"
    "Werkzeug"
    "MarkupSafe"
    "itsdangerous"
    "click"
    "blinker"
    "psutil"
    "requests"
    "urllib3"
    "certifi"
    "charset-normalizer"
    "idna"
    "typing_extensions"
    "zipp"
    "importlib_metadata"
)

# 找系统 Python（注意：必须用系统 Python 不是 Miniconda）
SYS_PYTHON=""
if command -v python3 &>/dev/null; then
    # 排除 Miniconda 路径
    candidate=$(command -v python3)
    if [[ "$candidate" != *"$SCRIPT_DIR/miniconda3"* ]]; then
        SYS_PYTHON="$candidate"
    fi
fi
if [ -z "$SYS_PYTHON" ] && command -v python &>/dev/null; then
    candidate=$(command -v python)
    if [[ "$candidate" != *"$SCRIPT_DIR/miniconda3"* ]]; then
        SYS_PYTHON="$candidate"
    fi
fi

if [ -z "$SYS_PYTHON" ]; then
    echo -e "${YELLOW}未找到系统 Python（或系统 Python 已被 Miniconda 遮蔽）${NC}"
    echo "如果之前 v5.17 deploy.sh 没有跑过系统 Python 路径，则无需回滚。"
    exit 0
fi

echo -e "${YELLOW}系统 Python: $SYS_PYTHON${NC}"
echo -e "${YELLOW}版本: $($SYS_PYTHON --version 2>&1)${NC}"
echo ""

echo -e "${CYAN}===== 步骤 1: 列出当前系统 Python 中的相关包版本 =====${NC}"
echo "（这是 v5.17 deploy 之后的版本，请对比客户业务需要的版本）"
echo ""

found_count=0
for pkg in "${POLLUTED_PACKAGES[@]}"; do
    info=$($SYS_PYTHON -m pip show "$pkg" 2>/dev/null)
    if [ -n "$info" ]; then
        name=$(echo "$info" | grep "^Name:" | awk '{print $2}')
        ver=$(echo "$info" | grep "^Version:" | awk '{print $2}')
        loc=$(echo "$info" | grep "^Location:" | awk '{print $2}')
        printf "  ${GREEN}%-25s${NC} %-15s ${YELLOW}[%s]${NC}\n" "$name" "$ver" "$loc"
        found_count=$((found_count + 1))
    fi
done

if [ $found_count -eq 0 ]; then
    echo -e "${GREEN}未在系统 Python 中找到任何相关包，可能本机未被污染${NC}"
    exit 0
fi

echo ""
echo -e "${RED}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${RED}║                          ⚠️  警告 ⚠️                            ║${NC}"
echo -e "${RED}╠════════════════════════════════════════════════════════════════╣${NC}"
echo -e "${RED}║  卸载这些包后，依赖它们的其他业务可能也会停止工作              ║${NC}"
echo -e "${RED}║                                                                ║${NC}"
echo -e "${RED}║  建议先做的事情：                                              ║${NC}"
echo -e "${RED}║   1. 截图记下上面的包名+版本号                                  ║${NC}"
echo -e "${RED}║   2. 联系本机其他业务的负责人，确认它们对这些版本的容忍度       ║${NC}"
echo -e "${RED}║   3. 准备好用 yum/apt 重装业务需要的版本                       ║${NC}"
echo -e "${RED}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

read -p "确认要卸载这 ${found_count} 个包吗？[y/N] " confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    echo "已取消"
    exit 0
fi

echo ""
echo -e "${CYAN}===== 步骤 2: 停止 Agent 服务 =====${NC}"
if [ -f .agent.pid ]; then
    OLD_PID=$(cat .agent.pid)
    if kill -0 "$OLD_PID" 2>/dev/null; then
        kill "$OLD_PID" 2>/dev/null || true
        sleep 1
        echo -e "  ${GREEN}✓ Agent 已停止 (PID: $OLD_PID)${NC}"
    fi
    rm -f .agent.pid
else
    # 兜底：按进程名清理
    pkill -f "$SCRIPT_DIR/agent.py" 2>/dev/null || true
    echo -e "  ${YELLOW}未找到 .agent.pid，已尝试按进程名停止${NC}"
fi

echo ""
echo -e "${CYAN}===== 步骤 3: 卸载所有 v5.17 装到系统的包 =====${NC}"
for pkg in "${POLLUTED_PACKAGES[@]}"; do
    if $SYS_PYTHON -m pip show "$pkg" &>/dev/null; then
        if $SYS_PYTHON -m pip uninstall -y "$pkg" 2>/dev/null >/dev/null; then
            echo -e "  ${GREEN}✓${NC} 卸载 $pkg"
        else
            echo -e "  ${YELLOW}⚠${NC} 卸载 $pkg 失败（可能权限不足，请用 sudo 重跑）"
        fi
    fi
done

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                         ✅ 回滚完成                            ║${NC}"
echo -e "${GREEN}╠════════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║                                                                ║${NC}"
echo -e "${GREEN}║  下一步操作：                                                  ║${NC}"
echo -e "${GREEN}║                                                                ║${NC}"
echo -e "${GREEN}║  1. 用系统包管理器重装业务依赖的旧版本，例如：                  ║${NC}"
echo -e "${GREEN}║     CentOS/RHEL:  yum install python3-jinja2 python3-requests  ║${NC}"
echo -e "${GREEN}║     Ubuntu/Debian: apt install python3-jinja2 python3-requests ║${NC}"
echo -e "${GREEN}║                                                                ║${NC}"
echo -e "${GREEN}║  2. 验证业务恢复正常                                            ║${NC}"
echo -e "${GREEN}║                                                                ║${NC}"
echo -e "${GREEN}║  3. 部署 v5.18+ Agent（独立 Miniconda 环境，不再动系统）：      ║${NC}"
echo -e "${GREEN}║     ./deploy.sh                                                ║${NC}"
echo -e "${GREEN}║                                                                ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
