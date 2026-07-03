FROM python:3.12-slim AS build

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim

COPY --from=build /install /usr/local
COPY config/policy.example.yaml /config/policy.yaml

ENV TVD_DB_PATH=/data/tv-decider.db \
    TVD_POLICY_PATH=/config/policy.yaml \
    TVD_LISTEN_PORT=8130

RUN useradd -r -u 1032 -g users tvdecider && mkdir -p /data && chown tvdecider:users /data
USER tvdecider
VOLUME /data
EXPOSE 8130

ENTRYPOINT ["tv-decider"]
CMD ["serve"]
