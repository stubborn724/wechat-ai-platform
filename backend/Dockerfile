FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖。
# Noto CJK 用于文章图片署名的中文字体；如果容器缺少中文字体，Pillow 会退回
# 固定尺寸的默认位图字体，导致代码设置的大字号在微信预览中仍然变成细小灰点。
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
