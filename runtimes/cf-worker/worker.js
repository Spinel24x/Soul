// Minimal VLESS-over-WebSocket for Cloudflare Workers.
//
// This is the durable, ToS-safe "like Cloudflare Workers" path referenced in the
// project. Deploy with wrangler, set UUID as a secret, then generate a matching
// client link with the generator:
//
//   python3 generator/generate.py --host <worker-host> --public-port 443 \
//       --protocols vless-ws --path-prefix "" --remark-prefix cf
//   # then set the UUID in the produced link to match your Worker's UUID,
//   # or just build the link by hand (host=<worker>.workers.dev, tls, ws, path=/).
//
// Community-standard pattern (edge tunnel). Provided as-is: deploy & test with
// your own account. Requires the Workers "TCP Sockets" API (cloudflare:sockets).
import { connect } from "cloudflare:sockets";

const DEFAULT_UUID = "00000000-0000-0000-0000-000000000000";

export default {
  async fetch(request, env) {
    const uuid = (env.UUID || DEFAULT_UUID).toLowerCase();
    if (request.headers.get("Upgrade") !== "websocket") {
      // Look like an ordinary site to casual probes.
      return new Response("OK", { status: 200, headers: { "content-type": "text/plain" } });
    }
    return handleWS(request, uuid);
  },
};

async function handleWS(request, uuid) {
  const [client, server] = Object.values(new WebSocketPair());
  server.accept();

  let remote = { socket: null };
  let headerSent = false;
  const early = request.headers.get("sec-websocket-protocol") || "";
  const readable = wsReadable(server, early);

  readable
    .pipeTo(
      new WritableStream({
        async write(chunk) {
          if (remote.socket) {
            const w = remote.socket.writable.getWriter();
            await w.write(chunk);
            w.releaseLock();
            return;
          }
          const h = parseVlessHeader(chunk, uuid);
          if (h.error) throw new Error(h.error);
          if (h.isUDP) throw new Error("UDP not supported by this minimal worker");
          const respHeader = new Uint8Array([h.version, 0]);
          const payload = chunk.slice(h.dataIndex);
          const sock = connect({ hostname: h.address, port: h.port });
          remote.socket = sock;
          const w = sock.writable.getWriter();
          await w.write(payload);
          w.releaseLock();
          pipeRemoteToWS(sock, server, respHeader, () => {
            headerSent = true;
          });
        },
        close() {
          safeClose(server);
        },
        abort() {
          safeClose(server);
        },
      })
    )
    .catch(() => safeClose(server));

  return new Response(null, { status: 101, webSocket: client });
}

async function pipeRemoteToWS(sock, ws, respHeader, onFirst) {
  let sentHeader = false;
  await sock.readable
    .pipeTo(
      new WritableStream({
        write(chunk) {
          if (ws.readyState !== 1) return;
          if (!sentHeader) {
            const merged = new Uint8Array(respHeader.length + chunk.byteLength);
            merged.set(respHeader, 0);
            merged.set(new Uint8Array(chunk), respHeader.length);
            ws.send(merged);
            sentHeader = true;
            onFirst && onFirst();
          } else {
            ws.send(chunk);
          }
        },
        close() {
          safeClose(ws);
        },
        abort() {
          safeClose(ws);
        },
      })
    )
    .catch(() => safeClose(ws));
}

function wsReadable(ws, earlyDataHeader) {
  let cancelled = false;
  return new ReadableStream({
    start(controller) {
      ws.addEventListener("message", (e) => !cancelled && controller.enqueue(e.data));
      ws.addEventListener("close", () => {
        safeClose(ws);
        if (!cancelled) controller.close();
      });
      ws.addEventListener("error", (e) => controller.error(e));
      const { data, error } = b64ToBuf(earlyDataHeader);
      if (error) controller.error(error);
      else if (data) controller.enqueue(data);
    },
    cancel() {
      cancelled = true;
      safeClose(ws);
    },
  });
}

// VLESS request header: [ver(1)][uuid(16)][optLen(1)][opt][cmd(1)][port(2)][atyp(1)][addr][data]
function parseVlessHeader(buf, uuid) {
  const b = new Uint8Array(buf);
  if (b.byteLength < 24) return { error: "header too short" };
  const version = b[0];
  const id = b.slice(1, 17);
  if (formatUUID(id) !== uuid) return { error: "invalid user" };
  const optLen = b[17];
  const cmd = b[18 + optLen];
  let isUDP = false;
  if (cmd === 1) isUDP = false;
  else if (cmd === 2) isUDP = true;
  else return { error: `unsupported cmd ${cmd}` };
  let i = 18 + optLen + 1;
  const port = (b[i] << 8) | b[i + 1];
  i += 2;
  const atyp = b[i++];
  let address = "";
  if (atyp === 1) {
    address = b.slice(i, i + 4).join(".");
    i += 4;
  } else if (atyp === 2) {
    const len = b[i++];
    address = new TextDecoder().decode(b.slice(i, i + len));
    i += len;
  } else if (atyp === 3) {
    const parts = [];
    for (let j = 0; j < 8; j++) {
      parts.push(((b[i] << 8) | b[i + 1]).toString(16));
      i += 2;
    }
    address = parts.join(":");
  } else {
    return { error: `bad atyp ${atyp}` };
  }
  return { version, isUDP, port, address, dataIndex: i };
}

function formatUUID(b) {
  const h = [...b].map((x) => x.toString(16).padStart(2, "0"));
  return `${h.slice(0, 4).join("")}-${h.slice(4, 6).join("")}-${h
    .slice(6, 8)
    .join("")}-${h.slice(8, 10).join("")}-${h.slice(10, 16).join("")}`;
}

function b64ToBuf(s) {
  if (!s) return { data: null, error: null };
  try {
    s = s.replace(/-/g, "+").replace(/_/g, "/");
    const bin = atob(s);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return { data: out.buffer, error: null };
  } catch (e) {
    return { data: null, error: e };
  }
}

function safeClose(ws) {
  try {
    if (ws.readyState === 1 || ws.readyState === 0) ws.close();
  } catch (_) {}
}
