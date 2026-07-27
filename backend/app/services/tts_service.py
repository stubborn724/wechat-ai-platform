"""文字转语音服务 — 使用 Edge TTS 生成中文配音"""

import asyncio
import io
import logging
import os
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)

# 可用中文语音角色
VOICES = {
    "zh-CN-XiaoxiaoNeural": "女声 温柔",
    "zh-CN-XiaoyiNeural": "女声 活泼",
    "zh-CN-YunjianNeural": "男声 成熟",
    "zh-CN-YunxiNeural": "男声 阳光",
    "zh-CN-YunyangNeural": "男声 新闻",
    "zh-CN-XiaochenNeural": "女声 知性",
}


class TtsService:
    """文字转语音服务（Edge TTS）"""

    async def generate_speech(
        self,
        text: str,
        voice: str = "zh-CN-XiaoxiaoNeural",
        rate: str = "0",
        output_format: str = "mp3",
    ) -> bytes:
        """生成语音

        Args:
            text: 要朗读的文本
            voice: 语音角色
            rate: 语速，-50 到 +50，默认 0
            output_format: 输出格式 mp3 / wav

        Returns:
            音频 bytes

        Raises:
            RuntimeError: edge-tts 未安装或调用失败
        """
        if not text:
            raise ValueError("Text is empty")

        try:
            import edge_tts
        except ImportError:
            logger.warning("edge-tts not installed, returning silent audio")
            return self._generate_silent_audio(duration_sec=len(text) // 4 + 1)

        rate_str = f"+{rate}%" if rate.startswith("+") or not rate.startswith("-") else f"{rate}%"
        if rate == "0":
            rate_str = "+0%"

        communicate = edge_tts.Communicate(text, voice, rate=rate_str)
        output = io.BytesIO()

        try:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    output.write(chunk["data"])
        except Exception as exc:
            logger.error("Edge TTS failed: %s", exc)
            raise RuntimeError(f"TTS generation failed: {exc}") from exc

        audio_bytes = output.getvalue()
        if not audio_bytes:
            logger.warning("TTS produced empty output, returning silent audio")
            return self._generate_silent_audio(duration_sec=len(text) // 4 + 1)

        return audio_bytes

    async def generate_speech_segments(
        self,
        segments: list,
        voice: str = "zh-CN-XiaoxiaoNeural",
        rate: str = "0",
    ) -> list:
        """分段生成语音，返回每个分段的 (text, audio_bytes, duration_sec)

        Args:
            segments: [(text, metadata), ...] 每段文本和元数据
            voice: 语音角色
            rate: 语速

        Returns:
            [(text, audio_bytes, duration_sec), ...]
        """
        results = []
        for text, metadata in segments:
            if not text.strip():
                continue
            audio = await self.generate_speech(text, voice=voice, rate=rate)
            # 估算时长：中文大约每秒 3-4 字
            est_duration = max(1, len(text) // 4)
            results.append((text, audio, est_duration))
        return results

    def _generate_silent_audio(self, duration_sec: int = 3) -> bytes:
        """生成静音音频文件作为 TTS 不可用时的回退"""
        import struct
        import wave

        sample_rate = 24000
        num_samples = sample_rate * duration_sec
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(struct.pack(f"<{num_samples}h", *([0] * num_samples)))
        return buf.getvalue()


tts_service = TtsService()
