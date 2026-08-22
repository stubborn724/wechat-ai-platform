"""文生文主备路由服务。

标题、大纲、正文和 HTML 槽位修复都依赖同一文本生成边界。主站发生网络、限流
或上游错误时，本服务按配置顺序切换到百炼；业务 Agent 不再自行管理客户端，
从而避免不同入口使用不同模型或遗漏兜底配置。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from openai import AsyncOpenAI

from app.config import settings as application_settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TextGenerationRequest:
    """一次提供商无关的文生文请求。"""

    system_prompt: str
    user_message: str
    temperature: float = 0.8
    model_override: str | None = None


class TextGenerationProvider(Protocol):
    """文生文提供商必须实现的最小同步结果与流式结果接口。"""

    name: str

    async def complete(self, request: TextGenerationRequest) -> str:
        """返回完整文本。"""

    async def stream(
        self,
        request: TextGenerationRequest,
        stream_handler: Callable[[str], None],
    ) -> str:
        """逐块输出并返回完整文本。"""


class TextGenerationChainError(RuntimeError):
    """所有文本提供商均失败时返回的脱敏异常。"""

    def __init__(self, failures: list[tuple[str, Exception]]) -> None:
        # 上游异常消息可能包含请求信息，因此这里只保留提供商名与异常类型。
        summary = "；".join(
            f"{provider_name}:{type(error).__name__}"
            for provider_name, error in failures
        )
        super().__init__(f"文生文提供商全部失败：{summary}")
        self.failures = tuple(failures)


class OpenAICompatibleTextProvider:
    """通过 OpenAI Chat Completions 协议调用单个文本提供商。"""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int,
        client: Any = None,
    ) -> None:
        """保存独立站点配置；客户端延迟创建便于测试和 Worker 重启加载。"""
        self.name = name
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.model = str(model or "").strip()
        self.timeout_seconds = int(timeout_seconds)
        self._client = client

    def _get_client(self) -> AsyncOpenAI:
        """创建当前提供商专属客户端，密钥不会传递给其他提供商。"""
        self._validate_configuration()
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout_seconds,
            )
        return self._client

    async def complete(self, request: TextGenerationRequest) -> str:
        """执行非流式 Chat Completions 请求。"""
        model = request.model_override or self.model
        response = await self._get_client().chat.completions.create(
            model=model,
            messages=self._messages(request),
            temperature=request.temperature,
            stream=False,
        )
        from app.services.model_usage_service import record_text_token_usage

        record_text_token_usage(self.name, model, getattr(response, "usage", None))
        return response.choices[0].message.content or ""

    async def stream(
        self,
        request: TextGenerationRequest,
        stream_handler: Callable[[str], None],
    ) -> str:
        """执行流式请求，并保证回调顺序与上游分片顺序一致。"""
        model = request.model_override or self.model
        stream = await self._get_client().chat.completions.create(
            model=model,
            messages=self._messages(request),
            temperature=request.temperature,
            stream=True,
        )
        chunks: list[str] = []
        async for chunk in stream:
            # OpenAI 协议可能在最后一个 ``choices=[]`` 分片携带总 usage。先记录
            # 再跳过空心跳，避免流式标题/正文因没有可见文字而漏记真实 token。
            from app.services.model_usage_service import record_text_token_usage

            record_text_token_usage(self.name, model, getattr(chunk, "usage", None))
            # 部分 OpenAI 兼容网关会在真实内容前发送 ``choices=[]`` 的心跳分片。
            # 该分片不是上游故障，若直接索引会误判主模型失败并切换到备用模型。
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta_object = getattr(choices[0], "delta", None)
            delta = getattr(delta_object, "content", None) or ""
            if not delta:
                continue
            chunks.append(delta)
            stream_handler(delta)
        return "".join(chunks)

    def _validate_configuration(self) -> None:
        """在请求前明确报告缺失项，但绝不包含配置值。"""
        missing = []
        if not self.base_url:
            missing.append("base_url")
        if not self.api_key:
            missing.append("api_key")
        if not self.model:
            missing.append("model")
        if missing:
            raise RuntimeError(
                f"文本提供商 {self.name} 缺少配置：{', '.join(missing)}"
            )

    @staticmethod
    def _messages(request: TextGenerationRequest) -> list[dict[str, str]]:
        """集中构造消息，确保主备提供商收到完全相同的业务输入。"""
        return [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_message},
        ]


class TextGenerationService:
    """按配置顺序执行文本提供商，并在失败时逐级降级。"""

    def __init__(
        self,
        *,
        settings=application_settings,
        providers: Mapping[str, TextGenerationProvider] | None = None,
    ) -> None:
        """解析有序提供商链，并允许测试注入内存替身。"""
        self.settings = settings
        raw_chain = str(
            getattr(settings, "text_generation_provider_chain", "kuai,dashscope")
            or "kuai,dashscope"
        )
        self.provider_names = tuple(
            dict.fromkeys(name.strip().lower() for name in raw_chain.split(",") if name.strip())
        )
        self.providers = dict(providers or _build_default_text_providers(settings))

    async def complete(self, request: TextGenerationRequest) -> str:
        """执行完整文本请求，任一提供商成功后立即返回。"""
        return await self._run_chain(
            lambda provider: provider.complete(request)
        )

    async def stream(
        self,
        request: TextGenerationRequest,
        stream_handler: Callable[[str], None],
    ) -> str:
        """在首个分片前允许降级；已有输出后失败则停止，避免正文重复。"""
        failures: list[tuple[str, Exception]] = []
        for provider_name in self.provider_names:
            provider = self._require_provider(provider_name)
            emitted_chunks: list[str] = []

            def guarded_handler(chunk: str) -> None:
                emitted_chunks.append(chunk)
                stream_handler(chunk)

            try:
                result = await provider.stream(request, guarded_handler)
                if provider_name != self.provider_names[0]:
                    logger.warning("文生文流式降级成功 provider=%s", provider_name)
                return result
            except Exception as exc:
                failures.append((provider_name, exc))
                if emitted_chunks:
                    # 已经向前端或任务日志输出内容后不能切换提供商，否则会把两份
                    # 正文拼接在一起；此时明确失败，由任务重试整次 Agent。
                    raise TextGenerationChainError(failures) from exc
                logger.warning(
                    "文生文流式提供商失败 provider=%s error=%s",
                    provider_name,
                    type(exc).__name__,
                )
        raise TextGenerationChainError(failures)

    async def _run_chain(self, operation) -> str:
        """执行非流式提供商链，日志只记录异常类型。"""
        failures: list[tuple[str, Exception]] = []
        for provider_name in self.provider_names:
            provider = self._require_provider(provider_name)
            try:
                result = await operation(provider)
                if provider_name != self.provider_names[0]:
                    logger.warning("文生文降级成功 provider=%s", provider_name)
                return result
            except Exception as exc:
                failures.append((provider_name, exc))
                logger.warning(
                    "文生文提供商失败 provider=%s error=%s",
                    provider_name,
                    type(exc).__name__,
                )
        raise TextGenerationChainError(failures)

    def _require_provider(self, provider_name: str) -> TextGenerationProvider:
        """拒绝未知提供商名称，防止拼写错误被静默忽略。"""
        provider = self.providers.get(provider_name)
        if provider is None:
            raise RuntimeError(f"未配置文生文提供商：{provider_name}")
        return provider


def _build_default_text_providers(settings) -> dict[str, TextGenerationProvider]:
    """集中组装双 Kuai 文本链路，并保留百炼兼容提供商。"""
    timeout_seconds = int(getattr(settings, "text_generation_timeout_seconds", 180))
    primary_base_url = getattr(settings, "text_generation_base_url", "")
    primary_api_key = getattr(settings, "text_generation_api_key", "")
    return {
        "kuai": OpenAICompatibleTextProvider(
            name="kuai",
            base_url=primary_base_url,
            api_key=primary_api_key,
            model=getattr(settings, "text_generation_model", "gpt-4.1-mini"),
            timeout_seconds=timeout_seconds,
        ),
        "kuai_secondary": OpenAICompatibleTextProvider(
            name="kuai_secondary",
            base_url=getattr(settings, "text_generation_secondary_base_url", "")
            or primary_base_url,
            api_key=getattr(settings, "text_generation_secondary_api_key", "")
            or primary_api_key,
            model=getattr(settings, "text_generation_secondary_model", "")
            or getattr(settings, "text_generation_model", "gpt-4.1-mini"),
            timeout_seconds=timeout_seconds,
        ),
        "dashscope": OpenAICompatibleTextProvider(
            name="dashscope",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key=getattr(settings, "dashscope_api_key", ""),
            model=getattr(settings, "dashscope_model", "qwen-plus"),
            timeout_seconds=timeout_seconds,
        ),
    }


# 单例在进程启动时读取配置；修改 .env 后必须重启 API 与 Celery 进程。
text_generation_service = TextGenerationService()
