#!/usr/bin/env python3
"""
Xray Config Forge - core generator (stdlib only).

Generates, from a small set of inputs:
  * an Xray SERVER config (output/xray_server.json)
  * client share links (output/share_links.txt)
  * a base64 subscription (output/subscription.txt)  <- import this in v2rayNG / Hiddify / v2rayN / Streisand
  * a ready desktop client with SOCKS+HTTP inbound (output/client_remote.json)
  * a machine-readable summary (output/summary.json) used by the health checker

Two profiles:
  edge     -> protocols over ws / xhttp, TLS terminated at the edge (GitHub Codespaces
              tunnel, a CDN, Cloudflare, ...). Xray itself listens plain HTTP.
  reality  -> VLESS + TCP/XHTTP + REALITY for a real VPS (best censorship resistance).

This file has NO third-party dependencies. For REALITY it shells out to `xray x25519`.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote, urlencode


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def rand_path(prefix: str) -> str:
    """A random, hard-to-guess ws/xhttp path like /forge-a1b2c3d4."""
    token = secrets.token_hex(4)
    prefix = prefix.strip("/") or "forge"
    return f"/{prefix}-{token}"


def gen_uuid() -> str:
    return str(uuid.uuid4())


def gen_password(nbytes: int = 16) -> str:
    return secrets.token_urlsafe(nbytes)


def gen_reality_keys(xray_bin: str) -> tuple[str, str]:
    """Return (private_key, public_key) using `xray x25519`."""
    out = subprocess.run([xray_bin, "x25519"], capture_output=True, text=True, check=True).stdout
    priv = pub = ""
    for line in out.splitlines():
        low = line.lower()
        # xray prints "PrivateKey: xxx" / "Password: xxx" and "Password:"/"PublicKey: yyy"
        if "private" in low:
            priv = line.split(":", 1)[1].strip()
        elif "public" in low or "password" in low:
            pub = line.split(":", 1)[1].strip()
    if not priv or not pub:
        raise RuntimeError(f"could not parse `xray x25519` output:\n{out}")
    return priv, pub


# --------------------------------------------------------------------------- #
# spec model
# --------------------------------------------------------------------------- #
# Each enabled endpoint is described by a spec dict with keys:
#   name        e.g. "vless-ws"
#   protocol    "vless" | "trojan"
#   transport   "ws" | "xhttp" | "tcp"
#   port        internal listen port (int)
#   path        ws/xhttp path (str) - unused for tcp/reality
#   cred        uuid (vless) or password (trojan)
#   security    "tls" | "none" | "reality"

def build_specs(args) -> list[dict]:
    specs: list[dict] = []
    base = args.internal_base_port

    if args.reality:
        # single VLESS + TCP + REALITY endpoint (classic Vision) for a VPS.
        specs.append({
            "name": "vless-reality",
            "protocol": "vless",
            "transport": "tcp",
            "port": args.public_port,           # REALITY listens directly on the public port
            "path": "",
            "cred": gen_uuid(),
            "security": "reality",
        })
        return specs

    wanted = [p.strip() for p in args.protocols.split(",") if p.strip()]
    security = "tls" if args.edge_tls else "none"
    for i, name in enumerate(wanted):
        try:
            proto, transport = name.split("-", 1)
        except ValueError:
            raise SystemExit(f"bad protocol spec '{name}', expected e.g. vless-ws / trojan-ws / vless-xhttp")
        if proto not in ("vless", "trojan"):
            raise SystemExit(f"unsupported protocol '{proto}' in '{name}'")
        if transport not in ("ws", "xhttp"):
            raise SystemExit(f"unsupported transport '{transport}' in '{name}' (edge profile allows ws/xhttp)")
        cred = gen_uuid() if proto == "vless" else gen_password()
        specs.append({
            "name": name,
            "protocol": proto,
            "transport": transport,
            "port": base + i,
            "path": rand_path(args.path_prefix),
            "cred": cred,
            "security": security,
        })
    return specs


# --------------------------------------------------------------------------- #
# server config
# --------------------------------------------------------------------------- #
def server_stream(spec: dict, reality_ctx: dict | None) -> dict:
    t = spec["transport"]
    stream: dict = {"network": t}
    if t == "ws":
        stream["wsSettings"] = {"path": spec["path"]}
    elif t == "xhttp":
        stream["xhttpSettings"] = {"path": spec["path"], "mode": "auto"}
    elif t == "tcp":
        stream["tcpSettings"] = {}

    if spec["security"] == "reality" and reality_ctx:
        stream["security"] = "reality"
        stream["realitySettings"] = {
            "show": False,
            "dest": reality_ctx["dest"],
            "xver": 0,
            "serverNames": [reality_ctx["sni"]],
            "privateKey": reality_ctx["private_key"],
            "shortIds": [reality_ctx["short_id"]],
        }
    # edge profile: no TLS on xray itself (edge terminates it) -> security stays absent
    return stream


def server_inbound(spec: dict, reality_ctx: dict | None) -> dict:
    if spec["protocol"] == "vless":
        client = {"id": spec["cred"]}
        if spec["security"] == "reality":
            client["flow"] = "xtls-rprx-vision"
        settings = {"clients": [client], "decryption": "none"}
    else:  # trojan
        settings = {"clients": [{"password": spec["cred"]}]}

    return {
        "tag": spec["name"],
        "listen": "0.0.0.0",
        "port": spec["port"],
        "protocol": spec["protocol"],
        "settings": settings,
        "streamSettings": server_stream(spec, reality_ctx),
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
    }


def build_server_config(specs: list[dict], reality_ctx: dict | None) -> dict:
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [server_inbound(s, reality_ctx) for s in specs],
        "outbounds": [
            {"protocol": "freedom", "tag": "direct"},
            {"protocol": "blackhole", "tag": "block"},
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {"type": "field", "protocol": ["bittorrent"], "outboundTag": "block"},
            ],
        },
    }


# --------------------------------------------------------------------------- #
# client config (used for desktop + health checks)
# --------------------------------------------------------------------------- #
def client_stream(spec: dict, host: str, tls: bool, reality_ctx: dict | None) -> dict:
    t = spec["transport"]
    stream: dict = {"network": t}
    if t == "ws":
        stream["wsSettings"] = {"path": spec["path"], "host": host}
    elif t == "xhttp":
        stream["xhttpSettings"] = {"path": spec["path"], "host": host, "mode": "auto"}
    elif t == "tcp":
        stream["tcpSettings"] = {}

    if spec["security"] == "reality" and reality_ctx:
        stream["security"] = "reality"
        stream["realitySettings"] = {
            "serverName": reality_ctx["sni"],
            "fingerprint": "chrome",
            "publicKey": reality_ctx["public_key"],
            "shortId": reality_ctx["short_id"],
            "spiderX": "/",
        }
    elif tls:
        stream["security"] = "tls"
        stream["tlsSettings"] = {"serverName": host, "fingerprint": "chrome", "allowInsecure": False}
    return stream


def client_outbound(spec: dict, host: str, port: int, tls: bool, reality_ctx: dict | None, tag="proxy") -> dict:
    if spec["protocol"] == "vless":
        user = {"id": spec["cred"], "encryption": "none"}
        if spec["security"] == "reality":
            user["flow"] = "xtls-rprx-vision"
        settings = {"vnext": [{"address": host, "port": port, "users": [user]}]}
    else:
        settings = {"servers": [{"address": host, "port": port, "password": spec["cred"]}]}
    return {
        "tag": tag,
        "protocol": spec["protocol"],
        "settings": settings,
        "streamSettings": client_stream(spec, host, tls, reality_ctx),
    }


def build_client_config(spec: dict, host: str, port: int, tls: bool,
                        reality_ctx: dict | None, socks_port: int, http_port: int | None) -> dict:
    inbounds = [{
        "tag": "socks-in",
        "listen": "127.0.0.1",
        "port": socks_port,
        "protocol": "socks",
        "settings": {"udp": True},
    }]
    if http_port:
        inbounds.append({
            "tag": "http-in",
            "listen": "127.0.0.1",
            "port": http_port,
            "protocol": "http",
        })
    return {
        "log": {"loglevel": "warning"},
        "inbounds": inbounds,
        "outbounds": [
            client_outbound(spec, host, port, tls, reality_ctx),
            {"protocol": "freedom", "tag": "direct"},
        ],
    }


# --------------------------------------------------------------------------- #
# share links
# --------------------------------------------------------------------------- #
def share_link(spec: dict, host: str, port: int, tls: bool, reality_ctx: dict | None, remark: str) -> str:
    params: dict[str, str] = {"type": spec["transport"]}
    if spec["protocol"] == "vless":
        params["encryption"] = "none"

    if spec["security"] == "reality" and reality_ctx:
        params.update({
            "security": "reality",
            "sni": reality_ctx["sni"],
            "fp": "chrome",
            "pbk": reality_ctx["public_key"],
            "sid": reality_ctx["short_id"],
            "flow": "xtls-rprx-vision",
        })
    elif tls:
        params.update({"security": "tls", "sni": host, "fp": "chrome"})
        if spec["transport"] in ("ws", "xhttp"):
            params["host"] = host
    else:
        params["security"] = "none"
        if spec["transport"] in ("ws", "xhttp"):
            params["host"] = host

    if spec["transport"] in ("ws", "xhttp"):
        params["path"] = spec["path"]
    if spec["transport"] == "xhttp":
        params["mode"] = "auto"

    query = urlencode(params, quote_via=quote)
    scheme = "vless" if spec["protocol"] == "vless" else "trojan"
    return f"{scheme}://{spec['cred']}@{host}:{port}?{query}#{quote(remark)}"


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Xray Config Forge - generate + wire Xray configs")
    p.add_argument("--host", default=os.environ.get("FORGE_HOST", ""),
                   help="public host/domain clients connect to (e.g. name-8080.app.github.dev)")
    p.add_argument("--public-port", type=int, default=int(os.environ.get("FORGE_PUBLIC_PORT", "443")),
                   help="port clients connect to (edge TLS usually 443)")
    p.add_argument("--internal-base-port", type=int,
                   default=int(os.environ.get("FORGE_INTERNAL_PORT", "8080")),
                   help="first internal port xray listens on (increments per protocol)")
    p.add_argument("--protocols", default=os.environ.get("FORGE_PROTOCOLS", "vless-ws"),
                   help="comma list: vless-ws,trojan-ws,vless-xhttp")
    p.add_argument("--path-prefix", default=os.environ.get("FORGE_PATH_PREFIX", "forge"))
    p.add_argument("--remark-prefix", default=os.environ.get("FORGE_REMARK", "forge"))
    tls = p.add_mutually_exclusive_group()
    tls.add_argument("--edge-tls", dest="edge_tls", action="store_true", default=True,
                     help="clients use TLS to the edge (default; port 443)")
    tls.add_argument("--no-edge-tls", dest="edge_tls", action="store_false",
                     help="no TLS (plain) - only for local loopback testing")
    p.add_argument("--codespace-name", default=os.environ.get("CODESPACE_NAME", ""),
                   help="GitHub Codespaces name; enables per-port *.app.github.dev hosts, port 443, edge TLS")
    p.add_argument("--codespace-domain",
                   default=os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev"))
    p.add_argument("--reality", action="store_true", help="REALITY profile for a VPS (VLESS+TCP+Vision)")
    p.add_argument("--reality-dest", default=os.environ.get("FORGE_REALITY_DEST", "www.microsoft.com:443"))
    p.add_argument("--reality-sni", default=os.environ.get("FORGE_REALITY_SNI", "www.microsoft.com"))
    p.add_argument("--xray-bin", default=os.environ.get("FORGE_XRAY_BIN", ""),
                   help="path to xray binary (needed for REALITY key generation)")
    p.add_argument("--outdir", default=os.environ.get("FORGE_OUTDIR", ""),
                   help="output directory (default: <repo>/output)")
    return p.parse_args(argv)


def resolve_paths(args):
    repo = Path(__file__).resolve().parent.parent
    outdir = Path(args.outdir) if args.outdir else repo / "output"
    outdir.mkdir(parents=True, exist_ok=True)
    xray_bin = args.xray_bin
    if not xray_bin:
        cand = repo / "bin" / "xray"
        xray_bin = str(cand) if cand.exists() else "xray"
    return repo, outdir, xray_bin


def endpoint_target(args, spec: dict) -> tuple[str, int, bool]:
    """Return (host, public_port, edge_tls) for one endpoint.

    In Codespaces every forwarded port is exposed on its own subdomain
    ``{name}-{port}.app.github.dev`` served over HTTPS (443), so each endpoint
    gets a distinct host derived from its internal port.
    """
    if args.codespace_name:
        host = f"{args.codespace_name}-{spec['port']}.{args.codespace_domain}"
        return host, 443, True
    return args.host, args.public_port, args.edge_tls


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.host and not args.codespace_name:
        print("ERROR: --host is required (or set FORGE_HOST / CODESPACE_NAME).", file=sys.stderr)
        return 2

    repo, outdir, xray_bin = resolve_paths(args)

    reality_ctx = None
    if args.reality:
        priv, pub = gen_reality_keys(xray_bin)
        reality_ctx = {
            "dest": args.reality_dest,
            "sni": args.reality_sni,
            "private_key": priv,
            "public_key": pub,
            "short_id": secrets.token_hex(4),
        }

    specs = build_specs(args)

    # server
    server = build_server_config(specs, reality_ctx)
    (outdir / "xray_server.json").write_text(json.dumps(server, indent=2))

    # links + subscription + summary  (per-endpoint host/port/tls)
    links: list[str] = []
    summary_endpoints = []
    for s in specs:
        host, pub_port, tls = endpoint_target(args, s)
        remark = f"{args.remark_prefix}-{s['name']}"
        link = share_link(s, host, pub_port, tls, reality_ctx, remark)
        links.append(link)
        summary_endpoints.append({
            "name": s["name"], "protocol": s["protocol"], "transport": s["transport"],
            "internal_port": s["port"], "host": host, "public_port": pub_port, "edge_tls": tls,
            "path": s["path"], "security": s["security"], "cred": s["cred"], "remark": remark,
        })

    (outdir / "share_links.txt").write_text("\n".join(links) + "\n")
    sub_b64 = base64.b64encode(("\n".join(links) + "\n").encode()).decode()
    (outdir / "subscription.txt").write_text(sub_b64 + "\n")

    # ready desktop client (primary endpoint) with SOCKS 10808 + HTTP 10809
    primary = specs[0]
    p_host, p_port, p_tls = endpoint_target(args, primary)
    desktop = build_client_config(primary, p_host, p_port, p_tls,
                                  reality_ctx, socks_port=10808, http_port=10809)
    (outdir / "client_remote.json").write_text(json.dumps(desktop, indent=2))

    summary = {
        "host": p_host,
        "public_port": p_port,
        "edge_tls": p_tls,
        "profile": "reality" if args.reality else "edge",
        "codespace": bool(args.codespace_name),
        "reality": {k: reality_ctx[k] for k in ("dest", "sni", "public_key", "short_id")} if reality_ctx else None,
        "endpoints": summary_endpoints,
    }
    (outdir / "summary.json").write_text(json.dumps(summary, indent=2))

    # human summary
    print("=" * 64)
    print(" Xray Config Forge - generated")
    print("=" * 64)
    print(f" profile        : {summary['profile']}  (codespace={summary['codespace']})")
    for e in summary_endpoints:
        print(f"  - {e['name']:<14} {e['host']}:{e['public_port']}  internal:{e['internal_port']}  path {e['path'] or '(tcp)'}")
    print(f" server config  : {outdir / 'xray_server.json'}")
    print(f" subscription   : {outdir / 'subscription.txt'}")
    print(f" share links    : {outdir / 'share_links.txt'}")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
