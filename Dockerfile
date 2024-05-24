# 使用官方的 Python 镜像作为基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装 Poetry
RUN pip install poetry

# 将项目的 pyproject.toml 和 poetry.lock 文件复制到容器中
COPY pyproject.toml poetry.lock* /app/

# 安装依赖项
RUN poetry config virtualenvs.create false \
    && poetry install --no-dev --no-interaction --no-ansi

# 复制项目代码到容器中
COPY . /app

# 运行命令
CMD ["poetry", "run", "python", "-m", "twitter_user_tweet_crawler"]
