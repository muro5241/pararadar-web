FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update --error-on=any && apt-get install -y --no-install-recommends ffmpeg gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 pararadar \
    && useradd --uid 10001 --gid 10001 --no-create-home pararadar
WORKDIR /app
COPY requirements.lock .
RUN --mount=type=secret,id=pip_ca \
    if [ -f /run/secrets/pip_ca ]; then \
      PIP_CERT=/run/secrets/pip_ca python -m pip install --no-cache-dir --require-hashes -r requirements.lock; \
    else \
      python -m pip install --no-cache-dir --require-hashes -r requirements.lock; \
    fi
COPY --chown=10001:10001 app.py settings.py store.py tiktok.py nvidia.py ./
COPY --chown=10001:10001 templates ./templates
COPY --chown=10001:10001 assets ./assets
COPY scripts/entrypoint.sh /usr/local/bin/pararadar-start
RUN chmod 755 /usr/local/bin/pararadar-start
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/pararadar-start"]
