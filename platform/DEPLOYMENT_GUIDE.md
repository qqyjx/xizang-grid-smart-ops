# 西藏电网智能运维平台 - 部署指南

## 一、部署架构

```
┌─────────────────────────────────────┐
│    平台服务器 (xizang-offline-mini)  │
│    - 监控中心                        │
│    - Web界面 (端口: 5001)           │
│    - 智能诊断引擎                    │
└──────────────┬──────────────────────┘
               │ HTTP (内网连接)
    ┌──────────┴──────────┬───────────────┐
    │                     │               │
┌───▼────┐          ┌───▼────┐     ┌───▼────┐
│Agent-01│          │Agent-02│     │Agent-N │
│ 8089   │          │ 8089   │     │ 8089   │
└────────┘          └────────┘     └────────┘
被监控的ECS服务器 (xizang-agent)
```

## 二、部署步骤

### 步骤1: 部署平台服务器

1. 上传 `xizang-offline-mini.tar.gz` 到平台服务器
2. 解压并部署:
```bash
tar -xzf xizang-offline-mini.tar.gz
cd xizang-offline-mini
chmod +x deploy.sh stop.sh
./deploy.sh
```

3. 验证部署:
```bash
# 检查服务状态
ps aux | grep app.py

# 访问Web界面
curl http://localhost:5001
```

### 步骤2: 部署Agent服务器

在每个需要监控的ECS服务器上:

1. 上传 `xizang-agent.tar.gz`
2. 解压并部署:
```bash
tar -xzf xizang-agent.tar.gz
cd xizang-agent
chmod +x deploy.sh stop.sh
./deploy.sh
```

3. 记录Agent信息:
   - IP地址: `hostname -I | awk '{print $1}'`
   - 端口: 8089 (默认)
   - Token: CHANGE_ME_AGENT_TOKEN

### 步骤3: 添加Agent服务器到平台

1. 浏览器访问: `http://<平台IP>:5001`
2. 点击"添加服务器" → 选择"Agent(推荐)"
3. 填写信息:
   - IP地址: Agent服务器的内网IP
   - 名称: 如"生产服务器-01"
   - 端口: 8089
   - Token: CHANGE_ME_AGENT_TOKEN

4. **重要**: 添加成功后:
   - 刷新浏览器页面 (F5)
   - 或点击"所有服务器"下拉菜单查看
   - 新添加的Agent会显示为 `[真实]` 标签

## 三、常见问题

### 问题1: Agent添加成功但看不到

**原因**: 浏览器缓存或页面未刷新

**解决方案**:
1. 硬刷新浏览器: `Ctrl+F5` (Windows) 或 `Cmd+Shift+R` (Mac)
2. 清除浏览器缓存
3. 重新加载页面

### 问题2: 无法连接到Agent

**检查清单**:
```bash
# 1. 确认Agent服务正在运行
ps aux | grep agent.py

# 2. 检查端口监听
netstat -tulpn | grep 8089
# 或
ss -tulpn | grep 8089

# 3. 测试网络连通性（在平台服务器上）
curl http://<Agent-IP>:8089/health

# 4. 检查防火墙规则
firewall-cmd --list-all  # CentOS/RHEL
iptables -L -n           # 通用

# 5. 开放端口（如果需要）
firewall-cmd --permanent --add-port=8089/tcp
firewall-cmd --reload
```

### 问题3: Token认证失败

**解决方案**:
1. 检查Agent配置:
```bash
cd xizang-agent
grep AGENT_TOKEN agent.py
```

2. 修改Token (如需要):
```bash
export AGENT_TOKEN="your_custom_token"
./deploy.sh
```

### 问题4: 数据文件权限问题

```bash
cd xizang-offline-mini
# 确保data目录有写权限
chmod 755 data
chmod 644 data/*.json
```

## 四、验证功能

### 1. 验证监控功能
```bash
# API测试
curl http://localhost:5001/api/all/servers | python3 -m json.tool

# 应该看到所有服务器，包括Agent
```

### 2. 验证自动修复功能
```bash
# 执行自动修复
curl -X POST http://localhost:5001/api/agent/server/<server-id>/auto-repair \
  -H "Content-Type: application/json" \
  -d '{}'
```

### 3. 验证Agent状态获取
```bash
# 获取Agent资源状态
curl http://localhost:5001/api/agent/server/<server-id>/status
```

## 五、运维命令

### 平台服务器
```bash
# 启动
./deploy.sh

# 停止
./stop.sh

# 查看日志
tail -f logs/app.log

# 重启
./stop.sh && ./deploy.sh
```

### Agent服务器
```bash
# 启动
./deploy.sh

# 停止
./stop.sh

# 查看日志
tail -f logs/agent.log

# 重启
./stop.sh && ./deploy.sh
```

## 六、端口说明

| 服务 | 端口 | 说明 |
|-----|------|------|
| 平台Web界面 | 5001 | HTTP访问，需在内网可访问 |
| Agent服务 | 8089 | HTTP API，平台服务器需能访问 |

## 七、安全建议

1. **Token安全**: 在生产环境修改默认Token
```bash
# 修改agent.py中的AGENT_TOKEN
export AGENT_TOKEN="production_secure_token_$(date +%s)"
```

2. **网络隔离**: 确保服务只在内网访问
```bash
# 检查监听地址
netstat -tulpn | grep -E "5001|8089"
# 应该看到 0.0.0.0:端口 或 内网IP:端口
```

3. **日志审计**: 定期检查操作日志
```bash
cat operation_logs/operation_history.json
cat operation_logs/auto_repair_history.json
```

## 八、技术支持

如遇问题，请提供:
1. 日志文件: `logs/app.log` 和 `logs/agent.log`
2. 系统信息: `uname -a` 和 Python版本
3. 错误截图
