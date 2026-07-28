#!/usr/bin/env bash
# Generate configs, start Xray, expose the tunnel port(s), print the subscription,
# then health-check the LIVE tunnel. Re-runnable.
set -euo pipefail
cd "$(dirname "$0")/../.."

PROTOCOLS="${PROTOCOLS:-vless-ws}"     # e.g. "vless-xhttp,vless-httpupgrade,vless-ws,trojan-ws"
XHTTP_MODE="${XHTTP_MODE:-auto}"       # auto | packet-up | stream-up ; packet-up is best vs strict DPI (Iran)
SERVE_SUB="${SERVE_SUB:-1}"            # 1 = also serve subscription.txt over HTTP for easy phone import
SUB_PORT="${SUB_PORT:-9000}"
DOMAIN="${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"

mkdir -p output/_run

echo "[*] Generating configs (protocols: $PROTOCOLS, xhttp-mode: $XHTTP_MODE)"
python3 generator/generate.py --protocols "$PROTOCOLS" --xhttp-mode "$XHTTP_MODE"

echo "[*] (Re)starting Xray"
pkill -f "bin/xray run" 2>/dev/null || true
nohup ./bin/xray run -c output/xray_server.json > output/_run/xray.log 2>&1 &
echo $! > output/_run/xray.pid
sleep 1
if ! kill -0 "$(cat output/_run/xray.pid)" 2>/dev/null; then
  echo "[x] Xray failed to start. Log:"; tail -20 output/_run/xray.log; exit 1
fi
echo "    xray pid $(cat output/_run/xray.pid)"

# Make each endpoint port public on the tunnel.
PORTS=$(python3 -c "import json;print(' '.join(str(e['internal_port']) for e in json.load(open('output/summary.json'))['endpoints']))")
[ "$SERVE_SUB" = "1" ] && PORTS="$PORTS $SUB_PORT"
for p in $PORTS; do
  if command -v gh >/dev/null 2>&1 && [ -n "${CODESPACE_NAME:-}" ]; then
    if gh codespace ports visibility "${p}:public" -c "$CODESPACE_NAME" >/dev/null 2>&1; then
      echo "[*] port $p -> public"
    else
      echo "[!] Could not set port $p public via gh (token scope). Set it manually:"
      echo "    PORTS tab -> right-click port $p -> Port Visibility -> Public"
    fi
  else
    echo "[!] gh/CODESPACE_NAME unavailable. In the PORTS tab set port $p to Public."
  fi
done

echo "[i] IMPORTANT: the tunnel port MUST be Public or clients get GitHub's auth wall"
echo "    and nothing connects. If a check below says 'PORT NOT PUBLIC': open the PORTS"
echo "    tab -> right-click the port -> Port Visibility -> Public (keep protocol = HTTP)."

# Optional: serve the subscription file so a phone can import it by URL.
if [ "$SERVE_SUB" = "1" ]; then
  pkill -f "http.server ${SUB_PORT}" 2>/dev/null || true
  nohup python3 -m http.server "$SUB_PORT" --directory output > output/_run/sub.log 2>&1 &
  echo $! > output/_run/sub.pid
  if [ -n "${CODESPACE_NAME:-}" ]; then
    echo "[*] Subscription URL (import this in your app):"
    echo "    https://${CODESPACE_NAME}-${SUB_PORT}.${DOMAIN}/subscription.txt"
    echo "    (WARNING: public & unauthenticated - anyone with the URL sees your configs)"
  fi
fi

echo
echo "================= SHARE LINKS ================="
cat output/share_links.txt
echo "============== SUBSCRIPTION (base64) =========="
cat output/subscription.txt
echo

echo "[*] Waiting for the tunnel to settle, then health-checking the LIVE endpoints..."
sleep 6
python3 healthcheck/check.py --mode remote || \
  echo "[!] Remote check failed. Ensure the port(s) are Public, then re-run: python3 healthcheck/check.py --mode remote"
