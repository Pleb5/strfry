# syntax=docker/dockerfile:1.7

FROM alpine:3.22@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce AS build

# Build the reviewed checkout, not a legacy remote core with a newer Python
# plugin. Initialize its submodules first. Record the resulting image digest.
ARG SOURCE_REVISION=local-unreleased

WORKDIR /build

RUN apk add --no-cache \
      ca-certificates \
      flatbuffers-dev \
      g++ \
      git \
      libsecp256k1-dev \
      libtool \
      linux-headers \
      lmdb-dev \
      make \
      openssl-dev \
      perl \
      pkgconf \
      zlib-dev \
      zstd-dev

COPY Makefile golpe.yaml ./
COPY src ./src
COPY golpe ./golpe
COPY external ./external
RUN test -f src/apps/relay/ReadAdmission.h \
    && test -f golpe/external/hoytech-cpp/README.md \
    && make -j2

FROM alpine:3.22@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce
ARG SOURCE_REVISION=local-unreleased
LABEL org.opencontainers.image.revision=$SOURCE_REVISION

RUN apk add --no-cache \
      ca-certificates \
      flatbuffers \
      libb2 \
      libcrypto3 \
      libsecp256k1 \
      libssl3 \
      libstdc++ \
      lmdb \
      python3 \
      tini \
      zlib \
      zstd-libs \
    && addgroup -S -g 10001 strfry \
    && adduser -S -D -H -u 10001 -G strfry strfry

COPY --from=build /build/strfry /usr/local/bin/strfry
COPY deploy/budabit/write-policy.py /usr/local/lib/strfry/write-policy.py
COPY deploy/budabit/read-policy.py /usr/local/lib/strfry/read-policy.py
COPY deploy/budabit/audit.py /usr/local/lib/strfry/audit.py
COPY deploy/budabit/check-read-control.py /usr/local/lib/strfry/check-read-control.py
COPY deploy/budabit/start-relay.sh /usr/local/lib/strfry/start-relay.sh
COPY deploy/budabit/policy /usr/local/lib/strfry/policy

RUN chmod 0555 /usr/local/bin/strfry /usr/local/lib/strfry/write-policy.py /usr/local/lib/strfry/audit.py \
    /usr/local/lib/strfry/check-read-control.py /usr/local/lib/strfry/start-relay.sh /usr/local/lib/strfry/read-policy.py \
    && find /usr/local/lib/strfry/policy -type d -exec chmod 0555 {} + \
    && find /usr/local/lib/strfry/policy -type f -exec chmod 0444 {} + \
    && rm -rf /usr/local/lib/strfry/policy/__pycache__ /usr/local/lib/strfry/policy/budabit/__pycache__

USER 10001:10001
WORKDIR /var/lib/strfry

EXPOSE 7777

ENTRYPOINT ["/sbin/tini", "--", "/usr/local/lib/strfry/start-relay.sh"]
CMD ["relay"]
