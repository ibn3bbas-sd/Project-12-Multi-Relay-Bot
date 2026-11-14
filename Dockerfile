# Image for Huawei Cloud CCE (k8s/relay-bot.yaml), built by .github/workflows/build-push-dockerhub.yml.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    XDG_CACHE_HOME=/tmp/.cache \
    DENO_DIR=/tmp/.cache/deno \
    TZ=Asia/Riyadh

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home relaybot

COPY requirements.txt .
RUN pip install -r requirements.txt && deno --version | head -1

COPY src/ ./src/
COPY config/config.yaml ./config/

RUN mkdir -p downloads logs data && chown -R 10001:10001 /app

USER 10001

CMD ["python", "-m", "src.main"]
