# SPDX-License-Identifier: MIT
# cartolex's app as a container: `cartolex api` on one project volume.
#
#   docker build -f deploy/Dockerfile -t cartolex .
#   docker run --rm -v cartolex-project:/project cartolex init /project --name "My map" --field "My field"
#   docker run -d --name cartolex --read-only --tmpfs /tmp -p 127.0.0.1:8000:8000 \
#       -v cartolex-project:/project --stop-timeout 30 cartolex
#
# See docs/hosting.md: the host names, the launch link, a reverse proxy for HTTPS.

FROM python:3.12-slim AS app

# Install the language models of the extraction (pinned, with their hashes); 0 skips them.
ARG WITH_MODELS=1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/cartolex
COPY pyproject.toml README.md LICENSE ./
COPY cartolex ./cartolex
COPY tools/requirements-models.txt ./requirements-models.txt
RUN pip install . \
    && if [ "$WITH_MODELS" = "1" ]; then pip install --require-hashes -r requirements-models.txt; fi \
    && rm -rf /opt/cartolex/cartolex /root/.cache

# A user without a login and without a home of its own; every writable place is a mount:
# /project (the volume) and /tmp (a tmpfs with a read-only root filesystem).
RUN useradd --system --uid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin cartolex \
    && mkdir -p /project && chown cartolex /project
ENV HOME=/tmp \
    MPLCONFIGDIR=/tmp/matplotlib \
    NUMBA_CACHE_DIR=/tmp/numba
USER cartolex
WORKDIR /project
VOLUME ["/project"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]

ENTRYPOINT ["cartolex"]
CMD ["api", "/project", "--host", "0.0.0.0", "--port", "8000", "--data-dir", "/tmp/cartolex"]
