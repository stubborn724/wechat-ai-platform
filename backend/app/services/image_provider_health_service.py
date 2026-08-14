"""图片提供商短期健康状态。

图片生成的调用方通常会在一篇文章中并发请求多张图。某个 Provider 已经发生
网络超时或响应截断时，如果每张图都重新等待完整超时，会把一次上游故障放大为
整篇文章的长尾。本服务只维护短期可用性，负责让后续请求快速降级，不参与图片
质量、计费和最终的业务失败判定。
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from time import monotonic

from app.services.image_generation_models import ImageErrorCategory


_CIRCUIT_FAILURE_CATEGORIES = frozenset({
    ImageErrorCategory.TEMPORARY,
    ImageErrorCategory.RATE_LIMIT,
    ImageErrorCategory.UPSTREAM,
    ImageErrorCategory.TRUNCATED_RESPONSE,
})


@dataclass
class _ProviderHealthState:
    """单个 Provider 操作类型的连续失败状态。"""

    consecutive_failures: int = 0
    open_until: float = 0.0


class ImageProviderHealthService:
    """以进程内状态保护同一 Worker 中的连续图片请求。

    状态刻意使用轻量内存实现：图片服务在每个 Worker 中是单例，首期目标是避免
    一篇文章内多张图片重复撞向已确认不可用的上游。进程重启后自动恢复探测，
    不会因为 Redis 故障反向阻断图片生成。后续横向扩容时可以在不改变调用接口的
    前提下替换为 Redis 实现。
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: int = 600,
    ) -> None:
        """创建熔断策略，并拒绝无意义的阈值配置。"""

        self.failure_threshold = max(int(failure_threshold), 1)
        self.cooldown_seconds = max(int(cooldown_seconds), 1)
        self._states: dict[tuple[str, str], _ProviderHealthState] = {}
        self._lock = Lock()

    def allow_request(self, provider: str, operation: str) -> bool:
        """判断 Provider 当前是否允许接收请求。

        冷却时间结束后，首个请求自然承担半开探测职责；成功会清空连续失败计数，
        失败则由 ``record_failure`` 再次打开熔断。
        """

        with self._lock:
            state = self._states.get(self._key(provider, operation))
            return state is None or state.open_until <= monotonic()

    def record_success(self, provider: str, operation: str) -> None:
        """记录成功并恢复该 Provider 的健康状态。"""

        with self._lock:
            self._states.pop(self._key(provider, operation), None)

    def record_failure(
        self,
        provider: str,
        operation: str,
        category: ImageErrorCategory,
    ) -> None:
        """记录可用性失败，达到阈值后打开固定冷却期。

        鉴权、参数和本地存储等确定性错误不属于上游健康问题，不能导致同 Provider
        的其他图片被跳过，因此直接忽略。
        """

        if category not in _CIRCUIT_FAILURE_CATEGORIES:
            return

        with self._lock:
            key = self._key(provider, operation)
            state = self._states.setdefault(key, _ProviderHealthState())
            state.consecutive_failures += 1
            if state.consecutive_failures >= self.failure_threshold:
                state.open_until = monotonic() + self.cooldown_seconds

    @staticmethod
    def _key(provider: str, operation: str) -> tuple[str, str]:
        """统一规格化键，避免不同大小写配置产生独立熔断状态。"""

        return (str(provider or "").strip().lower(), str(operation or "").strip().lower())
