import asyncio
import os
import json
import logging
import sys
from datetime import datetime
import aiohttp
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

LMSTUDIO_URL = os.getenv("LMSTUDIO_URL", "http://127.0.0.1:1234")
PROXY_HOST = os.getenv("PROXY_HOST", "127.0.0.1")
PROXY_PORT = int(os.getenv("PROXY_PORT", "8080"))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


async def telegram_send(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram no configurado: no se enviará notificación.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.error("Telegram error %s: %s", resp.status, body)
                    return False
                return True
    except Exception as exc:
        log.error("Telegram unreachable: %s", exc)
        return False


def should_notify_path(path: str) -> bool:
    watched = ["/v1/chat/completions", "/v1/completions", "/v1/responses", "/chat/completions", "/completions", "/responses"]
    return any(path.endswith(endpoint) or endpoint in path for endpoint in watched)


def is_streaming_response(headers: aiohttp.typedefs.LooseHeaders) -> bool:
    content_type = str(headers.get("Content-Type", "")).lower()
    transfer_encoding = str(headers.get("Transfer-Encoding", "")).lower()
    return (
        "event-stream" in content_type
        or "application/x-ndjson" in content_type
        or "chunked" in transfer_encoding
    )


def prepare_forward_headers(source_headers):
    headers = {}
    for name, value in source_headers.items():
        if name.lower() in HOP_BY_HOP_HEADERS:
            continue
        if name.lower() == "host":
            continue
        headers[name] = value
    return headers


def prepare_response_headers(source_headers, target_headers):
    for name, value in source_headers.items():
        if name.lower() in HOP_BY_HOP_HEADERS:
            continue
        target_headers[name] = value


def set_cors_headers(headers):
    headers["Access-Control-Allow-Origin"] = "*"
    headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
    headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With, Accept"
    headers["Access-Control-Max-Age"] = "3600"


def _truncate(text: str, max_chars: int = 120) -> str:
    text = text.strip()
    return text[:max_chars] + "…" if len(text) > max_chars else text


def build_request_summary(path: str, payload: dict) -> str:
    """Genera un resumen compacto de un POST /v1/chat/completions."""
    model = payload.get("model", "?")
    max_tokens = payload.get("max_tokens") or payload.get("maxTokens", "?")
    stream = "sí" if payload.get("stream") else "no"
    messages = payload.get("messages", [])
    tools = payload.get("tools", [])

    # Último mensaje del usuario (no tool)
    last_user = next(
        (m for m in reversed(messages) if m.get("role") == "user"),
        None,
    )
    last_user_text = _truncate(str(last_user.get("content", ""))) if last_user else "—"

    # Conteo por rol
    role_counts: dict[str, int] = {}
    for m in messages:
        r = m.get("role", "?")
        role_counts[r] = role_counts.get(r, 0) + 1
    roles_line = "  ".join(f"{r}×{n}" for r, n in role_counts.items())

    # Tool calls pendientes del último assistant (si hay)
    last_assistant = next(
        (m for m in reversed(messages) if m.get("role") == "assistant"),
        None,
    )
    tool_calls_info = ""
    if last_assistant and last_assistant.get("tool_calls"):
        calls = last_assistant["tool_calls"]
        names = [c.get("function", {}).get("name", "?") for c in calls[:3]]
        suffix = f" +{len(calls)-3}" if len(calls) > 3 else ""
        tool_calls_info = f"\n🔧 <b>Tool calls:</b> {', '.join(names)}{suffix}"

    # Herramientas disponibles
    tools_line = ""
    if tools:
        tool_names = [t.get("function", {}).get("name", "?") for t in tools[:5]]
        suffix = f" +{len(tools)-5}" if len(tools) > 5 else ""
        tools_line = f"\n🛠 <b>Tools disp.:</b> {', '.join(tool_names)}{suffix}"

    ts = datetime.now().strftime("%H:%M:%S")
    return (
        f"📨 <b>Nueva inferencia</b>\n"
        f"🤖 <b>Modelo:</b> <code>{model}</code>\n"
        f"💬 <b>Mensajes:</b> {len(messages)}  ({roles_line})\n"
        f"📝 <b>Último user:</b> {last_user_text}"
        f"{tool_calls_info}"
        f"{tools_line}\n"
        f"⚙️ max_tokens={max_tokens}  stream={stream}  🕐{ts}"
    )


async def _notify_done(path: str, model_id: str, mode: str = "stream") -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    model_line = f"\n🤖 Modelo: <code>{model_id}</code>" if model_id else ""
    message = (
        f"✅ <b>Inferencia completada</b>{model_line}\n"
        f"Endpoint: <code>{path}</code>  [{mode}]\n"
        f"Hora: <code>{ts}</code>"
    )
    await telegram_send(message)
    log.info("Notificación Telegram enviada — %s finalizado en %s", mode, path)


async def proxy_stream(
    resp: aiohttp.ClientResponse,
    proxy_resp: web.StreamResponse,
    path: str,
    model_id: str = "",
) -> None:
    saw_done = False
    try:
        async for chunk in resp.content.iter_chunked(1024):
            if not chunk:
                continue
            await proxy_resp.write(chunk)
            if should_notify_path(path):
                text = chunk.decode("utf-8", errors="ignore")
                if (
                    "data: [DONE]" in text
                    or "data:[DONE]" in text
                    or '"finish_reason":"stop"' in text
                    or '"finish_reason": "stop"' in text
                    or '"finish_reason":"length"' in text
                    or '"finish_reason": "length"' in text
                    or '"finish_reason":"tool_calls"' in text
                    or '"finish_reason": "tool_calls"' in text
                ):
                    saw_done = True
        await proxy_resp.write_eof()
    finally:
        if should_notify_path(path) and saw_done:
            log.info("Finished streaming response for %s", model_id or path)
            await _notify_done(path, model_id, "stream")


async def handle(request: web.Request) -> web.StreamResponse:
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
        set_cors_headers(resp.headers)
        return resp

    upstream_url = f"{LMSTUDIO_URL}{request.rel_url}"
    log.info("Proxy %s %s -> %s", request.method, request.rel_url, upstream_url)

    body = await request.read()
    if request.method == "POST" and should_notify_path(request.path):
        preview = _truncate(body.decode("utf-8", errors="ignore")) if body else "<empty body>"
        log.debug("Received request: %s to %s with body %s", request.method, request.path, preview)
    headers = prepare_forward_headers(request.headers)

    async with aiohttp.ClientSession() as session:
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
        set_cors_headers(resp.headers)
        return resp

    upstream_url = f"{LMSTUDIO_URL}{request.rel_url}"
    log.info("Proxy %s %s -> %s", request.method, request.rel_url, upstream_url)

    body = await request.read()
    headers = prepare_forward_headers(request.headers)

    async with aiohttp.ClientSession() as session:
        async with session.request(
            request.method,
            upstream_url,
            headers=headers,
            data=body,
            timeout=aiohttp.ClientTimeout(total=None),
            allow_redirects=False,
        ) as resp:
            proxy_resp = web.StreamResponse(status=resp.status)
            prepare_response_headers(resp.headers, proxy_resp.headers)
            set_cors_headers(proxy_resp.headers)
            if "Content-Length" in proxy_resp.headers:
                proxy_resp.headers.pop("Content-Length")
            await proxy_resp.prepare(request)

            # Extraer model_id y enviar resumen de la petición entrante
            model_id = ""
            if request.method == "POST" and body and should_notify_path(request.path):
                try:
                    payload = json.loads(body)
                    model_id = payload.get("model", "")
                    summary = build_request_summary(request.path, payload)
                    asyncio.ensure_future(telegram_send(summary))
                except Exception as exc:
                    log.debug("No se pudo parsear body para resumen: %s", exc)

            if should_notify_path(request.path) or is_streaming_response(resp.headers):
                await proxy_stream(resp, proxy_resp, request.path, model_id)
            else:
                data = await resp.read()
                await proxy_resp.write(data)
                await proxy_resp.write_eof()
                # Notificar también en respuestas no-streaming
                if should_notify_path(request.path) and data:
                    try:
                        resp_json = json.loads(data)
                        finish = (
                            resp_json.get("choices", [{}])[0]
                            .get("finish_reason", "")
                        )
                        if finish in ("stop", "length", "tool_calls"):
                            await _notify_done(request.path, model_id, f"no-stream/{finish}")
                    except Exception:
                        pass

            return proxy_resp


async def health(request: web.Request) -> web.Response:
    resp = web.json_response({"status": "ok", "upstream": LMSTUDIO_URL})
    set_cors_headers(resp.headers)
    return resp


def main() -> None:
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_route("GET", "/{tail:.*}", handle)
    app.router.add_route("POST", "/{tail:.*}", handle)
    app.router.add_route("PUT", "/{tail:.*}", handle)
    app.router.add_route("PATCH", "/{tail:.*}", handle)
    app.router.add_route("DELETE", "/{tail:.*}", handle)

    log.info("Iniciando proxy en http://%s:%s, upstream %s", PROXY_HOST, PROXY_PORT, LMSTUDIO_URL)
    web.run_app(app, host=PROXY_HOST, port=PROXY_PORT)


if __name__ == "__main__":
    main()
