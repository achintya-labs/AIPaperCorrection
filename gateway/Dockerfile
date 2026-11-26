# Caddy with the Coraza WAF module (OWASP Core Rule Set). The WAF runs first, then Tinyauth's forward_auth, then the app.
FROM docker.io/library/caddy:2-builder AS builder

# Cache Go modules (and sumdb lookups) across builds
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    xcaddy build \
    --with github.com/corazawaf/coraza-caddy/v2

FROM docker.io/library/caddy:2

COPY --from=builder /usr/bin/caddy /usr/bin/caddy
COPY gateway/Caddyfile /etc/caddy/Caddyfile
