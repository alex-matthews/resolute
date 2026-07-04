FROM python:3.14-slim@sha256:b877e50bd90de10af8d82c57a022fc2e0dc731c5320d762a27986facfc3355c1 AS build

COPY --from=ghcr.io/astral-sh/uv:0.11.24@sha256:99ea34acedc870ba4ad11a1f540a1c04267c9f30aadc465a94406f52dfda2c36 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
RUN uv sync --locked --no-dev --no-editable

FROM python:3.14-slim@sha256:b877e50bd90de10af8d82c57a022fc2e0dc731c5320d762a27986facfc3355c1

ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/alex-matthews/resolute" \
      org.opencontainers.image.description="Seerr-first 1080p/2160p TV decision engine" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

COPY --from=build /app/.venv /app/.venv
COPY config/policy.example.yaml /config/policy.yaml

ENV PATH="/app/.venv/bin:$PATH" \
    RESOLUTE_DB_PATH=/data/resolute.db \
    RESOLUTE_POLICY_PATH=/config/policy.yaml \
    RESOLUTE_LISTEN_PORT=8130

RUN useradd -r -u 1032 -g users resolute && mkdir -p /data && chown resolute:users /data
USER resolute
VOLUME /data
EXPOSE 8130

ENTRYPOINT ["resolute"]
CMD ["serve"]
