"""定时任务 COS 临时对象清理测试。"""

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """清理器只验证内存对象，不连接业务数据库。"""
    yield


def test_scheduled_pipeline_deletes_cos_object_on_failure():
    """流水线主异常不能阻止 finally 清理已记录的 COS 对象。"""
    from app.tasks.scheduled_task_executor import (
        _cleanup_cos_relay_objects,
        _run_with_cos_cleanup,
    )

    class FakeRelay:
        """记录精确删除键，验证清理器不会执行前缀删除。"""

        def __init__(self):
            self.deleted_keys = []

        def delete_object(self, object_key):
            self.deleted_keys.append(object_key)

    relay = FakeRelay()
    object_keys = ["temporary/107/6/image.jpg"]

    async def failing_pipeline():
        """模拟万相或发布阶段失败。"""
        raise RuntimeError("图生图失败")

    async def run():
        return await _run_with_cos_cleanup(failing_pipeline, relay, object_keys)

    with pytest.raises(RuntimeError, match="图生图失败"):
        import asyncio
        asyncio.run(run())

    assert relay.deleted_keys == object_keys
