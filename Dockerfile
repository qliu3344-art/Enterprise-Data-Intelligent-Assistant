# syntax=docker/dockerfile:1
# ============================================================
# 企业数据智能助手
#   启动整套（PG + 建表 + 应用）：docker compose up -d
#
# 三个设计点：
#   1) 前端单独一个构建阶段，运行镜像里只有 dist，不带 node_modules
#   2) 先 COPY requirements.txt 再 COPY 源码 —— 改代码不会触发重装依赖
#   3) 应用以非 root 用户运行
# ============================================================

# ---------- 阶段一：构建前端 ----------
FROM node:22-alpine AS web
WORKDIR /src
# 只拷锁文件：依赖没变时这一层直接命中缓存
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- 阶段二：运行时 ----------
FROM python:3.10-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl 只给 HEALTHCHECK 用
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# torch 先装 CPU 版：sentence-transformers 默认拉的是 CUDA 版（2GB+），
# 这套服务只在 CPU 上跑 BGE 推理，装 CUDA 版纯属白占镜像
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch \
 && pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY --from=web /src/dist ./frontend/dist

# 模型缓存目录必须先在镜像里建好并 chown：命名卷首次挂载时会继承这一层，
# 否则挂进来的是 root 目录，appuser 写不进去
RUN useradd --create-home --uid 1000 appuser \
 && mkdir -p data logs /home/appuser/.cache/huggingface \
 && chown -R appuser:appuser /app /home/appuser
USER appuser

EXPOSE 8002

# start-period 给足：首次启动要下 BGE 模型（~100MB）并建索引
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8002/health || exit 1

# Windows 上那个 --loop app.main:selector_loop_factory 在这里不需要：
# Linux 默认就是 SelectorEventLoop，psycopg 的异步模式可以直接用
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
