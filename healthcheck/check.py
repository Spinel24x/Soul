#!/usr/bin/env python3
"""
Health checker - proves a generated config is "healthy AND connected".

It launches Xray and pushes a real request THROUGH the proxy:

  local  (default): start the SERVER (output/xray_server.json), then for every
                    endpoint start a matching CLIENT that dials 127.0.0.1 with
                    TLS off, and curl an internet test URL through its SOCKS port.
                    Proves the generated protocol/transport/credentials actually
                    carry traffic end to end - independent of any tunnel.

  remote           : build a CLIENT from summary.json pointing at the PUBLIC host
                    (e.g. the live Codespaces tunnel) and test that. Run this from
                    inside the Codespace (or anywhere) once the port is public.

Exit code 0 = all endpoints passed, 1 = at least one failed.
No third-party deps. Requires `curl` on PATH.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# reuse the exact builders the generator uses
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "generator"))
import generate  # noqa: E402

TEST_URLS = [
    "https://www.gstatic.com/generate_204",   # returns 204
    "https://cp.cloudflare.com/generate_204",  # returns 204
    "https://www.google.com/generate_204",
]
GOOD_CODES = {"200", "204"}


def resolve_xray(cli_bin: str) -> str:
    if cli_bin:
        return cli_bin
    cand = REPO / "bin" / "xray"
    return str(cand) if cand.exists() else "xray"


def spec_from_endpoint(ep: dict, *, local: bool) -> dict:
    """Rebuild a generator 'spec' from a summary endpoint."""
    return {
        "name": ep["name"],
        "protocol": ep["protocol"],
        "transport": ep["transport"],
        "port": ep["internal_port"] if local else ep["public_port"],
        "path": ep.get("path", ""),
        "cred": ep["cred"],
        "security": "none" if local else ep["security"],
        "xhttp_mode": ep.get("xhttp_mode", "auto"),
    }


def http_probe(host: str) -> tuple[str, str]:
    """Plain HTTPS GET to the tunnel root. Used to tell 'port is private' (GitHub
    auth wall) apart from 'reached the app'. Returns (http_code, redirect_url)."""
    try:
        r = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code} %{redirect_url}",
             "--max-time", "15", f"https://{host}/"],
            capture_output=True, text=True, timeout=20,
        )
        code, _, redir = r.stdout.strip().partition(" ")
        return code.strip(), redir.strip()
    except subprocess.TimeoutExpired:
        return "000", ""


def classify_probe(code: str, redir: str) -> str:
    if code in ("", "000"):
        return "unreachable"      # codespace stopped / wrong host / DNS
    if code in ("301", "302", "401", "403") or "github" in (redir or "").lower():
        return "private"          # GitHub auth wall -> port is NOT public
    return "public"               # reached the edge/app (xray may answer 400/404 to a plain GET)


def reality_ctx_from_summary(summary: dict) -> dict | None:
    r = summary.get("reality")
    if not r:
        return None
    return {
        "dest": r["dest"], "sni": r["sni"],
        "private_key": "", "public_key": r["public_key"], "short_id": r["short_id"],
    }


def wait_port(host: str, port: int, timeout: float = 8.0) -> bool:
    import socket
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def curl_through(socks_port: int, url: str, timeout: int = 15) -> str:
    try:
        r = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "-x", f"socks5h://127.0.0.1:{socks_port}", "--max-time", str(timeout), url],
            capture_output=True, text=True, timeout=timeout + 5,
        )
        return r.stdout.strip()
    except subprocess.TimeoutExpired:
        return "timeout"


def start_xray(xray_bin: str, cfg_path: Path, log_path: Path) -> subprocess.Popen:
    log = open(log_path, "w")
    return subprocess.Popen([xray_bin, "run", "-c", str(cfg_path)], stdout=log, stderr=log)


def probe(socks_port: int) -> tuple[bool, str]:
    for url in TEST_URLS:
        for _ in range(3):
            code = curl_through(socks_port, url)
            if code in GOOD_CODES:
                return True, f"{code} via {url}"
            time.sleep(1.0)
    return False, f"last={code}"


def run(argv=None) -> int:
    p = argparse.ArgumentParser(description="Xray Config Forge - health checker")
    p.add_argument("--mode", choices=["local", "remote"], default="local")
    p.add_argument("--summary", default=str(REPO / "output" / "summary.json"))
    p.add_argument("--server", default=str(REPO / "output" / "xray_server.json"))
    p.add_argument("--xray-bin", default="")
    args = p.parse_args(argv)

    xray_bin = resolve_xray(args.xray_bin)
    summary = json.loads(Path(args.summary).read_text())
    endpoints = summary["endpoints"]
    reality_ctx = reality_ctx_from_summary(summary)
    local = args.mode == "local"

    tmp = REPO / "output" / "_healthcheck"
    tmp.mkdir(parents=True, exist_ok=True)

    procs: list[subprocess.Popen] = []
    results = []
    try:
        if local:
            server_proc = start_xray(xray_bin, Path(args.server), tmp / "server.log")
            procs.append(server_proc)
            time.sleep(0.4)
            if server_proc.poll() is not None:
                print("ERROR: server xray exited early. Log:\n" + (tmp / "server.log").read_text())
                return 1
            for ep in endpoints:
                wait_port("127.0.0.1", ep["internal_port"])

        for i, ep in enumerate(endpoints):
            socks_port = 11000 + i
            spec = spec_from_endpoint(ep, local=local)
            host = "127.0.0.1" if local else ep.get("host", summary["host"])
            port = ep["internal_port"] if local else ep.get("public_port", summary["public_port"])
            tls = False if local else ep.get("edge_tls", summary["edge_tls"])

            # Remote pre-check: is the tunnel port actually public & reachable?
            if not local:
                code, redir = http_probe(host)
                cls = classify_probe(code, redir)
                if cls == "private":
                    results.append((ep["name"], ep["protocol"], ep["transport"], False,
                                    f"PORT NOT PUBLIC - GitHub auth wall (HTTP {code}). "
                                    f"Set port {ep['internal_port']} to Public in the PORTS tab."))
                    continue
                if cls == "unreachable":
                    results.append((ep["name"], ep["protocol"], ep["transport"], False,
                                    f"UNREACHABLE (HTTP {code or '000'}) - codespace stopped or wrong host."))
                    continue
            client_cfg = generate.build_client_config(
                spec, host, port, tls, reality_ctx, socks_port=socks_port, http_port=None)
            cfg_path = tmp / f"client_{ep['name']}.json"
            cfg_path.write_text(json.dumps(client_cfg, indent=2))

            cproc = start_xray(xray_bin, cfg_path, tmp / f"client_{ep['name']}.log")
            procs.append(cproc)
            wait_port("127.0.0.1", socks_port)
            time.sleep(0.6)

            ok, detail = probe(socks_port)
            results.append((ep["name"], ep["protocol"], ep["transport"], ok, detail))
    finally:
        for pr in procs:
            pr.terminate()
        for pr in procs:
            try:
                pr.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pr.kill()

    # report
    print("=" * 68)
    print(f" HEALTH CHECK ({args.mode})  host={summary['host']}:{summary['public_port']}")
    print("=" * 68)
    all_ok = True
    for name, proto, transport, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        all_ok = all_ok and ok
        print(f"  [{mark}] {name:<16} {proto}/{transport:<6} {detail}")
    print("=" * 68)
    print(" RESULT:", "ALL HEALTHY & CONNECTED ✅" if all_ok else "SOME ENDPOINTS FAILED ❌")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
