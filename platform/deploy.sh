#!/bin/bash
#===============================================================================
# 西藏电网智能运维平台 - 7B多模态版一键部署脚本 v5.0-7B
# 基于内网大模型网关（OpenAI兼容接口）
# 支持图片上传分析（监控截图、巡检记录、设备照片等）
# 适用于：完全离线、无Python、无Docker的空服务器
#===============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CONDA_DIR="$SCRIPT_DIR/miniconda3"
PORT=5001
VERSION="6.0-7B"

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   西藏电网智能运维平台 - 7B多模态版部署 v${VERSION}            ║${NC}"
echo -e "${BLUE}║   ✓ 7B多模态大模型  ✓ 图片分析  ✓ 智能运维                 ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

#===============================================================================
# Step 0a: v5.59 — 磁盘空间预检（Miniconda 解压+wheel 安装至少需 1GB；磁盘满时 bash INSTALLER 会静默失败）
#===============================================================================
AVAIL_KB=$(df -Pk "$SCRIPT_DIR" 2>/dev/null | tail -1 | awk '{print $4}')
if [ -n "$AVAIL_KB" ] && [ "$AVAIL_KB" -lt 1048576 ]; then
    echo -e "${RED}✗ 错误: 当前分区可用空间不足 1GB（实测 $((AVAIL_KB/1024)) MB），无法安装 Miniconda + 依赖${NC}"
    echo -e "${RED}  请清理磁盘后重试（建议 df -h 看分区使用率，常见占用 /var /tmp /opt 等）${NC}"
    exit 1
fi

#===============================================================================
# Step 0: 备份已有数据（防止换包丢失服务器配置）
#===============================================================================
BACKUP_DIR="/tmp/xizang-data-backup-$$"
if [ -d "$SCRIPT_DIR/data" ] && [ "$(ls -A "$SCRIPT_DIR/data/" 2>/dev/null | grep -v '.gitkeep')" ]; then
    echo -e "${YELLOW}→ 备份已有服务器数据...${NC}"
    mkdir -p "$BACKUP_DIR"
    cp -r "$SCRIPT_DIR/data/"* "$BACKUP_DIR/" 2>/dev/null || true
    echo -e "      ${GREEN}✓ 数据已备份到 $BACKUP_DIR${NC}"
fi

#===============================================================================
# Step 1: 系统优化与工具安装
#===============================================================================
echo -e "${CYAN}[1/6]${NC} ${YELLOW}系统环境优化...${NC}"

# 增加inotify watches限制（避免"无法监视文件更改"警告）
CURRENT_WATCHES=$(cat /proc/sys/fs/inotify/max_user_watches 2>/dev/null || echo "0")
if [ "$CURRENT_WATCHES" -lt 524288 ]; then
    echo -e "      ${YELLOW}→ 优化文件监视限制 (当前: $CURRENT_WATCHES)...${NC}"
    if [ -w /etc/sysctl.conf ]; then
        if ! grep -q "fs.inotify.max_user_watches" /etc/sysctl.conf 2>/dev/null; then
            echo "fs.inotify.max_user_watches=524288" >> /etc/sysctl.conf
        fi
        sysctl -p > /dev/null 2>&1 || true
        echo -e "      ${GREEN}✓ inotify限制已优化${NC}"
    else
        echo -e "      ${YELLOW}⚠ 跳过inotify优化（无sudo权限，不影响使用）${NC}"
    fi
else
    echo -e "      ${GREEN}✓ 系统配置正常${NC}"
fi

# 安装sshpass（用于SSH密码认证）
if ! command -v sshpass &> /dev/null; then
    echo -e "      ${YELLOW}→ 安装 sshpass 工具...${NC}"
    if [ -f "$SCRIPT_DIR/tools/sshpass_1.06-1_amd64.deb" ]; then
        if command -v dpkg &> /dev/null; then
            sudo dpkg -i "$SCRIPT_DIR/tools/sshpass_1.06-1_amd64.deb" > /dev/null 2>&1 && \
                echo -e "      ${GREEN}✓ sshpass 已安装${NC}" || \
                echo -e "      ${YELLOW}⚠ sshpass安装失败（需要sudo权限，SSH密码认证不可用）${NC}"
        else
            echo -e "      ${YELLOW}⚠ 非Debian系统，跳过sshpass安装${NC}"
        fi
    else
        echo -e "      ${YELLOW}⚠ sshpass安装包不存在，跳过${NC}"
    fi
else
    echo -e "      ${GREEN}✓ sshpass 已就绪${NC}"
fi

#===============================================================================
# Step 1: 安装 Miniconda (Python环境)
#===============================================================================
echo -e "${CYAN}[2/6]${NC} ${YELLOW}检查 Python 环境...${NC}"
if [ -f "$CONDA_DIR/bin/python" ]; then
    PY_VER=$("$CONDA_DIR/bin/python" --version 2>&1)
    echo -e "      ${GREEN}✓ Python 已就绪: $PY_VER${NC}"
else
    echo -e "      ${YELLOW}→ 正在安装 Python 环境 (首次约需30秒)...${NC}"
    
    INSTALLER="$SCRIPT_DIR/python_installer/Miniconda3-latest-Linux-x86_64.sh"
    if [ ! -f "$INSTALLER" ]; then
        echo -e "      ${RED}✗ 错误: 找不到 Miniconda 安装包${NC}"
        echo -e "      ${RED}  请确保文件存在: python_installer/Miniconda3-latest-Linux-x86_64.sh${NC}"
        exit 1
    fi
    
    bash "$INSTALLER" -b -p "$CONDA_DIR" > /dev/null 2>&1
    if [ $? -eq 0 ]; then
        PY_VER=$("$CONDA_DIR/bin/python" --version 2>&1)
        echo -e "      ${GREEN}✓ Python 安装成功: $PY_VER${NC}"
    else
        echo -e "      ${RED}✗ Python 安装失败${NC}"
        exit 1
    fi
fi

PYTHON="$CONDA_DIR/bin/python"
PIP="$CONDA_DIR/bin/pip"

#===============================================================================
# Step 2: 安装 Python 依赖包 (从本地离线包)
#===============================================================================
echo -e "${CYAN}[3/6]${NC} ${YELLOW}安装 Python 依赖包...${NC}"

PACKAGES_DIR="$SCRIPT_DIR/python_packages"
if [ ! -d "$PACKAGES_DIR" ] || [ -z "$(ls -A $PACKAGES_DIR/*.whl 2>/dev/null)" ]; then
    echo -e "      ${RED}✗ 错误: 找不到离线依赖包目录 python_packages/${NC}"
    exit 1
fi

if "$PYTHON" -c "import flask, requests, psutil, pymysql" 2>/dev/null; then
    echo -e "      ${GREEN}✓ 依赖包已安装${NC}"
else
    echo -e "      ${YELLOW}→ 从离线包安装中...${NC}"
    # v6.0 修复：用 set +e 包裹捕获 pip 返回码。否则 set -e 下 pip 一旦失败(如缺某个 wheel)
    # 会当场退出脚本，根本到不了下面的"逐个安装"兜底与诊断 —— 表现为"从离线包安装中..."后
    # 静默退出、服务起不来(客户 v5.7 漏 pymysql wheel 即此故障)。
    set +e
    "$PIP" install --no-index --find-links="$PACKAGES_DIR" \
        setuptools werkzeug markupsafe jinja2 click blinker itsdangerous \
        flask flask-cors psutil pymysql \
        certifi charset-normalizer idna urllib3 requests -q 2>/dev/null
    _pip_rc=$?
    set -e
    if [ $_pip_rc -eq 0 ]; then
        echo -e "      ${GREEN}✓ 依赖包安装成功${NC}"
    else
        echo -e "      ${RED}✗ 依赖包安装失败，尝试逐个安装...${NC}"
        for whl in "$PACKAGES_DIR"/*.whl; do
            "$PIP" install --no-index --no-deps "$whl" -q 2>/dev/null || true
        done
        if "$PYTHON" -c "import flask, requests, psutil, pymysql" 2>/dev/null; then
            echo -e "      ${GREEN}✓ 依赖包安装成功（逐个模式）${NC}"
        else
            echo -e "      ${RED}✗ 依赖包安装失败${NC}"
            echo -e "      ${RED}  已安装的包:${NC}"
            "$PIP" list 2>/dev/null | head -20
            exit 1
        fi
    fi
fi

#===============================================================================
# Step 3: 检查静态资源
#===============================================================================
echo -e "${CYAN}[4/6]${NC} ${YELLOW}检查静态资源...${NC}"

if [ -f "$SCRIPT_DIR/static/js/echarts.min.js" ] && [ -f "$SCRIPT_DIR/static/css/bootstrap.min.css" ]; then
    echo -e "      ${GREEN}✓ 前端资源已就绪${NC}"
else
    echo -e "      ${RED}✗ 警告: 前端静态资源不完整，界面可能无法正常显示${NC}"
fi

mkdir -p logs data/raw reports checkpoints operation_logs
echo -e "      ${GREEN}✓ 目录结构已创建${NC}"

# 恢复备份数据（换包时保留服务器配置）
if [ -d "$BACKUP_DIR" ] && [ "$(ls -A "$BACKUP_DIR/" 2>/dev/null)" ]; then
    echo -e "      ${YELLOW}→ 恢复服务器数据...${NC}"
    cp -r "$BACKUP_DIR/"* "$SCRIPT_DIR/data/" 2>/dev/null || true
    rm -rf "$BACKUP_DIR"
    echo -e "      ${GREEN}✓ 服务器数据已恢复（换包不丢失）${NC}"
fi

#===============================================================================
# Step 4: 停止旧服务
#===============================================================================
echo -e "${CYAN}[5/6]${NC} ${YELLOW}检查现有服务...${NC}"

PID_FILE="$SCRIPT_DIR/.app.pid"
# v6.0 修复(第二根因): 仅靠 .app.pid 停服, 在"rm -rf 旧目录重装 / PID 文件丢失"时会漏杀旧进程,
# 导致 :5001 仍被旧 python app.py 占用 → 新实例 app.run() 触发 Address already in use 立即退出
# (表现为"服务起来就停/页面打不开")。改为三重兜底: PID 文件 + 进程名 pgrep + 端口占用(lsof/fuser/ss)。
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE" 2>/dev/null)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo -e "      ${YELLOW}→ 停止旧服务 (PID: $OLD_PID)...${NC}"
        kill "$OLD_PID" 2>/dev/null || true; sleep 1; kill -9 "$OLD_PID" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
fi
# 进程名兜底(不依赖 lsof, 精简 CentOS 也可用)
APP_PIDS=$(pgrep -f "python.*app\.py" 2>/dev/null || true)
if [ -n "$APP_PIDS" ]; then
    echo -e "      ${YELLOW}→ 清理残留 app.py 进程: $APP_PIDS${NC}"
    for p in $APP_PIDS; do kill "$p" 2>/dev/null || true; done
    sleep 1
    for p in $APP_PIDS; do kill -9 "$p" 2>/dev/null || true; done
fi
# 端口兜底(lsof / fuser / ss 任一可用)
PORT_PID=""
if command -v lsof >/dev/null 2>&1; then
    PORT_PID=$(lsof -ti :$PORT 2>/dev/null | head -1)
elif command -v fuser >/dev/null 2>&1; then
    PORT_PID=$(fuser ${PORT}/tcp 2>/dev/null | tr -d ' ')
elif command -v ss >/dev/null 2>&1; then
    PORT_PID=$(ss -ltnp 2>/dev/null | grep ":$PORT " | grep -oP 'pid=\K[0-9]+' | head -1)
fi
if [ -n "$PORT_PID" ]; then
    echo -e "      ${YELLOW}→ 端口 $PORT 仍被占用 (PID: $PORT_PID), 强制释放...${NC}"
    kill "$PORT_PID" 2>/dev/null || true; sleep 1; kill -9 "$PORT_PID" 2>/dev/null || true
fi
echo -e "      ${GREEN}✓ 服务检查完成${NC}"

#===============================================================================
# Step 5: 启动服务
#===============================================================================
echo -e "${CYAN}[6/6]${NC} ${YELLOW}启动服务...${NC}"

# 清除代理设置（内网环境不需要代理，避免requests库走代理导致API超时）
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy no_proxy 2>/dev/null || true

# 7B版：默认使用LLM多模态模式
export MODEL_TYPE=${MODEL_TYPE:-llm}
export LLM_PROVIDER=${LLM_PROVIDER:-qwen}
export FLASK_ENV=production

# ==================== MySQL 持久化（v5.39 交互式密码 + 自动建表 + 兜底） ====================
# 客户环境：<内网IP>:3306/gmdmxzdjx，账号密码由现场运维提供（不入库）
# （Navicat 截图里的 "yw" 是连接名，不是数据库账号）
# 若需禁用：export DB_ENABLED=false
export DB_ENABLED=${DB_ENABLED:-true}
export DB_HOST=${DB_HOST:-<内网IP>}
export DB_PORT=${DB_PORT:-3306}
export DB_USER=${DB_USER:-xz_gmdmxzdjx}
export DB_NAME=${DB_NAME:-gmdmxzdjx}
# DB_PASSWORD：优先 env → ~/.xizang_db_password 文件（chmod 600）→ 交互式 prompt
if [ "$DB_ENABLED" = "true" ] && [ -z "$DB_PASSWORD" ] && [ -f "$HOME/.xizang_db_password" ]; then
    DB_PASSWORD=$(cat "$HOME/.xizang_db_password" | tr -d '\r\n')
    export DB_PASSWORD
fi
if [ "$DB_ENABLED" = "true" ] && [ -z "$DB_PASSWORD" ] && [ -t 0 ]; then
    echo ""
    echo -e "      ${YELLOW}→ 需要 MySQL 密码（用户 ${DB_USER}@${DB_HOST}/${DB_NAME}）${NC}"
    echo -e "      ${YELLOW}  直接回车跳过（将降级到 SQLite）；或输入密码${NC}"
    read -rsp "      DB_PASSWORD: " DB_PASSWORD
    echo ""
    if [ -n "$DB_PASSWORD" ]; then
        export DB_PASSWORD
        # 询问是否保存以便下次免输
        read -rp "      保存到 ~/.xizang_db_password 下次免输入? (y/N): " _save
        if [ "$_save" = "y" ] || [ "$_save" = "Y" ]; then
            echo -n "$DB_PASSWORD" > "$HOME/.xizang_db_password"
            chmod 600 "$HOME/.xizang_db_password"
            echo -e "      ${GREEN}✓ 已保存（chmod 600）${NC}"
        fi
    fi
fi
if [ -n "$DB_PASSWORD" ] && [ "$DB_ENABLED" = "true" ]; then
    echo -e "      数据库: ${GREEN}启用 (${DB_HOST}:${DB_PORT}/${DB_NAME})${NC}"
    echo -e "              ${CYAN}首次部署会自动建表；失败原因见 logs/app.log 中 [Schema]/[DB] 行${NC}"
elif [ "$DB_ENABLED" = "true" ]; then
    echo -e "      数据库: ${YELLOW}未设置 DB_PASSWORD，将降级到本地 SQLite${NC}"
    echo -e "              ${YELLOW}后续如需启用 MySQL：python init_mysql_schema.py（交互式建表）${NC}"
else
    echo -e "      数据库: ${YELLOW}已禁用（DB_ENABLED=false），使用本地 SQLite${NC}"
fi

echo -e "      模型模式: ${GREEN}${MODEL_TYPE}${NC} | 提供商: ${GREEN}${LLM_PROVIDER}${NC}"

# 验证 requests 库（7B大模型API必需）
if ! "$PYTHON" -c "import requests" 2>/dev/null; then
    echo -e "      ${RED}✗ requests库未安装，7B大模型API将不可用！${NC}"
    echo -e "      ${YELLOW}→ 尝试单独安装requests...${NC}"
    "$PIP" install --no-index --find-links="$PACKAGES_DIR" certifi charset-normalizer idna urllib3 requests -q 2>/dev/null
fi

nohup "$PYTHON" app.py > logs/app.log 2>&1 &
APP_PID=$!
echo $APP_PID > "$PID_FILE"

# 等待并检查服务状态
for i in {1..10}; do
    sleep 1
    if ! kill -0 $APP_PID 2>/dev/null; then
        echo -e "      ${RED}✗ 服务启动失败${NC}"
        echo -e "      ${RED}  查看错误日志: tail -50 logs/app.log${NC}"
        exit 1
    fi
    # 尝试健康检查
    if command -v curl &> /dev/null; then
        if curl -s http://localhost:$PORT/api/health > /dev/null 2>&1; then
            break
        fi
    else
        if [ $i -ge 3 ]; then
            break
        fi
    fi
done

echo -e "      ${GREEN}✓ 服务启动成功 (PID: $APP_PID)${NC}"

#===============================================================================
# v5.39: MySQL 建表兜底 —— 启动后等待 3s，检查 app.log 是否出现 Schema 初始化完成
# 若没看到 [Schema] 初始化完成 N/N，自动再跑一次 init_mysql_schema.py（幂等）
#===============================================================================
if [ "$DB_ENABLED" = "true" ] && [ -n "$DB_PASSWORD" ]; then
    sleep 3
    SCHEMA_OK_LINE=$(grep -E "\[Schema\] 初始化完成 [0-9]+/[0-9]+" logs/app.log 2>/dev/null | tail -1)
    SCHEMA_ALL_OK=$(echo "$SCHEMA_OK_LINE" | grep -oE "([0-9]+)/\1" | head -1)  # 形如 "9/9" 表示全部成功
    if [ -z "$SCHEMA_ALL_OK" ]; then
        echo -e "      ${YELLOW}→ 未检测到 Schema 初始化成功行，尝试独立建表（幂等）${NC}"
        "$PYTHON" init_mysql_schema.py 2>&1 | tail -5 || true
    else
        echo -e "      ${GREEN}✓ MySQL 建表已完成 ($SCHEMA_ALL_OK)${NC}"
    fi
fi

#===============================================================================
# 完成提示
#===============================================================================
echo ""
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -z "$IP" ] && IP="localhost"

echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    ✅ 部署成功!                              ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║                                                              ║${NC}"
echo -e "${GREEN}║  🌐 访问地址: ${CYAN}http://${IP}:${PORT}${GREEN}                         ${NC}"
echo -e "${GREEN}║                                                              ║${NC}"
echo -e "${GREEN}║  📋 常用命令:                                                ║${NC}"
echo -e "${GREEN}║     停止服务: ${CYAN}./stop.sh${GREEN}                                    ║${NC}"
echo -e "${GREEN}║     查看日志: ${CYAN}tail -f logs/app.log${GREEN}                         ║${NC}"
echo -e "${GREEN}║     重启服务: ${CYAN}./stop.sh && ./deploy.sh${GREEN}                     ║${NC}"
echo -e "${GREEN}║                                                              ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}验证命令:${NC}"
echo -e "  curl http://${IP}:${PORT}/api/health"
echo -e "  curl -X POST http://${IP}:${PORT}/api/chat -H 'Content-Type: application/json' -d '{\"message\":\"你好\"}'"
echo ""
