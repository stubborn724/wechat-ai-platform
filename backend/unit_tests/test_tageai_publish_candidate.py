"""TaGeAI 预览后正式发布候选的边界回归测试。

这些测试只验证候选令牌的纯领域规则，不连接真实微信、Gateway 或数据库。发布候选
必须是一次性、租户和账号绑定的能力凭据，不能因为桌面端重复提交或账号切换而复用。
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


def test_publish_candidate_invocation_foreign_key_matches_the_source_primary_key_type():
    """候选表外键必须与现有调用表主键保持同一 MySQL 类型。

    ``tageai_integration_invocations`` 在已部署环境中使用 BIGINT 主键；候选表若退回
    INTEGER，MySQL 会拒绝创建外键，使预览任务永远无法签发可确认的发布版本。
    """

    from sqlalchemy import BigInteger

    from app.models.mysql_models import TageAiIntegrationInvocation, TageAiPublishCandidate

    assert isinstance(TageAiIntegrationInvocation.__table__.c.id.type, BigInteger)
    assert isinstance(TageAiPublishCandidate.__table__.c.source_invocation_id.type, BigInteger)


def _candidate(**overrides):
    """构造处于可确认发布状态的最小候选，便于单独验证领域边界。"""

    values = {
        "candidate_id": "wpc_test_candidate_001",
        "tenant_id": 7,
        "target_account_ref": "my-furniture-account",
        "account_id": 103,
        "status": "READY",
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=30),
        "publish_invocation_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_claim_publish_candidate_accepts_only_the_original_tenant_and_account():
    """候选发布必须冻结到预览时选定的租户和公众号，不能跨账号转发。"""

    from app.integrations.tageai.publish_candidate import claim_publish_candidate

    candidate = _candidate()
    claim_publish_candidate(
        candidate,
        tenant_id=7,
        target_account_ref="my-furniture-account",
        publish_invocation_id="invoke-publish-1",
        now=datetime.now(timezone.utc),
    )

    assert candidate.status == "RESERVED"
    assert candidate.publish_invocation_id == "invoke-publish-1"


def test_claim_publish_candidate_normalizes_database_naive_utc_expiry_time():
    """MySQL 返回无时区 UTC 时间时，候选仍应按正确期限发布。

    当前生产库的 DATETIME 字段不会保留 ``tzinfo``，而发布请求传入的是带 UTC
    时区的当前时间。领域层需要在边界处完成归一化，既不能因类型异常阻断合法发布，
    也不能错误放行已经过期的候选。
    """

    from app.integrations.tageai.publish_candidate import claim_publish_candidate

    candidate = _candidate(
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=30)).replace(tzinfo=None),
    )

    claim_publish_candidate(
        candidate,
        tenant_id=7,
        target_account_ref="my-furniture-account",
        publish_invocation_id="invoke-publish-naive-utc",
        now=datetime.now(timezone.utc),
    )

    assert candidate.status == "RESERVED"
    assert candidate.publish_invocation_id == "invoke-publish-naive-utc"


def test_claim_publish_candidate_rejects_expired_or_reused_candidate():
    """过期或已经被占用的候选必须失败关闭，避免重复触发公众号副作用。"""

    from app.integrations.tageai.publish_candidate import PublishCandidateError, claim_publish_candidate

    with pytest.raises(PublishCandidateError, match="已过期"):
        claim_publish_candidate(
            _candidate(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)),
            tenant_id=7,
            target_account_ref="my-furniture-account",
            publish_invocation_id="invoke-publish-1",
            now=datetime.now(timezone.utc),
        )

    with pytest.raises(PublishCandidateError, match="已被使用"):
        claim_publish_candidate(
            _candidate(status="RESERVED", publish_invocation_id="invoke-publish-1"),
            tenant_id=7,
            target_account_ref="my-furniture-account",
            publish_invocation_id="invoke-publish-2",
            now=datetime.now(timezone.utc),
        )


def test_claim_publish_candidate_rejects_cross_tenant_and_cross_account_requests():
    """候选 ID 即使泄露，也不能作为跨租户或跨公众号的发布授权。"""

    from app.integrations.tageai.publish_candidate import PublishCandidateError, claim_publish_candidate

    with pytest.raises(PublishCandidateError, match="不属于当前租户"):
        claim_publish_candidate(
            _candidate(), tenant_id=8, target_account_ref="my-furniture-account",
            publish_invocation_id="invoke-publish-1", now=datetime.now(timezone.utc),
        )

    with pytest.raises(PublishCandidateError, match="目标公众号不匹配"):
        claim_publish_candidate(
            _candidate(), tenant_id=7, target_account_ref="another-account",
            publish_invocation_id="invoke-publish-1", now=datetime.now(timezone.utc),
        )
