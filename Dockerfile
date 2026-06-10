FROM python:3.12-slim

WORKDIR /opt/reflow

# 先装依赖（利用层缓存），再拷代码
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

# 数据库放独立卷，容器升级不丢数据
ENV REFLOW_DB=/data/reflow.sqlite
RUN mkdir -p /data
VOLUME /data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
