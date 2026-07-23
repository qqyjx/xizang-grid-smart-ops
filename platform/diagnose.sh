#!/bin/bash
#===============================================================================
# 西藏电网智能运维平台 - 一键诊断脚本
# 用法: chmod +x diagnose.sh && ./diagnose.sh
# 输出:
#   diagnose_report_<时间戳>.html  ← 浏览器打开查看（v5.39 新增，推荐）
#   diagnose_report_<时间戳>.txt   ← 纯文本版（同内容，方便发邮件 / 命令行阅读）
# 两个文件都会生成，开发人员发回任一即可。
#===============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
REPORT="diagnose_report_${TIMESTAMP}.txt"

# 写入函数
w() { echo "$@" >> "$REPORT"; }
section() { w ""; w "========== $1 =========="; }
cmd() { w ">>> $1"; eval "$1" >> "$REPORT" 2>&1; w ""; }

# 清空
> "$REPORT"

w "西藏电网智能运维平台 - 诊断报告"
w "生成时间: $(date '+%Y-%m-%d %H:%M:%S')"
w "服务器: $(hostname)"
w "IP: $(hostname -I 2>/dev/null | awk '{print $1}')"

#--- 1. 系统环境 ---
section "1. 系统环境"
cmd "uname -a"
cmd "cat /etc/os-release 2>/dev/null | head -5"
cmd "free -h"
cmd "df -h / /tmp 2>/dev/null"

#--- 2. Python 环境 ---
section "2. Python 环境"
CONDA_DIR="$SCRIPT_DIR/miniconda3"
if [ -f "$CONDA_DIR/bin/python" ]; then
    PYTHON="$CONDA_DIR/bin/python"
    PIP="$CONDA_DIR/bin/pip"
    w "Python来源: 包内Miniconda ($CONDA_DIR)"
else
    PYTHON=$(command -v python3 || command -v python)
    PIP="$PYTHON -m pip"
    w "Python来源: 系统 ($PYTHON)"
fi
cmd "$PYTHON --version"
cmd "$PIP list 2>/dev/null"

w "--- 关键包检查 ---"
for pkg in flask requests flask_cors psutil; do
    result=$($PYTHON -c "import $pkg; print(f'$pkg: OK ({$pkg.__version__})')" 2>&1)
    w "  $result"
done

#--- 3. 配置检查 ---
section "3. 配置检查"
w "--- config.py 关键值 ---"
$PYTHON -c "
import sys; sys.path.insert(0, '.')
from config import LLMConfig, ServerConfig
print(f'MODEL_TYPE: {LLMConfig.MODEL_TYPE}')
print(f'MULTIMODAL_ENABLED: {LLMConfig.MULTIMODAL_ENABLED}')
print(f'QWEN_API_BASE: {LLMConfig.QWEN_API_BASE}')
print(f'QWEN_API_KEY: {LLMConfig.QWEN_API_KEY[:8]}...' if LLMConfig.QWEN_API_KEY else 'QWEN_API_KEY: 空!')
print(f'QWEN_MODEL: {LLMConfig.QWEN_MODEL}')
print(f'PORT: {ServerConfig.PORT}')
" >> "$REPORT" 2>&1

#--- 4. 环境变量 ---
section "4. 环境变量"
for var in MODEL_TYPE LLM_PROVIDER QWEN_API_KEY QWEN_API_BASE QWEN_MODEL FLASK_ENV http_proxy https_proxy HTTP_PROXY HTTPS_PROXY; do
    val=$(eval echo \$$var)
    if [ -n "$val" ]; then
        # 隐藏完整密钥
        if [[ "$var" == *KEY* || "$var" == *TOKEN* ]]; then
            w "  $var=${val:0:8}..."
        else
            w "  $var=$val"
        fi
    else
        w "  $var=(未设置)"
    fi
done

#--- 5. LLM 模块加载测试 ---
section "5. LLM模块加载测试"
$PYTHON -c "
import sys; sys.path.insert(0, '.')
try:
    from models.llm_models import LLMClient
    print('LLM模块: 加载成功')
    client = LLMClient()
    print(f'  api_base: {client.api_base}')
    print(f'  model: {client.model}')
    print(f'  api_key: {client.api_key[:8]}...' if client.api_key else '  api_key: 空!')
except Exception as e:
    print(f'LLM模块: 加载失败 → {e}')
    import traceback
    traceback.print_exc()
" >> "$REPORT" 2>&1

#--- 6. API 连通性测试 ---
section "6. API连通性测试"
# 从config读取API地址
API_BASE=$($PYTHON -c "import sys; sys.path.insert(0,'.'); from config import LLMConfig; print(LLMConfig.QWEN_API_BASE)" 2>/dev/null)
API_KEY=$($PYTHON -c "import sys; sys.path.insert(0,'.'); from config import LLMConfig; print(LLMConfig.QWEN_API_KEY)" 2>/dev/null)
MODEL=$($PYTHON -c "import sys; sys.path.insert(0,'.'); from config import LLMConfig; print(LLMConfig.QWEN_MODEL)" 2>/dev/null)

if [ -n "$API_BASE" ] && [ -n "$API_KEY" ]; then
    w "测试: curl ${API_BASE}/chat/completions"
    CURL_RESULT=$(curl -s -o /tmp/api_test.json -w "HTTP_CODE:%{http_code} TIME:%{time_total}s" \
        -X POST "${API_BASE}/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${API_KEY}" \
        -d "{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"你好\"}],\"max_tokens\":20}" \
        --connect-timeout 10 --max-time 30 2>&1)
    w "  结果: $CURL_RESULT"
    w "  响应内容(前500字):"
    head -c 500 /tmp/api_test.json >> "$REPORT" 2>/dev/null
    w ""
    rm -f /tmp/api_test.json
else
    w "跳过: API地址或Key为空"
fi

#--- 7. 服务状态 ---
section "7. 服务状态"
cmd "netstat -tlnp 2>/dev/null | grep -E '5001|8089' || ss -tlnp | grep -E '5001|8089'"

if [ -f ".app.pid" ]; then
    PID=$(cat .app.pid)
    w "PID文件: .app.pid → $PID"
    if kill -0 "$PID" 2>/dev/null; then
        w "进程状态: 运行中"
        cmd "ps -p $PID -o pid,ppid,user,%cpu,%mem,etime,cmd"
    else
        w "进程状态: 已停止（PID文件过期）"
    fi
else
    w "PID文件: 不存在"
fi

# 用curl测试本地服务
w "--- 本地API测试 ---"
for endpoint in "/api/health" "/api/model/config" "/api/model/info"; do
    w "  GET http://localhost:5001${endpoint}"
    result=$(curl -s --connect-timeout 3 --max-time 5 "http://localhost:5001${endpoint}" 2>&1)
    w "    → ${result:0:200}"
    w ""
done

# 测试chat
w "  POST /api/chat"
chat_result=$(curl -s --connect-timeout 3 --max-time 30 \
    -X POST "http://localhost:5001/api/chat" \
    -H "Content-Type: application/json" \
    -d '{"message":"你好"}' 2>&1)
w "    → ${chat_result:0:300}"
w ""

#--- 8. 知识库 ---
section "8. 知识库检查"
$PYTHON -c "
import sys; sys.path.insert(0, '.')
from services.knowledge_base import KnowledgeBase
kb = KnowledgeBase()
patterns = kb.get_fault_patterns()
solutions = kb.get_solutions()
print(f'故障模式: {len(patterns)} 条')
print(f'处置方案: {len(solutions)} 条')
# 测试搜索
result = kb.search('CPU')
print(f'搜索测试(CPU): {result[\"total_results\"]} 条结果')
for r in result['results'][:2]:
    print(f'  → type={r[\"type\"]}, name={r[\"data\"].get(\"name\",\"?\")}, score={r[\"score\"]}')
" >> "$REPORT" 2>&1

#--- 9. Agent 连通性 ---
section "9. Agent服务器连通性"
if [ -f "data/agent_servers.json" ]; then
    $PYTHON -c "
import json
with open('data/agent_servers.json') as f:
    servers = json.load(f)
if isinstance(servers, list):
    print(f'已注册Agent: {len(servers)} 台')
    for s in servers:
        ip = s.get('ip', '?')
        port = s.get('port', 8089)
        print(f'  {s.get(\"name\",\"?\")} → {ip}:{port}')
elif isinstance(servers, dict):
    items = servers.get('servers', [])
    print(f'已注册Agent: {len(items)} 台')
    for s in items:
        ip = s.get('ip', '?')
        port = s.get('port', 8089)
        print(f'  {s.get(\"name\",\"?\")} → {ip}:{port}')
" >> "$REPORT" 2>&1

    # ping每个agent
    $PYTHON -c "
import json, subprocess
with open('data/agent_servers.json') as f:
    servers = json.load(f)
items = servers if isinstance(servers, list) else servers.get('servers', [])
for s in items[:10]:
    ip = s.get('ip', '')
    port = s.get('port', 8089)
    if ip:
        import urllib.request
        try:
            req = urllib.request.urlopen(f'http://{ip}:{port}/health', timeout=5)
            print(f'  {ip}:{port}/health → {req.status} {req.read().decode()[:100]}')
        except Exception as e:
            print(f'  {ip}:{port}/health → 失败: {e}')
" >> "$REPORT" 2>&1
else
    w "data/agent_servers.json 不存在"
fi

#--- 10. 日志尾部 ---
section "10. 最近日志 (logs/app.log 最后50行)"
if [ -f "logs/app.log" ]; then
    tail -50 logs/app.log >> "$REPORT" 2>&1
else
    w "logs/app.log 不存在"
fi

#--- 11. 文件完整性 ---
section "11. 文件完整性"
w "--- 关键文件 ---"
for f in app.py config.py index.html deploy.sh stop.sh \
         models/__init__.py models/llm_models.py models/ml_models.py models/dl_models.py \
         services/knowledge_base.py services/agent_manager.py services/auto_repair.py \
         knowledge_base/fault_patterns.json knowledge_base/solutions.json knowledge_base/historical_cases.json \
         static/js/echarts.min.js static/css/bootstrap.min.css; do
    if [ -f "$f" ]; then
        size=$(stat -c%s "$f" 2>/dev/null || stat -f%z "$f" 2>/dev/null)
        w "  ✓ $f (${size}B)"
    else
        w "  ✗ $f 缺失!"
    fi
done

#--- 12. 离线包 ---
section "12. 离线包检查"
w "--- python_packages/ ---"
ls -lh python_packages/*.whl 2>/dev/null >> "$REPORT" || w "  (空或不存在)"
w ""
w "--- python_installer/ ---"
ls -lh python_installer/ 2>/dev/null >> "$REPORT" || w "  (空或不存在)"

#--- 13. 统一诊断 API（含 system 字段分布、分组匹配情况） ---
section "13. 统一诊断 API /api/diagnose"
w "--- GET http://localhost:5001/api/diagnose ---"
diag_json=$(curl -s --connect-timeout 3 --max-time 10 "http://localhost:5001/api/diagnose" 2>&1)
if [ -n "$diag_json" ]; then
    # 尝试格式化 JSON
    echo "$diag_json" | $PYTHON -m json.tool 2>/dev/null >> "$REPORT" || echo "$diag_json" >> "$REPORT"
else
    w "  (服务未响应)"
fi
w ""

#--- 完成 ---
w ""
w "========== 诊断完成 =========="
w "请将此文件发回开发人员: $REPORT"

#===============================================================================
# v5.39: 额外生成 HTML 版报告（同内容，彩色样式、分节可折叠、浏览器直接打开）
# 客户反馈 .txt 可读性差，改为默认同时产出 .html
#===============================================================================
REPORT_HTML="${REPORT%.txt}.html"

"$PYTHON" - "$REPORT" "$REPORT_HTML" <<'PYEOF' 2>/dev/null || true
import html
import re
import sys
from datetime import datetime

txt_path, html_path = sys.argv[1], sys.argv[2]

with open(txt_path, 'r', encoding='utf-8', errors='replace') as f:
    raw = f.read()

lines = raw.splitlines()
# 头 4 行：标题 / 时间 / 服务器 / IP
header = {}
for ln in lines[:4]:
    if ':' in ln:
        k, v = ln.split(':', 1)
        header[k.strip()] = v.strip()

# 按 section 切分：行型 "========== N. xxx ==========" 作为分节标题
sections = []  # [(title, body_lines)]
current_title = '概览'
current_body = []
SEC_RE = re.compile(r'^=+\s*([^=]+?)\s*=+$')

for ln in lines[4:]:
    m = SEC_RE.match(ln.strip())
    if m:
        sections.append((current_title, current_body))
        current_title = m.group(1).strip()
        current_body = []
    else:
        current_body.append(ln)
sections.append((current_title, current_body))

# 状态摘要：尝试从 body 抓几个关键指标
summary_items = []
body_all = '\n'.join(raw.splitlines())

def extract(pattern, default='?'):
    m = re.search(pattern, body_all)
    return m.group(1).strip() if m else default

proc_state = '运行中' if '进程状态: 运行中' in body_all else ('已停止' if '已停止' in body_all else '未知')
mysql_state_match = re.search(r'\[DB\].*?(成功|失败|未启用|降级)', body_all)
mysql_state = mysql_state_match.group(1) if mysql_state_match else '未知'
api_match = re.search(r'HTTP_CODE:(\d+)', body_all)
api_state = api_match.group(1) if api_match else '未知'
agent_count = extract(r'已注册Agent:\s*(\d+)\s*台', '?')

def badge(label, value, kind):
    colors = {
        'ok':   ('#0f9d58', '#e6f4ea'),
        'warn': ('#f4b400', '#fff8e1'),
        'err':  ('#db4437', '#fdecea'),
        'info': ('#4285f4', '#e8f0fe'),
    }
    fg, bg = colors.get(kind, colors['info'])
    return (f'<div class="badge" style="background:{bg};color:{fg};'
            f'border:1px solid {fg}33">'
            f'<span class="badge-label">{html.escape(label)}</span>'
            f'<span class="badge-value">{html.escape(str(value))}</span></div>')

proc_kind = 'ok' if proc_state == '运行中' else ('err' if proc_state == '已停止' else 'warn')
mysql_kind = 'ok' if mysql_state in ('成功', '正常') else ('err' if mysql_state in ('失败',) else 'warn')
api_kind = 'ok' if api_state == '200' else ('err' if api_state.startswith(('4','5')) else 'warn')
agent_kind = 'ok' if agent_count.isdigit() and int(agent_count) > 0 else 'warn'

summary_html = (
    badge('主进程', proc_state, proc_kind)
    + badge('MySQL',  mysql_state, mysql_kind)
    + badge('大模型 API', 'HTTP ' + api_state, api_kind)
    + badge('Agent 数', agent_count, agent_kind)
)

# 高亮规则：行内关键字着色
def highlight_line(line):
    esc = html.escape(line)
    if not esc.strip():
        return '<br>'
    # 命令行 >>> 提示符
    if esc.startswith('&gt;&gt;&gt; '):
        return f'<span class="cmd">{esc}</span>'
    # 成功 / 失败 / 警告关键字
    esc = re.sub(r'(✓|OK|成功|healthy|200)',  r'<span class="ok">\1</span>',   esc)
    esc = re.sub(r'(✗|失败|error|ERROR|已停止|缺失)', r'<span class="err">\1</span>', esc)
    esc = re.sub(r'(未设置|未启用|跳过|WARNING|warning|降级)', r'<span class="warn">\1</span>', esc)
    return esc

section_html_parts = []
for idx, (title, body) in enumerate(sections):
    body_rendered = '\n'.join(highlight_line(ln) for ln in body)
    is_open = 'open' if idx < 2 else ''  # 默认展开前 2 节
    section_html_parts.append(
        f'<details {is_open}><summary>{html.escape(title)}</summary>'
        f'<pre>{body_rendered}</pre></details>'
    )

now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

template = f'''<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="UTF-8">
<title>西藏电网智能运维 - 诊断报告 {html.escape(header.get("服务器","?"))} {html.escape(now)}</title>
<style>
*{{box-sizing:border-box}}
body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
     margin:0;background:#f5f7fa;color:#202124;line-height:1.55}}
.wrap{{max-width:1100px;margin:0 auto;padding:20px}}
.hero{{background:linear-gradient(135deg,#1a73e8 0%,#0d47a1 100%);color:#fff;
      padding:20px 28px;border-radius:10px;margin-bottom:16px;
      box-shadow:0 2px 10px rgba(26,115,232,.25)}}
.hero h1{{margin:0 0 4px 0;font-size:20px;font-weight:600}}
.hero .meta{{opacity:.85;font-size:13px}}
.summary{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}}
.badge{{padding:10px 14px;border-radius:8px;font-size:13px;min-width:140px}}
.badge-label{{display:block;font-size:11px;opacity:.75;margin-bottom:2px}}
.badge-value{{font-weight:600;font-size:15px}}
details{{background:#fff;border-radius:8px;margin-bottom:10px;
        box-shadow:0 1px 3px rgba(60,64,67,.1);overflow:hidden}}
summary{{padding:12px 18px;font-weight:600;cursor:pointer;
        background:#f8f9fa;border-bottom:1px solid #e8eaed;user-select:none;
        list-style:none}}
summary::-webkit-details-marker{{display:none}}
summary::before{{content:'▸ ';color:#5f6368;display:inline-block;
                transition:transform .2s}}
details[open] summary::before{{content:'▾ '}}
pre{{margin:0;padding:14px 18px;font-family:"JetBrains Mono",Menlo,Consolas,monospace;
    font-size:12.5px;white-space:pre-wrap;word-break:break-all;
    background:#fafbfc;color:#24292e;max-height:520px;overflow:auto}}
.cmd{{color:#1a73e8;font-weight:600}}
.ok{{color:#0f9d58;font-weight:600}}
.err{{color:#db4437;font-weight:600}}
.warn{{color:#f4b400;font-weight:600}}
.foot{{color:#5f6368;font-size:12px;text-align:center;padding:18px 0 30px}}
@media print{{details{{break-inside:avoid}} details[open] pre{{max-height:none}}}}
</style>
</head><body><div class="wrap">
<div class="hero">
  <h1>西藏电网智能运维平台 — 诊断报告</h1>
  <div class="meta">服务器：{html.escape(header.get("服务器","?"))} &nbsp;•&nbsp;
       IP：{html.escape(header.get("IP","?"))} &nbsp;•&nbsp;
       生成：{html.escape(header.get("生成时间",""))}
  </div>
</div>
<div class="summary">{summary_html}</div>
{''.join(section_html_parts)}
<div class="foot">由 diagnose.sh v5.39 生成 • 若问题未解决，请将同目录下的 .html 或 .txt 文件发回开发</div>
</div></body></html>
'''

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(template)

print(f'[HTML] 已生成 {html_path}')
PYEOF

echo ""
echo "============================================"
echo "  诊断完成！共生成两份报告:"
echo "    - ${REPORT_HTML}  ← 浏览器打开（推荐）"
echo "    - ${REPORT}       ← 纯文本版"
echo ""
echo "  请将任一文件发回开发人员"
echo "============================================"
echo ""
