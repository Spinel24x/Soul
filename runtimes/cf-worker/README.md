# Cloudflare Worker runtime (durable, ToS-safe)

The **recommended** always-on option. Unlike Codespaces, Cloudflare Workers are
built to serve public traffic, so this won't get your account flagged.

## Deploy

```bash
cd runtimes/cf-worker
npm i -g wrangler            # or use npx
wrangler login
wrangler secret put UUID     # paste a UUID (generate one with: python3 -c "import uuid;print(uuid.uuid4())")
wrangler deploy
```

You'll get `https://xray-forge-worker.<subdomain>.workers.dev`.

## Client link

VLESS over WebSocket, TLS, port 443, path `/`, host = your worker domain, uuid = your secret.

Generate a matching subscription/link (then set the uuid to your Worker secret):

```bash
python3 generator/generate.py --host xray-forge-worker.<subdomain>.workers.dev \
  --public-port 443 --protocols vless-ws --path-prefix "" --remark-prefix cf
```

> A custom domain routed through Cloudflare (orange cloud) is more robust than the
> default `*.workers.dev` in some networks. `worker.js` is the community-standard
> minimal VLESS-over-WS relay, provided as-is - deploy and verify on your own account.
