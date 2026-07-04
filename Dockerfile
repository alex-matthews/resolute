FROM python:3.14-alpine3.24@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92 AS build

COPY --from=ghcr.io/astral-sh/uv:0.11.24@sha256:99ea34acedc870ba4ad11a1f540a1c04267c9f30aadc465a94406f52dfda2c36 /uv /usr/local/bin/uv

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
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RESOLUTE_DB_PATH=/data/resolute.db \
    RESOLUTE_POLICY_PATH=/config/policy.yaml \
    RESOLUTE_LISTEN_PORT=8130

USER nobody:nogroup
EXPOSE 8130

ENTRYPOINT ["resolute"]
CMD ["serve"]
