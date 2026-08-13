"""TaGeAI 外部调用取消语义回归测试。

这些测试只使用最小数据库替身，重点验证取消状态不能在远端副作用尚未停止时伪装成
``CANCELLED``。真实微信投递由发布器替身隔离，测试环境不会访问公众号开放平台。
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


class _CancellationQuery:
    """满足取消服务查询链路的最小 SQLAlchemy Query 替身。"""

    def __init__(self, row):
        """保存该模型的唯一测试行，过滤条件由夹具预先保证。"""

        self._row = row

    def filter(self, *args, **kwargs):
        """保留 ORM 链式调用形态，不重新解释生产查询表达式。"""

        return self

    def first(self):
        """返回当前测试调用对应的唯一记录。"""

        return self._row


class _CancellationDb:
    """记录取消持久化行为的数据库替身。"""

    def __init__(self, invocation, job):
        """分别保存 Integration Invocation 和 ContentJob 两个状态事实。"""

        self._invocation = invocation
        self._job = job
        self.commit_count = 0

    def query(self, model_class):
        """按模型返回相应记录，使服务层仍走真实的查询边界。"""

        from app.models.mysql_models import (
            ContentJob,
            TageAiIntegrationCallbackOutbox,
            TageAiIntegrationInvocation,
        )

        if model_class is TageAiIntegrationInvocation:
            return _CancellationQuery(self._invocation)
        if model_class is ContentJob:
            return _CancellationQuery(self._job)
        if model_class is TageAiIntegrationCallbackOutbox.id:
            # 取消请求的快照尚未入队，生产代码应继续创建一条 outbox 记录。
            return _CancellationQuery(None)
        raise AssertionError(f"取消路径不应查询模型: {model_class}")

    def begin_nested(self):
        """提供 outbox 去重插入所需的嵌套事务上下文。"""

        return self

    def __enter__(self):
        """让数据库替身可以作为嵌套事务上下文管理器使用。"""

        return self

    def __exit__(self, *_args):
        """不吞掉 outbox 插入过程中的异常，保持真实事务语义。"""

        return False

    def add(self, _value):
        """接受 callback outbox 对象；本测试只关注取消状态提交。"""

        return None

    def flush(self):
        """兼容生产代码在唯一键检查前的 flush。"""

        return None

    def commit(self):
        """记录状态写入，防止取消仅修改内存对象。"""

        self.commit_count += 1

    def close(self):
        """兼容服务层 finally 中的会话释放。"""

        return None


def test_running_invocation_returns_cancel_requested_until_worker_acknowledges(monkeypatch):
    """运行中的生成任务只能确认“已收到取消请求”，不能抢先报告已取消。

    真实 Worker 可能正等待模型、图片或公众号接口响应；在它检查持久化取消标志并停止
    前，Gateway 必须保留可观测的中间状态，避免用户误以为发布副作用已经被撤回。
    """

    from app.integrations.tageai import service as tageai_service

    invocation = SimpleNamespace(
        id=10001,
        invocation_id="tage-inv-running-cancel",
        tenant_id=7,
        content_job_id=901,
        tenant_binding_id="tenant-binding-7",
        status="GENERATING",
        phase="GENERATING",
        progress=35,
        finished_at=None,
    )
    job = SimpleNamespace(id=901, tenant_id=7, status="generating")
    db = _CancellationDb(invocation, job)

    monkeypatch.setattr(tageai_service, "MysqlSessionLocal", lambda: db)
    monkeypatch.setattr(
        tageai_service,
        "_serialize_invocation",
        lambda current, *_args, **_kwargs: {
            "status": current.status,
            "phase": current.phase,
            "progress": current.progress,
            "finished_at": current.finished_at,
        },
    )

    result = tageai_service.cancel_invocation("tage-inv-running-cancel", tenant_id=7)

    assert result["status"] == "CANCEL_REQUESTED"
    assert job.status == "cancel_requested"
    assert invocation.status == "CANCEL_REQUESTED"
    assert invocation.phase == "CANCEL_REQUESTED"
    assert invocation.finished_at is None
    assert db.commit_count == 1


def test_cancelled_job_probe_stops_worker_before_delivery():
    """Worker 读取到持久化取消标志后必须中断，不得进入草稿或发布逻辑。"""

    from app.services import job_queue_service

    class _CancelledJobDb:
        """只提供 Worker 取消探针所需的最新任务状态。"""

        def query(self, _model_class):
            """返回列查询兼容替身，模拟其他事务已写入取消请求。"""

            return self

        def filter(self, *args, **kwargs):
            """保留生产查询的链式 API。"""

            return self

        def scalar(self):
            """提供取消请求状态，验证 Worker 不是只读取陈旧 ORM 对象。"""

            return "cancel_requested"

    with pytest.raises(job_queue_service.JobCancellationRequested):
        job_queue_service.raise_if_job_cancellation_requested(_CancelledJobDb(), job_id=901)


def test_delivery_stops_when_worker_observes_cancellation_request(monkeypatch):
    """投递前的取消检查必须发生在任何微信发布器调用之前。"""

    from app.tasks import job_tasks

    class _StopDelivery(Exception):
        """用来证明取消探针中断了投递分支的测试专用异常。"""

    class _DeliveryDb:
        """为文章投递辅助函数提供最小持久化替身。"""

        def __init__(self):
            """记录异常分支前的对象写入，避免真实数据库副作用。"""

            self.added = []

        def add(self, value):
            """保存待写入对象以兼容 ORM 流程。"""

            self.added.append(value)

        def flush(self):
            """模拟数据库回填 ID，满足文章与发布尝试关联。"""

            for index, value in enumerate(self.added, start=1):
                if getattr(value, "id", None) is None:
                    value.id = index

        def commit(self):
            """投递被取消时不应依赖真实事务。"""

            return None

    def stop_for_cancellation(_db, _job_id):
        """模拟 Worker 正好在投递前读取到另一事务写入的取消标志。"""

        raise _StopDelivery()

    def unexpected_draft_publish(*_args, **_kwargs):
        """一旦到达真实发布器，代表取消边界已经失效。"""

        raise AssertionError("取消请求后不得调用草稿投递")

    monkeypatch.setattr(
        job_tasks,
        "raise_if_job_cancellation_requested",
        stop_for_cancellation,
    )
    from app.services import wechat_publisher

    monkeypatch.setattr(wechat_publisher, "save_article_as_draft", unexpected_draft_publish)

    job = SimpleNamespace(
        id=901,
        tenant_id=7,
        created_by=11,
        account_id=103,
        topic="取消边界测试",
        generation_config={"publish_mode": "draft", "account_ids": [103]},
    )
    version = SimpleNamespace(
        id=1001,
        title="测试文章",
        summary="测试摘要",
        body_markdown="测试正文",
        model_metadata=None,
        article_content_type="image_text",
    )

    with pytest.raises(_StopDelivery):
        job_tasks._save_versions_as_articles_and_drafts(_DeliveryDb(), job, [version])
