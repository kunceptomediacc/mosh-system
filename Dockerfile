FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd --gid 10001 mosh \
    && useradd --uid 10001 --gid mosh --no-create-home --shell /usr/sbin/nologin mosh \
    && mkdir -p /var/data \
    && chown mosh:mosh /var/data

COPY apps/mosh-core/requirements-identity.txt /tmp/requirements-identity.txt
COPY apps/mosh-core /app/apps/mosh-core
COPY apps/mosh-ui /app/apps/mosh-ui
COPY deploy/render-entrypoint.sh /usr/local/bin/render-entrypoint

RUN pip install --no-cache-dir -r /tmp/requirements-identity.txt \
    && pip install --no-cache-dir /app/apps/mosh-core \
    && chmod 0755 /usr/local/bin/render-entrypoint

EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','10000')+'/healthz', timeout=3)"

ENTRYPOINT ["/usr/local/bin/render-entrypoint"]
