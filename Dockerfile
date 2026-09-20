FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && groupadd --gid 10001 solin \
    && useradd --uid 10001 --gid 10001 --no-create-home solin \
    && mkdir /app/data && chown 10001:10001 /app/data
COPY --chown=10001:10001 engine/ ./engine/
COPY --chown=10001:10001 bot.py .
USER 10001:10001
VOLUME ["/app/data"]
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 CMD ["python", "-m", "engine.healthcheck"]
CMD ["python", "bot.py"]
