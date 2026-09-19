"""
PAPIATMA BACKEND - Simple single-file version
Telegram Admin Bot + FastAPI
"""

import os
import sqlite3
import secrets
import string
import asyncio
import logging
import html
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
import uvicorn

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ---------------- CONFIG (saari values yahin hardcode) ----------------
BOT_TOKEN = os.environ.get("PAPIATMA_BOT_TOKEN", "8774741924:AAH5DkvAMUlVa0CFJ7ZjPB1mFSm8LoXYImo").strip()
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "5004292319"))
DEVICE_SECRET = os.environ.get("DEVICE_SECRET", "50485cc07fd8787bc8c5d1e7c26d111a")
DB_PATH = os.environ.get("DB_PATH", "/data/papiatma.db")
PORT = int(os.environ.get("PORT", 8080))

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("papiatma")


# ---------------- DB ----------------
def db_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = db_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        target_chat_id TEXT NOT NULL,
        active INTEGER DEFAULT 1,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_key TEXT, client_name TEXT, sender_id TEXT, body TEXT,
        status TEXT, telegram_msg_id INTEGER, error TEXT, direction TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS pending_injects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_key TEXT, sender_id TEXT, body TEXT,
        status TEXT DEFAULT 'queued', created_at TEXT NOT NULL, delivered_at TEXT
    );
    """)
    conn.commit()
    conn.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def gen_key(name: str) -> str:
    prefix = "".join(c for c in name.lower() if c.isalnum())[:8] or "client"
    rand = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    return f"{prefix}_{rand}"


# ---------------- Telegram Sender ----------------
async def send_to_telegram(chat_id: str, sender: str, body: str) -> int:
    text = f"<b>{html.escape(sender)}</b>\n{html.escape(body)}"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
    data = r.json()
    if not data.get("ok"):
        raise Exception(data.get("description", "Telegram error"))
    return data["result"]["message_id"]


# ---------------- FastAPI ----------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if not BOT_TOKEN:
        logger.error("❌ PAPIATMA_BOT_TOKEN missing! Bot start nahi hoga.")
    elif ADMIN_CHAT_ID == 0:
        logger.error("❌ ADMIN_CHAT_ID missing! Bot start nahi hoga.")
    else:
        asyncio.create_task(run_bot())
        logger.info("✅ FastAPI + Bot starting...")
    yield


app = FastAPI(lifespan=lifespan)


class InjectInput(BaseModel):
    client_key: str
    sender_id: str
    body: str


@app.get("/")
async def root():
    return {"status": "ok", "service": "papiatma-backend"}


@app.get("/health")
async def health():
    return {"ok": True, "bot": bool(BOT_TOKEN), "admin": ADMIN_CHAT_ID != 0}


@app.post("/api/inject")
async def inject(data: InjectInput):
    key = data.client_key.strip()
    conn = db_conn()
    client = conn.execute("SELECT * FROM clients WHERE LOWER(key)=LOWER(?)", (key,)).fetchone()
    if not client:
        conn.close()
        raise HTTPException(404, detail=f"Client key '{key}' nahi mila")
    if not client["active"]:
        conn.close()
        raise HTTPException(400, detail="Client inactive")

    status, msg_id, error = "pending", None, None
    try:
        msg_id = await send_to_telegram(client["target_chat_id"], data.sender_id, data.body)
        status = "delivered"
    except Exception as e:
        status, error = "failed", str(e)

    conn.execute(
        "INSERT INTO logs (client_key,client_name,sender_id,body,status,telegram_msg_id,error,direction,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (client["key"], client["name"], data.sender_id, data.body, status, msg_id, error, "web_to_telegram", now_iso()),
    )
    one_line = " ".join(data.body.splitlines())
    conn.execute(
        "INSERT INTO pending_injects (client_key,sender_id,body,status,created_at) VALUES (?,?,?,?,?)",
        (client["key"], data.sender_id, one_line, "queued", now_iso()),
    )
    conn.commit()
    conn.close()
    return {"status": status, "msg_id": msg_id, "detail": error}


@app.get("/api/device/pull", response_class=PlainTextResponse)
async def device_pull(key: str, secret: str, max: int = 20):
    if not DEVICE_SECRET or secret != DEVICE_SECRET:
        raise HTTPException(403, detail="Invalid device secret")
    conn = db_conn()
    rows = conn.execute(
        "SELECT id,sender_id,body FROM pending_injects WHERE LOWER(client_key)=LOWER(?) AND status='queued' ORDER BY created_at ASC LIMIT ?",
        (key, max),
    ).fetchall()
    if not rows:
        conn.close()
        return ""
    ids = [r["id"] for r in rows]
    conn.execute(
        f"UPDATE pending_injects SET status='delivered', delivered_at=? WHERE id IN ({','.join('?'*len(ids))})",
        [now_iso()] + ids,
    )
    conn.commit()
    conn.close()
    return "\n".join(f"{r['sender_id']}|{r['body']}" for r in rows)


@app.get("/api/logs")
async def api_logs(client_key: str | None = None, limit: int = 50):
    conn = db_conn()
    if client_key:
        rows = conn.execute("SELECT * FROM logs WHERE LOWER(client_key)=LOWER(?) ORDER BY id DESC LIMIT ?", (client_key, limit)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ================================================================
#  TELEGRAM ADMIN BOT
# ================================================================
ASK_NAME, ASK_CHATID = range(2)

MENU_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("🔑 Generate Client Key", callback_data="gen")],
    [InlineKeyboardButton("📋 List Clients", callback_data="list"),
     InlineKeyboardButton("📊 Stats", callback_data="stats")],
])


def is_admin(update: Update) -> bool:
    return update.effective_chat and update.effective_chat.id == ADMIN_CHAT_ID


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("❌ Aap admin nahi ho.")
        return ConversationHandler.END
    await update.message.reply_text(
        "👑 <b>PAPIATMA ADMIN PANEL</b>\n\nNeeche se option chuno:",
        parse_mode=ParseMode.HTML, reply_markup=MENU_KB,
    )
    return ConversationHandler.END


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(update):
        return ConversationHandler.END

    if q.data == "list":
        conn = db_conn()
        rows = conn.execute("SELECT key,name,target_chat_id,active FROM clients ORDER BY id DESC").fetchall()
        conn.close()
        if not rows:
            await q.message.reply_text("📭 Koi client nahi hai.")
        else:
            text = "📋 <b>Clients:</b>\n\n"
            for r in rows:
                text += f"{'✅' if r['active'] else '❌'} <b>{html.escape(r['name'])}</b>\n<code>{r['key']}</code>\nTarget: <code>{r['target_chat_id']}</code>\n\n"
            await q.message.reply_text(text, parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    if q.data == "stats":
        conn = db_conn()
        total = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM clients WHERE active=1").fetchone()[0]
        sent = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
        ok = conn.execute("SELECT COUNT(*) FROM logs WHERE status='delivered'").fetchone()[0]
        fail = conn.execute("SELECT COUNT(*) FROM logs WHERE status='failed'").fetchone()[0]
        conn.close()
        rate = round((ok/sent)*100, 1) if sent else 0
        await q.message.reply_text(
            f"📊 <b>Stats</b>\n\n👥 Clients: {total} (Active: {active})\n📨 Sent: {sent}\n✅ Delivered: {ok}\n❌ Failed: {fail}\n📈 Success: {rate}%",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    if q.data == "gen":
        await q.message.reply_text("📝 Client ka <b>naam</b> bhejo:", parse_mode=ParseMode.HTML)
        return ASK_NAME
    return ConversationHandler.END


async def got_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return ConversationHandler.END
    name = update.message.text.strip()
    context.user_data["new_client_name"] = name
    await update.message.reply_text(
        f"✅ Naam: <b>{html.escape(name)}</b>\n\nAb <b>target channel/group ki chat_id</b> bhejo:",
        parse_mode=ParseMode.HTML,
    )
    return ASK_CHATID


async def got_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return ConversationHandler.END
    chat_id = update.message.text.strip()
    name = context.user_data.pop("new_client_name", "Client")
    conn = db_conn()
    for _ in range(5):
        key = gen_key(name)
        if not conn.execute("SELECT 1 FROM clients WHERE key=?", (key,)).fetchone():
            break
    conn.execute("INSERT INTO clients (key,name,target_chat_id,active,created_at) VALUES (?,?,?,1,?)", (key, name, chat_id, now_iso()))
    conn.commit()
    conn.close()
    await update.message.reply_text(
        f"🎉 <b>Client Created!</b>\n\n👤 {html.escape(name)}\n🔑 <code>{key}</code>\n📬 <code>{chat_id}</code>",
        parse_mode=ParseMode.HTML, reply_markup=MENU_KB,
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancel kiya.")
    return ConversationHandler.END


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update):
        await update.message.reply_text("Samajh nahi aaya. /start bhejo.")


async def run_bot():
    request = HTTPXRequest(connection_pool_size=2, connect_timeout=15.0, read_timeout=60.0, pool_timeout=10.0)
    app_tg = Application.builder().token(BOT_TOKEN).get_updates_request(request).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CallbackQueryHandler(menu_callback, pattern="^(gen|list|stats)$"),
        ],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_name)],
            ASK_CHATID: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_chatid)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app_tg.add_handler(conv)
    app_tg.add_handler(MessageHandler(filters.ALL, unknown))

    await app_tg.initialize()
    await app_tg.start()
    await app_tg.updater.start_polling(drop_pending_updates=True)
    logger.info("✅ Telegram bot polling started")

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        await app_tg.updater.stop()
        await app_tg.stop()
        await app_tg.shutdown()


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=PORT, log_level="info")
