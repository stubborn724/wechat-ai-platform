"""AI 视频生成服务 — 使用 DashScope 通义万相视频模型直接生成视频"""

import asyncio
import json
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

VIDEO_SUBMIT_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
)
TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
POLL_INTERVAL = 5.0
MAX_POLL_ATTEMPTS = 120  # 最长等 10 分钟（视频生成较慢）


class VideoGenService:
    """使用 DashScope 通义万相视频模型直接生成视频"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.dashscope_api_key

    async def generate_video(
        self,
        prompt: str,
        size: str = "1280*720",
        duration: int = 5,
    ) -> Optional[str]:
        """提交文生视频任务并等待完成

        Args:
            prompt: 视频内容描述
            size: 分辨率 (1280*720 / 720*1280)
            duration: 视频时长（秒）

        Returns:
            视频 URL，失败返回 None
        """
        if not self.api_key:
            logger.warning("No DashScope API key configured for video generation")
            return None

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        body = {
            "model": "wanx2.1-t2v-turbo",
            "input": {"prompt": prompt},
            "parameters": {
                "size": size,
            },
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Step 1: 提交任务
            try:
                resp = await client.post(VIDEO_SUBMIT_URL, headers=headers, json=body)
                if resp.status_code != 200:
                    detail = ""
                    try:
                        detail = json.dumps(resp.json(), ensure_ascii=False)
                    except Exception:
                        detail = resp.text[:500]
                    logger.warning("视频生成请求失败 (HTTP %s): %s", resp.status_code, detail)
                    return None
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as e:
                logger.warning("视频生成请求异常: %s", e)
                return None

            task_id = data.get("output", {}).get("task_id")
            if not task_id:
                msg = f"视频提交响应无 task_id: {data}"
                logger.warning(msg)
                print(f"    [ERROR] {msg}")
                return None

            logger.info("Video task submitted: task_id=%s, prompt='%s'", task_id, prompt[:60])
            print(f"    [INFO] 视频任务已提交: task_id={task_id}")

            # Step 2: 轮询完成
            for attempt in range(MAX_POLL_ATTEMPTS):
                await asyncio.sleep(POLL_INTERVAL)
                try:
                    poll_resp = await client.get(
                        TASK_URL.format(task_id=task_id), headers=headers,
                    )
                    poll_resp.raise_for_status()
                    poll_data = poll_resp.json()
                except httpx.HTTPError as e:
                    print(f"    [POLL] 第{attempt+1}次请求失败: {e}")
                    logger.warning("Poll attempt %d/%d failed: %s", attempt + 1, MAX_POLL_ATTEMPTS, e)
                    continue

                status = poll_data.get("output", {}).get("task_status")
                print(f"    [POLL] 第{attempt+1}次: status={status}")
                logger.debug("Video poll: attempt=%d, status=%s", attempt + 1, status)

                if status == "SUCCEEDED":
                    out = poll_data.get("output", {})
                    url = out.get("video_url", "")
                    if not url:
                        results = out.get("results", [])
                        if results:
                            url = results[0].get("url", "")
                    if url:
                        logger.info("Video generated: %s", url[:80])
                        print(f"    ✅ 视频生成成功")
                        return url
                    print(f"    [ERROR] 任务成功但无结果: {poll_data}")
                    return None
                elif status in ("FAILED", "CANCELED"):
                    msg = poll_data.get("output", {}).get("message", "unknown")
                    logger.warning("Video task %s: %s", status, msg)
                    print(f"    [ERROR] 视频任务 {status}: {msg}")
                    return None

            logger.warning("Video task timed out after %d attempts", MAX_POLL_ATTEMPTS)
            print(f"    [ERROR] 视频生成超时（{MAX_POLL_ATTEMPTS * POLL_INTERVAL}秒）")
            return None


# Singleton
video_gen_service = VideoGenService()
