"""DashScope Wanxiang (通义万相) text-to-image service.

Uses the async task API: submit -> poll -> return image URL.
"""

import asyncio
import logging
import time
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

WANXIANG_SUBMIT_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
)
WANXIANG_IMAGE_EDIT_SUBMIT_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/image2image/image-synthesis"
)
WANXIANG_TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
POLL_INTERVAL = 2.0
MAX_POLL_ATTEMPTS = 60
WANXIANG_MODEL = "wanx2.1-t2i-turbo"
# 图生图使用同一异步服务接口，但必须显式传入产品参考图，才能让生成结果以 ERP
# 商品为主体而不是仅依据产品名称重新臆造外观。
# 经过实际 API 探测，`wanx2.1-i2i-turbo` 已不可用；改用百炼当前可识别的
# 图像编辑模型，仍通过参考图约束保留 ERP 产品主体。
WANXIANG_IMAGE_TO_IMAGE_MODEL = "wanx2.1-imageedit"


def _summarize(value: object, limit: int) -> str:
    """压缩诊断日志字段，保留故障线索并避免输出敏感或过长内容。"""
    return " ".join(str(value or "").split())[:limit]


class WanxiangImageService:
    """Generate images using Alibaba Cloud DashScope Wanxiang API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.dashscope_api_key

    async def generate_image(
        self,
        prompt: str,
        size: str = "1024*1024",
        n: int = 1,
        no_text: bool = True,
        reference_image_url: Optional[str] = None,
    ) -> Optional[str]:
        """Submit an image generation task and wait for completion.

        Args:
            prompt: Text description of the image to generate.
            size: Image size (e.g. "1024*1024", "720*1280").
            n: Number of images to generate (1-4).
            no_text: Whether to instruct the model not to generate text.
            reference_image_url: 可访问的产品原图 URL。存在时使用图生图模型，
                保留产品主体并仅变化背景、场景和氛围。

        Returns:
            URL of the first generated image, or None on failure.
        """
        if not self.api_key:
            logger.warning("No DashScope API key configured for image generation")
            return None

        started_at = time.monotonic()

        # 如果未明确说要文字，默认添加无文字指令
        if no_text and "文字" not in prompt and "文本" not in prompt:
            prompt = f"{prompt}。不要包含任何文字或文本标签，纯图像。"

        # Check if Wanxiang is explicitly enabled in settings
        if not getattr(settings, 'wanxiang_enabled', True):
            logger.debug("Wanxiang image generation is disabled in settings")
            return None

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        if reference_image_url:
            # 万相 2.1 图像编辑使用独立 image2image 接口。description_edit 适合不依赖
            # 蒙版的指令编辑；低强度优先保留 ERP 家具主体、材质和颜色，只替换背景。
            model = WANXIANG_IMAGE_TO_IMAGE_MODEL
            submit_url = WANXIANG_IMAGE_EDIT_SUBMIT_URL
            body = {
                "model": model,
                "input": {
                    "function": "description_edit",
                    "prompt": prompt,
                    "base_image_url": reference_image_url,
                },
                "parameters": {"n": n, "strength": 0.35},
            }
        else:
            model = WANXIANG_MODEL
            submit_url = WANXIANG_SUBMIT_URL
            body = {
                "model": model,
                "input": {"prompt": prompt},
                "parameters": {"size": size, "n": n},
            }
        logger.info(
            "Wanxiang submit model=%s size=%s count=%s prompt_len=%d prompt=%r",
            model, size, n, len(prompt), _summarize(prompt, 240),
        )
        print(
            f"  [万相] 提交 model={model} size={size} count={n} "
            f"prompt_len={len(prompt)}"
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Step 1: Submit task
            try:
                resp = await client.post(submit_url, headers=headers, json=body)
                if resp.status_code != 200:
                    try:
                        err_detail = resp.json()
                    except Exception:
                        err_detail = resp.text[:500]
                    logger.warning(
                        "Wanxiang submit failed status=%s response=%r",
                        resp.status_code, _summarize(err_detail, 800),
                    )
                    print(
                        f"  [万相] 提交失败 status={resp.status_code}: "
                        f"{_summarize(err_detail, 800)}"
                    )
                    return None
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as e:
                err_text = repr(e) if not str(e) else str(e)
                logger.warning(
                    "Wanxiang submit network_error type=%s message=%r",
                    type(e).__name__, _summarize(err_text, 800),
                )
                print(f"  ⚠️ 通义万相请求失败: {err_text}")
                return None
            except Exception as e:
                logger.warning(
                    "Wanxiang submit unexpected_error type=%s message=%r",
                    type(e).__name__, _summarize(repr(e), 800),
                )
                print(f"  ⚠️ 通义万相请求未知异常: {repr(e)}")
                return None

            task_id = data.get("output", {}).get("task_id")
            if not task_id:
                logger.warning("Wanxiang submit missing_task_id response=%r", _summarize(data, 800))
                return None

            logger.info("Wanxiang task submitted task_id=%s", task_id)
            print(f"  [万相] 任务已提交 task_id={task_id}")

            # Step 2: Poll for completion
            for attempt in range(MAX_POLL_ATTEMPTS):
                await asyncio.sleep(POLL_INTERVAL)
                try:
                    poll_resp = await client.get(
                        WANXIANG_TASK_URL.format(task_id=task_id),
                        headers=headers,
                    )
                    poll_resp.raise_for_status()
                    poll_data = poll_resp.json()
                except httpx.HTTPError as e:
                    err_text = repr(e) if not str(e) else str(e)
                    logger.warning(
                        "Wanxiang poll request failed task_id=%s attempt=%d/%d type=%s message=%r",
                        task_id, attempt + 1, MAX_POLL_ATTEMPTS,
                        type(e).__name__, _summarize(err_text, 800),
                    )
                    continue

                task_status = poll_data.get("output", {}).get("task_status")
                logger.debug("Wanxiang poll: attempt=%d, status=%s", attempt + 1, task_status)

                if task_status == "SUCCEEDED":
                    results = poll_data.get("output", {}).get("results", [])
                    if results:
                        url = results[0].get("url", "")
                        logger.info(
                            "Wanxiang task succeeded task_id=%s elapsed_ms=%d image_url=%r",
                            task_id, int((time.monotonic() - started_at) * 1000), _summarize(url, 160),
                        )
                        print(
                            f"  [万相] 生成成功 task_id={task_id} "
                            f"elapsed_ms={int((time.monotonic() - started_at) * 1000)}"
                        )
                        return url
                    logger.warning("Wanxiang task succeeded_without_result task_id=%s", task_id)
                    return None
                elif task_status in ("FAILED", "CANCELED"):
                    err_msg = poll_data.get("output", {}).get("message", "unknown error")
                    logger.warning(
                        "Wanxiang task failed task_id=%s status=%s elapsed_ms=%d message=%r",
                        task_id, task_status, int((time.monotonic() - started_at) * 1000),
                        _summarize(err_msg, 800),
                    )
                    print(
                        f"  [万相] 任务失败 task_id={task_id} status={task_status}: "
                        f"{_summarize(err_msg, 800)}"
                    )
                    return None

            logger.warning(
                "Wanxiang task timed_out task_id=%s attempts=%d elapsed_ms=%d",
                task_id, MAX_POLL_ATTEMPTS, int((time.monotonic() - started_at) * 1000),
            )
            print(f"  [万相] 任务超时 task_id={task_id} attempts={MAX_POLL_ATTEMPTS}")
            return None


# Singleton for easy import
wanxiang_service = WanxiangImageService()
