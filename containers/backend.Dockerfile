FROM python:3.13.18-slim-bookworm@sha256:f3dac913ab53b78c64f26d5518fcd7043d5a1a46522d6a493b598c3ed12f62a6 AS dependencies
WORKDIR /build
COPY containers/requirements-runtime.lock ./requirements.lock
RUN python -m pip install --no-cache-dir --no-compile --require-hashes --only-binary=:all: --no-deps --target=/runtime -r requirements.lock \
    && PYTHONPATH=/runtime python -m pip check

FROM python:3.13.18-slim-bookworm@sha256:f3dac913ab53b78c64f26d5518fcd7043d5a1a46522d6a493b598c3ed12f62a6 AS runtime
ENV PYTHONPATH=/runtime:/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1
WORKDIR /app
COPY --from=dependencies /runtime /runtime
COPY support_agent /app/support_agent
RUN rm -rf /usr/local/lib/python3.13/site-packages/pip* /usr/local/bin/pip* /usr/local/include /root/.cache \
    && mkdir -p /var/lib/rag && chown 10001:10001 /var/lib/rag && chmod 700 /var/lib/rag
USER 10001:10001
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:8000/health/ready', timeout=2).close()"]
ENTRYPOINT ["python", "-m", "support_agent.production"]
