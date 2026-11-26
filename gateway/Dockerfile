# Caddy with the Coraza WAF module (OWASP Core Rule Set). The WAF runs first, then Tinyauth's forward_auth, then the app.
# The builder always runs on the machine doing the build (--platform=$BUILDPLATFORM) and CROSS-COMPILES for the target (Go does this
# natively), instead of running the Go compiler under QEMU emulation for another architecture, which took ~20 minutes in CI.
FROM --platform=$BUILDPLATFORM docker.io/library/caddy:2-builder AS builder
ARG TARGETOS
ARG TARGETARCH

# Cache Go modules (and sumdb lookups) across builds
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    GOOS=$TARGETOS GOARCH=$TARGETARCH xcaddy build \
    --with github.com/corazawaf/coraza-caddy/v2

FROM docker.io/library/caddy:2

COPY --from=builder /usr/bin/caddy /usr/bin/caddy
COPY gateway/Caddyfile /etc/caddy/Caddyfile
