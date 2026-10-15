FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --gid 65532 gmd \
    && useradd --uid 65532 --gid 65532 --home-dir /nonexistent \
       --no-create-home --shell /usr/sbin/nologin gmd

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --requirement /app/requirements.txt

COPY src /app/src
COPY seed /app/seed
COPY scripts/healthcheck.py /app/scripts/healthcheck.py

RUN mkdir -p /data /tmp/gmd \
    && chown -R 65532:65532 /data /tmp/gmd /app

USER 65532:65532

ENTRYPOINT ["python", "-m", "gmd"]
CMD ["stats"]
