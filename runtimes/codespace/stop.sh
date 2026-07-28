#!/usr/bin/env bash
# Stop Xray and the subscription server.
cd "$(dirname "$0")/../.."
[ -f output/_run/xray.pid ] && kill "$(cat output/_run/xray.pid)" 2>/dev/null || true
[ -f output/_run/sub.pid ]  && kill "$(cat output/_run/sub.pid)"  2>/dev/null || true
pkill -f "bin/xray run" 2>/dev/null || true
pkill -f "http.server"  2>/dev/null || true
echo "stopped."
