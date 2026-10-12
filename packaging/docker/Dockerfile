# ============================================================
#  多阶段轻量跨架构镜像构建 (linux/amd64, linux/arm64)
# ============================================================

# ---------- 阶段 1: 构建/安装依赖 ----------
FROM python:3.12-slim AS builder

WORKDIR /app

# 安装系统依赖 (Pillow 需要的库)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libjpeg62-turbo \
        libfreetype6 \
        && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖到独立目录
COPY requirements-docker.txt /app/requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---------- 阶段 2: 运行时 ----------
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RUN_MODE=lan \
    XHS_OUTPUT_DIR=/output \
    XHS_WEB_PORT=5088

RUN apt-get update && apt-get install -y --no-install-recommends \
        libjpeg62-turbo \
        libfreetype6 \
        && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 app

COPY --from=builder /install /usr/local

COPY core_engine /app/core_engine
COPY web_frontend /app/web_frontend
COPY home_edition /app/home_edition

RUN mkdir -p /output && chown -R app:app /app /output

USER app

VOLUME ["/output"]
EXPOSE 5088

# lan 模式启动统一 Web 服务,归档目录指向挂载的 /output
CMD ["python", "web_frontend/server.py", "--mode", "lan", "--host", "0.0.0.0", "--port", "5088", "--output-dir", "/output"]
