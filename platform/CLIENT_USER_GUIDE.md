# 西藏电网智能运维平台 — 客户使用手册（v5.43）

> 本文档面向**西藏电网运维工程师**，覆盖部署、日常使用、数据库表结构、常见问题排查。
> 如遇未覆盖的问题，请运行 `./diagnose.sh` 把生成的 `.html` 报告发回研发。

---

## 一、部署与升级

### 1.1 首次部署

**平台端（1 台，IP `<内网IP>`）**

```bash
# 1) 解包
cd /root && tar -xzf xizang-offline-7B-v5.43-7B.tar.gz && cd xizang-offline-7B

# 2) 保存数据库密码到用户家目录（deploy.sh 会自动读取，免交互）
echo -n '<数据库密码>' > ~/.xizang_db_password
chmod 600 ~/.xizang_db_password

# 3) 一键部署（自动装 Miniconda、装依赖、建表、启服务）
./deploy.sh
```

**Agent 端（4 台：133/134/135/136）**

```bash
cd /root && tar -xzf xizang-agent-7B-v5.43-7B.tar.gz && cd xizang-agent-7B
./deploy.sh
```

### 1.2 升级（已有旧版本）

```bash
# 平台端
cd /root/xizang-offline-7B && ./stop.sh          # 先停旧服务
cd /root && mv xizang-offline-7B xizang-offline-7B.bak_$(date +%Y%m%d)
tar -xzf xizang-offline-7B-v5.43-7B.tar.gz
cd xizang-offline-7B && ./deploy.sh               # 旧的 miniconda3 会被自动复用

# Agent 端同理
```

### 1.3 部署成功的判定标志

部署脚本末尾应看到：

```
[DB] MySQL 连接正常: <内网IP>:3306/gmdmxzdjx user=xz_gmdmxzdjx
[Schema] 初始化完成 9/9 个语句
✓ MySQL 建表已完成 (9/9)
✓ 服务启动成功 (PID: xxxxx)
```

然后浏览器打开 http://<内网IP>:5001/ 应能看到登录页。

---

## 二、日常使用

### 2.1 Web 界面访问

- **地址**：http://<内网IP>:5001/
- **端口**：平台 5001，Agent 8089

### 2.2 监控图表使用（v5.43 重点优化）

1. 左上角**下拉框**可多选服务器：
   - 点 "✅ 全部服务器" → 一键全选所有 agent
   - 点 "🚫 清空选择" → 取消所有
   - 点系统名（人工智能平台/默认分组）→ 全选该系统下所有
   - 勾选单台 checkbox → 单独加入/移除
2. 勾选后**右上角出现蓝色 toast**：`⏳ 正在加载 N 台服务器监控数据...`
3. **最长 6 秒**内 5 张图（CPU/内存/磁盘/IO/连接数）会切换为多彩折线
4. 如果有 agent 响应慢，会跳过该台继续画其他台（v5.43 超时保护）

### 2.3 运行诊断脚本

任何异常都先跑：

```bash
cd /root/xizang-offline-7B
./diagnose.sh
```

脚本在同目录生成两份文件：
- **`diagnose_report_<时间戳>.html`** ← 浏览器打开，彩色徽章展示主进程/MySQL/API/Agent 四项状态
- `diagnose_report_<时间戳>.txt` ← 纯文本版（便于邮件）

---

## 三、MySQL 数据库表结构说明

**数据库**：`gmdmxzdjx` @ `<内网IP>:3306`
**账号**：`xz_gmdmxzdjx`
**表前缀**：`xzyw_`（西藏运维，共 9 张表）

### 3.1 服务器注册表 `xzyw_servers`

**最常查的表**，存所有 agent/virtual 服务器基本信息与**业务系统分组**。

| 字段 | 含义 | 举例 |
|------|------|------|
| `server_id` | 唯一 ID（主键） | `agent-25-84-170-133` |
| `server_name` | 界面显示名 | `133` / `人工智能-133` |
| `ip` | IP 地址 | `<内网IP>` |
| `port` | Agent 端口 | `8089` |
| `type` | 类型 | `agent` / `virtual` |
| **`system`** | **业务系统分组名** ⭐ | `人工智能平台` / `default` |
| `token` | Agent 认证令牌 | `CHANGE_ME_AGENT_TOKEN` |
| `status` | 当前状态 | `running` / `offline` |
| `last_check` | 上次健康检查时间 | `2026-04-19 16:12:00` |
| `extra` | 其他字段（JSON） | `{}` |

> **新加的"人工智能平台"系统名存在这里的 `system` 列。** Navicat 双击表即可看到。

**常用 SQL**：
```sql
-- 查某个系统下的所有 agent
SELECT server_name, ip, status FROM xzyw_servers WHERE system='人工智能平台';

-- 统计每个系统的服务器数
SELECT system, COUNT(*) FROM xzyw_servers GROUP BY system;
```

### 3.2 监控指标历史 `xzyw_metrics_history`

每次 agent 采样都写一行（目前每 5 秒一次）。

| 字段 | 含义 |
|------|------|
| `id` | 自增主键 |
| `server_id` | 关联到 `xzyw_servers.server_id` |
| `timestamp` | 采样时刻 |
| `cpu_usage` / `mem_percent` / `disk_percent` / `io_util` | 四大指标百分比 |
| `connections` | 连接数 |
| `raw_data` | 完整原始 JSON |

> 长期运行数据量会很大，建议每月清理一次 90 天前的记录：
> ```sql
> DELETE FROM xzyw_metrics_history WHERE timestamp < DATE_SUB(NOW(), INTERVAL 90 DAY);
> ```

### 3.3 其他表速查

| 表名 | 用途 |
|------|------|
| `xzyw_chat_sessions` | 用户与 AI 的对话会话 |
| `xzyw_chat_messages` | 具体每条对话消息（role: user/assistant） |
| `xzyw_reports` | 生成的运维报告元数据 |
| `xzyw_fault_records` | 检测到的故障记录 |
| `xzyw_repair_records` | 修复执行历史 |
| `xzyw_operation_log` | 操作审计日志 |
| `xzyw_knowledge_base` | 故障模式 / 解决方案 / 历史案例知识库 |

---

## 四、常见问题排查（FAQ）

### Q1. 浏览器打开 http://<内网IP>:5001/ 打不开？

```bash
# 在平台机器上运行
cd /root/xizang-offline-7B
./diagnose.sh       # 看 html 报告里"主进程"徽章
# 若显示"已停止"：
./deploy.sh         # 重新启动
```

### Q2. 勾选 agent 后监控图表长时间"加载中"？

**v5.43 已修**：fetch 最多等 6 秒，超时跳过慢 agent 继续画其他。如果仍然卡：

```bash
# 检查各 Agent 健康状态
curl http://<内网IP>:8089/health
curl http://<内网IP>:8089/health
curl http://<内网IP>:8089/health
curl http://<内网IP>:8089/health

# 任何一台不通 → ssh 到该机器 → ./stop.sh && ./deploy.sh
```

### Q3. 数据库还是没表？

**v5.43 会自动兜底**，但如果仍无表：

```bash
# 1) 先确认密码文件存在
cat ~/.xizang_db_password     # 应输出 <数据库密码>（无换行）

# 2) 手工跑建表脚本（幂等，多跑无害）
cd /root/xizang-offline-7B
python init_mysql_schema.py
# 成功会输出：[Init] ✓ 已创建 xzyw_* 表 9 张: xzyw_servers, ...

# 3) 如果报连接失败，用命令行直连验证账号
mysql -h <内网IP> -P 3306 -u xz_gmdmxzdjx -p'<数据库密码>' gmdmxzdjx -e "SHOW TABLES;"
```

> ⚠️ 密码含 `#`，`export DB_PASSWORD=<数据库密码>` 会被 bash 截断！**必须用单引号**：`export DB_PASSWORD='<数据库密码>'`。

### Q4. 新加的"人工智能平台"系统分组在哪？

存在 `xzyw_servers` 表的 `system` 列。用 Navicat：
1. 左侧树双击 `gmdmxzdjx` → `表` → `xzyw_servers`
2. 右侧滚动横向，看 `system` 列
3. 值为 `人工智能平台` 即你新加的分组

### Q5. Agent 加了新机器但界面不出现？

```bash
# 平台机器执行
cd /root/xizang-offline-7B
curl http://localhost:5001/api/agent/servers | python -m json.tool | head -30
# 确认新机器在 list 里。若没有，Agent 端可能没注册成功 → 检查 Agent 日志：
# ssh 到 Agent 机器：
tail -50 /root/xizang-agent-7B/logs/agent.log
```

### Q6. 大模型 API 没响应 / 聊天没回复？

诊断报告 "大模型 API" 徽章若不是 `HTTP 200`：

```bash
# 平台机器上直接 curl 内网网关
curl -X POST "http://<内网IP>:80/xlm-gateway-pscr-c/sfm-api-gateway/gateway/compatible-mode/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{"model":"qwen2-vl-7b-instruct","messages":[{"role":"user","content":"你好"}],"max_tokens":20}'
# 若 curl 也失败 → 大模型网关侧问题，联系网关管理员
```

### Q7. 日志在哪？

| 服务 | 日志路径 |
|------|----------|
| 平台 | `/root/xizang-offline-7B/logs/app.log` |
| Agent | `/root/xizang-agent-7B/logs/agent.log` |

实时跟踪：`tail -f /root/xizang-offline-7B/logs/app.log`

### Q8. 如何停止 / 重启服务？

```bash
cd /root/xizang-offline-7B
./stop.sh            # 停止
./deploy.sh          # 启动（会自动跳过已装依赖）
```

### Q9. 服务重启后数据会丢吗？

**不会**。重要数据都入 MySQL（`gmdmxzdjx` 库下 9 张 `xzyw_*` 表）；本地 JSON 文件是冗余副本。

### Q10. 升级会影响数据库数据吗？

**不会**。`deploy.sh` 里建表用的是 `CREATE TABLE IF NOT EXISTS`，对已存在的表不做 DROP 或 TRUNCATE。升级前如果不放心可 Navicat 右键 → 备份。

---

## 五、联系研发

发 **诊断报告 `.html`** + 问题截图到研发邮箱即可，`.html` 已包含定位所需 90% 信息。

**版本**：v5.43-7B
**最后更新**：2026-04-19
