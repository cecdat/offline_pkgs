ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive

# 安装基础工具 (支持 HTTPS 和 GPG)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

VOLUME /output
ENTRYPOINT ["/app/entrypoint.sh"]
