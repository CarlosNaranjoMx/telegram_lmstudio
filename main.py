"""
LM Studio Telegram Notifier
Polls LM Studio and sends a Telegram message when the model goes idle.

Setup:
  1. Crea un bot en Telegram con @BotFather y copia el token.
  2. Habla con tu bot y obtén tu chat_id (usa @userinfobot).
  3. Crea un archivo .env (ver .env.example).
  4. pip install -r requirements.txt
  5. python main.py
"""

import asyncio
import aiohttp
import os
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ── Configuración ────────────────────────────────────────────────────────────
LMSTUDIO_URL  = os.getenv("LMSTUDIO_URL",  "http://127.0.0.1:1234")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLL_INTERVAL    = int(os.getenv("POLL_INTERVAL", "5"))   # segundos entre checks
IDLE_CONFIRM     = int(os.getenv("IDLE_CONFIRM",  "3"))   # polls seguidos en idle para notificar

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Telegram ─────────────────────────────────────────────────────────────────
async def telegram_send(text: str) -> bool:
    """Envía un mensaje al chat configurado. Retorna True si tuvo éxito."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.error("Telegram error %s: %s", resp.status, body)
                    return False
                return True
    except Exception as exc:
        log.error("Telegram unreachable: %s", exc)
        return False


# ── LM Studio ────────────────────────────────────────────────────────────────
async def lmstudio_get_models() -> dict | list | None:
    """
    Consulta /api/v1/models de LM Studio.
    Retorna el JSON o None si el servidor no responde.
    """
    for path in ("/api/v1/models", "/v1/models"):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{LMSTUDIO_URL}{path}",
                    timeout=aiohttp.ClientTimeout(total=3),
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception:
            continue
    return None


def _extract_model_list(models_response: dict | list) -> list:
    if isinstance(models_response, list):
        return models_response

    if not isinstance(models_response, dict):
        return []

    data = models_response.get("data")
    if isinstance(data, list):
        return data

    if all(isinstance(value, dict) for value in models_response.values()):
        return list(models_response.values())

    return []


def _is_generating(models_response: dict | list) -> bool:
    """
    Detecta si algún modelo está generando tokens.

    LM Studio puede devolver:
      - lista directa de modelos
      - objeto con campo `data`

    El criterio de actividad incluye:
      - model.state == "generating"
      - model.stats.tokens_per_second > 0
      - campos alternativos de estado
    """
    models = _extract_model_list(models_response)
    if not models:
        log.debug("LM Studio /models response no contiene modelos detectables: %s", models_response)
        return False

    for model in models:
        state = model.get("state")
        stats = model.get("stats", {}) or {}
        tps = stats.get("tokens_per_second", 0)

        if state in ("generating", "processing", "busy", "active"):
            log.debug("Modelo %s detectado activo por state=%s", model.get("name") or model.get("id"), state)
            return True

        if isinstance(tps, (int, float)) and tps > 0:
            log.debug("Modelo %s detectado activo por tokens_per_second=%s", model.get("name") or model.get("id"), tps)
            return True

        if stats.get("is_active") or stats.get("busy"):
            log.debug("Modelo %s detectado activo por stats extra %s", model.get("name") or model.get("id"), stats)
            return True

    return False


# ── Monitor principal ────────────────────────────────────────────────────────
async def run_monitor() -> None:
    log.info("Iniciando monitor de LM Studio en %s", LMSTUDIO_URL)
    log.info("Intervalo de polling: %ss  |  Confirmación idle: %s checks", POLL_INTERVAL, IDLE_CONFIRM)

    await telegram_send(
        "🟢 <b>Monitor LM Studio iniciado</b>\n"
        f"Servidor: <code>{LMSTUDIO_URL}</code>\n"
        "Te avisaré cuando termine de generar."
    )

    was_generating = False
    idle_streak = 0

    while True:
        response = await lmstudio_get_models()

        if response is None:
            log.warning("LM Studio no responde, reintentando en %ss...", POLL_INTERVAL)
            await asyncio.sleep(POLL_INTERVAL)
            continue

        generating = _is_generating(response)

        if generating:
            if not was_generating:
                log.info("LM Studio: generación en curso")
            was_generating = True
            idle_streak = 0

        else:  # idle
            if was_generating:
                idle_streak += 1
                log.info("LM Studio: idle check %d/%d", idle_streak, IDLE_CONFIRM)

                if idle_streak >= IDLE_CONFIRM:
                    ts = datetime.now().strftime("%H:%M:%S")
                    log.info("LM Studio: IDLE confirmado a las %s", ts)
                    await telegram_send(
                        "✅ <b>LM Studio terminó de generar</b>\n"
                        f"Hora: <code>{ts}</code>\n"
                        "El modelo está libre."
                    )
                    was_generating = False
                    idle_streak = 0

        await asyncio.sleep(POLL_INTERVAL)


# ── Entrada ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    missing = [v for v in ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID") if not os.getenv(v)]
    if missing:
        print(f"\nError: faltan variables en .env: {', '.join(missing)}")
        print("Copia .env.example a .env y rellena los valores.\n")
        raise SystemExit(1)

    try:
        asyncio.run(run_monitor())
    except KeyboardInterrupt:
        log.info("Monitor detenido por el usuario.")
