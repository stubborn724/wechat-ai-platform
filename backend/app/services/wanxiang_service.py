"""DashScope Wanxiang (通义万相) text-to-image service.

Uses the async task API: submit -> poll -> return image URL.
"""

import logging
import time
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

WANXIANG_SUBMIT_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
)
WANXIANG_TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
POLL_INTERVAL = 2.0
MAX_POLL_ATTEMPTS = 60


class WanxiangImageService:
    """Generate images using Alibaba Cloud DashScope Wanxiang API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.dashscope_api_key

    async def generate_image(
        self,
        prompt: str,
        size: str = "1024*1024",
        n: int = 1,
    ) -> Optional[str]:
        """Submit an image generation task and wait for completion.

        Args:
            prompt: Text description of the image to generate.
            size: Image size (e.g. "1024*1024", "720*1280").
            n: Number of images to generate (1-4).

        Returns:
            URL of the first generated image, or None on failure.
        """
        if not self.api_key:
            logger.warning("No DashScope API key configured for image generation")
            return None

        # Check if Wanxiang is explicitly enabled in settings
        if not getattr(settings, 'wanxiang_enabled', True):
            logger.debug("Wanxiang image generation is disabled in settings")
            return None

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        body = {
            "model": "wanx2.1-t2i-turbo",
            "input": {"prompt": prompt},
            "parameters": {"size": size, "n": n},
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Step 1: Submit task
            try:
                resp = await client.post(WANXIANG_SUBMIT_URL, headers=headers, json=body)
                if resp.status_code != 200:
                    try:
                        err_detail = resp.json()
                    except Exception:
                        err_detail = resp.text[:500]
                    logger.warning("通义万相图片生成请求失败 (HTTP %s): %s", resp.status_code, err_detail)
                    return None
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as e:
                logger.warning("通义万相图片生成请求异常: %s", e)
                return None

            task_id = data.get("output", {}).get("task_id")
            if not task_id:
                logger.debug("No task_id in Wanxiang response: %s", data)
                return None

            logger.info("Wanxiang task submitted: task_id=%s, prompt='%s'", task_id, prompt[:80])

            # Step 2: Poll for completion
            for attempt in range(MAX_POLL_ATTEMPTS):
                time.sleep(POLL_INTERVAL)
                try:
                    poll_resp = await client.get(
                        WANXIANG_TASK_URL.format(task_id=task_id),
                        headers=headers,
                    )
                    poll_resp.raise_for_status()
                    poll_data = poll_resp.json()
                except httpx.HTTPError as e:
                    logger.debug("Wanxiang poll attempt %d/%d failed: %s",
                                   attempt + 1, MAX_POLL_ATTEMPTS, e)
                    continue

                task_status = poll_data.get("output", {}).get("task_status")
                logger.debug("Wanxiang poll: attempt=%d, status=%s", attempt + 1, task_status)

                if task_status == "SUCCEEDED":
                    results = poll_data.get("output", {}).get("results", [])
                    if results:
                        url = results[0].get("url", "")
                        logger.info("Wanxiang image generated: %s", url[:80])
                        return url
                    logger.debug("Wanxiang succeeded but no results returned")
                    return None
                elif task_status in ("FAILED", "CANCELED"):
                    err_msg = poll_data.get("output", {}).get("message", "unknown error")
                    logger.debug("Wanxiang task %s: %s", task_status, err_msg)
                    return None

            logger.debug("Wanxiang task timed out after %d attempts", MAX_POLL_ATTEMPTS)
            return None


# Singleton for easy import
wanxiang_service = WanxiangImageService()
