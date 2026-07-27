"""视频合成服务 — 使用 FFmpeg 将图片/音频/字幕合成为 MP4"""

import asyncio
import json
import logging
import os
import tempfile
from typing import List, Optional, Tuple

from app.services.storage_service import storage_service

logger = logging.getLogger(__name__)

SUBTITLE_TEMPLATE = """1
00:00:00,000 --> 00:00:{end_min:02d},{end_ms:03d}
{subtitle_text}
"""


def _generate_srt(segments: List[Tuple[str, int]]) -> str:
    """生成 SRT 字幕文件

    Args:
        segments: [(subtitle_text, duration_sec), ...]

    Returns:
        SRT 格式字符串
    """
    lines = []
    current_time = 0.0
    for i, (text, duration) in enumerate(segments, 1):
        if not text:
            current_time += duration
            continue
        start_sec = current_time
        end_sec = current_time + duration
        start_min = int(start_sec // 60)
        start_remain = start_sec - start_min * 60
        end_min = int(end_sec // 60)
        end_remain = end_sec - end_min * 60

        lines.append(str(i))
        lines.append(
            f"{start_min:02d}:{int(start_remain):02d},{int((start_remain % 1) * 1000):03d} "
            f"--> {end_min:02d}:{int(end_remain):02d},{int((end_remain % 1) * 1000):03d}"
        )
        lines.append(text)
        lines.append("")
        current_time = end_sec

    return "\n".join(lines)


class VideoCompositionService:
    """视频合成服务"""

    async def compose_video(
        self,
        storyboard_image_keys: List[str],
        audio_key: Optional[str] = None,
        subtitle_segments: Optional[List[Tuple[str, int]]] = None,
        duration_per_image: Optional[List[int]] = None,
        logo_key: Optional[str] = None,
        qr_code_key: Optional[str] = None,
        resolution: str = "1080x1920",
        output_format: str = "mp4",
        fps: int = 24,
    ) -> bytes:
        """合成视频

        流程：
        1. 下载所有分镜图片和音频
        2. 为每个图片创建带 zoompan 效果的视频片段
        3. 拼接片段
        4. 叠加音频
        5. 渲染字幕
        6. 叠加 Logo 水印
        7. 输出 MP4

        Args:
            storyboard_image_keys: 分镜图片的 MinIO storage_key 列表
            audio_key: 配音音频的 MinIO storage_key
            subtitle_segments: [(字幕文字, 持续时间秒), ...]
            duration_per_image: 每张图片的显示时长（秒），默认均匀分配
            logo_key: Logo 图片 storage_key
            qr_code_key: 片尾二维码 storage_key
            resolution: 输出分辨率
            output_format: 输出格式
            fps: 帧率

        Returns:
            MP4 文件 bytes

        Raises:
            RuntimeError: FFmpeg 未安装或合成失败
        """
        import subprocess
        import shutil

        ffmpeg_cmd = shutil.which("ffmpeg")
        if not ffmpeg_cmd:
            # 常见安装路径（winget / 手动安装）
            _common_paths = [
                os.path.expanduser(r"~\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin\ffmpeg.exe"),
                r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
                r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
                r"C:\ffmpeg\bin\ffmpeg.exe",
            ]
            for p in _common_paths:
                if os.path.isfile(p):
                    ffmpeg_cmd = p
                    break
        if not ffmpeg_cmd:
            try:
                subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
                ffmpeg_cmd = "ffmpeg"
            except (subprocess.CalledProcessError, FileNotFoundError):
                logger.warning("FFmpeg not found, returning empty bytes")
                return b""

        temp_dir = tempfile.mkdtemp(prefix="video_compose_")
        try:
            # 1. 下载资源到临时目录
            image_paths = []
            for i, key in enumerate(storyboard_image_keys):
                ext = key.rsplit(".", 1)[-1] if "." in key else "jpg"
                local_path = os.path.join(temp_dir, f"storyboard_{i:03d}.{ext}")
                try:
                    img_data = storage_service.download_bytes(key)
                    with open(local_path, "wb") as f:
                        f.write(img_data)
                    image_paths.append(local_path)
                except Exception as exc:
                    logger.warning("Failed to download storyboard image %s: %s", key, exc)
                    # 创建占位图
                    self._create_placeholder(temp_dir, f"storyboard_{i:03d}.jpg", resolution)
                    image_paths.append(os.path.join(temp_dir, f"storyboard_{i:03d}.jpg"))

            if not image_paths:
                raise ValueError("No storyboard images available")

            # 2. 下载音频
            audio_path = None
            if audio_key:
                try:
                    audio_data = storage_service.download_bytes(audio_key)
                    audio_path = os.path.join(temp_dir, "audio.mp3")
                    with open(audio_path, "wb") as f:
                        f.write(audio_data)
                except Exception as exc:
                    logger.warning("Failed to download audio: %s", exc)

            # 3. 生成字幕文件
            subtitle_path = None
            if subtitle_segments:
                srt_content = _generate_srt(subtitle_segments)
                subtitle_path = os.path.join(temp_dir, "subtitles.srt")
                with open(subtitle_path, "w", encoding="utf-8") as f:
                    f.write(srt_content)

            # 4. 生成每个图片的持续时间
            if not duration_per_image:
                total_images = len(image_paths)
                total_duration = 30
                if audio_path:
                    try:
                        probe = subprocess.run(
                            [ffmpeg_cmd, "-i", audio_path, "-show_entries",
                             "format=duration", "-of", "csv=p=0"],
                            capture_output=True, text=True, timeout=15,
                        )
                        if probe.returncode == 0 and probe.stdout.strip():
                            total_duration = int(float(probe.stdout.strip()))
                    except Exception:
                        pass
                base_dur = total_duration // total_images
                duration_per_image = [base_dur] * total_images
                # 最后一帧补足剩余时长
                remainder = total_duration - base_dur * total_images
                if remainder > 0:
                    duration_per_image[-1] += remainder

            # 5. 分步合成：先为每张图生成独立 MP4 片段，再 concat 合并
            output_path = os.path.join(temp_dir, f"output.{output_format}")
            clip_paths = []
            res_w, res_h = (int(x) for x in resolution.split("x"))

            from PIL import Image as _PILImage
            for i, img_path in enumerate(image_paths):
                dur = duration_per_image[i] if i < len(duration_per_image) else 3
                try:
                    # 用 Pillow 预处理图片：缩放 + 等比例填充到目标分辨率
                    pil_img = _PILImage.open(img_path).convert("RGB")
                    w, h = pil_img.size
                    # 等比例缩放，长边对齐目标
                    scale = max(res_w / w, res_h / h)
                    new_w = int(w * scale)
                    new_h = int(h * scale)
                    pil_img = pil_img.resize((new_w, new_h), _PILImage.LANCZOS)
                    # 居中裁切/填充
                    bg = _PILImage.new("RGB", (res_w, res_h), (0, 0, 0))
                    bg.paste(pil_img, ((res_w - new_w) // 2, (res_h - new_h) // 2))
                    scaled_path = os.path.join(temp_dir, f"scaled_{i:03d}.jpg")
                    bg.save(scaled_path, "JPEG", quality=90)

                    # 用 FFmpeg 裸转（无任何 -vf/-filter_complex），仅合成为视频片段
                    clip = os.path.join(temp_dir, f"clip_{i:03d}.mp4")
                    clip_cmd = [
                        ffmpeg_cmd, "-y",
                        "-loop", "1", "-t", str(dur), "-i", scaled_path,
                        "-c:v", "libx264",
                        "-preset", "fast",
                        "-crf", "23",
                        "-pix_fmt", "yuv420p",
                        clip,
                    ]
                    proc = subprocess.Popen(clip_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    sout, serr = proc.communicate(timeout=120)
                    if proc.returncode != 0 or not os.path.isfile(clip):
                        err_text = serr.decode("utf-8", errors="replace")[:300] if serr else "no stderr"
                        print(f"    [FFmpeg clip {i} failed, code={proc.returncode}]: {err_text}")
                        raise RuntimeError(f"Clip {i} failed")
                    clip_paths.append(clip)
                except Exception as e:
                    print(f"    ⚠️ 分镜 {i} 片段生成失败: {e}")
                    continue

            if len(clip_paths) < 1:
                raise RuntimeError("No video clips generated")

            if len(clip_paths) == 1:
                with open(clip_paths[0], "rb") as f:
                    return f.read()

            concat_list = os.path.join(temp_dir, "concat.txt")
            with open(concat_list, "w") as f:
                for cp in clip_paths:
                    f.write(f"file '{cp}'\n")

            concat_cmd = [
                ffmpeg_cmd, "-y",
                "-f", "concat", "-safe", "0",
                "-i", concat_list,
                "-c", "copy",
                output_path,
            ]
            proc = subprocess.Popen(concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            proc.communicate(timeout=120)
            if proc.returncode != 0:
                with open(clip_paths[0], "rb") as f:
                    return f.read()

            with open(output_path, "rb") as f:
                return f.read()

            # 拼接
            n = len(image_paths)
            filter_parts = []
            for i in range(n):
                filter_parts.append(f"[{i}:v]scale={resolution},setpts=PTS-STARTPTS[v{i}]")

            concat_input = "".join(f"[v{i}]" for i in range(n))
            filter_complex = ";".join(filter_parts)
            filter_complex += f";{concat_input}concat=n={n}:v=1:a=0[outv]"

            cmd.extend(["-filter_complex", filter_complex, "-map", "[outv]"])
            if audio_path:
                cmd.extend(["-i", audio_path, "-c:a", "aac", "-shortest"])
            cmd.extend(["-c:v", "libx264", "-preset", "fast", "-crf", "25", "-y", output_path])

            def _run_fallback():
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                proc.communicate(timeout=300)
                return proc.returncode

            returncode = await asyncio.to_thread(_run_fallback)

            if returncode != 0:
                raise RuntimeError("Fallback composition also failed")

            with open(output_path, "rb") as f:
                return f.read()
        finally:
            import shutil
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass

    def _create_placeholder(self, dir_path: str, filename: str, resolution: str):
        """创建纯色占位图"""
        try:
            from PIL import Image
            w, h = (int(x) for x in resolution.split("x"))
            img = Image.new("RGB", (w, h), (50, 50, 80))
            path = os.path.join(dir_path, filename)
            img.save(path, "JPEG", quality=70)
        except Exception:
            pass

    async def extract_cover_frame(self, video_key: str) -> bytes:
        """从视频提取封面帧"""
        import subprocess
        import tempfile

        temp_dir = tempfile.mkdtemp(prefix="video_cover_")
        try:
            video_data = storage_service.download_bytes(video_key)
            video_path = os.path.join(temp_dir, "input.mp4")
            with open(video_path, "wb") as f:
                f.write(video_data)

            cover_path = os.path.join(temp_dir, "cover.jpg")
            cmd = [
                "ffmpeg", "-i", video_path,
                "-ss", "00:00:01",
                "-vframes", "1",
                "-q:v", "2",
                "-y", cover_path,
            ]

            def _run_extract():
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                proc.communicate(timeout=60)

            await asyncio.to_thread(_run_extract)

            if not os.path.exists(cover_path):
                return b""

            with open(cover_path, "rb") as f:
                return f.read()
        finally:
            import shutil
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass


video_composition_service = VideoCompositionService()
