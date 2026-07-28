#!/usr/bin/env bash
# One-shot installer for a real VPS (Ubuntu/Debian). Run as root.
# Generates VLESS + TCP + REALITY (Vision) - the most censorship-resistant setup -
# installs Xray as a systemd service, and prints the client link.
#
#   sudo PORT=443 DEST=www.microsoft.com:443 SNI=www.microsoft.com bash runtimes/vps/setup.sh
set -euo pipefail
cd "$(dirname "$0")/../.."

PORT="${PORT:-443}"
DEST="${DEST:-www.microsoft.com:443}"
SNI="${SNI:-www.microsoft.com}"
PUBIP="${PUBIP:-$(curl -fsS https://api.ipify.org 2>/dev/null || echo YOUR_SERVER_IP)}"

command -v unzip >/dev/null || { apt-get update -y && apt-get install -y unzip curl python3; }

mkdir -p bin
if [ ! -x bin/xray ]; then
  case "$(uname -m)" in
    x86_64|amd64)  ASSET="Xray-linux-64.zip" ;;
    aarch64|arm64) ASSET="Xray-linux-arm64-v8a.zip" ;;
    *) echo "unsupported arch: $(uname -m)"; exit 1 ;;
  esac
  curl -fL --retry 3 -o /tmp/xray.zip \
    "https://github.com/XTLS/Xray-core/releases/latest/download/${ASSET}"
  unzip -o /tmp/xray.zip -d bin/ xray geoip.dat geosite.dat
  chmod +x bin/xray
fi

python3 generator/generate.py --host "$PUBIP" --public-port "$PORT" --reality \
  --reality-dest "$DEST" --reality-sni "$SNI" --xray-bin ./bin/xray

# validate before installing
./bin/xray run -test -c output/xray_server.json

install -m 0755 bin/xray /usr/local/bin/xray
install -d /usr/local/etc/xray
install -m 0644 output/xray_server.json /usr/local/etc/xray/config.json
install -m 0644 bin/geoip.dat bin/geosite.dat /usr/local/etc/xray/ 2>/dev/null || true

cat >/etc/systemd/system/xray.service <<'UNIT'
[Unit]
Description=Xray Service
After=network.target nss-lookup.target
[Service]
User=root
ExecStart=/usr/local/bin/xray run -c /usr/local/etc/xray/config.json
Restart=on-failure
RestartSec=3
LimitNOFILE=1048576
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now xray
systemctl --no-pager status xray | head -5 || true

echo
echo "================= CLIENT LINK ================="
cat output/share_links.txt
echo "==============================================="
echo "Config: /usr/local/etc/xray/config.json   Service: systemctl status xray"
