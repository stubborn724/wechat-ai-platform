"""定时任务生成图片的视觉质量校验服务。

本模块只负责两类稳定边界：

1. 为 ERP 图生图提示词补充真实场景约束，避免模型只输出抠图或纯色画布；
2. 在图片进入文章归档前检查结果是否确实包含视觉信息。

图片质量判断与文章排版、投喂源格式分析保持独立。这样新增质量规则不会改变
standard 版式，也不会修改已经生成的历史文章；调用方可以只在 ERP 图生图
分支启用这项保护。
"""

from __future__ import annotations

import io
import logging
import asyncio
from dataclasses import dataclass

import httpx
from PIL import Image, ImageStat

from app.services.url_safety import validate_url
from app.services.minio_url_resolver import MinioUrlResolver
from app.services.storage_service import storage_service


logger = logging.getLogger(__name__)

# 提示词标记必须稳定，便于同一任务在不同阶段重复经过编排函数时保持幂等。
_SCENE_GUARD_MARKER = "【场景质量硬约束】"
_LOW_INFORMATION_RETRY_MARKER = "【低信息量结果修复】"

# 解析器只负责判断图片地址是否属于本地 MinIO；真正的对象读取交给内部 SDK，
# 这样 Docker Worker 不会把宿主机的 localhost 地址当作容器内服务地址访问。
_local_minio_url_resolver = MinioUrlResolver.from_settings()


@dataclass(frozen=True)
class ImageQualityReport:
    """单张图片的可交付性报告。

    is_usable 只表示是否通过“不是空白/低信息量”的最低门槛，不替代人工
    审美审核；reason 会进入日志和最终异常，方便定位是下载失败还是图片本身
    过于单色。
    """

    is_usable: bool
    reason: str


def assess_image_bytes(image_bytes: bytes) -> ImageQualityReport:
    """从图片字节判断结果是否接近空白画布。

    规则刻意不采用“白色像素占比”单一指标。家具产品图经常使用白墙、白棚或白
    背景，若只按白色比例拒绝，会把有主体、有阴影的有效商品图误判为失败。这里
    综合透明度、像素方差和相邻像素变化：纯白/纯灰/透明图没有足够差异，而白底
    商品图会因主体、轮廓、阴影或材质细节保留明显变化。
    """

    if not image_bytes:
        return ImageQualityReport(False, "低信息量：图片字节为空")

    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            source.load()
            rgba = source.convert("RGBA")
    except Exception as exc:
        logger.warning("图片质量检查无法解析图片: %s", exc)
        return ImageQualityReport(False, "质量检查失败：图片格式无法解析")

    width, height = rgba.size
    if width < 2 or height < 2:
        return ImageQualityReport(False, "低信息量：图片尺寸过小")

    # 透明像素不是可交付的场景背景。先统计 alpha，再把可见图层合成到白底，
    # 这样透明 PNG 和普通白底 JPG 可以走同一套方差/边缘判断。
    alpha = rgba.getchannel("A")
    alpha_stat = ImageStat.Stat(alpha)
    if alpha_stat.mean[0] < 8:
        return ImageQualityReport(False, "低信息量：图片几乎完全透明")

    white_background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    visible = Image.alpha_composite(white_background, rgba).convert("RGB")

    # 缩小后判断能降低大图计算成本，同时保留家具主体轮廓和场景层次。
    sample = visible.copy()
    sample.thumbnail((96, 96), Image.Resampling.BILINEAR)
    stats = ImageStat.Stat(sample)
    channel_variance = sum(stats.var) / len(stats.var)

    # 使用相邻像素差异作为“是否存在轮廓/阴影/材质”的廉价边缘指标。阈值不宜
    # 过低，否则 JPEG 压缩噪声会把纯色图误判为有内容；也不宜过高，否则低对比
    # 的浅色家具会被拒绝。
    pixels = list(sample.convert("L").tobytes())
    sample_width, sample_height = sample.size
    edge_count = 0
    edge_pairs = 0
    for y in range(sample_height):
        row_start = y * sample_width
        for x in range(sample_width):
            current = pixels[row_start + x]
            if x + 1 < sample_width:
                edge_pairs += 1
                edge_count += abs(current - pixels[row_start + x + 1]) >= 12
            if y + 1 < sample_height:
                edge_pairs += 1
                edge_count += abs(current - pixels[row_start + sample_width]) >= 12
    edge_ratio = edge_count / edge_pairs if edge_pairs else 0.0

    # 纯色或接近纯色的结果同时满足低方差、低边缘信息两个条件。采用“同时满足”
    # 是为了放过极简但确实有主体轮廓的有效图，也避免白底商品图因背景占比高而误杀。
    if channel_variance < 18 and edge_ratio < 0.006:
        return ImageQualityReport(
            False,
            f"低信息量：图片接近纯色（方差={channel_variance:.1f}，边缘={edge_ratio:.3f}）",
        )

    return ImageQualityReport(
        True,
        f"图片包含足够视觉信息（方差={channel_variance:.1f}，边缘={edge_ratio:.3f}）",
    )


async def inspect_generated_image_url(image_url: str) -> ImageQualityReport:
    """下载模型结果并执行质量检查。

    生成服务返回的 URL 通常是短期地址，检查必须在归档之前完成。下载失败也按
    不可交付处理，而不是把未经检查的地址继续交给微信发布；调用方最多重新生成
    一次，第二次仍失败就终止当前任务，防止空白图进入草稿箱。
    """

    normalized_url = str(image_url or "").strip()
    if not normalized_url:
        return ImageQualityReport(False, "质量检查失败：图片地址为空")

    try:
        local_object_key = _local_minio_url_resolver.extract_object_key(normalized_url)
        if local_object_key:
            # ``storage_service`` 使用 MINIO_ENDPOINT（Docker 内为 minio:9000），
            # 不使用 MINIO_PUBLIC_ENDPOINT（通常是宿主机 localhost:9002）。
            # 通过线程执行同步 MinIO SDK，避免阻塞生成图片的异步事件循环。
            content = await asyncio.to_thread(
                storage_service.download_bytes,
                local_object_key,
            )
        else:
            validate_url(normalized_url)
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(30.0, connect=10.0),
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            ) as client:
                response = await client.get(normalized_url)
                response.raise_for_status()
                content = response.content
    except Exception as exc:
        logger.warning("图片质量检查下载失败 url=%s error=%s", normalized_url[:180], exc)
        return ImageQualityReport(False, "质量检查失败：无法下载生成图片")

    # 文章图片通常远小于此限制。提前拒绝异常大响应可以防止质量检查被错误的
    # 上游响应拖垮 Worker；真正的归档服务仍会按自己的响应大小策略再次保护。
    if len(content) > 50 * 1024 * 1024:
        return ImageQualityReport(False, "质量检查失败：图片响应超过 50MB")
    return assess_image_bytes(content)


def append_scene_quality_guard(prompt: str) -> str:
    """向场景提示词追加真实空间背景约束，并保证重复调用不叠加。

    这段约束只在 ERP 图生图入口注入。普通投喂源文章的 HTML/Markdown 格式分析
    不会调用本函数，因此不会污染原有的版式提示词或历史任务内容。
    """

    normalized_prompt = str(prompt or "").strip()
    if _SCENE_GUARD_MARKER in normalized_prompt:
        return normalized_prompt

    guard = (
        f"{_SCENE_GUARD_MARKER}\n"
        "画面必须呈现真实可识别的室内或商业空间，包含真实空间层次、墙面/地面、"
        "自然光影、接触关系和主体投影，让产品明确落在环境中。禁止只输出商品抠图、"
        "透明底、纯白、纯灰或纯色空背景；即使背景规则偏向浅色，也必须保留地面、"
        "环境层次和产品阴影。不要生成空白画布，不要让主体漂浮。"
    )
    return f"{normalized_prompt}\n\n{guard}" if normalized_prompt else guard


def append_low_information_retry_instruction(prompt: str) -> str:
    """为第一次低信息量结果追加一次明确的修复指令。

    标记保证同一提示词重复经过重试编排时不会无限增长。最多一次重试由调用方
    控制，本函数只负责让第二次生成知道第一次失败的具体视觉原因。
    """

    normalized_prompt = str(prompt or "").strip()
    if _LOW_INFORMATION_RETRY_MARKER in normalized_prompt:
        return normalized_prompt
    instruction = (
        f"{_LOW_INFORMATION_RETRY_MARKER}\n"
        "上一次生成结果信息量不足。请重新生成完整场景，明显增加墙面、地面、"
        "家具空间关系、自然阴影和材质纹理，确保主体轮廓清晰且背景绝不是纯色空白。"
    )
    return f"{normalized_prompt}\n\n{instruction}" if normalized_prompt else instruction
