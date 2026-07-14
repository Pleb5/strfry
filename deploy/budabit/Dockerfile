# syntax=docker/dockerfile:1.7

FROM alpine:3.22@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce AS build

ARG STRFRY_REPOSITORY=https://github.com/Pleb5/strfry.git
ARG STRFRY_COMMIT=b80cda3a812af1b662223edad47eb70b053508b6

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

RUN git clone "$STRFRY_REPOSITORY" . \
    && git checkout --detach "$STRFRY_COMMIT" \
    && git submodule update --init \
    && make setup-golpe \
    && make -j2

FROM alpine:3.22@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce

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

RUN chmod 0555 /usr/local/bin/strfry /usr/local/lib/strfry/write-policy.py

USER 10001:10001
WORKDIR /var/lib/strfry

EXPOSE 7777

ENTRYPOINT ["/sbin/tini", "--", "/usr/local/bin/strfry"]
CMD ["relay"]
