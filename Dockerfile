FROM python:3.12-slim

WORKDIR /opt/reflow

# 先装依赖（利用层缓存），再拷代码
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

# 放在 pip install 之后：版本号变化不会使依赖层缓存失效
ARG VERSION=dev
ENV REFLOW_VERSION=$VERSION

# 数据库与硬更改上传图片都放独立卷，容器升级不丢数据
# 注意：上传目录必须和 DB 同在 /data 卷上，否则重部署后图片随容器销毁、
# 而 DB 仍引用这些文件名 → 取图 404（见 issue #24）
ENV REFLOW_DB=/data/reflow.sqlite
ENV REFLOW_UPLOAD_DIR=/data/uploads
RUN mkdir -p /data
VOLUME /data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
