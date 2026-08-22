"""文生文主备路由测试。"""

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """文本路由测试只使用内存替身，不访问数据库。"""
    yield


class FakeTextProvider:
    """记录请求并按配置返回文本或抛出异常。"""

    def __init__(self, name, result="", error=None):
        self.name = name
        self.result = result
        self.error = error
        self.calls = []

    async def complete(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return self.result

    async def stream(self, request, stream_handler):
        """模拟流式输出；异常发生在任何内容写入前。"""
        self.calls.append(request)
        if self.error:
            raise self.error
        for chunk in self.result:
            stream_handler(chunk)
        return "".join(self.result)


class FakeStreamingCompletions:
    """模拟部分 OpenAI 兼容站会发送的空 choices 心跳分片。"""

    def __init__(self, chunks):
        self.chunks = chunks

    async def create(self, **_kwargs):
        """返回异步可迭代分片，行为与 SDK 的流式响应一致。"""
        async def generate_chunks():
            for chunk in self.chunks:
                yield chunk

        return generate_chunks()


class FakeStreamingClient:
    """仅实现文本提供商访问的 Chat Completions 最小接口。"""

    def __init__(self, chunks):
        self.chat = SimpleNamespace(
            completions=FakeStreamingCompletions(chunks),
        )


class FakeCompletionClient:
    """提供带 usage 的非流式响应，验证服务不会自行估算 token。"""

    def __init__(self, response):
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create),
        )
        self.response = response

    async def create(self, **_kwargs):
        return self.response


def build_settings():
    """构造快站主用、百炼兜底的文本配置。"""
    return SimpleNamespace(
        text_generation_provider_chain="kuai,dashscope",
    )


def test_text_provider_builder_supports_two_independent_kuai_models():
    """第二层 Kuai 必须可独立配置，不能复用第一层的模型字段。"""
    from app.services.text_generation_service import _build_default_text_providers

    settings = SimpleNamespace(
        text_generation_timeout_seconds=90,
        text_generation_base_url="https://api.kuai.example/v1",
        text_generation_api_key="primary-key",
        text_generation_model="gpt-5-mini",
        text_generation_secondary_base_url="https://api.kuai.example/v1",
        text_generation_secondary_api_key="secondary-key",
        text_generation_secondary_model="qwen3.5-flash",
        dashscope_api_key="dashscope-key",
        dashscope_model="qwen-plus",
    )

    providers = _build_default_text_providers(settings)

    assert providers["kuai"].model == "gpt-5-mini"
    assert providers["kuai_secondary"].model == "qwen3.5-flash"
    assert providers["kuai_secondary"].api_key == "secondary-key"


@pytest.mark.asyncio
async def test_text_primary_success_does_not_call_dashscope():
    """快站成功时不能继续调用百炼，避免重复计费。"""
    from app.services.text_generation_service import (
        TextGenerationRequest,
        TextGenerationService,
    )

    kuai = FakeTextProvider("kuai", result='{"title":"家具新标题"}')
    dashscope = FakeTextProvider("dashscope", result="不应调用")
    service = TextGenerationService(
        settings=build_settings(),
        providers={"kuai": kuai, "dashscope": dashscope},
    )
    request = TextGenerationRequest(
        system_prompt="只返回 JSON",
        user_message="生成家具标题",
        temperature=0.5,
    )

    result = await service.complete(request)

    assert result == '{"title":"家具新标题"}'
    assert kuai.calls == [request]
    assert dashscope.calls == []


@pytest.mark.asyncio
async def test_text_primary_failure_falls_back_to_dashscope():
    """快站网络失败时应使用相同输入调用百炼完成本次 Agent。"""
    from app.services.text_generation_service import (
        TextGenerationRequest,
        TextGenerationService,
    )

    kuai = FakeTextProvider("kuai", error=TimeoutError("gateway timeout"))
    dashscope = FakeTextProvider("dashscope", result='{"title":"百炼兜底标题"}')
    service = TextGenerationService(
        settings=build_settings(),
        providers={"kuai": kuai, "dashscope": dashscope},
    )
    request = TextGenerationRequest(
        system_prompt="只返回 JSON",
        user_message="生成家具标题",
    )

    result = await service.complete(request)

    assert result == '{"title":"百炼兜底标题"}'
    assert kuai.calls == [request]
    assert dashscope.calls == [request]


@pytest.mark.asyncio
async def test_text_all_providers_fail_without_leaking_keys():
    """两端都失败时错误只包含提供商和异常类型，不能拼接密钥。"""
    from app.services.text_generation_service import (
        TextGenerationRequest,
        TextGenerationService,
    )

    kuai = FakeTextProvider("kuai", error=RuntimeError("kuai unavailable"))
    dashscope = FakeTextProvider("dashscope", error=RuntimeError("dashscope unavailable"))
    service = TextGenerationService(
        settings=build_settings(),
        providers={"kuai": kuai, "dashscope": dashscope},
    )

    with pytest.raises(RuntimeError) as error_info:
        await service.complete(TextGenerationRequest("system", "user"))

    message = str(error_info.value)
    assert "kuai" in message
    assert "dashscope" in message
    assert "sk-" not in message


@pytest.mark.asyncio
async def test_text_chain_error_exposes_all_provider_failures_for_scheduler():
    """总失败异常必须保留底层异常集合，供调度器区分临时故障与配置错误。"""
    from app.services.text_generation_service import (
        TextGenerationChainError,
        TextGenerationRequest,
        TextGenerationService,
    )

    kuai = FakeTextProvider("kuai", error=TimeoutError("暂时超时"))
    dashscope = FakeTextProvider("dashscope", error=ValueError("参数错误"))
    service = TextGenerationService(
        settings=build_settings(),
        providers={"kuai": kuai, "dashscope": dashscope},
    )

    with pytest.raises(TextGenerationChainError) as error_info:
        await service.complete(TextGenerationRequest("system", "user"))

    assert [name for name, _error in error_info.value.failures] == ["kuai", "dashscope"]


@pytest.mark.asyncio
async def test_streaming_text_falls_back_before_emitting_content():
    """快站在首个分片前失败时，正文流必须由百炼完整接管。"""
    from app.services.text_generation_service import (
        TextGenerationRequest,
        TextGenerationService,
    )

    kuai = FakeTextProvider("kuai", error=TimeoutError("stream timeout"))
    dashscope = FakeTextProvider("dashscope", result=["完整", "正文"])
    service = TextGenerationService(
        settings=build_settings(),
        providers={"kuai": kuai, "dashscope": dashscope},
    )
    chunks = []
    request = TextGenerationRequest("system", "user")

    result = await service.stream(request, chunks.append)

    assert result == "完整正文"
    assert chunks == ["完整", "正文"]
    assert kuai.calls == [request]
    assert dashscope.calls == [request]


@pytest.mark.asyncio
async def test_openai_stream_ignores_empty_choices_heartbeat_chunk():
    """空 choices 只是协议心跳，不能触发主模型降级。"""
    from app.services.text_generation_service import (
        OpenAICompatibleTextProvider,
        TextGenerationRequest,
    )

    empty_heartbeat = SimpleNamespace(choices=[])
    content_chunk = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content="主模型正文"))],
    )
    provider = OpenAICompatibleTextProvider(
        name="kuai",
        base_url="https://api.example.test/v1",
        api_key="test-key",
        model="gpt-5-mini",
        timeout_seconds=30,
        client=FakeStreamingClient([empty_heartbeat, content_chunk]),
    )
    received_chunks: list[str] = []

    result = await provider.stream(
        TextGenerationRequest("system", "user"),
        received_chunks.append,
    )

    assert result == "主模型正文"
    assert received_chunks == ["主模型正文"]


@pytest.mark.asyncio
async def test_text_usage_records_actual_provider_usage_without_estimating_missing_values():
    """只有上游实际返回 usage 时才累计 token，不能按字符数伪造费用数据。"""
    from app.services.model_usage_service import (
        begin_model_usage_collection,
        end_model_usage_collection,
    )
    from app.services.text_generation_service import (
        OpenAICompatibleTextProvider,
        TextGenerationRequest,
    )

    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="测试正文"))],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=45, total_tokens=165),
    )
    provider = OpenAICompatibleTextProvider(
        name="kuai",
        base_url="https://api.example.test/v1",
        api_key="test-key",
        model="gpt-5-mini",
        timeout_seconds=30,
        client=FakeCompletionClient(response),
    )
    token = begin_model_usage_collection("usage-test")
    try:
        assert await provider.complete(TextGenerationRequest("system", "user")) == "测试正文"
    finally:
        summary = end_model_usage_collection(token)

    assert summary.text_request_count == 1
    assert summary.input_tokens == 120
    assert summary.output_tokens == 45
    assert summary.total_tokens == 165


@pytest.mark.asyncio
async def test_streaming_text_records_usage_from_final_usage_chunk():
    """流式 Agent 的最终 usage 分片也必须进入同一次任务账本。"""
    from app.services.model_usage_service import (
        begin_model_usage_collection,
        end_model_usage_collection,
    )
    from app.services.text_generation_service import (
        OpenAICompatibleTextProvider,
        TextGenerationRequest,
    )

    content_chunk = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content="流式正文"))],
        usage=None,
    )
    usage_chunk = SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=80, completion_tokens=30, total_tokens=110),
    )
    provider = OpenAICompatibleTextProvider(
        name="kuai",
        base_url="https://api.example.test/v1",
        api_key="test-key",
        model="gpt-5-mini",
        timeout_seconds=30,
        client=FakeStreamingClient([content_chunk, usage_chunk]),
    )
    token = begin_model_usage_collection("stream-usage-test")
    try:
        result = await provider.stream(TextGenerationRequest("system", "user"), lambda _chunk: None)
    finally:
        summary = end_model_usage_collection(token)

    assert result == "流式正文"
    assert summary.text_request_count == 1
    assert summary.total_tokens == 110
