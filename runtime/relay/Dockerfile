# Supply a reviewed Debian bookworm-slim reference pinned with @sha256: (no mutable default).
ARG DEBIAN_IMAGE=debian:bookworm-slim
ARG DEBIAN_DIGEST
FROM ${DEBIAN_IMAGE}@${DEBIAN_DIGEST} AS build
ARG DEBIAN_IMAGE
ARG DEBIAN_DIGEST
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libssl-dev python3 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY . .
RUN sh build.sh /relay && python3 -I cli_test.py --build-peer /peer \
    && mkdir /provenance \
    && printf '%s\n' "$DEBIAN_IMAGE@$DEBIAN_DIGEST" > /provenance/base-image.txt \
    && cc --version > /provenance/compiler.txt \
    && dpkg-query -W > /provenance/build-packages.txt \
    && find . -type f -print0 | sort -z | xargs -0 sha256sum > /provenance/source-sha256.txt \
    && sha256sum /relay > /provenance/binary-sha256.txt

FROM ${DEBIAN_IMAGE}@${DEBIAN_DIGEST} AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends libssl3 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /usr/share/nfsmw-relay/provenance /var/lib/relay /run/relay \
    && chown 65532:65532 /var/lib/relay /run/relay \
    && chmod 0700 /var/lib/relay /run/relay \
    && dpkg-query -W > /usr/share/nfsmw-relay/provenance/runtime-packages.txt
COPY --from=build /relay /relay
COPY --from=build /provenance /usr/share/nfsmw-relay/provenance
USER 65532:65532
EXPOSE 47800/udp
STOPSIGNAL SIGTERM
ENTRYPOINT ["/relay"]
CMD ["--bind", "127.0.0.1", "--port", "47800", "--near", "32", "--max", "100", "--state-dir", "/var/lib/relay", "--admin-dir", "/run/relay"]

# Test-only image: never used by the installed service.
FROM runtime AS smoke
COPY --from=build /peer /peer

# The default build target is also production; test tooling requires --target smoke.
FROM runtime AS production
