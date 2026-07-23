-- ============================================================================
-- 西藏电网智能运维平台 — MySQL Schema v5.36
-- 数据库：gmdmxzdjx (客户 MySQL 8.0.34, <内网IP>:3306)
-- 表前缀：xzyw_ (西藏运维，避免和客户其他表冲突)
-- ============================================================================

-- 1. 服务器注册表（agent/real/virtual 三合一）
CREATE TABLE IF NOT EXISTS `xzyw_servers` (
    `server_id`   VARCHAR(128) NOT NULL COMMENT '服务器唯一标识',
    `server_name` VARCHAR(255) DEFAULT NULL,
    `ip`          VARCHAR(64)  DEFAULT NULL,
    `port`        INT          DEFAULT NULL,
    `type`        VARCHAR(32)  DEFAULT 'agent' COMMENT 'agent/real/virtual/monitor',
    `system`      VARCHAR(128) DEFAULT 'default' COMMENT '业务系统分组',
    `token`       VARCHAR(255) DEFAULT NULL,
    `ssh_user`    VARCHAR(64)  DEFAULT NULL,
    `ssh_port`    INT          DEFAULT NULL,
    `status`      VARCHAR(32)  DEFAULT 'unknown',
    `last_check`  DATETIME     DEFAULT NULL,
    `extra`       JSON         DEFAULT NULL COMMENT '其他字段（token、监控端口等）',
    `created_at`  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    `updated_at`  DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`server_id`),
    KEY `idx_type` (`type`),
    KEY `idx_system` (`system`),
    KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='服务器注册表';

-- 2. 监控指标时序数据
CREATE TABLE IF NOT EXISTS `xzyw_metrics_history` (
    `id`          BIGINT       NOT NULL AUTO_INCREMENT,
    `server_id`   VARCHAR(128) NOT NULL,
    `timestamp`   DATETIME     NOT NULL,
    `cpu_usage`   DECIMAL(5,2) DEFAULT NULL,
    `mem_percent` DECIMAL(5,2) DEFAULT NULL,
    `disk_percent` DECIMAL(5,2) DEFAULT NULL,
    `io_util`     DECIMAL(5,2) DEFAULT NULL,
    `connections` INT          DEFAULT NULL,
    `raw_data`    JSON         DEFAULT NULL COMMENT '完整原始指标 JSON',
    PRIMARY KEY (`id`),
    KEY `idx_server_time` (`server_id`, `timestamp`),
    KEY `idx_timestamp` (`timestamp`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='监控指标历史';

-- 3. 对话会话
CREATE TABLE IF NOT EXISTS `xzyw_chat_sessions` (
    `session_id`  VARCHAR(64)  NOT NULL,
    `title`       VARCHAR(255) DEFAULT NULL,
    `created_at`  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    `updated_at`  DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`session_id`),
    KEY `idx_updated` (`updated_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='对话会话';

-- 4. 对话消息
CREATE TABLE IF NOT EXISTS `xzyw_chat_messages` (
    `id`         BIGINT       NOT NULL AUTO_INCREMENT,
    `session_id` VARCHAR(64)  NOT NULL,
    `role`       VARCHAR(32)  NOT NULL COMMENT 'user/assistant/system',
    `content`    MEDIUMTEXT   NOT NULL,
    `model`      VARCHAR(128) DEFAULT NULL,
    `created_at` DATETIME     DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_session_time` (`session_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='对话消息';

-- 5. 运维报告
CREATE TABLE IF NOT EXISTS `xzyw_reports` (
    `report_id`   VARCHAR(64)  NOT NULL,
    `title`       VARCHAR(255) DEFAULT NULL,
    `type`        VARCHAR(32)  DEFAULT NULL COMMENT 'daily/fault/repair/chat',
    `server_id`   VARCHAR(128) DEFAULT NULL,
    `file_path`   VARCHAR(512) DEFAULT NULL COMMENT 'Markdown 文件路径',
    `content`     MEDIUMTEXT   DEFAULT NULL COMMENT '完整报告内容（可选）',
    `summary`     TEXT         DEFAULT NULL,
    `created_at`  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`report_id`),
    KEY `idx_type` (`type`),
    KEY `idx_created` (`created_at`),
    KEY `idx_server` (`server_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='运维报告元数据';

-- 6. 故障记录
CREATE TABLE IF NOT EXISTS `xzyw_fault_records` (
    `id`           BIGINT       NOT NULL AUTO_INCREMENT,
    `server_id`    VARCHAR(128) DEFAULT NULL,
    `fault_type`   VARCHAR(64)  DEFAULT NULL COMMENT 'cpu_high/mem_high/disk_full 等',
    `severity`     VARCHAR(32)  DEFAULT NULL COMMENT 'info/warning/critical',
    `description`  TEXT         DEFAULT NULL,
    `detected_at`  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    `resolved_at`  DATETIME     DEFAULT NULL,
    `resolution`   TEXT         DEFAULT NULL,
    `auto_repaired` TINYINT(1)  DEFAULT 0,
    PRIMARY KEY (`id`),
    KEY `idx_server_time` (`server_id`, `detected_at`),
    KEY `idx_type` (`fault_type`),
    KEY `idx_severity` (`severity`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='故障记录';

-- 7. 修复历史
CREATE TABLE IF NOT EXISTS `xzyw_repair_records` (
    `id`          BIGINT       NOT NULL AUTO_INCREMENT,
    `server_id`   VARCHAR(128) DEFAULT NULL,
    `action`      VARCHAR(64)  DEFAULT NULL COMMENT 'clear_cache/kill_zombie 等',
    `params`      JSON         DEFAULT NULL,
    `status`      VARCHAR(32)  DEFAULT NULL COMMENT 'success/failed/pending',
    `result`      TEXT         DEFAULT NULL,
    `triggered_by` VARCHAR(64) DEFAULT NULL COMMENT 'manual/auto/llm',
    `executed_at` DATETIME     DEFAULT CURRENT_TIMESTAMP,
    `duration_ms` INT          DEFAULT NULL,
    PRIMARY KEY (`id`),
    KEY `idx_server_time` (`server_id`, `executed_at`),
    KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='修复历史';

-- 8. 操作审计日志
CREATE TABLE IF NOT EXISTS `xzyw_operation_log` (
    `id`         BIGINT       NOT NULL AUTO_INCREMENT,
    `operation`  VARCHAR(64)  NOT NULL,
    `target`     VARCHAR(255) DEFAULT NULL,
    `details`    JSON         DEFAULT NULL,
    `user`       VARCHAR(64)  DEFAULT 'system',
    `status`     VARCHAR(32)  DEFAULT 'success',
    `created_at` DATETIME     DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_op_time` (`operation`, `created_at`),
    KEY `idx_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='操作审计日志';

-- 9. 知识库（故障模式 + 解决方案 + 历史案例）
CREATE TABLE IF NOT EXISTS `xzyw_knowledge_base` (
    `id`          VARCHAR(64)  NOT NULL,
    `category`    VARCHAR(32)  NOT NULL COMMENT 'pattern/solution/case',
    `name`        VARCHAR(255) DEFAULT NULL,
    `content`     JSON         DEFAULT NULL COMMENT '完整条目 JSON',
    `tags`        VARCHAR(512) DEFAULT NULL,
    `updated_at`  DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`, `category`),
    KEY `idx_category` (`category`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识库条目';
