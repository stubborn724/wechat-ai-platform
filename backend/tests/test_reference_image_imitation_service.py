"""参考图片仿写编排服务的单元测试。

这些测试不调用真实模型或存储服务，而是通过可注入依赖验证编排边界：二维码必须
在提示词和图片生成之前被跳过，其余图片必须维持参考文章中的原始顺序。
"""

import asyncio

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """共享编排服务是纯函数测试，不应依赖或改动本地测试数据库。"""
    yield


def test_imitate_reference_images_skips_qrcode_and_preserves_non_qrcode_order():
    """二维码不参与仿写，前后的普通图片仍按参考顺序生成。"""
    from app.services.reference_image_imitation_service import imitate_reference_images

    generated_prompts: list[str] = []
    archived_urls: list[str] = []

    async def generate_image(prompt: str, *, size: str) -> str:
        assert size == "1024*1365"
        generated_prompts.append(prompt)
        return f"https://generated.example/{len(generated_prompts)}.png"

    async def archive_image(tenant_id: int, image_url: str, *, keywords: str) -> None:
        assert tenant_id == 1
        assert keywords == "新主题"
        archived_urls.append(image_url)

    result = asyncio.run(
        imitate_reference_images(
            ["first", "qr", "last"],
            "新主题",
            tenant_id=1,
            understand_images_fn=lambda _: [
                {"subject": "first", "is_qrcode": False},
                {"subject": "qr", "is_qrcode": True},
                {"subject": "last", "composition": "最后一张构图", "is_qrcode": False},
            ],
            craft_prompt_fn=lambda description, **_: {"prompt": description["subject"]},
            fallback_prompt_fn=lambda *_: "fallback",
            generate_image_fn=generate_image,
            archive_image_fn=archive_image,
        )
    )

    assert result.generated_urls == (
        "https://generated.example/1.png",
        "https://generated.example/2.png",
    )
    assert result.skipped_qrcode_count == 1
    assert result.skipped_invalid_count == 0
    assert all("新主题" in prompt for prompt in generated_prompts)
    assert "最后一张构图" in generated_prompts[1]
    assert archived_urls == list(result.generated_urls)


def test_imitate_reference_images_does_not_generate_when_all_references_are_qrcodes():
    """全部参考图为二维码时，不得构建提示词、生成或归档任何图片。"""
    from app.services.reference_image_imitation_service import imitate_reference_images

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("二维码不应进入图片生成流程")

    result = asyncio.run(
        imitate_reference_images(
            ["qr"],
            "新主题",
            tenant_id=1,
            understand_images_fn=lambda _: [{"subject": "qr", "is_qrcode": True}],
            craft_prompt_fn=fail_if_called,
            fallback_prompt_fn=fail_if_called,
            generate_image_fn=fail_if_called,
            archive_image_fn=fail_if_called,
        )
    )

    assert result.generated_urls == ()
    assert result.skipped_qrcode_count == 1


def test_compose_visual_imitation_prompt_keeps_visual_constraints_and_replaces_subject():
    """最终提示词必须以新主体承载参考图的完整视觉约束。"""
    from app.services.reference_image_imitation_service import compose_visual_imitation_prompt

    prompt = compose_visual_imitation_prompt(
        {
            "subject": "旧主体",
            "scene": "雨夜街头",
            "composition": "居中对称",
            "camera": "低机位广角",
            "lighting": "侧逆光",
            "color_palette": "青橙色调",
            "visual_style": "电影海报",
            "details": ["雨水反光", "地面倒影"],
            "mood": "紧张",
        },
        subject="新主体",
        supplement="画面层次丰富",
    )

    assert "新主体" in prompt
    assert "旧主体" not in prompt
    assert "居中对称" in prompt
    assert "低机位广角" in prompt
    assert "侧逆光" in prompt
    assert "青橙色调" in prompt
    assert "电影海报" in prompt
    assert "不要包含任何文字、品牌、水印、签名、标签或二维码" in prompt
