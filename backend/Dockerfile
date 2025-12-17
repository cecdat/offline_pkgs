FROM python:3.9-slim

WORKDIR /app

# 1. 先复制依赖文件
COPY requirements.txt .

# 2. 安装依赖 (使用官方源)
RUN pip install --no-cache-dir -r requirements.txt

# 3. 复制源码
COPY . /app

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]