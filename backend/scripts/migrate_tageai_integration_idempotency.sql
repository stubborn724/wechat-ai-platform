-- TaGeAI Integration 幂等与回调事件表升级
-- 运行方式：mysql -u <user> -p <database> < scripts/migrate_tageai_integration_idempotency.sql
-- 适用于已经执行过 migrate_tageai_integration.sql 的 MySQL 实例；脚本可重复运行。

DELIMITER //

DROP PROCEDURE IF EXISTS tageai_add_column_if_missing //

CREATE PROCEDURE tageai_add_column_if_missing(
    IN target_table VARCHAR(128),
    IN target_column VARCHAR(128),
    IN column_definition TEXT
)
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = target_table
          AND column_name = target_column
    ) THEN
        SET @tageai_sql = CONCAT('ALTER TABLE `', target_table, '` ADD COLUMN `', target_column, '` ', column_definition);
        PREPARE tageai_stmt FROM @tageai_sql;
        EXECUTE tageai_stmt;
        DEALLOCATE PREPARE tageai_stmt;
    END IF;
END //

CALL tageai_add_column_if_missing('tageai_integration_invocations', 'tenant_binding_id', 'VARCHAR(128) NULL AFTER tenant_id') //
CALL tageai_add_column_if_missing('tageai_integration_invocations', 'idempotency_key', 'VARCHAR(128) NULL AFTER input_data') //
CALL tageai_add_column_if_missing('tageai_integration_invocations', 'request_hash', 'CHAR(64) NULL AFTER idempotency_key') //

-- 历史记录没有服务账号绑定与请求体指纹。用不可与新调用冲突的稳定占位值回填，保留可查询性，
-- 新的 Integration API 仍会要求真实 tenantBindingId 和 Idempotency-Key。
UPDATE tageai_integration_invocations
SET tenant_binding_id = 'legacy-unbound'
WHERE tenant_binding_id IS NULL OR tenant_binding_id = '' //

UPDATE tageai_integration_invocations
SET idempotency_key = CONCAT('legacy:', invocation_id)
WHERE idempotency_key IS NULL OR idempotency_key = '' //

UPDATE tageai_integration_invocations
SET request_hash = SHA2(CONCAT('legacy:', invocation_id, ':', tenant_id), 256)
WHERE request_hash IS NULL OR request_hash = '' //

ALTER TABLE tageai_integration_invocations
    MODIFY COLUMN tenant_binding_id VARCHAR(128) NOT NULL,
    MODIFY COLUMN idempotency_key VARCHAR(128) NOT NULL,
    MODIFY COLUMN request_hash CHAR(64) NOT NULL //

SET @tageai_index_exists = (
    SELECT COUNT(*)
    FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'tageai_integration_invocations'
      AND index_name = 'uq_tageai_invocation_tenant_idempotency'
) //
SET @tageai_sql = IF(
    @tageai_index_exists = 0,
    'ALTER TABLE tageai_integration_invocations ADD UNIQUE KEY uq_tageai_invocation_tenant_idempotency (tenant_id, idempotency_key)',
    'SELECT 1'
) //
PREPARE tageai_stmt FROM @tageai_sql //
EXECUTE tageai_stmt //
DEALLOCATE PREPARE tageai_stmt //

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
  COMMENT='TaGeAI Integration 回调事件去重与审计事实' //

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
  COMMENT='TaGeAI Integration 状态回调可靠投递队列' //

DROP PROCEDURE tageai_add_column_if_missing //

DELIMITER ;
