"""TaGeAI 文章预览候选的领域规则。

本模块只定义“谁可以把哪一篇预览文章交给正式发布”的纯规则，不依赖 ORM、HTTP 或
微信发布器。这样同一套失败关闭约束可在平台创建发布调用、异步 Worker 和单元测试中
复用，避免某一层遗漏租户或账号校验后把文章投递到错误的公众号。
"""

from datetime import datetime, timezone
from typing import Protocol


class PublishCandidateError(ValueError):
    """预览候选无法用于正式发布时抛出的稳定领域错误。

    调用方应把该异常转换为不可重试的业务拒绝，而不是重新生成或改投另一篇文章；
    因为候选失败意味着用户确认的文章版本已经不再是一个可安全执行的副作用。
    """


class PublishCandidate(Protocol):
    """正式发布前必须冻结并验证的候选最小事实。

    协议而非 ORM 类让规则可直接在内存测试对象与数据库实体上执行。字段只包含授权
    边界需要的信息，正文和公众号凭据始终留在其各自的持久化模型中。
    """

    candidate_id: str
    tenant_id: int
    target_account_ref: str
    account_id: int
    status: str
    expires_at: datetime
    publish_invocation_id: str | None


def claim_publish_candidate(
    candidate: PublishCandidate,
    *,
    tenant_id: int,
    target_account_ref: str,
    publish_invocation_id: str,
    now: datetime,
) -> None:
    """原子占用一个已预览的候选，禁止跨边界和重复发布。

    本函数假定调用方已经在同一数据库事务中锁住候选所属记录；这里不尝试自行加锁，
    以免纯领域模块反向依赖 ORM。成功后标记为 ``RESERVED``，该状态一经写入就不能由
    另一个发布调用重新占用，即使后续微信返回失败也必须通过人工新建预览再次确认。
    """

    if candidate.tenant_id != tenant_id:
        raise PublishCandidateError("发布候选不属于当前租户")
    if candidate.target_account_ref != target_account_ref:
        raise PublishCandidateError("发布候选目标公众号不匹配")
    if _normalize_utc_datetime(candidate.expires_at) <= _normalize_utc_datetime(now):
        raise PublishCandidateError("发布候选已过期，请重新生成文章预览")
    if candidate.status != "READY":
        raise PublishCandidateError("发布候选已被使用或正在发布")
    if not publish_invocation_id:
        raise PublishCandidateError("发布调用标识不能为空")

    candidate.status = "RESERVED"
    candidate.publish_invocation_id = publish_invocation_id


def _normalize_utc_datetime(value: datetime) -> datetime:
    """将候选期限和当前时间归一化为可比较的 UTC 时间。

    MySQL 的 DATETIME 默认不保存时区，ORM 读回的候选过期时间会是无时区对象；
    服务层则使用带 UTC 时区的当前时间。发布候选在创建时已按 UTC 写入，因此无时区
    值必须被解释为 UTC，而不是按机器本地时区重新换算。这样既避免合法候选因类型
    比较异常中断，也保留原有的过期失败关闭约束。
    """

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
