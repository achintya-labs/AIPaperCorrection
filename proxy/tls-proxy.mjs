// Minimal HTTPS front door for the no-container (native) mode: terminates TLS and forwards to the web server.
// No dependencies: Node's own https/http modules only. Replace nginx/Caddy with your own proxy if you prefer.
//
//   TLS_CERT=certs/tls.crt TLS_KEY=certs/tls.key LISTEN_PORT=8443 UPSTREAM_PORT=4321 node proxy/tls-proxy.mjs
//
// It keeps the browser's Host (with port) and sets X-Forwarded-Host/-Proto/-For itself (never appends), which is
// what the web service's cross-site check and the Secure session cookie rely on.
import fs from "node:fs";
import http from "node:http";
import https from "node:https";

const {
  TLS_CERT = "certs/tls.crt",
  TLS_KEY = "certs/tls.key",
  LISTEN_HOST = "0.0.0.0",
  LISTEN_PORT = "8443",
  UPSTREAM_HOST = "127.0.0.1",
  UPSTREAM_PORT = "4321",
} = process.env;

const server = https.createServer(
  { cert: fs.readFileSync(TLS_CERT), key: fs.readFileSync(TLS_KEY), minVersion: "TLSv1.2" },
  (req, res) => {
    const headers = {
      ...req.headers,
      "x-forwarded-proto": "https",
      "x-forwarded-host": req.headers.host ?? "",
      "x-forwarded-for": req.socket.remoteAddress ?? "",
    };
    const upstream = http.request(
      { host: UPSTREAM_HOST, port: Number(UPSTREAM_PORT), method: req.method, path: req.url, headers },
      (up) => {
        res.writeHead(up.statusCode ?? 502, up.headers);
        up.pipe(res);
      },
    );
    upstream.on("error", () => {
      if (!res.headersSent) res.writeHead(502, { "content-type": "text/plain" });
      res.end("Bad gateway: the web server is not answering");
    });
    req.on("aborted", () => upstream.destroy());
    req.pipe(upstream); // streams uploads instead of buffering them
  },
);
server.requestTimeout = 0; // large scans: no overall request timeout
server.listen(Number(LISTEN_PORT), LISTEN_HOST, () =>
  console.log(`https://${LISTEN_HOST}:${LISTEN_PORT} -> http://${UPSTREAM_HOST}:${UPSTREAM_PORT}`),
);
