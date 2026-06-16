# ═══════════════════════════════════════════════════════════════
#  TRADING ALERT BOT — XAU/USD & NAS100
#  TradingView → Webhook → Telegram
#  Carlos · Bogotá · Sesión Nueva York
# ═══════════════════════════════════════════════════════════════

import os
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify
from telegram import Bot
from telegram.constants import ParseMode
import asyncio

# ─── CONFIGURACIÓN ───────────────────────────────────────────
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "TU_TOKEN_AQUI")
CHAT_ID     = os.environ.get("CHAT_ID",   "TU_CHAT_ID_AQUI")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "clave_secreta_123")
PORT        = int(os.environ.get("PORT", 5000))

BOG_TZ = ZoneInfo("America/Bogota")

logging.basicConfig(
    format="%(asctime)s — %(levelname)s — %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

app  = Flask(__name__)
bot  = Bot(token=BOT_TOKEN)

# ─── HELPERS ─────────────────────────────────────────────────
def hora_bogota():
    return datetime.now(BOG_TZ).strftime("%I:%M:%S %p")

def fecha_bogota():
    return datetime.now(BOG_TZ).strftime("%d/%m/%Y")

def sesion_activa():
    """Determina si estamos en ventana óptima NY (8 AM - 12 PM Bogotá)."""
    h = datetime.now(BOG_TZ).hour
    m = datetime.now(BOG_TZ).minute
    hm = h + m / 60
    if 8.0 <= hm < 12.0:
        return "🟢 SESIÓN ÓPTIMA NY"
    elif 7.5 <= hm < 8.0:
        return "🟡 PRE-SESIÓN"
    elif 12.0 <= hm < 14.0:
        return "🟡 SESIÓN MODERADA"
    elif 3.0 <= hm < 8.0:
        return "🔵 SESIÓN LONDRES"
    else:
        return "🔴 FUERA DE SESIÓN"

def calcular_lote(balance: float, sl_puntos: float, instrumento: str) -> str:
    """Calcula lote sugerido con riesgo del 2%."""
    try:
        riesgo_usd = balance * 0.02
        # XAU: ~$1 por punto por 0.1 lot → $0.10 por punto por 0.01 lot
        # NAS: ~$1 por punto por 0.1 lot → similar
        pip_val_micro = 0.10  # por 0.01 lot
        lotes = riesgo_usd / (sl_puntos * pip_val_micro * 10)
        lote_final = max(0.01, round(lotes, 2))
        return f"{lote_final:.2f}"
    except:
        return "0.01"

def emoji_direccion(d: str) -> str:
    d = d.upper()
    if d in ("BUY", "LONG", "COMPRA"):  return "📈"
    if d in ("SELL", "SHORT", "VENTA"): return "📉"
    return "⚡"

def emoji_setup(s: str) -> str:
    s = (s or "").upper()
    if "A" in s or "EMA" in s or "CROSS" in s: return "〰️"
    if "B" in s or "PULL" in s:               return "🎯"
    if "C" in s or "BREAK" in s or "RUP" in s: return "💥"
    return "📊"

def construir_mensaje(data: dict) -> str:
    """Construye el mensaje formateado para Telegram."""
    instrumento = data.get("instrumento", "XAUUSD").upper()
    direccion   = data.get("direccion",   "BUY").upper()
    setup       = data.get("setup",       "A")
    entrada     = data.get("entrada",     "—")
    sl          = data.get("sl",          "—")
    tp          = data.get("tp",          "—")
    sl_puntos   = data.get("sl_puntos",   0)
    balance     = data.get("balance",     40)
    timeframe   = data.get("timeframe",   "5M")
    notas       = data.get("notas",       "")
    confluencias= data.get("confluencias",3)

    # Calcular lote y ganancia esperada
    lote = calcular_lote(float(balance), float(sl_puntos) if sl_puntos else 10, instrumento)
    riesgo_usd = float(balance) * 0.02
    ganancia_2rr = riesgo_usd * 2

    # Barra de confluencias
    barra = "🔵" * int(confluencias) + "⚪" * (5 - int(confluencias))

    dir_emoji = emoji_direccion(direccion)
    setup_emoji = emoji_setup(setup)

    # Color de alerta según confluencias
    if int(confluencias) >= 4:
        alerta_header = "🔥 *SETUP DE ALTA PROBABILIDAD*"
    elif int(confluencias) == 3:
        alerta_header = "✅ *SETUP VÁLIDO*"
    else:
        alerta_header = "⚠️ *SETUP DÉBIL — REVISAR*"

    msg = f"""
{alerta_header}
━━━━━━━━━━━━━━━━━━━━
{dir_emoji} *{instrumento}* · {direccion} · {timeframe}
{setup_emoji} Setup *{setup}*

📍 *ENTRADA:* `{entrada}`
🛑 *STOP LOSS:* `{sl}`
🎯 *TAKE PROFIT:* `{tp}`

💰 *Riesgo (2%):* `${riesgo_usd:.2f}`
📦 *Lote sugerido:* `{lote} lots`
✨ *Ganancia esperada (1:2):* `+${ganancia_2rr:.2f}`

{barra} Confluencias: *{confluencias}/5*

🕐 *Hora Bogotá:* {hora_bogota()}
📅 *Fecha:* {fecha_bogota()}
{sesion_activa()}
"""
    if notas:
        msg += f"\n📝 *Notas:* _{notas}_"

    msg += """
━━━━━━━━━━━━━━━━━━━━
⚠️ _Verifica el setup antes de entrar._
_Recuerda: máx. 3 trades por día._"""

    return msg.strip()

# ─── RUTAS FLASK ─────────────────────────────────────────────

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "bot": "Trading Alert Bot · XAU/NAS"}), 200


@app.route("/webhook", methods=["POST"])
def webhook():
    """Recibe alertas de TradingView y las envía a Telegram."""

    # Verificar secret en header o query param
    secret = request.headers.get("X-Secret") or request.args.get("secret", "")
    if secret != WEBHOOK_SECRET:
        log.warning("Intento con secret inválido")
        return jsonify({"error": "Unauthorized"}), 401

    # Parsear el body — TradingView manda JSON
    try:
        raw = request.get_data(as_text=True)
        log.info(f"Alerta recibida: {raw}")
        data = json.loads(raw)
    except Exception as e:
        log.error(f"Error parseando JSON: {e}")
        return jsonify({"error": "Invalid JSON"}), 400

    # Construir y enviar mensaje
    try:
        mensaje = construir_mensaje(data)
        asyncio.run(
            bot.send_message(
                chat_id=CHAT_ID,
                text=mensaje,
                parse_mode=ParseMode.MARKDOWN
            )
        )
        log.info("Mensaje enviado a Telegram ✓")
        return jsonify({"status": "sent"}), 200
    except Exception as e:
        log.error(f"Error enviando mensaje: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/test", methods=["GET"])
def test_alert():
    """Ruta de prueba — envía una alerta de ejemplo a Telegram."""
    data_test = {
        "instrumento": "XAUUSD",
        "direccion":   "BUY",
        "setup":       "B - Pullback EMA21",
        "entrada":     "2345.50",
        "sl":          "2338.00",
        "tp":          "2360.50",
        "sl_puntos":   "75",
        "balance":     "40",
        "timeframe":   "15M",
        "confluencias": 4,
        "notas":       "Rebote limpio en EMA21 + VWAP soporte"
    }
    try:
        mensaje = construir_mensaje(data_test)
        asyncio.run(
            bot.send_message(
                chat_id=CHAT_ID,
                text=mensaje,
                parse_mode=ParseMode.MARKDOWN
            )
        )
        return jsonify({"status": "test enviado ✓"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── INICIO ──────────────────────────────────────────────────
if __name__ == "__main__":
    log.info(f"🤖 Bot iniciando en puerto {PORT}...")
    app.run(host="0.0.0.0", port=PORT, debug=False)
