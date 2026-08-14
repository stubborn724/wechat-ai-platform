"""定时任务草稿投递的受控并发基础能力。

多公众号草稿保存彼此独立，串行执行会把同一篇文章的微信网络等待线性叠加；
但并发过高容易触发微信中转站限流。本模块只管理可测试的账号筛选与线程调度，
不持有 SQLAlchemy Session，也不直接调用微信接口。数据库会话和每账号的幂等
落库仍由定时执行器在独立工作单元中完成，避免跨线程共享 Session。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Generic, Mapping, Sequence, TypeVar

from app.services.publish_domain_policy import normalize_publish_domain


DeliveryValue = TypeVar("DeliveryValue")


@dataclass(frozen=True)
class DraftDeliveryExecution(Generic[DeliveryValue]):
    """单个账号草稿工作单元的最终结果。

    异常不在子线程中直接抛给协调器，而是与账号 ID 一起返回。协调器因此可以等待
    已经启动的账号全部收敛并保留它们的数据库结果，再由调用方决定是否触发任务级重试。
    """

    account_id: int
    value: DeliveryValue | None = None
    error: BaseException | None = None


def pending_draft_delivery_account_ids(
    *,
    article_id: int,
    account_ids: Sequence[int],
    delivery_results: Mapping[str, object] | None,
    publish_domain: str,
) -> list[int]:
    """返回尚未成功保存草稿的账号，保持输入配置的稳定顺序。

    草稿保存只复用同一文章、同一账号、同一发布域的成功记录。历史记录没有域字段时
    仅能兼容默认公域草稿，私域不得误用公域结果。重复账号在任务配置层无意义，这里
    主动去重，保证并发队列不会对同一账号发送两次请求。
    """

    normalized_domain = normalize_publish_domain(publish_domain)
    recorded_results = delivery_results or {}
    pending: list[int] = []
    seen_account_ids: set[int] = set()
    for raw_account_id in account_ids:
        account_id = int(raw_account_id)
        if account_id in seen_account_ids:
            continue
        seen_account_ids.add(account_id)
        result = recorded_results.get(f"{article_id}:{account_id}")
        recorded_domain = result.get("publish_domain") if isinstance(result, Mapping) else None
        is_success = (
            isinstance(result, Mapping)
            and result.get("status") == "success"
            and result.get("mode") == "draft"
            and (
                (recorded_domain is None and normalized_domain == "public")
                or (
                    recorded_domain is not None
                    and normalize_publish_domain(str(recorded_domain)) == normalized_domain
                )
            )
        )
        if not is_success:
            pending.append(account_id)
    return pending


def execute_bounded_draft_deliveries(
    account_ids: Sequence[int],
    *,
    max_workers: int,
    deliver: Callable[[int], DeliveryValue],
) -> list[DraftDeliveryExecution[DeliveryValue]]:
    """以有限并发执行独立草稿投递，并按账号配置顺序返回结果。

    ``ThreadPoolExecutor`` 适合草稿上传这种同步 HTTP I/O；工作线程数最少为一，
    并且不超过待投递账号数。所有已启动任务都会等待完成，避免首个失败导致其余成功
    结果没有机会持久化，下一次重试又把这些账号重复投递。
    """

    unique_account_ids = list(dict.fromkeys(int(account_id) for account_id in account_ids))
    if not unique_account_ids:
        return []
    worker_count = max(1, min(int(max_workers or 1), len(unique_account_ids)))
    results_by_account: dict[int, DraftDeliveryExecution[DeliveryValue]] = {}
    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="scheduled-draft-delivery",
    ) as executor:
        future_to_account = {
            executor.submit(deliver, account_id): account_id
            for account_id in unique_account_ids
        }
        for future in as_completed(future_to_account):
            account_id = future_to_account[future]
            try:
                results_by_account[account_id] = DraftDeliveryExecution(
                    account_id=account_id,
                    value=future.result(),
                )
            except BaseException as exc:
                results_by_account[account_id] = DraftDeliveryExecution(
                    account_id=account_id,
                    error=exc,
                )
    return [results_by_account[account_id] for account_id in unique_account_ids]
