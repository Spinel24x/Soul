#!/usr/bin/env bash
# postCreate: install Xray-core into ./bin (runs once when the Codespace is built)
set -euo pipefail
cd "$(dirname "$0")/../.."

mkdir -p bin
if [ ! -x bin/xray ]; then
  echo "[*] Downloading Xray-core..."
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

bin/xray version | head -1
echo "[*] Setup complete."
echo "    Next:  bash runtimes/codespace/start.sh"
