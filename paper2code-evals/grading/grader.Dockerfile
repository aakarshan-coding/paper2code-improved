# Build context: benchmarks/frontier-evals/project  (see README.md)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.13.9
COPY common /opt/project/common
COPY paperbench /opt/project/paperbench
WORKDIR /opt/project/paperbench
RUN uv sync --frozen
ENV PAPERBENCH_DATA_DIR=/data
