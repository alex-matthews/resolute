FROM python:3.14-alpine3.24@sha256:05b2b8b732ecd268fee8727a369f936f022d1321b59befd13c30ede22769dcdc AS build

COPY --from=ghcr.io/astral-sh/uv:0.12.10@sha256:2bb3ebca0a796a155094a27773d290c4b074572e6107f171d88d086682fd2500 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
RUN uv sync --locked --no-dev --no-editable

FROM python:3.14-alpine3.24@sha256:05b2b8b732ecd268fee8727a369f936f022d1321b59befd13c30ede22769dcdc

ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/alex-matthews/resolute" \
      org.opencontainers.image.description="Seerr-first 1080p/2160p TV decision engine" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

COPY --from=build /app/.venv /app/.venv

# The runtime installs nothing: pip/setuptools/wheel (and pip's vendored
# libraries) are the image's main CVE surface and serve no purpose here —
# uv built the venv at build time. ensurepip goes too so nothing can
# resurrect pip in a running container.
# Identity-agnostic image (home-operations/containers precedent, e.g.
# apps/tautulli): no user is created, nothing is chown'd, and no household
# file is baked in. Kubernetes owns storage identity (runAsUser/runAsGroup/
# fsGroup — 1032:100 in this cluster) and supplies /data (PVC) and
# /config/household.md (Secret; it names household members — ADR-0003);
# `nobody:nogroup` is only the default
# for bare `docker run`s. Bytecode is precompiled at build time, so the
# image runs with a read-only rootfs under any arbitrary uid:gid.
# /config and /data exist empty (no chown) so ConfigMap subPath/file
# mounts and PVC mount points have stable targets under kubelet with a
# read-only rootfs, not just under Docker bind mounts.
RUN rm -rf /usr/local/lib/python3.14/site-packages/pip* \
           /usr/local/lib/python3.14/site-packages/setuptools* \
           /usr/local/lib/python3.14/site-packages/wheel* \
           /usr/local/lib/python3.14/ensurepip \
           /usr/local/bin/pip* \
    && mkdir -p /config /data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RESOLUTE_DB_PATH=/data/resolute.db \
    RESOLUTE_HOUSEHOLD_POLICY_PATH=/config/household.md

# Numeric uid:gid (= nobody:nogroup) so hosts and Kubernetes runAsNonRoot
# checks can resolve it without the image's /etc/passwd (DL3066).
USER 65534:65534
# 8080 main app, 8081 metrics (home-operations org port convention).
EXPOSE 8080 8081

ENTRYPOINT ["resolute"]
CMD ["serve"]
