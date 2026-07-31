"""Local proxy with a live dashboard, for seeing what the MCP server actually does.

Runs a plain HTTP forward proxy plus a web page that streams every hop the server
makes, decoding the NodeMaven targeting username into labelled fields. Point the
server at it and watch a tool call turn into a real network request:

    python examples/traffic_dashboard.py

    NODEMAVEN_PROXY_HOST=127.0.0.1
    NODEMAVEN_HTTP_PORT=8080

Proxy on 127.0.0.1:8080, dashboard on http://127.0.0.1:8099.
Development aid only: it accepts any credentials and does not encrypt anything.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import datetime

PROXY_HOST, PROXY_PORT = "127.0.0.1", 8080
WEB_HOST, WEB_PORT = "127.0.0.1", 8099

TARGETING_KEYS = ("country", "region", "city", "isp", "sid", "filter", "ipv4")

subscribers: list[asyncio.Queue] = []
history: list[dict] = []
hop_counter = 0


def next_hop_id() -> int:
    global hop_counter
    hop_counter += 1
    return hop_counter


def parse_targeting(username: str) -> dict[str, str]:
    """Decode 'acct-country-de-city-munich-sid-ab12cd' into labelled fields."""
    parts = username.split("-")
    if not parts:
        return {}
    decoded = {"account": parts[0]}
    index = 1
    while index < len(parts) - 1:
        key = parts[index]
        if key not in TARGETING_KEYS:
            index += 1
            continue
        value, index = parts[index + 1], index + 2
        while index < len(parts) and parts[index] not in TARGETING_KEYS:
            value += "_" + parts[index]
            index += 1
        decoded[key] = value
    return decoded


def publish(event: dict) -> None:
    event.setdefault("time", datetime.now().strftime("%H:%M:%S"))
    history.append(event)
    del history[:-200]
    for queue in list(subscribers):
        queue.put_nowait(event)


async def pipe(reader, writer, counter: dict | None = None, key: str = "") -> None:
    total = 0
    try:
        while data := await reader.read(65536):
            total += len(data)
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    finally:
        if counter is not None and key:
            counter[key] = total
        writer.close()


async def read_request(reader) -> tuple[str, str, dict[str, str]] | None:
    request_line = await reader.readline()
    if not request_line:
        return None
    method, target, _ = request_line.decode("latin-1").split(" ", 2)
    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        name, _, value = line.decode("latin-1").partition(":")
        headers[name.strip().lower()] = value.strip()
    return method, target, headers


def proxy_username(headers: dict[str, str]) -> str:
    auth = headers.get("proxy-authorization", "")
    if not auth.lower().startswith("basic "):
        return ""
    decoded = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8", "replace")
    return decoded.split(":", 1)[0]


async def handle_proxy(client_reader, client_writer) -> None:
    started = time.perf_counter()
    try:
        parsed = await read_request(client_reader)
        if parsed is None:
            client_writer.close()
            return
        method, target, headers = parsed

        username = proxy_username(headers)
        event = {
            "id": next_hop_id(),
            "proto": "https" if method.upper() == "CONNECT" else "http",
            "target": target,
            "username": username,
            "targeting": parse_targeting(username) if username else {},
        }

        if method.upper() == "CONNECT":
            host, _, port = target.partition(":")
            up_reader, up_writer = await asyncio.open_connection(host, int(port or 443))
            client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await client_writer.drain()
            publish({**event, "status": "open"})
            counter: dict[str, int] = {}
            await asyncio.gather(
                pipe(client_reader, up_writer),
                pipe(up_reader, client_writer, counter, "down"),
            )
            publish(
                {
                    **event,
                    "status": "closed",
                    "bytes": counter.get("down"),
                    "ms": round((time.perf_counter() - started) * 1000),
                }
            )
            return

        _, _, rest = target.partition("://")
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        up_reader, up_writer = await asyncio.open_connection(host, int(port or 80))
        rebuilt = [f"{method} /{path} HTTP/1.1"]
        rebuilt += [f"{k}: {v}" for k, v in headers.items() if not k.startswith("proxy-")]
        rebuilt.append("connection: close")
        up_writer.write(("\r\n".join(rebuilt) + "\r\n\r\n").encode("latin-1"))
        await up_writer.drain()
        counter = {}
        await pipe(up_reader, client_writer, counter, "down")
        up_writer.close()
        publish(
            {
                **event,
                "status": "done",
                "bytes": counter.get("down"),
                "ms": round((time.perf_counter() - started) * 1000),
            }
        )
    except Exception as exc:  # noqa: BLE001 - dev aid: report and keep serving
        publish(
            {
                "id": next_hop_id(),
                "proto": "--",
                "target": f"{type(exc).__name__}: {exc}",
                "status": "error",
            }
        )
        client_writer.close()


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>nodemaven-mcp traffic</title>
<style>
  :root{
    --ink:#0c1014; --panel:#131920; --edge:#222c37; --edge-soft:#1a222b;
    --text:#e9edf2; --mute:#7b8899; --amber:#f5a524; --amber-dim:#8a6320; --cyan:#5ad1c8;
    --display:'Bahnschrift','DIN Alternate','Segoe UI Variable Display','Segoe UI',
              'Helvetica Neue',Arial,sans-serif;
    --mono:'Cascadia Mono','Consolas','SF Mono',ui-monospace,monospace;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--ink);color:var(--text);font-family:var(--mono);font-size:13px}
  .wrap{max-width:1180px;margin:0 auto;padding:28px 24px 64px}

  header{display:flex;align-items:baseline;gap:18px;flex-wrap:wrap;
         padding-bottom:18px;border-bottom:1px solid var(--edge)}
  h1{margin:0;font-family:var(--display);font-size:26px;font-weight:600;
     letter-spacing:.16em;text-transform:uppercase}
  h1 span{color:var(--amber)}
  .sub{color:var(--mute);font-size:12px;letter-spacing:.04em}
  .live{margin-left:auto;display:flex;align-items:center;gap:8px;color:var(--mute);
        font-size:11px;letter-spacing:.18em;text-transform:uppercase}
  .dot{width:7px;height:7px;border-radius:50%;background:var(--amber);
       box-shadow:0 0 0 0 rgba(245,165,36,.6);animation:pulse 2.4s infinite}
  @keyframes pulse{70%{box-shadow:0 0 0 9px rgba(245,165,36,0)}100%{box-shadow:0 0 0 0 rgba(245,165,36,0)}}

  .decoder{margin:26px 0 30px;padding:22px 24px;background:var(--panel);
           border:1px solid var(--edge);border-radius:3px}
  .decoder .eyebrow{color:var(--mute);font-size:10px;letter-spacing:.22em;text-transform:uppercase}
  .raw{margin:14px 0 20px;font-size:15px;color:var(--mute);word-break:break-all}
  .raw em{font-style:normal;color:var(--text)}
  .fields{display:flex;flex-wrap:wrap;gap:10px}
  .field{min-width:96px;padding:9px 14px;border:1px solid var(--edge);
         border-left:2px solid var(--amber-dim);background:#0f151b}
  .field .k{display:block;color:var(--mute);font-size:10px;letter-spacing:.16em;text-transform:uppercase}
  .field .v{display:block;margin-top:5px;font-family:var(--display);font-size:19px;letter-spacing:.06em}
  .field.country{border-left-color:var(--amber)}
  .field.country .v{color:var(--amber);font-size:26px}
  .field.sid .v{color:var(--cyan)}

  table{width:100%;border-collapse:collapse}
  th{text-align:left;color:var(--mute);font-weight:400;font-size:10px;letter-spacing:.2em;
     text-transform:uppercase;padding:0 14px 10px;border-bottom:1px solid var(--edge)}
  td{padding:13px 14px;border-bottom:1px solid var(--edge-soft);white-space:nowrap}
  td.dest{font-family:var(--display);font-size:17px;letter-spacing:.04em;white-space:normal}
  td.route{color:var(--amber);font-family:var(--display);font-size:17px;letter-spacing:.1em}
  .chip{display:inline-block;margin-right:6px;padding:2px 8px;border:1px solid var(--edge);
        color:var(--mute);font-size:11px}
  .chip b{color:var(--text);font-weight:500}
  .st{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--mute)}
  .st.open{color:var(--cyan)} .st.error{color:#ff6b6b}
  .num{color:var(--mute);text-align:right}
  tbody tr:first-child td{animation:land .7s ease-out}
  @keyframes land{from{background:rgba(245,165,36,.09)}to{background:transparent}}

  .empty{padding:56px 24px;text-align:center;color:var(--mute)}
  .empty strong{display:block;margin-bottom:8px;font-family:var(--display);font-size:17px;
                letter-spacing:.14em;text-transform:uppercase;color:var(--text)}
  @media (max-width:640px){ h1{font-size:20px} td,th{padding-left:8px;padding-right:8px} }
  @media (prefers-reduced-motion:reduce){ *{animation:none!important} }
</style></head>
<body>
<div class="wrap">
  <header>
    <h1>node<span>maven</span>-mcp</h1>
    <div class="sub">local proxy &middot; every hop the MCP server makes</div>
    <div class="live"><span class="dot"></span><span id="count">0 hops</span></div>
  </header>

  <section class="decoder">
    <div class="eyebrow">Targeting decoded from the proxy username</div>
    <div class="raw" id="raw">no request yet</div>
    <div class="fields" id="fields"></div>
  </section>

  <table>
    <thead><tr>
      <th>time</th><th>proto</th><th>destination</th><th>route</th>
      <th>session</th><th>status</th><th style="text-align:right">bytes</th><th style="text-align:right">ms</th>
    </tr></thead>
    <tbody id="rows"></tbody>
  </table>
  <div class="empty" id="empty">
    <strong>Waiting for the first hop</strong>
    Call a tool from the MCP Inspector, an agent, or your own client.
  </div>
</div>
<script>
  const rows = document.getElementById('rows');
  const empty = document.getElementById('empty');
  const rawEl = document.getElementById('raw');
  const fieldsEl = document.getElementById('fields');
  const countEl = document.getElementById('count');
  let hops = 0;

  const ORDER = ['account','country','region','city','isp','sid','filter','ipv4'];

  function paintDecoder(username, t){
    if (!username) return;
    rawEl.innerHTML = username.split('-')
      .map((p, i) => i === 0 ? `<em>${p}</em>` : p).join('<span> - </span>');
    fieldsEl.innerHTML = ORDER.filter(k => t[k]).map(k =>
      `<div class="field ${k}"><span class="k">${k}</span><span class="v">${t[k]}</span></div>`
    ).join('');
  }

  function cells(e){
    const t = e.targeting || {};
    const route = [t.country, t.city, t.isp].filter(Boolean).join(' / ') || '&mdash;';
    const session = t.sid
      ? `<span class="chip">sticky <b>${t.sid}</b></span>`
      : '<span class="chip">rotating</span>';
    return `<td class="num">${e.time}</td><td class="st">${e.proto||''}</td>` +
      `<td class="dest">${e.target}</td><td class="route">${route.toUpperCase()}</td>` +
      `<td>${session}</td><td class="st ${e.status||''}">${e.status||''}</td>` +
      `<td class="num">${e.bytes ?? ''}</td><td class="num">${e.ms ?? ''}</td>`;
  }

  new EventSource('/events').onmessage = ev => {
    const e = JSON.parse(ev.data);
    empty.style.display = 'none';

    // One row per hop: the closing event updates the row its opening event created.
    let tr = document.getElementById('hop-' + e.id);
    if (!tr) {
      tr = document.createElement('tr');
      tr.id = 'hop-' + e.id;
      rows.prepend(tr);
      countEl.textContent = `${++hops} hops`;
      paintDecoder(e.username, e.targeting || {});
    }
    tr.innerHTML = cells(e);
  };
</script>
</body></html>"""


async def handle_web(reader, writer) -> None:
    try:
        parsed = await read_request(reader)
        if parsed is None:
            writer.close()
            return
        _, path, _ = parsed

        if path.startswith("/events"):
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                b"Cache-Control: no-cache\r\nConnection: keep-alive\r\n\r\n"
            )
            queue: asyncio.Queue = asyncio.Queue()
            subscribers.append(queue)
            try:
                for past in history[-60:]:
                    writer.write(f"data: {json.dumps(past)}\n\n".encode())
                await writer.drain()
                while True:
                    writer.write(f"data: {json.dumps(await queue.get())}\n\n".encode())
                    await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                pass
            finally:
                subscribers.remove(queue)
            return

        body = PAGE.encode("utf-8")
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()
    except (ConnectionResetError, BrokenPipeError):
        writer.close()


async def main() -> None:
    proxy = await asyncio.start_server(handle_proxy, PROXY_HOST, PROXY_PORT)
    web = await asyncio.start_server(handle_web, WEB_HOST, WEB_PORT)
    print(f"proxy      {PROXY_HOST}:{PROXY_PORT}", flush=True)
    print(f"dashboard  http://{WEB_HOST}:{WEB_PORT}", flush=True)
    async with proxy, web:
        await asyncio.gather(proxy.serve_forever(), web.serve_forever())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
