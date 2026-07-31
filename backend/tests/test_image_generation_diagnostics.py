"""图片生成与中转发布诊断的回归测试。"""

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本模块只验证服务边界行为，不访问业务数据库。"""
    yield


@pytest.mark.asyncio
async def test_agent5_prints_final_prompt_and_generation_result(monkeypatch, capsys):
    """每张图片必须在后端控制台输出实际提交给万相的最终提示词。"""
    from app.schemas.article import ArticleState, ImageRequirement
    from app.services.article_agent_service import agent5_generate_images
    from app.services.image_service_v2 import ImageServiceStrategy

    final_prompt = "主体：原木子母茶几，场景：现代客厅，构图与版式：前后错落布局。"

    async def fake_execute(self, method, keywords, **kwargs):
        assert method == "DASHSCOPE"
        assert keywords == "原木子母茶几"
        assert kwargs["prompt"] == final_prompt
        return "https://image.example.com/generated.png"

    monkeypatch.setattr(ImageServiceStrategy, "execute", fake_execute)
    state = ArticleState(
        task_id="diagnostic-task",
        topic="家具",
        image_requirements=[
            ImageRequirement(
                position=1,
                type="inline",
                image_source="DASHSCOPE",
                keywords="原木子母茶几",
                prompt=final_prompt,
                placeholder_id="image-1",
            )
        ],
    )

    result = await agent5_generate_images(state)
    output = capsys.readouterr().out

    assert result.images[0].url == "https://image.example.com/generated.png"
    assert "图片生成 1/1" in output
    assert "最终提示词" in output
    assert final_prompt in output
    assert "生成结果" in output


def test_relay_publish_rejects_http_image_urls_instead_of_replacing_them_with_random_images():
    """中转站无法访问 HTTP 图片时必须中止发布，不能偷换为 Picsum。"""
    from app.services.wechat_publisher import ensure_relay_image_urls_are_https

    with pytest.raises(ValueError, match="MINIO_PUBLIC_ENDPOINT"):
        ensure_relay_image_urls_are_https(
            '<p>正文</p><img src="http://localhost:9002/wechat-assets/image.png"/>',
            "http://localhost:9002/wechat-assets/cover.png",
        )


def test_delivery_url_keeps_attributed_archive_when_relay_stages_local_minio(monkeypatch):
    """中转站会临时公网化本地归档图，交付地址必须保留产品署名版本。"""
    import app.api.v1.articles as articles_api

    monkeypatch.setattr(articles_api.settings, "wechat_api_channel", "relay")

    source_url = "https://dashscope.example.com/generated.png"
    archive_url = "http://localhost:9002/wechat-assets/assets/auto/image.png"

    assert articles_api.select_delivery_image_url(source_url, archive_url) == archive_url


def test_delivery_url_uses_archived_url_when_it_is_public_https():
    """配置了公网 HTTPS 对象存储后，仍优先使用带水印的归档图片。"""
    from app.api.v1.articles import select_delivery_image_url

    source_url = "https://dashscope.example.com/generated.png"
    archive_url = "https://assets.example.com/assets/auto/image.png"

    assert select_delivery_image_url(source_url, archive_url) == archive_url
