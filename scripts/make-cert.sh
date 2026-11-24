#!/usr/bin/env sh
# Create a SELF-SIGNED TLS certificate for the reverse proxy in ./certs (tls.crt, tls.key).
# Browsers will warn about it; that is expected. Replace the two files with a real certificate
# (Let's Encrypt, your school CA, ...) and restart the proxy: no other change is needed.
#
#   ./scripts/make-cert.sh                     localhost, 127.0.0.1
#   ./scripts/make-cert.sh grader.school.edu 10.0.0.5     add your host name(s) and/or IP(s)
#   ./scripts/make-cert.sh --force ...         overwrite an existing certificate
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT="$ROOT/certs"
FORCE=0
if [ "${1:-}" = "--force" ]; then FORCE=1; shift; fi

if [ -e "$OUT/tls.crt" ] && [ "$FORCE" = 0 ]; then
  echo "certs/tls.crt already exists; use --force to replace it." >&2
  exit 1
fi

SAN="DNS:localhost,IP:127.0.0.1"
CN=localhost
for name in "$@"; do
  case "$name" in
    *[!0-9.]*) SAN="$SAN,DNS:$name" ;;   # contains something other than digits/dots: a host name
    *) SAN="$SAN,IP:$name" ;;
  esac
  [ "$CN" = localhost ] && CN=$name
done

mkdir -p "$OUT"
umask 077
openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 365 \
  -keyout "$OUT/tls.key" -out "$OUT/tls.crt" \
  -subj "/CN=$CN" -addext "subjectAltName=$SAN" 2>/dev/null
chmod 644 "$OUT/tls.crt"
chmod 600 "$OUT/tls.key"  # nginx's master process runs as the container's root and reads it at start-up
echo "Wrote certs/tls.crt and certs/tls.key (valid 365 days) for: $SAN"
