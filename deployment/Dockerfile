FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /site
COPY huanchang_server.py hc_readiness.py ./
COPY outputs/huanchang-v1 ./outputs/huanchang-v1
RUN mkdir -p /data/media && chown -R 10001:10001 /data /site
USER 10001:10001
EXPOSE 8790
VOLUME ["/data"]
CMD ["python", "huanchang_server.py", "--host", "0.0.0.0", "--port", "8790", "--db", "/data/huanchang.sqlite3", "--media", "/data/media", "--secure-cookie"]
