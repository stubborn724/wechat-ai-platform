-- TaGeAI 服务间 Integration Invocation 持久化表
-- 运行方式：mysql -u <user> -p <database> < scripts/migrate_tageai_integration.sql
-- 说明：表只关联平台真实 ContentJob，不保存文章正文、公众号令牌或 HMAC 密钥。

CREATE TABLE IF NOT EXISTS tageai_integration_invocations (
    id BIGINT NOT NULL AUTO_INCREMENT,
    invocation_id VARCHAR(128) NOT NULL,
    tenant_id INT NOT NULL,
    tenant_binding_id VARCHAR(128) NOT NULL,
    content_job_id INT NOT NULL,
    operation VARCHAR(32) NOT NULL,
    delivery_mode VARCHAR(32) NOT NULL,
    target_account_ref VARCHAR(255) NULL,
    execution_id VARCHAR(128) NOT NULL,
    input_data JSON NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    request_hash CHAR(64) NOT NULL,
    external_job_id VARCHAR(128) NOT NULL,
    status VARCHAR(64) NOT NULL DEFAULT 'ACCEPTED',
    phase VARCHAR(64) NOT NULL DEFAULT 'QUEUED',
    progress INT NOT NULL DEFAULT 0,
    result_data JSON NULL,
    error_code VARCHAR(64) NULL,
    error_message TEXT NULL,
    retryable BOOLEAN NOT NULL DEFAULT FALSE,
    callback_event_ids JSON NOT NULL,
    started_at DATETIME NULL,
    finished_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tageai_invocation_id (invocation_id),
    UNIQUE KEY uq_tageai_invocation_tenant_idempotency (tenant_id, idempotency_key),
    UNIQUE KEY uq_tageai_content_job (content_job_id),
    UNIQUE KEY uq_tageai_external_job (external_job_id),
    KEY ix_tageai_invocation_tenant_status (tenant_id, status),
    KEY ix_tageai_invocation_execution_id (execution_id),
    CONSTRAINT fk_tageai_invocation_tenant
        FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    CONSTRAINT fk_tageai_invocation_content_job
        FOREIGN KEY (content_job_id) REFERENCES content_jobs(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='TaGeAI Gateway 服务间调用与真实 ContentJob 的持久化关联';

-- 回调事件使用独立事实表做数据库级去重，不能只依赖 Invocation 行上的 JSON 数组；后者
-- 有长度上限，在高频重试或服务重启后无法可靠阻止同一 eventId 重放。
CREATE TABLE IF NOT EXISTS tageai_integration_callback_events (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id INT NOT NULL,
    invocation_id BIGINT NOT NULL,
    event_id VARCHAR(128) NOT NULL,
    payload_hash CHAR(64) NOT NULL,
    received_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tageai_callback_event_id (event_id),
    UNIQUE KEY uq_tageai_callback_invocation_event (invocation_id, event_id),
    KEY ix_tageai_callback_event_tenant_received (tenant_id, received_at),
    CONSTRAINT fk_tageai_callback_event_tenant
        FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    CONSTRAINT fk_tageai_callback_event_invocation
        FOREIGN KEY (invocation_id) REFERENCES tageai_integration_invocations(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='TaGeAI Integration 回调事件去重与审计事实';

-- 可靠出站回调采用 transactional outbox。状态先写库、后由 Worker 签名发送，网络故障不会
-- 丢失完成/失败/取消事件，重复投递保持相同 event_id。
CREATE TABLE IF NOT EXISTS tageai_integration_callback_outbox (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id INT NOT NULL,
    invocation_id BIGINT NOT NULL,
    event_id VARCHAR(128) NOT NULL,
    snapshot_hash CHAR(64) NOT NULL,
    payload JSON NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    attempt_count INT NOT NULL DEFAULT 0,
    next_attempt_at DATETIME NULL,
    last_error TEXT NULL,
    delivered_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tageai_callback_outbox_event_id (event_id),
    UNIQUE KEY uq_tageai_callback_outbox_snapshot (invocation_id, snapshot_hash),
    KEY ix_tageai_callback_outbox_due (status, next_attempt_at),
    CONSTRAINT fk_tageai_callback_outbox_tenant
        FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    CONSTRAINT fk_tageai_callback_outbox_invocation
        FOREIGN KEY (invocation_id) REFERENCES tageai_integration_invocations(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='TaGeAI Integration 状态回调可靠投递队列';
