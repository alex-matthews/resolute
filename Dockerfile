FROM python:3.14-alpine3.24@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92 AS build

COPY --from=ghcr.io/astral-sh/uv:0.11.26@sha256:3d868e555f8f1dbc324afa005066cd11e1053fc4743b9808ca8025283e65efa5 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
RUN uv sync --locked --no-dev --no-editable

FROM python:3.14-alpine3.24@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92

ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/alex-matthews/resolute" \
      org.opencontainers.image.description="Seerr-first 1080p/2160p TV decision engine" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

COPY --from=build /app/.venv /app/.venv

# Identity-agnostic image (home-operations/containers precedent, e.g.
# apps/tautulli): no user is created, nothing is chown'd, and no policy
# file is baked in. Kubernetes owns storage identity (runAsUser/runAsGroup/
# fsGroup — 1032:100 in this cluster) and supplies /data (PVC) and
# /config/policy.yaml (ConfigMap); `nobody:nogroup` is only the default
# for bare `docker run`s. Bytecode is precompiled at build time, so the
# image runs with a read-only rootfs under any arbitrary uid:gid.
# /config and /data exist empty (no chown) so ConfigMap subPath/file
# mounts and PVC mount points have stable targets under kubelet with a
# read-only rootfs, not just under Docker bind mounts.
RUN mkdir -p /config /data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RESOLUTE_DB_PATH=/data/resolute.db \
    RESOLUTE_POLICY_PATH=/config/policy.yaml \
    RESOLUTE_LISTEN_PORT=8080

USER nobody:nogroup
# 8080 main app, 8081 metrics (home-operations org port convention).
EXPOSE 8080 8081

ENTRYPOINT ["resolute"]
CMD ["serve"]
