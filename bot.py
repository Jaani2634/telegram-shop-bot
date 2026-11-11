"""
Telegram Shop Bot - Digital Accounts Seller
Junaid ke liye - Manual (JazzCash/Easypaisa) + USDT (TRC20) dono

Flow:
 Buyer: /start -> Products -> Buy -> Payment choose
   - JazzCash/Easypaisa: number dikhega, TID ya screenshot bhejo -> Admin approve -> Auto delivery
   - USDT: TRC20 address + amount, TX hash bhejo -> Bot Tronscan se verify karega -> Auto delivery
 Admin: /admin -> Add Product, Add Stock, Pending Orders, Set Payments, Stock List

Setup: .env me BOT_TOKEN, ADMIN_ID bharo. Baaki payments bot se /admin me set ho jayenge.
Run: pip install -r requirements.txt && python bot.py
"""

import os
import sqlite3
import logging
import requests
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
DB_PATH = os.getenv("DB_PATH", "shop.db")

USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"  # USDT TRC20

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Conversation states
ADD_NAME, ADD_DESC, ADD_PKR, ADD_USDT = range(4)
STOCK_CHOOSE, STOCK_LINES = range(4, 6)
SET_PAY_KEY, SET_PAY_VAL = range(6, 8)
BUY_TID, BUY_HASH = range(8, 10)

# ---------- DB ----------

def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        price_pkr INTEGER NOT NULL,
        price_usdt REAL NOT NULL,
        active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS accounts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        login TEXT NOT NULL,
        password TEXT NOT NULL,
        status TEXT DEFAULT 'available',
        sold_to INTEGER,
        sold_at TEXT,
        order_id INTEGER
    );
    CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        username TEXT,
        product_id INTEGER NOT NULL,
        payment_method TEXT NOT NULL,
        amount_text TEXT,
        proof TEXT,
        status TEXT DEFAULT 'pending',
        account_id INTEGER,
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY, value TEXT
    );
    """)
    defaults = {
        "jazzcash_number": os.getenv("JAZZCASH_NUMBER", "03XX-XXXXXXX"),
        "jazzcash_name": os.getenv("JAZZCASH_NAME", "Junaid"),
        "easypaisa_number": os.getenv("EASYPAISA_NUMBER", "03XX-XXXXXXX"),
        "easypaisa_name": os.getenv("EASYPAISA_NAME", "Junaid"),
        "usdt_address": os.getenv("USDT_ADDRESS", "TXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"),
    }
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    # Auto-seed products on a fresh DB (e.g. Render free restarts wipe shop.db).
    # Stock is NOT seeded here — accounts must be added with /addstock.
    n = c.execute("SELECT COUNT(*) n FROM products").fetchone()["n"]
    if n == 0:
        for name, desc, pkr, usdt in SEED_PRODUCTS:
            c.execute("INSERT INTO products(name,description,price_pkr,price_usdt) VALUES(?,?,?,?)",
                      (name, desc, pkr, usdt))
        log.info("Seeded %d products", len(SEED_PRODUCTS))
    c.commit(); c.close()

# Products from Junaid's screenshots (11 Nov 2026). USDT at 280 PKR.
SEED_PRODUCTS = [
    ("Amazon Prime 6 Months", "Limited Deal - was PKR 850", 699, 2.50),
    ("Udemy Personal Plan 1M", "Limited Deal - was PKR 669", 559, 2.00),
    ("Capcut Pro 1 Month", "Premium Service", 526, 1.88),
    ("HMA Vpn Monthly", "Premium Service", 186, 0.66),
    ("Microsoft 365 Plus 1 Year", "Premium Service", 501, 1.79),
    ("Muse Ai 1B Credits", "Premium Service", 71, 0.25),
    ("Windows 10/11 Pro", "Premium Service", 276, 0.99),
    ("Muse Ai 31B Token (NW)", "Premium Service", 369, 1.32),
    ("Capcut Pro 7D 500 credits", "", 114, 0.41),
    ("500$ API CLAUDE 30D (FW)", "", 2499, 8.93),
    ("100$ API Claude 30D (FW)", "", 749, 2.68),
    ("Adobe Express 12M", "", 169, 0.60),
    ("Gemini Plus 6m Links (No Card Required)", "", 379, 1.35),
    ("MUSE AI 1B TOKEN (W2D)", "", 139, 0.50),
    ("Outlook IMAP", "", 35, 0.13),
    ("Hotmail Mail Accounts", "", 35, 0.13),
    ("Owl Proxy 200 Mb", "", 49, 0.18),
    ("iLovePdf Premium 1 Yr", "", 419, 1.50),
    ("Chatgpt Plus 1M (25DW)", "", 2549, 9.10),
    ("Coursera Plus 7 Days Trial", "", 211, 0.75),
    ("Bolt .new Pro 1 Month", "", 399, 1.43),
    ("3 in 1 VIP Group", "", 12999, 46.43),
    ("Canva Edu 3 Years Invite On Client Mail", "Invite on client mail", 250, 0.89),
    ("CrunchyRoll 1 Year Mega Fan Profile (FW)", "", 599, 2.14),
    ("Hostinger Unlimited Plan 12 Months (FW)", "", 5550, 19.82),
    ("Muse Ai Token On Client Account", "", 50, 0.18),
]

# Render free Web Service must bind to $PORT, otherwise deploy fails.
# This tiny health server also gives UptimeRobot something to ping every
# 5 minutes so the free instance does not sleep.
def start_health_server():
    # Render uses PORT, Botkeep uses SERVER_PORT — support both.
    # Botkeep killed the bot 3 times after ~3-4 min with no logs; most likely
    # cause is no HTTP listener (no PORT/SERVER_PORT injected -> port 0 ->
    # health server never started -> platform marks it unhealthy).
    # So ALWAYS bind something: env port first, else 8080 fallback.
    raw = os.getenv("PORT") or os.getenv("SERVER_PORT") or ""
    try:
        port = int(raw) if raw else 8080
    except ValueError:
        port = 8080
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK - RetailShop bot running")
        def log_message(self, *a):
            pass
    for try_port in (port, 8080, 8000):
        try:
            threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", try_port), H).serve_forever,
                             daemon=True).start()
            log.info("Health server on port %d", try_port)
            print(f"Health server on port {try_port}", flush=True)
            return
        except OSError as e:
            log.warning("Health server bind failed on port %d: %s", try_port, e)
    log.warning("Health server could not bind any port")

def get_setting(key):
    c = db(); r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone(); c.close()
    return r["value"] if r else ""

def set_setting(key, value):
    c = db(); c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value)); c.commit(); c.close()

def stock_count(product_id):
    c = db(); r = c.execute("SELECT COUNT(*) n FROM accounts WHERE product_id=? AND status='available'", (product_id,)).fetchone(); c.close()
    return r["n"]

def is_admin(uid):
    return ADMIN_ID != 0 and uid == ADMIN_ID

# ---------- Auto Delivery ----------

async def deliver_order(context: ContextTypes.DEFAULT_TYPE, order_id: int):
    """Ek unused account assign karo, sold mark karo, buyer ko credentials bhejo."""
    c = db()
    order = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        c.close(); return False
    product = c.execute("SELECT * FROM products WHERE id=?", (order["product_id"],)).fetchone()
    acc = c.execute("SELECT * FROM accounts WHERE product_id=? AND status='available' ORDER BY id LIMIT 1", (order["product_id"],)).fetchone()
    if not acc:
        c.execute("UPDATE orders SET status='out_of_stock' WHERE id=?", (order_id,))
        c.commit(); c.close()
        await context.bot.send_message(ADMIN_ID, f"⚠️ Order #{order_id} ke liye stock khatam! Product: {product['name']}. Stock add karo, phir manually deliver karna hoga.")
        await context.bot.send_message(order["user_id"], f"Sorry, {product['name']} ka stock khatam ho gaya. Admin se rabta karein, refund / replacement milega.")
        return False
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    c.execute("UPDATE accounts SET status='sold', sold_to=?, sold_at=?, order_id=? WHERE id=?", (order["user_id"], now, order_id, acc["id"]))
    c.execute("UPDATE orders SET status='delivered', account_id=? WHERE id=?", (acc["id"], order_id))
    c.commit(); c.close()
    msg = (
        f"✅ Payment confirmed! Aapka account mil gaya:\n\n"
        f"📦 Product: {product['name']}\n"
        f"👤 Login / Email: `{acc['login']}`\n"
        f"🔑 Password: `{acc['password']}`\n\n"
        f"Order #{order_id}\n"
        f"Account ko save kar lein. Koi issue ho to admin se rabta karein."
    )
    await context.bot.send_message(order["user_id"], msg, parse_mode="Markdown")
    return True

async def notify_admin_pending(context, order_id):
    c = db(); o = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    p = c.execute("SELECT * FROM products WHERE id=?", (o["product_id"],)).fetchone(); c.close()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Approve + Auto Deliver", callback_data=f"approve_{order_id}"),
         InlineKeyboardButton("❌ Reject", callback_data=f"reject_{order_id}")]
    ])
    text = (f"🛒 New Order #{order_id}\nProduct: {p['name']}\nBuyer: @{o['username'] or o['user_id']} ({o['user_id']})\n"
            f"Payment: {o['payment_method']}\nAmount: {o['amount_text']}\nProof: {o['proof']}\nStock left: {stock_count(p['id'])}")
    await context.bot.send_message(ADMIN_ID, text, reply_markup=kb)

# ---------- USDT Verify (Tronscan public API, no key) ----------

def verify_usdt(tx_hash: str, expected_address: str, expected_amount: float):
    """Returns (ok, reason). Tronscan se TX check karta hai."""
    try:
        r = requests.get("https://apilist.tronscanapi.com/api/transaction-info", params={"hash": tx_hash}, timeout=15)
        d = r.json()
        if not d or d.get("contractRet") != "SUCCESS":
            return False, "TX success nahi hai ya hash galat hai."
        # token transfers
        transfers = d.get("tokenTransferInfo", {}) or {}
        # tronscan format kabhi dict, kabhi list hota hai
        items = []
        if isinstance(transfers, dict):
            # single transfer ya transfers dict
            if "to_address" in transfers:
                items = [transfers]
            else:
                items = list(transfers.values())
        elif isinstance(transfers, list):
            items = transfers
        for t in items:
            if not isinstance(t, dict):
                continue
            to_addr = t.get("to_address", "")
            amount_raw = t.get("amount_str") or t.get("amount") or 0
            try:
                amt = int(amount_raw) / 1_000_000  # USDT 6 decimals
            except Exception:
                continue
            contract = (t.get("contract_address") or t.get("tokenId") or "")
            if to_addr == expected_address and abs(amt - expected_amount) < 0.01:
                return True, f"Verified: {amt} USDT to {to_addr}"
            if to_addr == expected_address:
                return False, f"Amount mismatch: bheje gaye {amt} USDT, chahiye {expected_amount} USDT."
        # fallback: raw contract data check
        return False, "Is TX me aapke USDT address par payment nahi mili. Hash dobara check karein."
    except Exception as e:
        log.exception("USDT verify error")
        return False, f"Verify API error: {e}. Admin manually check karega."

# ---------- Buyer ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c = db(); prods = c.execute("SELECT * FROM products WHERE active=1 ORDER BY id").fetchall(); c.close()
    if not prods:
        await update.message.reply_text("👋 Welcome! Abhi koi product available nahi hai. Thodi der me check karein.")
        return
    kb = []
    for p in prods:
        kb.append([InlineKeyboardButton(f"{p['name']} — Rs {p['price_pkr']} / ${p['price_usdt']}  (Stock: {stock_count(p['id'])})", callback_data=f"prod_{p['id']}")])
    await update.message.reply_text("🛍️ Products — koi ek choose karein:", reply_markup=InlineKeyboardMarkup(kb))

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = q.data

    if data.startswith("prod_"):
        pid = int(data.split("_")[1])
        c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone(); c.close()
        if not p:
            await q.edit_message_text("Product nahi mila."); return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Buy Now", callback_data=f"buy_{pid}")],
            [InlineKeyboardButton("⬅ Back", callback_data="back_list")]
        ])
        await q.edit_message_text(
            f"📦 {p['name']}\n\n{p['description']}\n\n💰 Price: Rs {p['price_pkr']}  |  ${p['price_usdt']} USDT\n📦 Stock: {stock_count(pid)}",
            reply_markup=kb)
        return

    if data == "back_list":
        c = db(); prods = c.execute("SELECT * FROM products WHERE active=1 ORDER BY id").fetchall(); c.close()
        kb = [[InlineKeyboardButton(f"{p['name']} — Rs {p['price_pkr']} / ${p['price_usdt']} (Stock: {stock_count(p['id'])})", callback_data=f"prod_{p['id']}")] for p in prods]
        await q.edit_message_text("🛍️ Products — koi ek choose karein:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("buy_"):
        pid = int(data.split("_")[1])
        if stock_count(pid) == 0:
            await q.edit_message_text("❌ Is product ka stock khatam hai. Dusra product choose karein.")
            return
        context.user_data["buy_pid"] = pid
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💚 JazzCash", callback_data="pay_jazzcash")],
            [InlineKeyboardButton("💚 Easypaisa", callback_data="pay_easypaisa")],
            [InlineKeyboardButton("🪙 USDT (TRC20)", callback_data="pay_usdt")],
            [InlineKeyboardButton("⬅ Back", callback_data=f"prod_{pid}")]
        ])
        await q.edit_message_text("💳 Payment method choose karein:", reply_markup=kb)
        return

    if data.startswith("pay_"):
        method = data.split("_")[1]  # jazzcash / easypaisa / usdt
        pid = context.user_data.get("buy_pid")
        if not pid:
            await q.edit_message_text("Session expire ho gayi. /start se dobara karein."); return
        c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone(); c.close()
        context.user_data["pay_method"] = method
        if method in ("jazzcash", "easypaisa"):
            num = get_setting(f"{method}_number"); name = get_setting(f"{method}_name")
            label = "JazzCash" if method == "jazzcash" else "Easypaisa"
            await q.edit_message_text(
                f"💚 {label} Payment\n\nProduct: {p['name']}\nAmount: Rs {p['price_pkr']}\n\nSend to:\nNumber: `{num}`\nName: {name}\n\nPayment karke **Transaction ID (TID)** yahan bhejein, ya screenshot bhejein.\nAdmin approve karte hi account auto deliver ho jayega.",
                parse_mode="Markdown")
            context.user_data["awaiting"] = "tid"
        else:
            addr = get_setting("usdt_address")
            await q.edit_message_text(
                f"🪙 USDT (TRC20) Payment\n\nProduct: {p['name']}\nAmount: **${p['price_usdt']} USDT**\n\nSend to (TRC20 only):\n`{addr}`\n\nPayment karke **Transaction Hash** yahan bhejein. Bot khud verify karega, phir account auto deliver ho jayega.",
                parse_mode="Markdown")
            context.user_data["awaiting"] = "hash"
        return

    if data.startswith("approve_"):
        if not is_admin(q.from_user.id):
            await q.answer("Sirf admin.", show_alert=True); return
        oid = int(data.split("_")[1])
        ok = await deliver_order(context, oid)
        if ok:
            await q.edit_message_text(f"✅ Order #{oid} approved + delivered.")
        else:
            await q.edit_message_text(f"⚠️ Order #{oid} deliver nahi hui — stock khatam hai.")
        return

    if data.startswith("reject_"):
        if not is_admin(q.from_user.id):
            await q.answer("Sirf admin.", show_alert=True); return
        oid = int(data.split("_")[1])
        c = db(); c.execute("UPDATE orders SET status='rejected' WHERE id=?", (oid,))
        o = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone(); c.commit(); c.close()
        await q.edit_message_text(f"❌ Order #{oid} rejected.")
        if o:
            await context.bot.send_message(o["user_id"], f"❌ Aapki Order #{oid} reject ho gayi. Payment proof galat ho sakta hai. Admin se rabta karein.")
        return

# Buyer proof messages (TID / hash / screenshot)
async def on_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    awaiting = context.user_data.get("awaiting")
    pid = context.user_data.get("buy_pid")
    method = context.user_data.get("pay_method")
    if not awaiting or not pid:
        return  # normal chat, ignore
    c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone(); c.close()
    user = update.effective_user

    if awaiting == "tid":
        proof = update.message.text if update.message.text else "screenshot"
        if update.message.photo:
            proof = f"photo:{update.message.photo[-1].file_id}"
        amount_text = f"Rs {p['price_pkr']} via {method}"
        c = db(); cur = c.execute(
            "INSERT INTO orders(user_id,username,product_id,payment_method,amount_text,proof,status,created_at) VALUES(?,?,?,?,?,?, 'pending', ?)",
            (user.id, user.username or "", pid, method, amount_text, proof, datetime.now().strftime("%Y-%m-%d %H:%M")))
        oid = cur.lastrowid; c.commit(); c.close()
        context.user_data.clear()
        await update.message.reply_text(f"✅ Order #{oid} mil gayi! Admin verify kar raha hai. Approve hote hi account isi chat me aa jayega.")
        await notify_admin_pending(context, oid)

    elif awaiting == "hash":
        tx = (update.message.text or "").strip()
        if len(tx) < 20:
            await update.message.reply_text("Ye hash lagta nahi. Poora Transaction Hash bhejein (TronScan link se copy karein)."); return
        addr = get_setting("usdt_address")
        ok, reason = verify_usdt(tx, addr, float(p["price_usdt"]))
        if ok:
            c = db(); cur = c.execute(
                "INSERT INTO orders(user_id,username,product_id,payment_method,amount_text,proof,status,created_at) VALUES(?,?,?,?,?,?, 'pending', ?)",
                (user.id, user.username or "", pid, "usdt", f"${p['price_usdt']} USDT", tx, datetime.now().strftime("%Y-%m-%d %H:%M")))
            oid = cur.lastrowid; c.commit(); c.close()
            context.user_data.clear()
            await update.message.reply_text(f"✅ USDT verified! {reason}\nOrder #{oid} — account abhi bhej raha hun...")
            await deliver_order(context, oid)
        else:
            # verify fail -> pending for manual admin check, buyer ko batao
            c = db(); cur = c.execute(
                "INSERT INTO orders(user_id,username,product_id,payment_method,amount_text,proof,status,created_at) VALUES(?,?,?,?,?,?, 'pending', ?)",
                (user.id, user.username or "", pid, "usdt", f"${p['price_usdt']} USDT", tx, datetime.now().strftime("%Y-%m-%d %H:%M")))
            oid = cur.lastrowid; c.commit(); c.close()
            context.user_data.clear()
            await update.message.reply_text(f"⚠️ Auto-verify nahi hui: {reason}\nOrder #{oid} admin ko bhej di hai — wo manually check karke deliver karega.")
            await notify_admin_pending(context, oid)

# ---------- Admin ----------

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Sirf admin ke liye hai."); return
    c = db()
    prods = c.execute("SELECT * FROM products ORDER BY id").fetchall()
    pending = c.execute("SELECT COUNT(*) n FROM orders WHERE status='pending'").fetchone()["n"]
    c.close()
    lines = ["👑 Admin Panel\n", f"Pending orders: {pending}", ""]
    for p in prods:
        lines.append(f"#{p['id']} {p['name']} — Rs {p['price_pkr']} / ${p['price_usdt']} — Stock: {stock_count(p['id'])}")
    lines.append("\nCommands:\n/addproduct — naya product\n/addstock — accounts add karo\n/orders — pending orders\n/setpayments — JazzCash/Easypaisa/USDT details\n/stock — saara stock dekho")
    await update.message.reply_text("\n".join(lines))

async def orders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    c = db(); rows = c.execute("SELECT * FROM orders WHERE status='pending' ORDER BY id DESC LIMIT 10").fetchall(); c.close()
    if not rows:
        await update.message.reply_text("Koi pending order nahi."); return
    for o in rows:
        c = db(); p = c.execute("SELECT name FROM products WHERE id=?", (o["product_id"],)).fetchone(); c.close()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"approve_{o['id']}"), InlineKeyboardButton("❌ Reject", callback_data=f"reject_{o['id']}")]])
        await update.message.reply_text(f"Order #{o['id']}\nProduct: {p['name']}\nBuyer: @{o['username'] or o['user_id']}\nPayment: {o['payment_method']} — {o['amount_text']}\nProof: {o['proof']}", reply_markup=kb)

async def stock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    c = db(); prods = c.execute("SELECT * FROM products ORDER BY id").fetchall(); c.close()
    msg = []
    for p in prods:
        msg.append(f"#{p['id']} {p['name']}: {stock_count(p['id'])} available")
    await update.message.reply_text("\n".join(msg) or "Koi product nahi.")

# Add product conversation
async def addproduct_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Sirf admin."); return ConversationHandler.END
    await update.message.reply_text("Product ka naam bhejein:")
    return ADD_NAME

async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["p_name"] = update.message.text.strip()
    await update.message.reply_text("Description bhejein (ya /skip):")
    return ADD_DESC

async def add_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["p_desc"] = "" if update.message.text == "/skip" else update.message.text.strip()
    await update.message.reply_text("Price PKR me bhejein (sirf number, jaise 500):")
    return ADD_PKR

async def add_pkr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["p_pkr"] = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Sirf number bhejein, jaise 500:"); return ADD_PKR
    await update.message.reply_text("Price USDT me bhejein (jaise 2.5):")
    return ADD_USDT

async def add_usdt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        usdt = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Number bhejein, jaise 2.5:"); return ADD_USDT
    c = db(); cur = c.execute("INSERT INTO products(name,description,price_pkr,price_usdt) VALUES(?,?,?,?)",
        (context.user_data["p_name"], context.user_data["p_desc"], context.user_data["p_pkr"], usdt))
    pid = cur.lastrowid; c.commit(); c.close()
    context.user_data.clear()
    await update.message.reply_text(f"✅ Product add ho gaya! ID #{pid}\nAb /addstock se is me accounts add karein.")
    return ConversationHandler.END

# Add stock conversation
async def addstock_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Sirf admin."); return ConversationHandler.END
    c = db(); prods = c.execute("SELECT * FROM products ORDER BY id").fetchall(); c.close()
    if not prods:
        await update.message.reply_text("Pehle /addproduct se product banao."); return ConversationHandler.END
    msg = "Kis product me stock add karna hai? Product ID bhejein:\n" + "\n".join([f"#{p['id']} {p['name']}" for p in prods])
    await update.message.reply_text(msg)
    return STOCK_CHOOSE

async def stock_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        pid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Product ID number me bhejein:"); return STOCK_CHOOSE
    c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone(); c.close()
    if not p:
        await update.message.reply_text("Ye ID nahi mili. Dobara bhejein:"); return STOCK_CHOOSE
    context.user_data["stock_pid"] = pid
    await update.message.reply_text(
        "Accounts bhejein — har line pe ek account:\n"
        "`email | password`\n"
        "ya\n`username | password`\n\nExample:\ntest@gmail.com | Pass123\nuser2 | Abc456",
        parse_mode="Markdown")
    return STOCK_LINES

async def stock_lines(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pid = context.user_data["stock_pid"]
    lines = [l.strip() for l in (update.message.text or "").splitlines() if l.strip()]
    added = 0
    c = db()
    for line in lines:
        if "|" in line:
            login, pwd = [x.strip() for x in line.split("|", 1)]
        elif "," in line:
            login, pwd = [x.strip() for x in line.split(",", 1)]
        else:
            parts = line.split()
            if len(parts) < 2: continue
            login, pwd = parts[0], parts[1]
        if login and pwd:
            c.execute("INSERT INTO accounts(product_id,login,password) VALUES(?,?,?)", (pid, login, pwd))
            added += 1
    c.commit(); c.close()
    context.user_data.clear()
    await update.message.reply_text(f"✅ {added} accounts add ho gaye! Total stock ab: {stock_count(pid)}")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

# Set payments
async def setpayments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    msg = (f"Current payments:\nJazzCash: {get_setting('jazzcash_number')} ({get_setting('jazzcash_name')})\n"
           f"Easypaisa: {get_setting('easypaisa_number')} ({get_setting('easypaisa_name')})\n"
           f"USDT TRC20: {get_setting('usdt_address')}\n\n"
           "Change karne ke liye format:\n`/set jazzcash_number 03001234567`\nKeys: jazzcash_number, jazzcash_name, easypaisa_number, easypaisa_name, usdt_address")
    await update.message.reply_text(msg, parse_mode="Markdown")

async def set_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /set jazzcash_number 03001234567"); return
    key, val = context.args[0], " ".join(context.args[1:])
    if key not in ("jazzcash_number", "jazzcash_name", "easypaisa_number", "easypaisa_name", "usdt_address"):
        await update.message.reply_text("Galat key. Keys: jazzcash_number, jazzcash_name, easypaisa_number, easypaisa_name, usdt_address"); return
    set_setting(key, val)
    await update.message.reply_text(f"✅ {key} = {val}")

# ---------- Main ----------

def main():
    init_db()
    start_health_server()
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN .env me nahi hai. .env.example dekho.")
        return
    if ADMIN_ID == 0:
        print("WARNING: ADMIN_ID set nahi hai — /admin kaam nahi karega. @userinfobot se apni ID le ke .env me dalo.")
    app = Application.builder().token(BOT_TOKEN).build()

    add_prod = ConversationHandler(
        entry_points=[CommandHandler("addproduct", addproduct_start)],
        states={ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
                ADD_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_desc)],
                ADD_PKR: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_pkr)],
                ADD_USDT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_usdt)]},
        fallbacks=[CommandHandler("cancel", cancel)])

    add_stock = ConversationHandler(
        entry_points=[CommandHandler("addstock", addstock_start)],
        states={STOCK_CHOOSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, stock_choose)],
                STOCK_LINES: [MessageHandler(filters.TEXT & ~filters.COMMAND, stock_lines)]},
        fallbacks=[CommandHandler("cancel", cancel)])

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("orders", orders_cmd))
    app.add_handler(CommandHandler("stock", stock_cmd))
    app.add_handler(CommandHandler("setpayments", setpayments))
    app.add_handler(CommandHandler("set", set_cmd))
    app.add_handler(add_prod)
    app.add_handler(add_stock)
    app.add_handler(CallbackQueryHandler(on_callback))
    # proof messages: text TID/hash + photo screenshot — admin commands se pehle nahi, baad me
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, on_proof))

    print("Bot chal raha hai... /start se test karo.", flush=True)
    # Keep the process alive: if polling ever raises, log it and restart
    # polling instead of letting the whole process exit (Botkeep does not
    # auto-restart a stopped server).
    import time as _time
    while True:
        try:
            app.run_polling()
            log.warning("run_polling returned cleanly; restarting polling")
        except Exception:
            log.exception("run_polling crashed; restarting in 5s")
        _time.sleep(5)

if __name__ == "__main__":
    main()
