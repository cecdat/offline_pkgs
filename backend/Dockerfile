FROM python:3.9-slim

WORKDIR /app

# 安装 Docker Python 库需要的依赖
RUN pip install --no-cache-dir fastapi uvicorn docker jinja2 python-multipart

# 复制当前目录下所有文件到容器
COPY . /app

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
