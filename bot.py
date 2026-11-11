"""
Telegram Shop Bot - Digital Accounts Seller
Junaid ke liye - Manual (JazzCash/Easypaisa) + USDT (TRC20) dono

Flow:
 Buyer: /start -> channel gate -> Products -> quantity -> terms -> summary
   -> payment (PKR manual / USDT TRC20 / wallet) -> proof -> admin approve
   / USDT auto-verify -> auto delivery
 Features: cart, wallet + deposits + top-up codes, first-order coupon (7%),
 categories + pagination + search, reviews, giveaways + referrals,
 support inbox, bulk discounts, mail-type delivery (Muse tokens).
 Admin: /admin -> Add Product, Add Stock, Pending Orders, Set Payments, Stock List
        + /setdesc /setterms /setbulk /setunlimited /setkind /addreview /topupcode /reply

Setup: .env me BOT_TOKEN, ADMIN_ID bharo. Baaki payments bot se /admin me set ho jayenge.
Run: pip install -r requirements.txt && python bot.py
"""

import os
import io
import json
import random
import sqlite3
import logging
import requests
import threading
import asyncio
import hashlib
import hmac
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
DB_PATH = os.getenv("DB_PATH", "shop.db")
# Channels: Telegram channel (username ya ID) + WhatsApp channel link.
# Dono /set se bhi change ho sakte hain: /set tg_channel @name, /set wa_channel_url https://...
TG_CHANNEL = os.getenv("TG_CHANNEL", "")
TG_GROUP_URL = os.getenv("TG_GROUP_URL", "")
WA_CHANNEL_URL = os.getenv("WA_CHANNEL_URL", "")

USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"  # USDT TRC20
USDT_RATE = 280  # 1 USDT = 280 PKR (seed prices isi rate pe hain)
BOT_USERNAME = "Shop_at_Retail_bot"

# ---------- Zurvapay (auto-verify JazzCash / Easypaisa / Raast QR) ----------
# zurvapay.com — hosted checkout + signed IPN. Keys kabhi code me nahi —
# sirf env se. Khaali ho to Zurvapay option dikhega hi nahi (feature off).
ZURVAPAY_PUBLIC_KEY = os.getenv("ZURVAPAY_PUBLIC_KEY", "")
ZURVAPAY_SECRET_KEY = os.getenv("ZURVAPAY_SECRET_KEY", "")
ZURVAPAY_BASE_URL = os.getenv("ZURVAPAY_BASE_URL", "https://zurvapay.com").rstrip("/")
# Bot ki public URL (Botkeep/Render host), jaise https://node19.botkeep.cloud:61495.
# IPN + success/cancel URLs isi se bante hain. ZURVAPAY_IPN_URL se IPN ka
# URL seedha bhi set kar sakte ho.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
ZURVAPAY_IPN_URL = os.getenv("ZURVAPAY_IPN_URL", "")
# Optional status-check endpoint — Zurvapay kehta hai status API hai, lekin
# uska URL public docs me nahi hai; dashboard docs se milte hi yahan set karo.
ZURVAPAY_STATUS_URL = os.getenv("ZURVAPAY_STATUS_URL", "")
# IPN signature scheme — 'auto' (default) tamam common schemes try karta hai.
# Dashboard docs me jo scheme likhi ho, usay yahan pin kar do:
# hmac_pipe_sha256 | hmac_pipe_sha512 | hmac_body_sha256 | hmac_body_sha512 |
# hmac_sorted_sha256 | hmac_sorted_sha512 | sha256_concat | hmac_pipe_status_sha256
ZURVAPAY_IPN_MODE = os.getenv("ZURVAPAY_IPN_MODE", "auto")

# IPN HTTP thread se PTB loop tak pohanchne ke liye (main() me set hota hai).
PTB_APP = None
PTB_LOOP = None

PROMO_BANNER = "🎁 Spend $6+ in one order, get Muse Ai 1B, Canva Pro On Mail FREE"
DEFAULT_TERMS = ("This product can be unstable. Warranty covers first login only. "
                 "No refund/replacement after first login.")
FIRST_COUPON_CODE = "FIRST-ORDER"
FIRST_COUPON_PCT = 7
GIVEAWAYS = [
    ("Muse 1B Credits", "Muse Ai 1B Credits"),
    ("Make.com 3 Months", "Make.com 3 months plan"),
    ("Canva Pro", "Canva Pro"),
]
CATEGORIES = ["Muse Ai", "Capcut Pro", "Claude Api", "Gemini"]
CATEGORY_MAP = {6: "Muse Ai", 8: "Muse Ai", 14: "Muse Ai", 26: "Muse Ai",
                3: "Capcut Pro", 9: "Capcut Pro", 10: "Claude Api",
                11: "Claude Api", 13: "Gemini"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Conversation states (addproduct / addstock only; baaki flows user_data["awaiting"] se chalte hain)
ADD_NAME, ADD_DESC, ADD_PKR, ADD_USDT = range(4)
STOCK_CHOOSE, STOCK_LINES = range(4, 6)

# ---------- DB ----------

def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def _add_col(c, table, col, ddl):
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")

def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        price_pkr INTEGER NOT NULL,
        price_usdt REAL NOT NULL,
        active INTEGER DEFAULT 1,
        kind TEXT DEFAULT 'account',
        category TEXT DEFAULT '',
        bulk TEXT DEFAULT '',
        terms TEXT DEFAULT '',
        unlimited INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS accounts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        login TEXT NOT NULL,
        password TEXT NOT NULL,
        extra TEXT DEFAULT '',
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
        created_at TEXT,
        code TEXT DEFAULT '',
        items_json TEXT DEFAULT '',
        qty INTEGER DEFAULT 1,
        discount_pkr INTEGER DEFAULT 0,
        coupon_code TEXT DEFAULT '',
        amount_pkr INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY, value TEXT
    );
    CREATE TABLE IF NOT EXISTS user_flags(
        user_id INTEGER PRIMARY KEY,
        wa_joined INTEGER DEFAULT 0,
        checked_at TEXT
    );
    CREATE TABLE IF NOT EXISTS coupons(
        user_id INTEGER NOT NULL,
        code TEXT NOT NULL,
        used INTEGER DEFAULT 0,
        created TEXT,
        PRIMARY KEY (user_id, code)
    );
    CREATE TABLE IF NOT EXISTS carts(
        user_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        qty INTEGER DEFAULT 1,
        PRIMARY KEY (user_id, product_id)
    );
    CREATE TABLE IF NOT EXISTS reviews(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        user_name TEXT,
        rating INTEGER NOT NULL,
        text TEXT,
        verified INTEGER DEFAULT 0,
        source TEXT DEFAULT 'user'
    );
    CREATE TABLE IF NOT EXISTS wallets(
        user_id INTEGER NOT NULL,
        currency TEXT NOT NULL,
        balance REAL DEFAULT 0,
        PRIMARY KEY (user_id, currency)
    );
    CREATE TABLE IF NOT EXISTS deposits(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        username TEXT,
        currency TEXT NOT NULL,
        amount REAL NOT NULL,
        payment_method TEXT NOT NULL,
        proof TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS topup_codes(
        code TEXT PRIMARY KEY,
        amount REAL NOT NULL,
        currency TEXT NOT NULL,
        used_by INTEGER,
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS referrals(
        referrer_id INTEGER NOT NULL,
        referred_id INTEGER NOT NULL,
        verified INTEGER DEFAULT 0,
        created TEXT,
        PRIMARY KEY (referrer_id, referred_id)
    );
    CREATE TABLE IF NOT EXISTS support_msgs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        username TEXT,
        text TEXT,
        direction TEXT DEFAULT 'user',
        created_at TEXT
    );
    """)
    defaults = {
        "jazzcash_number": os.getenv("JAZZCASH_NUMBER", "03XX-XXXXXXX"),
        "jazzcash_name": os.getenv("JAZZCASH_NAME", "Junaid"),
        "easypaisa_number": os.getenv("EASYPAISA_NUMBER", "03XX-XXXXXXX"),
        "easypaisa_name": os.getenv("EASYPAISA_NAME", "Junaid"),
        "usdt_address": os.getenv("USDT_ADDRESS", "TXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"),
        "tg_channel": TG_CHANNEL,
        "tg_group_url": TG_GROUP_URL,
        "wa_channel_url": WA_CHANNEL_URL,
        "support_username": "",
    }
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    # Migrations for DBs created before these columns existed.
    for col, ddl in (("kind", "TEXT DEFAULT 'account'"), ("category", "TEXT DEFAULT ''"),
                     ("bulk", "TEXT DEFAULT ''"), ("terms", "TEXT DEFAULT ''"),
                     ("unlimited", "INTEGER DEFAULT 0")):
        _add_col(c, "products", col, ddl)
    _add_col(c, "accounts", "extra", "TEXT DEFAULT ''")
    for col, ddl in (("code", "TEXT DEFAULT ''"), ("items_json", "TEXT DEFAULT ''"),
                     ("qty", "INTEGER DEFAULT 1"), ("discount_pkr", "INTEGER DEFAULT 0"),
                     ("coupon_code", "TEXT DEFAULT ''"), ("amount_pkr", "INTEGER DEFAULT 0")):
        _add_col(c, "orders", col, ddl)
    # Auto-seed products on a fresh DB (e.g. Render free restarts wipe shop.db).
    # Stock is NOT seeded here — accounts must be added with /addstock.
    n = c.execute("SELECT COUNT(*) n FROM products").fetchone()["n"]
    if n == 0:
        for name, desc, pkr, usdt in SEED_PRODUCTS:
            c.execute("INSERT INTO products(name,description,price_pkr,price_usdt) VALUES(?,?,?,?)",
                      (name, desc, pkr, usdt))
        log.info("Seeded %d products", len(SEED_PRODUCTS))
    # Categories assign karo (sirf jahan abhi khaali hai — admin ki change overwrite nahi hogi).
    for pid, cat in CATEGORY_MAP.items():
        c.execute("UPDATE products SET category=? WHERE id=? AND (category IS NULL OR category='')", (cat, pid))
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
            if self.path.startswith("/zurvapay/success"):
                body = b"Payment successful! Apne Telegram bot pe ja ke order check karein."
            elif self.path.startswith("/zurvapay/cancel"):
                body = b"Payment cancel ho gayi. Telegram bot se dobara try karein."
            else:
                body = b"OK - RetailShop bot running"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            # Zurvapay IPN — signed server-to-server callback. 200 = mil gaya
            # (Zurvapay 8 dafa retry karta hai agar 200 na mile).
            if self.path.startswith("/zurvapay/ipn"):
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                raw = self.rfile.read(length) if length > 0 else b""
                try:
                    ok, msg = handle_zurvapay_ipn_http(raw, self.headers)
                except Exception as e:
                    log.exception("Zurvapay IPN handler crashed")
                    ok, msg = False, f"handler error: {e}"
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(msg.encode("utf-8", "ignore"))
                return
            self.send_response(404)
            self.end_headers()
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

# ---------- Currency / prices ----------

def user_currency(uid):
    v = get_setting(f"currency_{uid}")
    return v if v in ("PKR", "USDT") else "PKR"

def set_user_currency(uid, cur):
    set_setting(f"currency_{uid}", cur)

def price_text(p, uid):
    if p["price_pkr"] == 0 and p["price_usdt"] == 0:
        return "🆓 FREE"
    return f"Rs {p['price_pkr']}" if user_currency(uid) == "PKR" else f"${p['price_usdt']} USDT"

def fmt_money(pkr, uid):
    """PKR amount ko user ki currency me dikhao."""
    if user_currency(uid) == "USDT":
        return f"${round(pkr / USDT_RATE, 2)} USDT"
    return f"Rs {pkr}"

def parse_bulk(s):
    """'3:360,5:345' -> [(3,360),(5,345)] sorted by qty."""
    tiers = []
    for part in (s or "").split(","):
        part = part.strip()
        if ":" not in part:
            continue
        q, up = part.split(":", 1)
        try:
            tiers.append((int(q.strip()), int(up.strip())))
        except ValueError:
            continue
    return sorted(tiers)

def unit_price_pkr(p, qty):
    price = p["price_pkr"]
    bulk = p["bulk"] if "bulk" in p.keys() else ""
    for q, up in parse_bulk(bulk):
        if qty >= q:
            price = up
    return price

def bulk_text(p, uid):
    tiers = parse_bulk(p["bulk"] if "bulk" in p.keys() else "")
    if not tiers:
        return ""
    parts = [f"{q}+ {fmt_money(up, uid)} each" for q, up in tiers]
    return "💸 Bulk discounts: " + ", ".join(parts)

def stock_tag(p):
    if p["unlimited"]:
        return "♾️"
    n = stock_count(p["id"])
    return f"Stock: {n}" if n else "Out of Stock"

def review_stats(pid):
    c = db(); r = c.execute("SELECT COUNT(*) n, COALESCE(AVG(rating),0) avg FROM reviews WHERE product_id=?", (pid,)).fetchone(); c.close()
    return r["n"], round(r["avg"], 1)

# ---------- Wallet ----------

def get_wallet(uid, currency):
    c = db(); r = c.execute("SELECT balance FROM wallets WHERE user_id=? AND currency=?", (uid, currency)).fetchone(); c.close()
    return r["balance"] if r else 0

def add_wallet(uid, currency, amount):
    c = db()
    c.execute("INSERT INTO wallets(user_id,currency,balance) VALUES(?,?,?) "
              "ON CONFLICT(user_id,currency) DO UPDATE SET balance=balance+excluded.balance",
              (uid, currency, amount))
    c.commit(); c.close()

# ---------- Coupon ----------

def ensure_first_coupon(uid):
    """Pehli /start (gate ke baad) pe FIRST-ORDER coupon banao. True agar naya bana."""
    c = db()
    r = c.execute("SELECT * FROM coupons WHERE user_id=?", (uid,)).fetchone()
    if r:
        c.close(); return False
    done = c.execute("SELECT COUNT(*) n FROM orders WHERE user_id=? AND status='delivered'", (uid,)).fetchone()["n"]
    if done:
        c.close(); return False
    c.execute("INSERT INTO coupons(user_id,code,used,created) VALUES(?,?,0,?)",
              (uid, FIRST_COUPON_CODE, datetime.now().strftime("%Y-%m-%d %H:%M")))
    c.commit(); c.close(); return True

def coupon_valid(uid, code):
    if (code or "").strip().upper() != FIRST_COUPON_CODE:
        return False, "Ye coupon code galat hai."
    c = db(); r = c.execute("SELECT * FROM coupons WHERE user_id=? AND code=?", (uid, FIRST_COUPON_CODE)).fetchone()
    if not r:
        c.close(); return False, "Aapke paas ye coupon nahi hai."
    if r["used"]:
        c.close(); return False, "Ye coupon use ho chuka hai."
    done = c.execute("SELECT COUNT(*) n FROM orders WHERE user_id=? AND status='delivered'", (uid,)).fetchone()["n"]
    c.close()
    if done:
        return False, "Ye coupon sirf pehli order ke liye hai."
    return True, "OK"

# ---------- Orders ----------

def gen_order_code():
    c = db()
    for _ in range(10):
        code = f"RS-{random.randint(100000, 999999)}"
        if not c.execute("SELECT 1 FROM orders WHERE code=?", (code,)).fetchone():
            c.close(); return code
    c.close()
    return f"RS-{random.randint(100000, 999999)}"

def create_order(uid, username, items, payment_method, amount_text, proof, amount_pkr, code, coupon_code, discount_pkr):
    """items: list of {pid, qty}. Returns order id."""
    c = db()
    first_pid = items[0]["pid"] if items else 0
    total_qty = sum(i["qty"] for i in items)
    cur = c.execute(
        "INSERT INTO orders(user_id,username,product_id,payment_method,amount_text,proof,status,created_at,code,items_json,qty,discount_pkr,coupon_code,amount_pkr) "
        "VALUES(?,?,?,?,?,?, 'pending', ?,?,?,?,?,?,?)",
        (uid, username, first_pid, payment_method, amount_text, proof,
         datetime.now().strftime("%Y-%m-%d %H:%M"), code, json.dumps(items), total_qty,
         discount_pkr, coupon_code or "", amount_pkr))
    oid = cur.lastrowid; c.commit(); c.close()
    return oid

def order_status_label(status):
    return {"pending": "⏳ PAID — admin review",
            "delivered": "✅ DELIVERED",
            "rejected": "❌ CANCELLED",
            "out_of_stock": "❌ CANCELLED (stock khatam)"}.get(status, status)

# ---------- Buy state (user_data) ----------

def buy_lines(context):
    """user_data se order lines banao: [{pid, qty, product, unit_pkr, line_pkr}]."""
    if context.user_data.get("buy_mode") == "cart":
        items = context.user_data.get("buy_items", [])
    else:
        pid = context.user_data.get("buy_pid")
        items = [{"pid": pid, "qty": context.user_data.get("buy_qty", 1)}] if pid else []
    lines = []
    c = db()
    for it in items:
        p = c.execute("SELECT * FROM products WHERE id=?", (it["pid"],)).fetchone()
        if not p:
            continue
        up = unit_price_pkr(p, it["qty"])
        lines.append({"pid": it["pid"], "qty": it["qty"], "product": p,
                      "unit_pkr": up, "line_pkr": up * it["qty"]})
    c.close()
    return lines

def buy_totals(context, uid):
    lines = buy_lines(context)
    subtotal = sum(l["line_pkr"] for l in lines)
    discount = 0
    code = context.user_data.get("buy_coupon_code")
    if code:
        ok, _ = coupon_valid(uid, code)
        if ok:
            discount = round(subtotal * FIRST_COUPON_PCT / 100)
    return lines, subtotal, discount, subtotal - discount

def clear_buy_state(context):
    for k in ("buy_mode", "buy_pid", "buy_qty", "buy_items", "buy_code", "buy_coupon_code"):
        context.user_data.pop(k, None)

def cart_lines_db(uid):
    c = db(); rows = c.execute("SELECT * FROM carts WHERE user_id=? ORDER BY product_id", (uid,)).fetchall(); c.close()
    lines = []
    c = db()
    for r in rows:
        p = c.execute("SELECT * FROM products WHERE id=?", (r["product_id"],)).fetchone()
        if not p:
            continue
        up = unit_price_pkr(p, r["qty"])
        lines.append({"pid": r["product_id"], "qty": r["qty"], "product": p,
                      "unit_pkr": up, "line_pkr": up * r["qty"]})
    c.close()
    return lines

def cart_count(uid):
    c = db(); r = c.execute("SELECT COALESCE(SUM(qty),0) n FROM carts WHERE user_id=?", (uid,)).fetchone(); c.close()
    return r["n"]

# ---------- Channels ----------

def tg_channel():
    """Telegram channel (settings pehle, env fallback)."""
    return (get_setting("tg_channel") or TG_CHANNEL or "").strip()

def wa_channel_url():
    return (get_setting("wa_channel_url") or WA_CHANNEL_URL or "").strip()

def tg_group_url():
    """Telegram group ka invite link — sirf dikhane ke liye (verify nahi ho
    sakta jab tak bot group me add na ho; us ke liye group ki numeric ID chahiye)."""
    return (get_setting("tg_group_url") or TG_GROUP_URL or "").strip()

def tg_chat_id():
    """get_chat_member ke liye chat id: @username ya numeric id."""
    ch = tg_channel()
    if not ch:
        return ""
    if ch.startswith("@") or ch.lstrip("-").isdigit():
        return ch
    return "@" + ch

def tg_join_link():
    ch = tg_channel().lstrip("@")
    if not ch or ch.lstrip("-").isdigit():
        return ""
    return f"https://t.me/{ch}"

async def tg_is_member(bot, user_id):
    """Returns True (joined), False (not joined), None (check nahi ho saka —
    aam tor pe bot channel me admin nahi hai ya channel naam galat hai)."""
    if not tg_channel():
        return True, ""
    try:
        m = await bot.get_chat_member(tg_chat_id(), user_id)
        return m.status in ("creator", "administrator", "member", "restricted"), ""
    except Exception as e:
        log.warning("get_chat_member failed for %s: %s", tg_chat_id(), e)
        return None, str(e)

def wa_joined_flag(user_id):
    c = db(); r = c.execute("SELECT wa_joined FROM user_flags WHERE user_id=?", (user_id,)).fetchone(); c.close()
    return bool(r and r["wa_joined"])

def set_wa_joined(user_id):
    c = db(); c.execute("INSERT OR REPLACE INTO user_flags(user_id, wa_joined, checked_at) VALUES(?,?,?)",
                        (user_id, 1, datetime.now().strftime("%Y-%m-%d %H:%M"))); c.commit(); c.close()

# ---------- Menu / products views ----------

async def show_menu_text(user_id=None):
    lines = ["👋 Welcome to RetailShop!"]
    if user_id:
        lines.append(f"\n💱 Currency: {user_currency(user_id)}")
        ensure_first_coupon(user_id)
        c = db()
        r = c.execute("SELECT used FROM coupons WHERE user_id=? AND code=?", (user_id, FIRST_COUPON_CODE)).fetchone()
        done = c.execute("SELECT COUNT(*) n FROM orders WHERE user_id=? AND status='delivered'", (user_id,)).fetchone()["n"]
        c.close()
        if r and not r["used"] and not done:
            lines.append(f"🎁 First Order Coupon: {FIRST_COUPON_CODE} — {FIRST_COUPON_PCT}% off your first order! (Checkout pe apply karein)")
    lines.append("\nNeeche se choose karein:")
    return "\n".join(lines)

def menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍️ Products", callback_data="menu_products"),
         InlineKeyboardButton("💰 Deposit", callback_data="menu_deposit")],
        [InlineKeyboardButton("📦 My Orders", callback_data="menu_orders"),
         InlineKeyboardButton("🎁 Giveaways", callback_data="menu_giveaways")],
        [InlineKeyboardButton("👛 Wallet", callback_data="menu_wallet"),
         InlineKeyboardButton("🤝 Join Us", callback_data="menu_joinus")],
        [InlineKeyboardButton("📢 Our Channels", callback_data="menu_channels"),
         InlineKeyboardButton("💱 Change Payment Method", callback_data="curr_toggle")],
        [InlineKeyboardButton("❓ Help", callback_data="menu_help"),
         InlineKeyboardButton("💬 Support", callback_data="menu_support")],
    ])

def _short_name(name, n=38):
    return name if len(name) <= n else name[:n - 1] + "…"

def products_view_kb(uid, page=0, cat_idx=None):
    """Category buttons + standalone products (10/page) + footer."""
    per = 10
    kb = []
    c = db()
    if cat_idx is None:
        row = []
        for i, name in enumerate(CATEGORIES):
            n = c.execute("SELECT COUNT(*) n FROM products WHERE active=1 AND category=?", (name,)).fetchone()["n"]
            row.append(InlineKeyboardButton(f"{name} ({n})", callback_data=f"cat_{i}_0"))
            if len(row) == 2:
                kb.append(row); row = []
        if row:
            kb.append(row)
        prods = c.execute("SELECT * FROM products WHERE active=1 AND (category IS NULL OR category='') ORDER BY id").fetchall()
        base = "plist"
    else:
        prods = c.execute("SELECT * FROM products WHERE active=1 AND category=? ORDER BY id", (CATEGORIES[cat_idx],)).fetchall()
        base = f"cat_{cat_idx}"
    c.close()
    total_pages = max(1, (len(prods) + per - 1) // per)
    page = max(0, min(page, total_pages - 1))
    for p in prods[page * per:(page + 1) * per]:
        kb.append([InlineKeyboardButton(
            f"{_short_name(p['name'])} — {price_text(p, uid)} ({stock_tag(p)})",
            callback_data=f"prod_{p['id']}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅ Prev", callback_data=f"{base}_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡", callback_data=f"{base}_{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("🛒 View Cart", callback_data="cart"),
               InlineKeyboardButton("🔄 Refresh Stock", callback_data=f"{base}_{page}")])
    kb.append([InlineKeyboardButton("🔍 Search Products", callback_data="search_prod"),
               InlineKeyboardButton("📝 Request Products", callback_data="req_prod")])
    kb.append([InlineKeyboardButton("⬅ Main Menu", callback_data="back_menu")])
    return InlineKeyboardMarkup(kb), total_pages

def products_view_text(page, cat_idx, total_pages):
    parts = [PROMO_BANNER, ""]
    if cat_idx is not None:
        parts.append(f"📂 {CATEGORIES[cat_idx]}")
    parts.append(f"🛍️ Products — koi ek choose karein:  (Page {page + 1}/{total_pages})")
    return "\n".join(parts)

async def render_products_list(q, context, page=0, cat_idx=None):
    kb, total_pages = products_view_kb(q.from_user.id, page, cat_idx)
    context.user_data["last_list"] = f"cat_{cat_idx}_{page}" if cat_idx is not None else f"plist_{page}"
    await q.edit_message_text(products_view_text(page, cat_idx, total_pages), reply_markup=kb)

async def render_product_detail(q, context, pid):
    uid = q.from_user.id
    c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone(); c.close()
    if not p:
        await q.edit_message_text("Product nahi mila."); return
    n, avg = review_stats(pid)
    rating_line = f"⭐ {avg} ({n} reviews)" if n else "⭐ No reviews yet"
    is_free = p["price_pkr"] == 0 and p["price_usdt"] == 0
    buy_label = "🎁 Get Free" if is_free else "🛒 Buy Now"
    bulk = bulk_text(p, uid)
    desc = p["description"] or "No description."
    text = (f"📦 {p['name']}\n\n{desc}\n\n"
            f"💰 Price: {price_text(p, uid)}\n"
            f"📦 {stock_tag(p)}\n{rating_line}\n")
    if bulk:
        text += bulk + "\n"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(buy_label, callback_data=f"buy_{pid}")],
        [InlineKeyboardButton("🛒 Add to Cart", callback_data=f"cartadd_{pid}")],
        [InlineKeyboardButton("🛒 View Cart", callback_data="cart"),
         InlineKeyboardButton("⭐ Reviews", callback_data=f"rev_{pid}_0")],
        [InlineKeyboardButton("⬅ Back to Products", callback_data="back_list")],
    ])
    await q.edit_message_text(text, reply_markup=kb)

async def show_gate_or_menu_message(update, context):
    """Join gate check karke ya gate dikhata hai ya main menu. Returns True
    agar menu dikhaya, False agar gate dikhaya."""
    user = update.effective_user
    member, _err = await tg_is_member(context.bot, user.id)
    if member is False:
        kb = []
        link = tg_join_link()
        if link:
            kb.append([InlineKeyboardButton("📢 Join Telegram Channel", url=link)])
        if tg_group_url():
            kb.append([InlineKeyboardButton("👥 Join Telegram Group", url=tg_group_url())])
        if wa_channel_url():
            kb.append([InlineKeyboardButton("💬 Join WhatsApp Channel", url=wa_channel_url())])
        kb.append([InlineKeyboardButton("✅ Check Join Status", callback_data="check_join")])
        await update.message.reply_text(
            "🔒 Pehle hamara Telegram channel join karein, phir products dikhenge.\n\n"
            "Channel join karein, phir neeche **Check Join Status** dabayein.",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return False
    if member is None:
        await update.message.reply_text(
            "⚠️ Channel check nahi ho saka. Admin ne bot ko Telegram channel me admin nahi banaya hoga. "
            "Thodi der me dobara /start karein.")
        return False
    if wa_channel_url() and not wa_joined_flag(user.id):
        kb = [[InlineKeyboardButton("💬 Join WhatsApp Channel", url=wa_channel_url())],
              [InlineKeyboardButton("✅ Maine Join Kar Liya", callback_data="wa_joined")]]
        await update.message.reply_text(
            "💬 Hamara WhatsApp channel bhi join kar lein — wahan naye products aur offers aate hain.\n\n"
            "Join karke neeche **Maine Join Kar Liya** dabayein.",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return False
    await update.message.reply_text(await show_menu_text(user.id), reply_markup=menu_kb())
    return True

# ---------- Auto Delivery ----------

async def deliver_order(context: ContextTypes.DEFAULT_TYPE, order_id: int):
    """Accounts assign karo (items_json lines ke hisaab se, qty ke mutabiq),
    sold mark karo, buyer ko credentials bhejo. Coupon use + referral verify
    sirf successful delivery pe hota hai."""
    c = db()
    order = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        c.close(); return False
    try:
        items = json.loads(order["items_json"]) if order["items_json"] else None
    except Exception:
        items = None
    if not items:
        items = [{"pid": order["product_id"], "qty": order["qty"] or 1}]
    plan = []  # (product, [accounts])
    for it in items:
        product = c.execute("SELECT * FROM products WHERE id=?", (it["pid"],)).fetchone()
        if not product:
            continue
        accs = c.execute("SELECT * FROM accounts WHERE product_id=? AND status='available' ORDER BY id LIMIT ?",
                         (it["pid"], it["qty"])).fetchall()
        if len(accs) < it["qty"]:
            c.execute("UPDATE orders SET status='out_of_stock' WHERE id=?", (order_id,))
            c.commit(); c.close()
            await context.bot.send_message(ADMIN_ID, f"⚠️ Order #{order_id} ke liye stock khatam! Product: {product['name']}. Stock add karo, phir manually deliver karna hoga.")
            await context.bot.send_message(order["user_id"], f"Sorry, {product['name']} ka stock khatam ho gaya. Admin se rabta karein, refund / replacement milega.")
            return False
        plan.append((product, accs))
    if not plan:
        c.execute("UPDATE orders SET status='out_of_stock' WHERE id=?", (order_id,))
        c.commit(); c.close()
        return False
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for _product, accs in plan:
        for acc in accs:
            c.execute("UPDATE accounts SET status='sold', sold_to=?, sold_at=?, order_id=? WHERE id=?",
                      (order["user_id"], now, order_id, acc["id"]))
    c.execute("UPDATE orders SET status='delivered', account_id=? WHERE id=?", (plan[0][1][0]["id"], order_id))
    if order["coupon_code"]:
        c.execute("UPDATE coupons SET used=1 WHERE user_id=? AND code=?", (order["user_id"], order["coupon_code"]))
    c.commit()
    n_delivered = c.execute("SELECT COUNT(*) n FROM orders WHERE user_id=? AND status='delivered'", (order["user_id"],)).fetchone()["n"]
    c.close()
    if n_delivered <= 1:
        # Pehli successful order -> referral verified (gate pass karke hi order hui thi)
        c = db(); c.execute("UPDATE referrals SET verified=1 WHERE referred_id=?", (order["user_id"],)); c.commit(); c.close()
    date_txt = datetime.now().strftime("%d/%m/%Y %I:%M %p PKT")
    for product, accs in plan:
        kind = (product["kind"] or "account") if "kind" in product.keys() else "account"
        if kind == "mail":
            # Mail-type delivery (Muse tokens etc.): buyer gets an email + mailbox
            # URL, enters the mail on muse.ai, fetches the OTP from the mailbox
            # site themselves. Stock format: code | email | mailbox_url
            # (extra=code, login=email, password=mailbox URL).
            lines = []
            for acc in accs:
                extra = (acc["extra"] or "") if "extra" in acc.keys() else ""
                lines.append(f"{extra} | {acc['login']} | {acc['password']}" if extra else f"{acc['login']} | {acc['password']}")
            msg = (
                f"✅ *Payment Verified*\n"
                f"```\nProduct   :  {product['name']}\nOrder ID  :  PAY-{order_id}\n"
                f"Paid      :  {order['amount_text']}\nDate      :  {date_txt}\n```\n\n"
                f"*Account*\n" + "\n".join(lines) + "\n\n"
                f"──────────\n*Order Delivery Note*\n"
                f"You have received mail and mailbox url.\n"
                f"Copy the mail and paste it on muse.ai.\n\n"
                f"Then go to mailbox site, get otp and paste on muse.ai to complete login.\n\n"
                f"━━━━━━━━━━━━━━\n"
                f"ℹ️ OTP na mile ya koi issue ho to Support se rabta karein — code admin bhej dega."
            )
            await context.bot.send_message(order["user_id"], msg, parse_mode="Markdown")
        elif len(accs) == 1 and len(plan) == 1:
            acc = accs[0]
            msg = (
                f"✅ Payment confirmed! Aapka account mil gaya:\n\n"
                f"📦 Product: {product['name']}\n"
                f"👤 Login / Email: `{acc['login']}`\n"
                f"🔑 Password: `{acc['password']}`\n\n"
                f"Order #{order_id} (PAY-{order_id})\n"
                f"Account ko save kar lein. Koi issue ho to admin se rabta karein."
            )
            await context.bot.send_message(order["user_id"], msg, parse_mode="Markdown")
        else:
            lines = []
            for i, acc in enumerate(accs, 1):
                lines.append(f"{i}. Login: `{acc['login']}`\n   Password: `{acc['password']}`")
            msg = (
                f"✅ Payment confirmed! Aapke {len(accs)} accounts mil gaye:\n\n"
                f"📦 Product: {product['name']}\n\n" + "\n".join(lines) +
                f"\n\nOrder #{order_id} (PAY-{order_id})\n"
                f"Accounts ko save kar lein. Koi issue ho to admin se rabta karein."
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
    text = (f"🛒 New Order #{order_id} ({o['code'] or ''})\nProduct: {p['name']}\nQty: {o['qty']}\nBuyer: @{o['username'] or o['user_id']} ({o['user_id']})\n"
            f"Payment: {o['payment_method']}\nAmount: {o['amount_text']}\nProof: {o['proof']}\nStock left: {stock_count(p['id'])}")
    await context.bot.send_message(ADMIN_ID, text, reply_markup=kb)

async def notify_admin_deposit(context, dep_id):
    c = db(); d = c.execute("SELECT * FROM deposits WHERE id=?", (dep_id,)).fetchone(); c.close()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Approve + Credit Wallet", callback_data=f"depapprove_{dep_id}"),
         InlineKeyboardButton("❌ Reject", callback_data=f"depreject_{dep_id}")]
    ])
    text = (f"💰 New Deposit #{dep_id}\nUser: @{d['username'] or d['user_id']} ({d['user_id']})\n"
            f"Amount: {d['amount']} {d['currency']}\nPayment: {d['payment_method']}\nProof: {d['proof']}")
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

# ---------- Zurvapay (auto-verify JazzCash / Easypaisa / Raast QR) ----------
# Docs: zurvapay.com (homepage). Initiate endpoint + params wahan se liye
# gaye hain. IPN ka EXACT payload/signature format public docs me NAHI hai —
# "full documentation" merchant dashboard ke peeche hai (login chahiye).
# Is liye verify_zurvapay_ipn 'auto' mode me tamam common HMAC schemes try
# karta hai; dashboard docs me jo scheme likhi ho, usay ZURVAPAY_IPN_MODE
# env se pin kar do. Signature ke baghair koi IPN deliver NAHI karega.

ZURVA_SUCCESS_STATUSES = {"success", "successful", "completed", "paid", "ok", "1", "true", "approved"}

def zurvapay_ipn_url():
    """IPN callback URL — env override pehle, phir PUBLIC_BASE_URL."""
    if ZURVAPAY_IPN_URL:
        return ZURVAPAY_IPN_URL
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/zurvapay/ipn"
    return ""

def _zurvapay_public_base():
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    ipn = zurvapay_ipn_url()
    if ipn:
        u = urlparse(ipn)
        return f"{u.scheme}://{u.netloc}"
    return ""

def zurvapay_enabled():
    """Keys + IPN URL dono ho to hi Zurvapay option dikhao."""
    return bool(ZURVAPAY_PUBLIC_KEY and ZURVAPAY_SECRET_KEY and zurvapay_ipn_url())

def zurvapay_initiate(identifier, amount_pkr, details, customer_name="", customer_email=""):
    """POST /payment/initiate (form-encoded, homepage sample ke mutabiq).
    Returns (checkout_url, None) ya (None, error)."""
    if not ZURVAPAY_PUBLIC_KEY:
        return None, "ZURVAPAY_PUBLIC_KEY set nahi hai"
    ipn = zurvapay_ipn_url()
    if not ipn:
        return None, "IPN URL nahi bani — PUBLIC_BASE_URL ya ZURVAPAY_IPN_URL set karein"
    base = _zurvapay_public_base()
    payload = {
        "public_key": ZURVAPAY_PUBLIC_KEY,
        "identifier": identifier,
        "currency": "PKR",
        "amount": int(amount_pkr),
        "details": details,
        "ipn_url": ipn,
        "success_url": f"{base}/zurvapay/success",
        "cancel_url": f"{base}/zurvapay/cancel",
        "customer_name": customer_name or "RetailShop Customer",
        "customer_email": customer_email or "customer@retailshop.local",
    }
    try:
        r = requests.post(f"{ZURVAPAY_BASE_URL}/payment/initiate", data=payload, timeout=20)
    except Exception as e:
        log.exception("Zurvapay initiate error")
        return None, f"Zurvapay se rabta nahi ho saka: {e}"
    try:
        d = r.json()
    except Exception:
        return None, f"Zurvapay ka jawab samajh nahi aaya (HTTP {r.status_code})"
    if isinstance(d, dict) and d.get("success") == "ok" and d.get("url"):
        return d["url"], None
    err = ""
    if isinstance(d, dict):
        err = str(d.get("message") or d.get("error") or d)
    return None, f"Zurvapay error: {err or 'unknown'}"

def zurvapay_status_check(identifier):
    """Optional status API — Zurvapay kehta hai status API hai, lekin endpoint
    public docs me nahi hai. ZURVAPAY_STATUS_URL set ho to hi call hoti hai;
    warna None (IPN hi source of truth hai)."""
    if not ZURVAPAY_STATUS_URL or not ZURVAPAY_PUBLIC_KEY:
        return None
    try:
        r = requests.post(ZURVAPAY_STATUS_URL,
                          data={"public_key": ZURVAPAY_PUBLIC_KEY, "identifier": identifier},
                          timeout=15)
        return r.json()
    except Exception:
        log.exception("Zurvapay status check error")
        return None

def _zurva_ipn_fields(data):
    """IPN dict se identifier/amount/status/signature nikalo — field names
    alag-alag ho sakte hain, is liye aliases check karte hain."""
    d = {str(k).lower(): v for k, v in (data or {}).items()}

    def pick(*names):
        for n in names:
            if n in d and d[n] not in (None, ""):
                return str(d[n])
        return ""

    return {
        "identifier": pick("identifier", "order_id", "merchant_order_id", "reference", "ref", "orderid"),
        "amount": pick("amount", "amount_pkr", "total", "price"),
        "status": pick("status", "payment_status", "transaction_status", "txn_status").lower(),
        "signature": pick("signature", "hash", "sign", "checksum"),
        "txid": pick("transaction_id", "payment_id", "zurva_txn_id", "txn_id"),
    }

def _hmac_hex(secret, msg, algo="sha256"):
    return hmac.new(secret.encode(), msg.encode(), getattr(hashlib, algo)).hexdigest()

def verify_zurvapay_ipn(data, raw_body=b"", headers=None):
    """Signed IPN verify karo. Returns (True, scheme_name) ya (False, reason).
    'auto' mode tamam candidate schemes try karta hai — har candidate secret
    se keyed HMAC hai, is liye baghair secret ke koi bhi forge nahi ho sakta.
    ZURVAPAY_IPN_MODE se ek scheme pin ki ja sakti hai."""
    secret = ZURVAPAY_SECRET_KEY
    if not secret:
        return False, "secret key set nahi hai"
    f = _zurva_ipn_fields(data)
    sig = f.get("signature", "")
    if not sig and headers:
        for h in ("X-Zurvapay-Signature", "X-Signature", "X-Zurva-Signature", "Signature"):
            if headers.get(h):
                sig = headers.get(h)
                break
    if not sig:
        return False, "IPN me signature nahi mili"
    sig = sig.strip().lower()
    ident = f.get("identifier", "")
    amount = f.get("amount", "")
    status = f.get("status", "")
    candidates = []  # (scheme_name, expected_hex)
    if ident and amount:
        candidates += [
            ("hmac_pipe_sha256", _hmac_hex(secret, f"{ident}|{amount}")),
            ("hmac_pipe_sha512", _hmac_hex(secret, f"{ident}|{amount}", "sha512")),
            ("sha256_concat", hashlib.sha256(f"{secret}{ident}{amount}".encode()).hexdigest()),
            ("sha256_concat2", hashlib.sha256(f"{ident}{amount}{secret}".encode()).hexdigest()),
        ]
        if status:
            candidates += [
                ("hmac_pipe_status_sha256", _hmac_hex(secret, f"{ident}|{amount}|{status}")),
                ("hmac_pipe_status_sha512", _hmac_hex(secret, f"{ident}|{amount}|{status}", "sha512")),
            ]
    if raw_body:
        try:
            raw_txt = raw_body.decode("utf-8")
        except Exception:
            raw_txt = ""
        if raw_txt:
            candidates += [
                ("hmac_body_sha256", _hmac_hex(secret, raw_txt)),
                ("hmac_body_sha512", _hmac_hex(secret, raw_txt, "sha512")),
            ]
        candidates.append(("hmac_body_bytes_sha256",
                           hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()))
    try:
        items = sorted((k, v) for k, v in data.items()
                       if str(k).lower() not in ("signature", "hash", "sign", "checksum"))
        canon = "&".join(f"{k}={v}" for k, v in items)
        candidates += [
            ("hmac_sorted_sha256", _hmac_hex(secret, canon)),
            ("hmac_sorted_sha512", _hmac_hex(secret, canon, "sha512")),
        ]
    except Exception:
        pass
    mode = (ZURVAPAY_IPN_MODE or "auto").strip()
    if mode != "auto":
        candidates = [c for c in candidates if c[0] == mode]
        if not candidates:
            return False, f"unknown ZURVAPAY_IPN_MODE: {mode}"
    for name, expected in candidates:
        if hmac.compare_digest(expected.lower(), sig):
            return True, name
    return False, "signature match nahi hui (koi scheme nahi mili)"

def _zurva_run_on_loop(coro, timeout=60):
    """IPN HTTP thread se PTB loop pe coroutine chalao, result ka wait karo."""
    if PTB_APP is None or PTB_LOOP is None or PTB_LOOP.is_closed():
        raise RuntimeError("PTB loop available nahi (bot abhi start ho raha hai?)")
    fut = asyncio.run_coroutine_threadsafe(coro, PTB_LOOP)
    return fut.result(timeout=timeout)

def _zurva_send(user_id, text):
    """Buyer/admin ko Telegram message — loop na ho to sirf log karo."""
    try:
        _zurva_run_on_loop(PTB_APP.bot.send_message(user_id, text), timeout=30)
    except Exception:
        log.warning("Zurvapay notify fail (user %s): %s", user_id, text[:120])

def _zurva_deliver_order(order_id):
    """deliver_order (async) ko IPN thread se chalao. Returns True/False."""
    ctx = SimpleNamespace(bot=PTB_APP.bot)
    return bool(_zurva_run_on_loop(deliver_order(ctx, order_id), timeout=60))

def _zurva_handle_order_ipn(code, f):
    c = db(); o = c.execute("SELECT * FROM orders WHERE code=?", (code,)).fetchone(); c.close()
    if not o:
        return False, f"order {code} nahi mila"
    try:
        amt = float(str(f["amount"]).replace(",", ""))
    except Exception:
        return False, "amount parse nahi hui"
    if abs(amt - (o["amount_pkr"] or 0)) > 0.5:
        log.warning("Zurvapay IPN amount mismatch order %s: IPN %s vs order %s", code, amt, o["amount_pkr"])
        return False, f"amount mismatch: IPN {amt} vs order {o['amount_pkr']}"
    if o["status"] == "delivered":
        return True, "already delivered"
    if o["status"] != "pending":
        return True, f"order pehle hi {o['status']} hai"
    # Atomic claim — duplicate/retry IPN dobara deliver nahi karega.
    c = db()
    cur = c.execute("UPDATE orders SET status='zurva_processing', proof=? WHERE id=? AND status='pending'",
                    (f"zurvapay:{f['txid'] or code}", o["id"]))
    claimed = cur.rowcount; c.commit(); c.close()
    if not claimed:
        return True, "order pehle hi process ho rahi hai / ho chuki"
    try:
        ok = _zurva_deliver_order(o["id"])
    except Exception as e:
        log.exception("Zurvapay delivery error order %s", o["id"])
        c = db(); c.execute("UPDATE orders SET status='pending' WHERE id=? AND status='zurva_processing'", (o["id"],)); c.commit(); c.close()
        return False, f"delivery error (retry hoga): {e}"
    if ok:
        return True, "delivered"
    # deliver_order ne status='out_of_stock' set kar diya + admin/buyer ko bataya.
    return True, "delivery fail (stock khatam) — admin ko notify ho gaya"

def _zurva_handle_deposit_ipn(ident, f):
    try:
        dep_id = int(ident.split("-", 1)[1])
    except Exception:
        return False, "deposit id parse nahi hua"
    c = db(); d = c.execute("SELECT * FROM deposits WHERE id=?", (dep_id,)).fetchone(); c.close()
    if not d:
        return False, f"deposit {dep_id} nahi mili"
    if d["status"] == "approved":
        return True, "already credited"
    if d["status"] != "pending":
        return True, f"deposit pehle hi {d['status']} hai"
    try:
        amt = float(str(f["amount"]).replace(",", ""))
    except Exception:
        return False, "amount parse nahi hui"
    if d["currency"] == "PKR" and abs(amt - float(d["amount"])) > 0.5:
        log.warning("Zurvapay IPN amount mismatch deposit %s: IPN %s vs deposit %s", dep_id, amt, d["amount"])
        return False, f"amount mismatch: IPN {amt} vs deposit {d['amount']}"
    c = db()
    cur = c.execute("UPDATE deposits SET status='approved' WHERE id=? AND status='pending'", (dep_id,))
    claimed = cur.rowcount; c.commit(); c.close()
    if not claimed:
        return True, "deposit pehle hi process ho chuki"
    add_wallet(d["user_id"], d["currency"], d["amount"])
    _zurva_send(d["user_id"], f"✅ Deposit approved! {d['amount']} {d['currency']} aapke wallet me add ho gaye (Zurvapay).")
    return True, "credited"

def handle_zurvapay_ipn_http(raw_body, headers=None):
    """HTTP thread se call hota hai. Returns (ok, message).
    ok=True -> HTTP 200 (Zurvapay retry nahi karega); ok=False -> HTTP 400."""
    if not ZURVAPAY_SECRET_KEY:
        log.warning("Zurvapay IPN aaya lekin ZURVAPAY_SECRET_KEY set nahi hai")
        return False, "secret key not configured"
    ctype = ""
    try:
        ctype = (headers.get("Content-Type") or "").lower()
    except Exception:
        pass
    data = {}
    try:
        if "json" in ctype:
            data = json.loads(raw_body.decode("utf-8") or "{}")
        else:
            qs = parse_qs(raw_body.decode("utf-8", "ignore"))
            data = {k: v[0] for k, v in qs.items() if v}
            if not data:
                data = json.loads(raw_body.decode("utf-8") or "{}")
    except Exception as e:
        return False, f"IPN parse error: {e}"
    if not isinstance(data, dict):
        return False, "IPN body dict nahi hai"
    ok, info = verify_zurvapay_ipn(data, raw_body, headers)
    if not ok:
        safe = {k: v for k, v in data.items() if str(k).lower() not in ("signature", "hash", "sign", "checksum")}
        log.warning("Zurvapay IPN signature FAIL (%s): %s", info, safe)
        _zurva_send(ADMIN_ID, f"⚠️ Zurvapay IPN signature fail ({info}).\nFields: {safe}\nDashboard docs se sahi scheme check karo aur ZURVAPAY_IPN_MODE set karo.")
        return False, f"bad signature: {info}"
    f = _zurva_ipn_fields(data)
    log.info("Zurvapay IPN verified (%s): identifier=%s amount=%s status=%s", info, f["identifier"], f["amount"], f["status"])
    if f["status"] and f["status"] not in ZURVA_SUCCESS_STATUSES:
        return True, f"ignored status {f['status']}"
    ident = f["identifier"]
    if not ident:
        return False, "identifier nahi mila"
    if ident.startswith("DEP-"):
        return _zurva_handle_deposit_ipn(ident, f)
    return _zurva_handle_order_ipn(ident, f)

# ---------- Buy flow renders ----------

def qty_kb(uid, pid):
    c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone(); c.close()
    kb = []
    row = []
    for q in (1, 2, 3, 5, 10):
        total = unit_price_pkr(p, q) * q
        row.append(InlineKeyboardButton(f"{q} × {fmt_money(total, uid)}", callback_data=f"qty_{pid}_{q}"))
        if len(row) == 3:
            kb.append(row); row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton("🔢 Custom Quantity", callback_data=f"qtycustom_{pid}")])
    kb.append([InlineKeyboardButton(f"💱 Change Currency ({user_currency(uid)})", callback_data=f"qtycur_{pid}")])
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")])
    return InlineKeyboardMarkup(kb)

def qty_text(uid, pid):
    c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone(); c.close()
    bulk = bulk_text(p, uid)
    text = f"📦 {p['name']}\n\nUnit price: {fmt_money(p['price_pkr'], uid)}\n"
    if bulk:
        text += bulk + "\n"
    text += f"\n📦 {stock_tag(p)}\n\nQuantity choose karein:"
    return text

async def render_qty(q, context, pid):
    await q.edit_message_text(qty_text(q.from_user.id, pid), reply_markup=qty_kb(q.from_user.id, pid))

def terms_for_product(p):
    t = (p["terms"] or "").strip() if "terms" in p.keys() else ""
    return t or DEFAULT_TERMS

def summary_text(uid, context):
    lines, subtotal, discount, total = buy_totals(context, uid)
    code = context.user_data.get("buy_code") or gen_order_code()
    context.user_data["buy_code"] = code
    parts = [f"🧾 Order Summary — {code}\n"]
    for l in lines:
        parts.append(f"• {l['product']['name']} × {l['qty']} — {fmt_money(l['line_pkr'], uid)} ({fmt_money(l['unit_pkr'], uid)} each)")
    parts.append(f"\nSubtotal: {fmt_money(subtotal, uid)}")
    if discount:
        parts.append(f"Coupon {context.user_data.get('buy_coupon_code')} (-{FIRST_COUPON_PCT}%): -{fmt_money(discount, uid)}")
    parts.append(f"Total: {fmt_money(total, uid)}")
    parts.append(f"\n👛 Wallet balance: Rs {get_wallet(uid, 'PKR')} | ${get_wallet(uid, 'USDT')} USDT")
    return "\n".join(parts)

def summary_kb(uid, context):
    _lines, _sub, _disc, total = buy_totals(context, uid)
    kb = []
    if not context.user_data.get("buy_coupon_code"):
        kb.append([InlineKeyboardButton("🎟️ Apply Coupon Code", callback_data="coupon_apply")])
    kb.append([InlineKeyboardButton("💚 Pay via PKR", callback_data="paypkr"),
               InlineKeyboardButton("🪙 Pay via USDT", callback_data="payusdt")])
    if zurvapay_enabled():
        kb.append([InlineKeyboardButton("⚡ Zurvapay — JazzCash/Easypaisa/Raast (Auto)", callback_data="pay_zurvapay")])
    cur = user_currency(uid)
    bal = get_wallet(uid, cur)
    need = total if cur == "PKR" else round(total / USDT_RATE, 2)
    if total > 0 and bal >= need:
        kb.append([InlineKeyboardButton("👛 Pay from Wallet", callback_data="pay_wallet")])
    kb.append([InlineKeyboardButton("🔢 Change Quantity", callback_data="qty_change"),
               InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")])
    return InlineKeyboardMarkup(kb)

async def render_summary(q, context):
    uid = q.from_user.id
    await q.edit_message_text(summary_text(uid, context), reply_markup=summary_kb(uid, context))

async def render_terms(q, context):
    uid = q.from_user.id
    lines, _sub, _disc, total = buy_totals(context, uid)
    pid = context.user_data.get("buy_pid")
    c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone() if pid else None; c.close()
    terms = terms_for_product(p) if p else DEFAULT_TERMS
    text = (f"📜 Terms & Conditions\n\n{terms}\n\n"
            f"Order: {lines[0]['product']['name']} × {lines[0]['qty']}\n"
            f"Total: {fmt_money(total, uid)}")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Agree & Continue to Payment", callback_data="terms_ok")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")],
    ])
    await q.edit_message_text(text, reply_markup=kb)

# ---------- Cart / wallet / orders / giveaways renders ----------

async def render_cart(q, context):
    uid = q.from_user.id
    lines = cart_lines_db(uid)
    if not lines:
        text = "🛒 Your Cart khaali hai."
    else:
        parts = ["🛒 Your Cart\n"]
        for l in lines:
            parts.append(f"• {l['product']['name']} × {l['qty']} — {fmt_money(l['line_pkr'], uid)}")
        parts.append(f"\nTotal: {fmt_money(sum(l['line_pkr'] for l in lines), uid)}")
        text = "\n".join(parts)
    kb = []
    for l in lines:
        kb.append([InlineKeyboardButton(f"❌ Remove {_short_name(l['product']['name'], 30)}", callback_data=f"cart_rm_{l['pid']}")])
    if lines:
        kb.append([InlineKeyboardButton("✅ Checkout", callback_data="cart_checkout"),
                   InlineKeyboardButton("🧹 Clear Cart", callback_data="cart_clear")])
    kb.append([InlineKeyboardButton("🛍️ Continue Shopping", callback_data="back_list")])
    kb.append([InlineKeyboardButton("⬅ Main Menu", callback_data="back_menu")])
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def render_wallet(q, context):
    uid = q.from_user.id
    user = q.from_user
    bal_pkr = get_wallet(uid, "PKR")
    bal_usdt = get_wallet(uid, "USDT")
    c = db()
    row = c.execute("SELECT COUNT(*) n, COALESCE(SUM(amount_pkr),0) s FROM orders WHERE user_id=? AND status='delivered'", (uid,)).fetchone()
    c.close()
    text = (f"👛 Wallet\n\n"
            f"Name: {user.first_name or ''}\n"
            f"ID: {uid}\n"
            f"Payment system: Easypaisa / JazzCash / USDT (TRC20)\n\n"
            f"Balance PKR: Rs {bal_pkr}\n"
            f"Balance USDT: ${bal_usdt}\n\n"
            f"Total purchases: {row['n']} orders (Rs {row['s']})")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Deposit", callback_data="menu_deposit"),
         InlineKeyboardButton("🎟️ Redeem Top-up Code", callback_data="wal_topup")],
        [InlineKeyboardButton("📄 Wallet Statement", callback_data="wal_stmt")],
        [InlineKeyboardButton("⬅ Main Menu", callback_data="back_menu")],
    ])
    await q.edit_message_text(text, reply_markup=kb)

async def render_orders_list(q, context):
    uid = q.from_user.id
    c = db(); rows = c.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (uid,)).fetchall(); c.close()
    if not rows:
        await q.edit_message_text("📦 Aapki koi order nahi hai.",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Main Menu", callback_data="back_menu")]]))
        return
    kb = []
    for o in rows:
        c = db(); p = c.execute("SELECT name FROM products WHERE id=?", (o["product_id"],)).fetchone(); c.close()
        kb.append([InlineKeyboardButton(f"#{o['id']} {_short_name(p['name'], 28) if p else '?'} — {order_status_label(o['status'])}",
                                        callback_data=f"ord_{o['id']}")])
    kb.append([InlineKeyboardButton("⬅ Main Menu", callback_data="back_menu")])
    await q.edit_message_text("📦 My Orders — detail ke liye order pe tap karein:", reply_markup=InlineKeyboardMarkup(kb))

async def render_order_detail(q, context, oid):
    uid = q.from_user.id
    c = db(); o = c.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (oid, uid)).fetchone(); c.close()
    if not o:
        await q.edit_message_text("Order nahi mili."); return
    c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (o["product_id"],)).fetchone(); c.close()
    text = (f"📦 Order #{o['id']} ({o['code'] or '—'})\n\n"
            f"Product: {p['name'] if p else '?'}\n"
            f"Qty: {o['qty']}\n"
            f"Amount: {o['amount_text']}\n"
            f"Payment: {o['payment_method']}\n"
            f"Status: {order_status_label(o['status'])}\n"
            f"Date: {o['created_at']}")
    kb = []
    if p:
        kb.append([InlineKeyboardButton("🔁 Buy Again", callback_data=f"buyagain_{p['id']}")])
    if o["status"] == "delivered":
        kb.append([InlineKeyboardButton("⭐ Write Review", callback_data=f"wrev_{o['id']}")])
    kb.append([InlineKeyboardButton("⬅ My Orders", callback_data="menu_orders")])
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def render_reviews(q, pid, page):
    c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    total = c.execute("SELECT COUNT(*) n FROM reviews WHERE product_id=?", (pid,)).fetchone()["n"]
    rows = c.execute("SELECT * FROM reviews WHERE product_id=? ORDER BY id DESC LIMIT 5 OFFSET ?", (pid, page * 5)).fetchall()
    c.close()
    n, avg = review_stats(pid)
    parts = [f"⭐ Reviews — {p['name'] if p else ''}\n"]
    parts.append(f"Average: {avg} ({n} reviews)\n" if n else "No reviews yet.\n")
    for r in rows:
        tag = " (Verified purchase)" if r["verified"] else (" (Added by shop)" if r["source"] == "shop" else "")
        parts.append(f"⭐{r['rating']} — {r['user_name'] or 'User'}{tag}\n{r['text'] or ''}\n")
    total_pages = max(1, (total + 4) // 5)
    kb = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅ Prev", callback_data=f"rev_{pid}_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡", callback_data=f"rev_{pid}_{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("⬅ Back to Product", callback_data=f"prod_{pid}")])
    await q.edit_message_text("\n".join(parts), reply_markup=InlineKeyboardMarkup(kb))

async def render_giveaways(q, context):
    uid = q.from_user.id
    c = db()
    total = c.execute("SELECT COUNT(*) n FROM referrals WHERE referrer_id=?", (uid,)).fetchone()["n"]
    verified = c.execute("SELECT COUNT(*) n FROM referrals WHERE referrer_id=? AND verified=1", (uid,)).fetchone()["n"]
    c.close()
    parts = ["🎁 Giveaways\n",
             f"Your invite link:\nhttps://t.me/{BOT_USERNAME}?start=ref_{uid}\n",
             f"Referrals: {total} (Verified: {verified})\n",
             "Apna link share karein — jab aapka dost channel join karke pehli order complete karega, referral verified hoga.\n",
             "🎁 Active Giveaways:"]
    for i, (name, desc) in enumerate(GIVEAWAYS, 1):
        parts.append(f"{i}. {name} — {desc}")
    parts.append("\n⚠️ Mandatory: hamara Telegram channel @retail_shop_official aur WhatsApp channel join karna zaroori hai.")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ I Have Joined (Verify Membership)", callback_data="giv_verify")],
        [InlineKeyboardButton("⬅ Main Menu", callback_data="back_menu")],
    ])
    await q.edit_message_text("\n".join(parts), reply_markup=kb)

# ---------- Buyer: /start ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # Referral payload: /start ref_<id>
    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                ref = int(arg[4:])
                if ref != user.id:
                    c = db()
                    if not c.execute("SELECT 1 FROM referrals WHERE referrer_id=? AND referred_id=?", (ref, user.id)).fetchone():
                        c.execute("INSERT INTO referrals(referrer_id,referred_id,verified,created) VALUES(?,?,0,?)",
                                  (ref, user.id, datetime.now().strftime("%Y-%m-%d %H:%M")))
                        c.commit()
                    c.close()
            except ValueError:
                pass
    await show_gate_or_menu_message(update, context)

# ---------- Buyer: callbacks ----------

async def start_payment(q, context, method):
    """method: jazzcash/easypaisa/usdt. Buy ya deposit context ke hisaab se
    payment instructions dikhao aur awaiting set karo."""
    uid = q.from_user.id
    dep = context.user_data.get("deposit")
    if dep:
        dep["method"] = method
        if method == "zurvapay":
            # Zurvapay sirf PKR karta hai.
            if dep["currency"] != "PKR":
                await q.edit_message_text("⚡ Zurvapay sirf PKR deposits ke liye hai. USDT ke liye USDT option use karein.")
                return
            c = db(); cur = c.execute(
                "INSERT INTO deposits(user_id,username,currency,amount,payment_method,proof,status,created_at) VALUES(?,?,?,?,?,?, 'pending', ?)",
                (uid, q.from_user.username or "", "PKR", dep["amount"], "zurvapay", "zurvapay", datetime.now().strftime("%Y-%m-%d %H:%M")))
            dep_id = cur.lastrowid; c.commit(); c.close()
            ident = f"DEP-{dep_id}"
            url, err = zurvapay_initiate(ident, dep["amount"], f"RetailShop wallet deposit #{dep_id}",
                                         q.from_user.first_name or "", "")
            context.user_data.clear()
            if err:
                await q.edit_message_text(f"⚠️ Zurvapay payment start nahi ho saki: {err}\nDeposit #{dep_id} pending hai — dobara try karein ya admin se rabta karein.")
                return
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("💳 Pay Now — JazzCash / Easypaisa / Raast QR", url=url)]])
            await q.edit_message_text(
                f"⚡ Zurvapay Deposit\n\nAmount: Rs {dep['amount']}\nDeposit #{dep_id}\n\nNeeche button se payment karein — JazzCash, Easypaisa ya Raast QR. Payment confirm hote hi wallet auto credit ho jayega.",
                reply_markup=kb)
            return
        if method in ("jazzcash", "easypaisa"):
            num = get_setting(f"{method}_number"); name = get_setting(f"{method}_name")
            label = "JazzCash" if method == "jazzcash" else "Easypaisa"
            await q.edit_message_text(
                f"💚 {label} Deposit\n\nAmount: {dep['amount']} {dep['currency']}\n\nSend to:\nNumber: `{num}`\nName: {name}\n\nPayment karke **Transaction ID (TID)** yahan bhejein, ya screenshot bhejein.\nAdmin approve karte hi wallet credit ho jayega.",
                parse_mode="Markdown")
            context.user_data["awaiting"] = "dep_tid"
        else:
            addr = get_setting("usdt_address")
            await q.edit_message_text(
                f"🪙 USDT (TRC20) Deposit\n\nAmount: **${dep['amount']} USDT**\n\nSend to (TRC20 only):\n`{addr}`\n\nPayment karke **Transaction Hash** yahan bhejein. Bot khud verify karega.",
                parse_mode="Markdown")
            context.user_data["awaiting"] = "dep_hash"
        return
    lines, _sub, _disc, total = buy_totals(context, uid)
    if not lines:
        await q.edit_message_text("Session expire ho gayi. /start se dobara karein."); return
    context.user_data["pay_method"] = method
    label = "Cart" if context.user_data.get("buy_mode") == "cart" else lines[0]["product"]["name"]
    if method == "zurvapay":
        # Order pehle banao (identifier = RS code), phir checkout URL do.
        # Payment confirm hote hi Zurvapay IPN bhejega aur bot auto deliver karega.
        code = context.user_data.get("buy_code") or gen_order_code()
        coupon_code = context.user_data.get("buy_coupon_code") or ""
        items = [{"pid": l["pid"], "qty": l["qty"]} for l in lines]
        oid = create_order(uid, q.from_user.username or "", items, "zurvapay", f"Rs {total} via Zurvapay", "zurvapay", total, code, coupon_code, _disc)
        if context.user_data.get("buy_mode") == "cart":
            c = db(); c.execute("DELETE FROM carts WHERE user_id=?", (uid,)); c.commit(); c.close()
        clear_buy_state(context)
        context.user_data.pop("pay_method", None)
        url, err = zurvapay_initiate(code, total, f"RetailShop order {code} — {label}",
                                     q.from_user.first_name or "", "")
        if err:
            await q.edit_message_text(f"⚠️ Zurvapay payment start nahi ho saki: {err}\nOrder #{oid} ({code}) pending hai — dobara try karein ya admin se rabta karein.")
            return
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("💳 Pay Now — JazzCash / Easypaisa / Raast QR", url=url)]])
        await q.edit_message_text(
            f"⚡ Zurvapay Payment\n\nProduct: {label}\nAmount: Rs {total}\nOrder #{oid} ({code})\n\nNeeche button se payment karein — JazzCash, Easypaisa ya Raast QR. Payment confirm hote hi account isi chat me auto deliver ho jayega.",
            reply_markup=kb)
        return
    if method in ("jazzcash", "easypaisa"):
        num = get_setting(f"{method}_number"); name = get_setting(f"{method}_name")
        mlabel = "JazzCash" if method == "jazzcash" else "Easypaisa"
        await q.edit_message_text(
            f"💚 {mlabel} Payment\n\nProduct: {label}\nAmount: Rs {total}\n\nSend to:\nNumber: `{num}`\nName: {name}\n\nPayment karke **Transaction ID (TID)** yahan bhejein, ya screenshot bhejein.\nAdmin approve karte hi account auto deliver ho jayega.",
            parse_mode="Markdown")
        context.user_data["awaiting"] = "tid"
    else:
        addr = get_setting("usdt_address")
        total_usdt = round(total / USDT_RATE, 2)
        await q.edit_message_text(
            f"🪙 USDT (TRC20) Payment\n\nProduct: {label}\nAmount: **${total_usdt} USDT**\n\nSend to (TRC20 only):\n`{addr}`\n\nPayment karke **Transaction Hash** yahan bhejein. Bot khud verify karega, phir account auto deliver ho jayega.",
            parse_mode="Markdown")
        context.user_data["awaiting"] = "hash"

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = q.data
    uid = q.from_user.id

    # ---------- Main menu ----------
    if data == "back_menu":
        await q.edit_message_text(await show_menu_text(uid), reply_markup=menu_kb())
        return

    if data == "curr_toggle":
        new = "USDT" if user_currency(uid) == "PKR" else "PKR"
        set_user_currency(uid, new)
        await q.answer(f"Currency ab {new} hai.", show_alert=False)
        await q.edit_message_text(await show_menu_text(uid), reply_markup=menu_kb())
        return

    if data == "menu_products":
        await render_products_list(q, context, 0, None)
        return

    if data == "menu_channels":
        kb = []
        link = tg_join_link()
        if link:
            kb.append([InlineKeyboardButton("📢 Telegram Channel", url=link)])
        if tg_group_url():
            kb.append([InlineKeyboardButton("👥 Telegram Group", url=tg_group_url())])
        if wa_channel_url():
            kb.append([InlineKeyboardButton("💬 WhatsApp Channel", url=wa_channel_url())])
        kb.append([InlineKeyboardButton("⬅ Main Menu", callback_data="back_menu")])
        await q.edit_message_text(
            "📢 Hamare Channels\n\nTelegram channel join karna zaroori hai — bot khud check karta hai.\n"
            "WhatsApp channel pe naye products aur offers milte hain.",
            reply_markup=InlineKeyboardMarkup(kb))
        return

    if data == "menu_joinus":
        kb = []
        link = tg_join_link()
        if link:
            kb.append([InlineKeyboardButton("📢 Telegram Channel", url=link)])
        if tg_group_url():
            kb.append([InlineKeyboardButton("👥 Telegram Group", url=tg_group_url())])
        if wa_channel_url():
            kb.append([InlineKeyboardButton("💬 WhatsApp Channel", url=wa_channel_url())])
        kb.append([InlineKeyboardButton("⬅ Main Menu", callback_data="back_menu")])
        await q.edit_message_text(
            "🤝 Join Us\n\nHamare channels join karein — naye products, offers aur giveaways sabse pehle yahin milte hain.",
            reply_markup=InlineKeyboardMarkup(kb))
        return

    if data == "menu_orders":
        await render_orders_list(q, context)
        return

    if data == "menu_support":
        sup = get_setting("support_username")
        kb = [[InlineKeyboardButton("✉️ Send Message to Admin", callback_data="support_send")]]
        if sup:
            kb.append([InlineKeyboardButton("💬 Message Admin (Telegram)", url=f"https://t.me/{sup.lstrip('@')}")])
        kb.append([InlineKeyboardButton("⬅ Main Menu", callback_data="back_menu")])
        await q.edit_message_text(
            "💬 Support\n\nKoi issue ho — payment, account kaam na kare, ya order na mile — to neeche se admin ko message bhejein.",
            reply_markup=InlineKeyboardMarkup(kb))
        return

    if data == "support_send":
        context.user_data["awaiting"] = "support"
        await q.edit_message_text("✉️ Apna message likhein — admin ko forward kar dunga.\n\n(Cancel ke liye /cancel likhein)")
        return

    if data == "menu_help":
        await q.edit_message_text(
            "❓ Help — Kaise khareedein?\n\n"
            "1️⃣ Products kholein, category ya search se product choose karein.\n"
            "2️⃣ Quantity choose karein (bulk discount ho to kam price milegi).\n"
            "3️⃣ Terms & Conditions parh ke Agree karein.\n"
            "4️⃣ Order Summary check karein — coupon ho to Apply Coupon Code dabayein.\n"
            "5️⃣ Payment karein: Easypaisa / JazzCash (TID ya screenshot bhejein), USDT TRC20 (TX hash bhejein), ya Zurvapay se JazzCash/Easypaisa/Raast QR — auto verify, account khud aa jayega. Wallet balance ho to Pay from Wallet bhi kar sakte hain.\n"

            "6️⃣ Admin verify karte hi account isi chat me auto deliver ho jayega.\n\n"
            "💰 Wallet me Deposit karke bhi pay kar sakte hain.\n"
            "🎁 Pehli order pe FIRST-ORDER coupon se 7% off!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Main Menu", callback_data="back_menu")]]))
        return

    if data == "menu_wallet":
        await render_wallet(q, context)
        return

    if data == "menu_giveaways":
        await render_giveaways(q, context)
        return

    if data == "giv_verify":
        member, _err = await tg_is_member(context.bot, uid)
        if member is True:
            await q.answer("✅ Verified! Aap giveaways ke liye eligible hain.", show_alert=True)
        elif member is None:
            await q.answer("Check nahi ho saka — thodi der me dobara try karein.", show_alert=True)
        else:
            await q.answer("Pehle Telegram channel join karein, phir verify dabayein.", show_alert=True)
        return

    # ---------- Deposit ----------
    if data == "menu_deposit":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💚 Deposit in PKR", callback_data="dep_cur_PKR"),
             InlineKeyboardButton("🪙 Deposit in USDT", callback_data="dep_cur_USDT")],
            [InlineKeyboardButton("⬅ Wallet", callback_data="menu_wallet")],
        ])
        await q.edit_message_text("💰 Deposit\n\nCurrency choose karein (minimum amount 1):", reply_markup=kb)
        return

    if data.startswith("dep_cur_"):
        cur = data.split("_")[2]
        context.user_data["deposit_currency"] = cur
        context.user_data["awaiting"] = "dep_amount"
        await q.edit_message_text(f"💰 Deposit ({cur})\n\nAmount bhejein — minimum 1 {cur}:")
        return

    if data == "wal_topup":
        context.user_data["awaiting"] = "topup"
        await q.edit_message_text("🎟️ Top-up code bhejein:")
        return

    if data == "wal_stmt":
        entries = wallet_statement_entries(uid, limit=20)
        text = "📄 Wallet Statement (last 20)\n\n" + ("\n".join(entries) if entries else "Koi entry nahi hai.")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬇️ Download Statement (.txt)", callback_data="wal_stmt_dl")],
            [InlineKeyboardButton("⬅ Wallet", callback_data="menu_wallet")],
        ])
        await q.edit_message_text(text, reply_markup=kb)
        return

    if data == "wal_stmt_dl":
        entries = wallet_statement_entries(uid, limit=200)
        txt = "RetailShop — Wallet Statement\n" + "\n".join(entries) if entries else "RetailShop — Wallet Statement\n\nKoi entry nahi hai."
        await context.bot.send_document(
            uid, document=InputFile(io.BytesIO(txt.encode("utf-8")), filename="wallet_statement.txt"),
            caption="📄 Aapki wallet statement.")
        return

    # ---------- Channel gate ----------
    if data == "check_join":
        member, _err = await tg_is_member(context.bot, uid)
        if member is True:
            if wa_channel_url() and not wa_joined_flag(uid):
                kb = [[InlineKeyboardButton("💬 Join WhatsApp Channel", url=wa_channel_url())],
                      [InlineKeyboardButton("✅ Maine Join Kar Liya", callback_data="wa_joined")]]
                await q.edit_message_text(
                    "✅ Telegram channel joined! Ab WhatsApp channel bhi join kar lein.",
                    reply_markup=InlineKeyboardMarkup(kb))
                return
            await q.edit_message_text("✅ Channel joined! " + await show_menu_text(uid), reply_markup=menu_kb())
            return
        if member is None:
            await q.answer("Check nahi ho saka — bot channel me admin nahi hai.", show_alert=True)
            return
        await q.answer("Abhi join nahi hua — pehle channel join karein, phir dobara check karein.", show_alert=True)
        return

    if data == "wa_joined":
        # NOTE: WhatsApp channel join ko Telegram bot verify NAHI kar sakta —
        # WhatsApp ka koi public API nahi hai. Ye button user ke bharose pe hai.
        set_wa_joined(uid)
        await q.edit_message_text("✅ Shukriya! " + await show_menu_text(uid), reply_markup=menu_kb())
        return

    # ---------- Products list / detail ----------
    if data.startswith("plist_"):
        try:
            page = int(data.split("_")[1])
        except (IndexError, ValueError):
            page = 0
        await render_products_list(q, context, page, None)
        return

    if data.startswith("cat_"):
        try:
            parts = data.split("_")
            await render_products_list(q, context, int(parts[2]), int(parts[1]))
        except (IndexError, ValueError):
            await render_products_list(q, context, 0, None)
        return

    if data == "search_prod":
        context.user_data["awaiting"] = "search"
        await q.edit_message_text("🔍 Product search karein — keyword bhejein (jaise 'muse' ya 'capcut'):")
        return

    if data == "req_prod":
        context.user_data["awaiting"] = "request"
        await q.edit_message_text("📝 Konsa product chahiye? Naam + details bhejein — admin ko forward kar dunga:")
        return

    if data == "back_list":
        last = context.user_data.get("last_list", "plist_0")
        if last.startswith("cat_"):
            try:
                parts = last.split("_")
                await render_products_list(q, context, int(parts[2]), int(parts[1]))
            except (IndexError, ValueError):
                await render_products_list(q, context, 0, None)
        else:
            try:
                await render_products_list(q, context, int(last.split("_")[1]), None)
            except (IndexError, ValueError):
                await render_products_list(q, context, 0, None)
        return

    if data.startswith("prod_"):
        await render_product_detail(q, context, int(data.split("_")[1]))
        return

    # ---------- Reviews ----------
    if data.startswith("rev_"):
        try:
            parts = data.split("_")
            await render_reviews(q, int(parts[1]), int(parts[2]))
        except (IndexError, ValueError):
            pass
        return

    # ---------- Cart ----------
    if data.startswith("cartadd_"):
        pid = int(data.split("_")[1])
        c = db()
        row = c.execute("SELECT qty FROM carts WHERE user_id=? AND product_id=?", (uid, pid)).fetchone()
        if row:
            c.execute("UPDATE carts SET qty=? WHERE user_id=? AND product_id=?", (min(row["qty"] + 1, 99), uid, pid))
        else:
            c.execute("INSERT INTO carts(user_id,product_id,qty) VALUES(?,?,1)", (uid, pid))
        c.commit(); c.close()
        await q.answer("🛒 Cart me add ho gaya!", show_alert=False)
        await render_product_detail(q, context, pid)
        return

    if data == "cart":
        await render_cart(q, context)
        return

    if data.startswith("cart_rm_"):
        pid = int(data.split("_")[2])
        c = db(); c.execute("DELETE FROM carts WHERE user_id=? AND product_id=?", (uid, pid)); c.commit(); c.close()
        await render_cart(q, context)
        return

    if data == "cart_clear":
        c = db(); c.execute("DELETE FROM carts WHERE user_id=?", (uid,)); c.commit(); c.close()
        await render_cart(q, context)
        return

    if data == "cart_checkout":
        lines = cart_lines_db(uid)
        if not lines:
            await q.answer("Cart khaali hai.", show_alert=True); return
        for l in lines:
            if not l["product"]["unlimited"] and l["qty"] > stock_count(l["pid"]):
                await q.answer(f"{l['product']['name']} ka sirf {stock_count(l['pid'])} stock hai.", show_alert=True); return
        context.user_data["buy_mode"] = "cart"
        context.user_data["buy_items"] = [{"pid": l["pid"], "qty": l["qty"]} for l in lines]
        context.user_data["buy_code"] = gen_order_code()
        context.user_data.pop("buy_coupon_code", None)
        context.user_data.pop("buy_pid", None)
        await render_summary(q, context)
        return

    # ---------- Buy flow ----------
    if data.startswith("buy_") or data.startswith("buyagain_"):
        pid = int(data.split("_")[1])
        c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone(); c.close()
        if not p:
            await q.edit_message_text("Product nahi mila."); return
        if not p["unlimited"] and stock_count(pid) == 0:
            await q.edit_message_text("❌ Is product ka stock khatam hai. Dusra product choose karein.")
            return
        if p["price_pkr"] == 0 and p["price_usdt"] == 0:
            # FREE product — no payment, instant delivery
            code = gen_order_code()
            oid = create_order(uid, q.from_user.username or "", [{"pid": pid, "qty": 1}],
                               "free", "FREE", "free", 0, code, "", 0)
            await q.edit_message_text(f"🎁 FREE product! Order #{oid} — account abhi bhej raha hun...")
            await deliver_order(context, oid)
            return
        context.user_data["buy_pid"] = pid
        context.user_data["buy_mode"] = "single"
        context.user_data["buy_qty"] = 1
        context.user_data["buy_code"] = gen_order_code()
        context.user_data.pop("buy_coupon_code", None)
        context.user_data.pop("buy_items", None)
        await render_qty(q, context, pid)
        return

    if data.startswith("qty_"):
        try:
            _t, pid, qty = data.split("_")
            pid, qty = int(pid), int(qty)
        except ValueError:
            return
        c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone(); c.close()
        if not p:
            return
        if not p["unlimited"] and qty > stock_count(pid):
            await q.answer(f"Sirf {stock_count(pid)} stock hai.", show_alert=True); return
        context.user_data["buy_pid"] = pid
        context.user_data["buy_mode"] = "single"
        context.user_data["buy_qty"] = qty
        if not context.user_data.get("buy_code"):
            context.user_data["buy_code"] = gen_order_code()
        await render_terms(q, context)
        return

    if data.startswith("qtycustom_"):
        pid = int(data.split("_")[1])
        context.user_data["custom_pid"] = pid
        context.user_data["awaiting"] = "custom_qty"
        await q.message.reply_text("🔢 Quantity kitni chahiye? Number bhejein (1–99):")
        return

    if data.startswith("qtycur_"):
        pid = int(data.split("_")[1])
        new = "USDT" if user_currency(uid) == "PKR" else "PKR"
        set_user_currency(uid, new)
        await render_qty(q, context, pid)
        return

    if data == "terms_ok":
        await render_summary(q, context)
        return

    if data == "cancel_order":
        clear_buy_state(context)
        context.user_data.pop("awaiting", None)
        await q.edit_message_text(await show_menu_text(uid), reply_markup=menu_kb())
        return

    if data == "qty_change":
        if context.user_data.get("buy_mode") == "cart":
            await render_cart(q, context)
        else:
            pid = context.user_data.get("buy_pid")
            if pid:
                await render_qty(q, context, pid)
            else:
                await render_products_list(q, context, 0, None)
        return

    if data == "coupon_apply":
        context.user_data["awaiting"] = "coupon"
        await q.message.reply_text("🎟️ Coupon code bhejein (jaise FIRST-ORDER):")
        return

    if data == "paypkr":
        lines, _sub, _disc, total = buy_totals(context, uid)
        if not lines:
            await q.edit_message_text("Session expire ho gayi. /start se dobara karein."); return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💚 JazzCash", callback_data="pay_jazzcash")],
            [InlineKeyboardButton("💚 Easypaisa", callback_data="pay_easypaisa")],
            [InlineKeyboardButton("⬅ Back to Summary", callback_data="summary_back")],
        ])
        await q.edit_message_text(f"💳 Pay via PKR — Total: Rs {total}\n\nMethod choose karein:", reply_markup=kb)
        return

    if data == "payusdt":
        await start_payment(q, context, "usdt")
        return

    if data == "summary_back":
        await render_summary(q, context)
        return

    if data == "pay_wallet":
        lines, _sub, discount, total = buy_totals(context, uid)
        if not lines:
            await q.edit_message_text("Session expire ho gayi. /start se dobara karein."); return
        cur = user_currency(uid)
        need = total if cur == "PKR" else round(total / USDT_RATE, 2)
        bal = get_wallet(uid, cur)
        if bal < need:
            await q.answer("Wallet balance kam hai. Pehle Deposit karein.", show_alert=True); return
        code = context.user_data.get("buy_code") or gen_order_code()
        coupon_code = context.user_data.get("buy_coupon_code") or ""
        items = [{"pid": l["pid"], "qty": l["qty"]} for l in lines]
        amount_text = f"{fmt_money(total, uid)} via wallet"
        add_wallet(uid, cur, -need)
        oid = create_order(uid, q.from_user.username or "", items, "wallet", amount_text, "wallet", total, code, coupon_code, discount)
        if context.user_data.get("buy_mode") == "cart":
            c = db(); c.execute("DELETE FROM carts WHERE user_id=?", (uid,)); c.commit(); c.close()
        clear_buy_state(context)
        await q.edit_message_text(f"✅ Wallet se payment ho gayi! Order #{oid} — account abhi bhej raha hun...")
        ok = await deliver_order(context, oid)
        if not ok:
            add_wallet(uid, cur, need)  # refund — stock khatam thi
            await context.bot.send_message(uid, "⚠️ Delivery nahi ho saki (stock khatam) — wallet refund kar diya gaya hai.")
        return

    if data.startswith("pay_"):
        method = data.split("_")[1]  # jazzcash / easypaisa / usdt
        await start_payment(q, context, method)
        return

    # ---------- My Orders detail / review ----------
    if data.startswith("ord_"):
        await render_order_detail(q, context, int(data.split("_")[1]))
        return

    if data.startswith("wrev_"):
        oid = int(data.split("_")[1])
        c = db(); o = c.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (oid, uid)).fetchone(); c.close()
        if not o or o["status"] != "delivered":
            await q.answer("Review sirf delivered order pe likhi ja sakti hai.", show_alert=True); return
        c = db(); p = c.execute("SELECT name FROM products WHERE id=?", (o["product_id"],)).fetchone(); c.close()
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐1", callback_data=f"revrate_{oid}_1"),
             InlineKeyboardButton("⭐2", callback_data=f"revrate_{oid}_2"),
             InlineKeyboardButton("⭐3", callback_data=f"revrate_{oid}_3"),
             InlineKeyboardButton("⭐4", callback_data=f"revrate_{oid}_4"),
             InlineKeyboardButton("⭐5", callback_data=f"revrate_{oid}_5")],
            [InlineKeyboardButton("⬅ Back", callback_data=f"ord_{oid}")],
        ])
        await q.edit_message_text(f"⭐ Review — {p['name'] if p else ''}\n\nRating choose karein (1–5):", reply_markup=kb)
        return

    if data.startswith("revrate_"):
        try:
            _t, oid, rating = data.split("_")
            oid, rating = int(oid), int(rating)
        except ValueError:
            return
        context.user_data["review"] = {"oid": oid, "rating": rating}
        context.user_data["awaiting"] = "review_text"
        await q.message.reply_text(f"⭐ Rating: {rating}/5\n\nAb apni review likhein (text bhejein):")
        return

    # ---------- Admin approve / reject ----------
    if data.startswith("approve_"):
        if not is_admin(uid):
            await q.answer("Sirf admin.", show_alert=True); return
        oid = int(data.split("_")[1])
        ok = await deliver_order(context, oid)
        if ok:
            await q.edit_message_text(f"✅ Order #{oid} approved + delivered.")
        else:
            await q.edit_message_text(f"⚠️ Order #{oid} deliver nahi hui — stock khatam hai.")
        return

    if data.startswith("reject_"):
        if not is_admin(uid):
            await q.answer("Sirf admin.", show_alert=True); return
        oid = int(data.split("_")[1])
        c = db(); c.execute("UPDATE orders SET status='rejected' WHERE id=?", (oid,))
        o = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone(); c.commit(); c.close()
        await q.edit_message_text(f"❌ Order #{oid} rejected.")
        if o:
            await context.bot.send_message(o["user_id"], f"❌ Aapki Order #{oid} reject ho gayi. Payment proof galat ho sakta hai. Admin se rabta karein.")
        return

    if data.startswith("depapprove_"):
        if not is_admin(uid):
            await q.answer("Sirf admin.", show_alert=True); return
        dep_id = int(data.split("_")[1])
        c = db(); d = c.execute("SELECT * FROM deposits WHERE id=?", (dep_id,)).fetchone()
        if not d:
            c.close(); await q.edit_message_text("Deposit nahi mili."); return
        if d["status"] != "pending":
            c.close(); await q.edit_message_text(f"Deposit #{dep_id} pehle hi {d['status']} hai."); return
        c.execute("UPDATE deposits SET status='approved' WHERE id=?", (dep_id,)); c.commit(); c.close()
        add_wallet(d["user_id"], d["currency"], d["amount"])
        await q.edit_message_text(f"✅ Deposit #{dep_id} approved + wallet credited ({d['amount']} {d['currency']}).")
        await context.bot.send_message(d["user_id"], f"✅ Deposit approved! {d['amount']} {d['currency']} aapke wallet me add ho gaye.")
        return

    if data.startswith("depreject_"):
        if not is_admin(uid):
            await q.answer("Sirf admin.", show_alert=True); return
        dep_id = int(data.split("_")[1])
        c = db(); d = c.execute("SELECT * FROM deposits WHERE id=?", (dep_id,)).fetchone()
        if not d:
            c.close(); await q.edit_message_text("Deposit nahi mili."); return
        c.execute("UPDATE deposits SET status='rejected' WHERE id=?", (dep_id,)); c.commit(); c.close()
        await q.edit_message_text(f"❌ Deposit #{dep_id} rejected.")
        await context.bot.send_message(d["user_id"], f"❌ Aapki Deposit #{dep_id} reject ho gayi. Payment proof galat ho sakta hai. Admin se rabta karein.")
        return

# ---------- Wallet statement ----------

def wallet_statement_entries(uid, limit=20):
    entries = []
    c = db()
    deps = c.execute("SELECT * FROM deposits WHERE user_id=? AND status='approved' ORDER BY id DESC LIMIT ?", (uid, limit)).fetchall()
    for d in deps:
        entries.append((d["created_at"] or "", f"💰 Deposit +{d['amount']} {d['currency']} — approved ({d['created_at'] or ''})"))
    ords = c.execute("SELECT * FROM orders WHERE user_id=? AND status='delivered' ORDER BY id DESC LIMIT ?", (uid, limit)).fetchall()
    for o in ords:
        c2 = db(); p = c2.execute("SELECT name FROM products WHERE id=?", (o["product_id"],)).fetchone(); c2.close()
        entries.append((o["created_at"] or "", f"🛒 Purchase -{o['amount_text']} — {p['name'] if p else '?'} (Order #{o['id']}, {o['created_at'] or ''})"))
    c.close()
    entries.sort(key=lambda x: x[0], reverse=True)
    return [e[1] for e in entries[:limit]]

# ---------- Buyer proof / text messages ----------

async def _handle_buy_proof(update, context, deposit=False):
    """TID/hash proof for buy (deposit=False) or deposit (deposit=True).
    Buy: TID -> pending + admin notify; hash -> Tronscan verify -> deliver ya pending.
    Deposit: TID -> pending deposit + admin notify; hash -> verify -> credit ya pending."""
    user = update.effective_user
    if deposit:
        dep = context.user_data.get("deposit") or {}
        method = dep.get("method", "?")
        amount = dep.get("amount", 0)
        currency = dep.get("currency", "PKR")
        proof = update.message.text if update.message.text else "screenshot"
        if update.message.photo:
            proof = f"photo:{update.message.photo[-1].file_id}"
        awaiting = context.user_data.get("awaiting")
        if awaiting == "dep_hash":
            tx = (update.message.text or "").strip()
            if len(tx) < 20:
                await update.message.reply_text("Ye hash lagta nahi. Poora Transaction Hash bhejein (TronScan link se copy karein)."); return
            addr = get_setting("usdt_address")
            ok, reason = verify_usdt(tx, addr, float(amount))
            if ok:
                c = db(); cur = c.execute(
                    "INSERT INTO deposits(user_id,username,currency,amount,payment_method,proof,status,created_at) VALUES(?,?,?,?,?,?, 'approved', ?)",
                    (user.id, user.username or "", currency, amount, "usdt", tx, datetime.now().strftime("%Y-%m-%d %H:%M")))
                dep_id = cur.lastrowid; c.commit(); c.close()
                add_wallet(user.id, currency, amount)
                context.user_data.clear()
                await update.message.reply_text(f"✅ USDT verified! {reason}\nDeposit #{dep_id} — {amount} {currency} aapke wallet me add ho gaye.")
                return
            c = db(); cur = c.execute(
                "INSERT INTO deposits(user_id,username,currency,amount,payment_method,proof,status,created_at) VALUES(?,?,?,?,?,?, 'pending', ?)",
                (user.id, user.username or "", currency, amount, "usdt", tx, datetime.now().strftime("%Y-%m-%d %H:%M")))
            dep_id = cur.lastrowid; c.commit(); c.close()
            context.user_data.clear()
            await update.message.reply_text(f"⚠️ Auto-verify nahi hui: {reason}\nDeposit #{dep_id} admin ko bhej di hai — wo manually check karke credit karega.")
            await notify_admin_deposit(context, dep_id)
            return
        # dep_tid (manual PKR)
        c = db(); cur = c.execute(
            "INSERT INTO deposits(user_id,username,currency,amount,payment_method,proof,status,created_at) VALUES(?,?,?,?,?,?, 'pending', ?)",
            (user.id, user.username or "", currency, amount, method, proof, datetime.now().strftime("%Y-%m-%d %H:%M")))
        dep_id = cur.lastrowid; c.commit(); c.close()
        context.user_data.clear()
        await update.message.reply_text(f"✅ Deposit #{dep_id} mil gayi! Admin verify kar raha hai. Approve hote hi wallet credit ho jayega.")
        await notify_admin_deposit(context, dep_id)
        return

    # --- buy proof ---
    lines, _sub, discount, total = buy_totals(context, user.id)
    method = context.user_data.get("pay_method", "?")
    if not lines:
        context.user_data.clear()
        await update.message.reply_text("Session expire ho gayi. /start se dobara karein.")
        return
    awaiting = context.user_data.get("awaiting")
    code = context.user_data.get("buy_code") or gen_order_code()
    coupon_code = context.user_data.get("buy_coupon_code") or ""
    items = [{"pid": l["pid"], "qty": l["qty"]} for l in lines]

    if awaiting == "tid":
        proof = update.message.text if update.message.text else "screenshot"
        if update.message.photo:
            proof = f"photo:{update.message.photo[-1].file_id}"
        amount_text = f"Rs {total} via {method}"
        oid = create_order(user.id, user.username or "", items, method, amount_text, proof, total, code, coupon_code, discount)
        if context.user_data.get("buy_mode") == "cart":
            c = db(); c.execute("DELETE FROM carts WHERE user_id=?", (user.id,)); c.commit(); c.close()
        context.user_data.clear()
        await update.message.reply_text(f"✅ Order #{oid} ({code}) mil gayi! Admin verify kar raha hai. Approve hote hi account isi chat me aa jayega.")
        await notify_admin_pending(context, oid)

    elif awaiting == "hash":
        tx = (update.message.text or "").strip()
        if len(tx) < 20:
            await update.message.reply_text("Ye hash lagta nahi. Poora Transaction Hash bhejein (TronScan link se copy karein)."); return
        addr = get_setting("usdt_address")
        total_usdt = round(total / USDT_RATE, 2)
        ok, reason = verify_usdt(tx, addr, total_usdt)
        if ok:
            oid = create_order(user.id, user.username or "", items, "usdt", f"${total_usdt} USDT", tx, total, code, coupon_code, discount)
            if context.user_data.get("buy_mode") == "cart":
                c = db(); c.execute("DELETE FROM carts WHERE user_id=?", (user.id,)); c.commit(); c.close()
            context.user_data.clear()
            await update.message.reply_text(f"✅ USDT verified! {reason}\nOrder #{oid} ({code}) — account abhi bhej raha hun...")
            await deliver_order(context, oid)
        else:
            # verify fail -> pending for manual admin check, buyer ko batao
            oid = create_order(user.id, user.username or "", items, "usdt", f"${total_usdt} USDT", tx, total, code, coupon_code, discount)
            if context.user_data.get("buy_mode") == "cart":
                c = db(); c.execute("DELETE FROM carts WHERE user_id=?", (user.id,)); c.commit(); c.close()
            context.user_data.clear()
            await update.message.reply_text(f"⚠️ Auto-verify nahi hui: {reason}\nOrder #{oid} ({code}) admin ko bhej di hai — wo manually check karke deliver karega.")
            await notify_admin_pending(context, oid)

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saare text/photo messages yahan aate hain — user_data['awaiting'] ke
    hisaab se route hota hai. ConversationHandlers (addproduct/addstock)
    is se pehle register hain, is liye un ke states yahan nahi aate."""
    awaiting = context.user_data.get("awaiting")
    user = update.effective_user
    if not awaiting:
        return

    if awaiting in ("tid", "hash"):
        await _handle_buy_proof(update, context, deposit=False)
        return
    if awaiting in ("dep_tid", "dep_hash"):
        await _handle_buy_proof(update, context, deposit=True)
        return

    if awaiting == "support":
        text = (update.message.text or "").strip()
        if not text:
            return
        c = db(); c.execute("INSERT INTO support_msgs(user_id,username,text,direction,created_at) VALUES(?,?,?,?,?)",
                            (user.id, user.username or "", text, "user", datetime.now().strftime("%Y-%m-%d %H:%M")))
        c.commit(); c.close()
        context.user_data.pop("awaiting", None)
        await update.message.reply_text("✅ Aapka message admin ko bhej diya gaya hai. Jawab isi chat me aayega.")
        await context.bot.send_message(
            ADMIN_ID,
            f"💬 Support message\nFrom: @{user.username or user.id} ({user.id})\n\n{text}\n\nReply ke liye: /reply {user.id} <text>")
        return

    if awaiting == "coupon":
        code = (update.message.text or "").strip()
        ok, reason = coupon_valid(user.id, code)
        if not ok:
            await update.message.reply_text(f"❌ {reason}")
            context.user_data.pop("awaiting", None)
            return
        context.user_data["buy_coupon_code"] = code.strip().upper()
        context.user_data.pop("awaiting", None)
        await update.message.reply_text(
            f"✅ Coupon {code.strip().upper()} apply ho gaya! {FIRST_COUPON_PCT}% off — summary dobara dikha raha hun...",
            reply_markup=None)
        # summary dobara bhejdo (message handler se edit nahi ho sakta)
        await update.message.reply_text(summary_text(user.id, context), reply_markup=summary_kb(user.id, context))
        return

    if awaiting == "search":
        kw = (update.message.text or "").strip()
        context.user_data.pop("awaiting", None)
        if not kw:
            return
        c = db(); prods = c.execute("SELECT * FROM products WHERE active=1 AND name LIKE ? ORDER BY id LIMIT 10", (f"%{kw}%",)).fetchall(); c.close()
        if not prods:
            await update.message.reply_text(f"🔍 '{kw}' — koi product nahi mila.", reply_markup=menu_kb())
            return
        kb = []
        for p in prods:
            kb.append([InlineKeyboardButton(f"{_short_name(p['name'])} — {price_text(p, user.id)} ({stock_tag(p)})",
                                            callback_data=f"prod_{p['id']}")])
        kb.append([InlineKeyboardButton("⬅ Products", callback_data="menu_products")])
        await update.message.reply_text(f"🔍 Search results for '{kw}':", reply_markup=InlineKeyboardMarkup(kb))
        return

    if awaiting == "request":
        text = (update.message.text or "").strip()
        context.user_data.pop("awaiting", None)
        if not text:
            return
        await update.message.reply_text("✅ Aapki request admin ko bhej di gayi hai. Shukriya!")
        await context.bot.send_message(
            ADMIN_ID,
            f"📝 Product request\nFrom: @{user.username or user.id} ({user.id})\n\n{text}")
        return

    if awaiting == "review_text":
        text = (update.message.text or "").strip()
        rev = context.user_data.pop("review", None)
        context.user_data.pop("awaiting", None)
        if not rev or not text:
            return
        c = db(); o = c.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (rev["oid"], user.id)).fetchone()
        if not o:
            c.close(); return
        name = user.first_name or user.username or "User"
        c.execute("INSERT INTO reviews(product_id,user_name,rating,text,verified,source) VALUES(?,?,?,?,1,'user')",
                  (o["product_id"], name, rev["rating"], text))
        c.commit(); c.close()
        await update.message.reply_text("✅ Shukriya! Aapki review save ho gayi.")
        return

    if awaiting == "topup":
        code = (update.message.text or "").strip().upper()
        context.user_data.pop("awaiting", None)
        if not code:
            return
        c = db(); r = c.execute("SELECT * FROM topup_codes WHERE code=?", (code,)).fetchone()
        if not r:
            c.close(); await update.message.reply_text("❌ Ye top-up code galat hai."); return
        if r["used_by"]:
            c.close(); await update.message.reply_text("❌ Ye code pehle hi use ho chuka hai."); return
        c.execute("UPDATE topup_codes SET used_by=? WHERE code=?", (user.id, code)); c.commit(); c.close()
        add_wallet(user.id, r["currency"], r["amount"])
        await update.message.reply_text(f"✅ Top-up successful! {r['amount']} {r['currency']} aapke wallet me add ho gaye.")
        return

    if awaiting == "dep_amount":
        cur = context.user_data.get("deposit_currency", "PKR")
        try:
            amount = float((update.message.text or "").strip())
        except ValueError:
            await update.message.reply_text("Sirf number bhejein (jaise 500):"); return
        if amount < 1:
            await update.message.reply_text("Minimum deposit 1 hai. Dobara amount bhejein:"); return
        context.user_data["deposit"] = {"currency": cur, "amount": amount}
        context.user_data.pop("awaiting", None)
        if cur == "PKR":
            kb_rows = [
                [InlineKeyboardButton("💚 JazzCash", callback_data="pay_jazzcash"),
                 InlineKeyboardButton("💚 Easypaisa", callback_data="pay_easypaisa")],
            ]
            if zurvapay_enabled():
                kb_rows.append([InlineKeyboardButton("⚡ Zurvapay (Auto) — JazzCash/Easypaisa/Raast", callback_data="pay_zurvapay")])
            kb_rows.append([InlineKeyboardButton("⬅ Back", callback_data="menu_deposit")])
            kb = InlineKeyboardMarkup(kb_rows)
            await update.message.reply_text(f"💰 Deposit Rs {amount} — payment method choose karein:", reply_markup=kb)
        else:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🪙 USDT (TRC20)", callback_data="pay_usdt")],
                [InlineKeyboardButton("⬅ Back", callback_data="menu_deposit")],
            ])
            await update.message.reply_text(f"💰 Deposit ${amount} USDT — payment method choose karein:", reply_markup=kb)
        return

    if awaiting == "custom_qty":
        pid = context.user_data.get("custom_pid")
        context.user_data.pop("awaiting", None)
        try:
            qty = int((update.message.text or "").strip())
        except ValueError:
            await update.message.reply_text("Sirf number bhejein (1–99):"); return
        if qty < 1 or qty > 99:
            await update.message.reply_text("Quantity 1 se 99 ke beech honi chahiye."); return
        c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone(); c.close()
        if not p:
            return
        if not p["unlimited"] and qty > stock_count(pid):
            await update.message.reply_text(f"❌ Sirf {stock_count(pid)} stock hai."); return
        context.user_data["buy_pid"] = pid
        context.user_data["buy_mode"] = "single"
        context.user_data["buy_qty"] = qty
        if not context.user_data.get("buy_code"):
            context.user_data["buy_code"] = gen_order_code()
        lines, _sub, _disc, total = buy_totals(context, user.id)
        terms = terms_for_product(p)
        text = (f"📜 Terms & Conditions\n\n{terms}\n\n"
                f"Order: {p['name']} × {qty}\nTotal: {fmt_money(total, user.id)}")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Agree & Continue to Payment", callback_data="terms_ok")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")],
        ])
        await update.message.reply_text(text, reply_markup=kb)
        return

# ---------- Admin ----------

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Sirf admin ke liye hai."); return
    c = db()
    prods = c.execute("SELECT * FROM products ORDER BY id").fetchall()
    pending = c.execute("SELECT COUNT(*) n FROM orders WHERE status='pending'").fetchone()["n"]
    dep_pending = c.execute("SELECT COUNT(*) n FROM deposits WHERE status='pending'").fetchone()["n"]
    c.close()
    lines = ["👑 Admin Panel\n", f"Pending orders: {pending}", f"Pending deposits: {dep_pending}", ""]
    for p in prods:
        lines.append(f"#{p['id']} {p['name']} — Rs {p['price_pkr']} / ${p['price_usdt']} — Stock: {stock_count(p['id'])}")
    lines.append("\nCommands:\n/addproduct — naya product\n/addstock — accounts add karo\n/removeproduct <id> — product delete\n/setprice <id> <pkr> <usdt> — price change (0 0 = FREE)\n/setkind <id> <mail|account> — delivery type (mail = email + mailbox URL)\n/setdesc <id> <text> — product description\n/setterms <id> <text> — terms & conditions\n/setbulk <id> <tiers> — bulk discount, jaise 3:360,5:345 (clear = 'clear')\n/setunlimited <id> <on|off> — ♾️ stock display\n/addreview <id> <name> <rating 1-5> <text> — shop ki taraf se review\n/topupcode <CODE> <amount> <currency> — wallet top-up code banao\n/reply <user_id> <text> — support message ka jawab\n/disableproduct <id> — list se chhupao\n/enableproduct <id> — wapas dikhao\n/clearstock <id> — stock khatam (Out of Stock)\n/orders — pending orders\n/setpayments — JazzCash/Easypaisa/USDT details\n/stock — saara stock dekho\n/set tg_channel @name — Telegram channel\n/set tg_group_url <link> — Telegram group link\n/set wa_channel_url <link> — WhatsApp channel")
    await update.message.reply_text("\n".join(lines))

async def orders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    c = db(); rows = c.execute("SELECT * FROM orders WHERE status='pending' ORDER BY id DESC LIMIT 10").fetchall(); c.close()
    if not rows:
        await update.message.reply_text("Koi pending order nahi."); return
    for o in rows:
        c = db(); p = c.execute("SELECT name FROM products WHERE id=?", (o["product_id"],)).fetchone(); c.close()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"approve_{o['id']}"), InlineKeyboardButton("❌ Reject", callback_data=f"reject_{o['id']}")]])
        await update.message.reply_text(f"Order #{o['id']} ({o['code'] or ''})\nProduct: {p['name']}\nQty: {o['qty']}\nBuyer: @{o['username'] or o['user_id']}\nPayment: {o['payment_method']} — {o['amount_text']}\nProof: {o['proof']}", reply_markup=kb)

async def stock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    c = db(); prods = c.execute("SELECT * FROM products ORDER BY id").fetchall(); c.close()
    msg = []
    for p in prods:
        state = "✅ active" if p["active"] else "🚫 hidden"
        msg.append(f"#{p['id']} {p['name']}: {stock_count(p['id'])} available — {state}")
    await update.message.reply_text("\n".join(msg) or "Koi product nahi.")

# ---------- Admin: product management ----------
# /removeproduct <id> — product + uska bacha hua (unsold) stock delete
# /setprice <id> <pkr> <usdt> — price change; 0 0 = FREE (bina payment delivery)
# /disableproduct <id> — list se chhupao (out of stock jaisa, delete nahi)
# /enableproduct <id> — wapas dikhao
# /clearstock <id> — saare available accounts delete (product Out of Stock ho jata hai)

def _pid_arg(context):
    try:
        return int(context.args[0])
    except (IndexError, ValueError):
        return None

async def removeproduct_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    pid = _pid_arg(context)
    if not pid:
        await update.message.reply_text("Usage: /removeproduct <id>  (ID /admin ya /stock se dekho)"); return
    c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        c.close(); await update.message.reply_text("Ye ID nahi mili."); return
    c.execute("DELETE FROM accounts WHERE product_id=? AND status='available'", (pid,))
    c.execute("DELETE FROM products WHERE id=?", (pid,)); c.commit(); c.close()
    await update.message.reply_text(f"🗑️ Product #{pid} ({p['name']}) delete ho gaya. Sold accounts ka record orders me mehfooz hai.")

async def setprice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try:
        pid = int(context.args[0]); pkr = int(context.args[1]); usdt = float(context.args[2])
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /setprice <id> <pkr> <usdt>\nExample: /setprice 5 500 1.79\nFREE karne ke liye: /setprice 5 0 0"); return
    c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        c.close(); await update.message.reply_text("Ye ID nahi mili."); return
    c.execute("UPDATE products SET price_pkr=?, price_usdt=? WHERE id=?", (pkr, usdt, pid)); c.commit(); c.close()
    tag = " — ab ye 🆓 FREE hai, buyer ko bina payment account milega." if pkr == 0 and usdt == 0 else ""
    await update.message.reply_text(f"✅ {p['name']} ki price ab Rs {pkr} / ${usdt}.{tag}")

async def disableproduct_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    pid = _pid_arg(context)
    if not pid:
        await update.message.reply_text("Usage: /disableproduct <id>"); return
    c = db(); c.execute("UPDATE products SET active=0 WHERE id=?", (pid,)); c.commit(); c.close()
    await update.message.reply_text(f"🚫 Product #{pid} list se chhupa diya (delete nahi hua). Wapas lane ke liye /enableproduct {pid}")

async def enableproduct_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    pid = _pid_arg(context)
    if not pid:
        await update.message.reply_text("Usage: /enableproduct <id>"); return
    c = db(); c.execute("UPDATE products SET active=1 WHERE id=?", (pid,)); c.commit(); c.close()
    await update.message.reply_text(f"✅ Product #{pid} wapas list me dikhne laga.")

async def clearstock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    pid = _pid_arg(context)
    if not pid:
        await update.message.reply_text("Usage: /clearstock <id>"); return
    c = db(); c.execute("DELETE FROM accounts WHERE product_id=? AND status='available'", (pid,)); c.commit(); c.close()
    await update.message.reply_text(f"🧹 Product #{pid} ka saara available stock clear — ab ye Out of Stock dikhega. /addstock se dobara add karo.")

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
        "ya\n`username | password`\n\n"
        "Mail-type product (Muse token jaisa) ke liye:\n"
        "`code | email | mailbox_url`\n\nExample:\ntest@gmail.com | Pass123\nuser2 | Abc456",
        parse_mode="Markdown")
    return STOCK_LINES

async def stock_lines(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pid = context.user_data["stock_pid"]
    lines = [l.strip() for l in (update.message.text or "").splitlines() if l.strip()]
    added = 0
    c = db()
    for line in lines:
        extra = ""
        if "|" in line:
            parts = [x.strip() for x in line.split("|")]
            if len(parts) >= 3:
                # Mail-type: code | email | mailbox_url
                extra, login, pwd = parts[0], parts[1], "|".join(parts[2:])
            else:
                login, pwd = parts[0], parts[1] if len(parts) > 1 else ""
        elif "," in line:
            login, pwd = [x.strip() for x in line.split(",", 1)]
        else:
            parts = line.split()
            if len(parts) < 2: continue
            login, pwd = parts[0], parts[1]
        if login and pwd:
            c.execute("INSERT INTO accounts(product_id,login,password,extra) VALUES(?,?,?,?)", (pid, login, pwd, extra))
            added += 1
    c.commit(); c.close()
    context.user_data.clear()
    await update.message.reply_text(f"✅ {added} accounts add ho gaye! Total stock ab: {stock_count(pid)}")
    return ConversationHandler.END

async def setkind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Product ki delivery type set karo: /setkind <id> <mail|account>.
    mail = Muse token jaisa — buyer ko email + mailbox URL milta hai, OTP wo
    khud mailbox site se leta hai. account = normal login + password."""
    if not is_admin(update.effective_user.id): return
    try:
        pid = int(context.args[0]); kind = context.args[1].lower()
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /setkind <id> <mail|account>\nExample: /setkind 8 mail"); return
    if kind not in ("mail", "account"):
        await update.message.reply_text("Kind sirf 'mail' ya 'account' ho sakta hai."); return
    c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        c.close(); await update.message.reply_text("Ye ID nahi mili."); return
    c.execute("UPDATE products SET kind=? WHERE id=?", (kind, pid)); c.commit(); c.close()
    await update.message.reply_text(f"✅ {p['name']} ki delivery type ab '{kind}' hai.")

async def setdesc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    pid = _pid_arg(context)
    if not pid or len(context.args) < 2:
        await update.message.reply_text("Usage: /setdesc <id> <text>"); return
    text = " ".join(context.args[1:])
    c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        c.close(); await update.message.reply_text("Ye ID nahi mili."); return
    c.execute("UPDATE products SET description=? WHERE id=?", (text, pid)); c.commit(); c.close()
    await update.message.reply_text(f"✅ {p['name']} ki description set ho gayi.")

async def setterms_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    pid = _pid_arg(context)
    if not pid or len(context.args) < 2:
        await update.message.reply_text("Usage: /setterms <id> <text>"); return
    text = " ".join(context.args[1:])
    c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        c.close(); await update.message.reply_text("Ye ID nahi mili."); return
    c.execute("UPDATE products SET terms=? WHERE id=?", (text, pid)); c.commit(); c.close()
    await update.message.reply_text(f"✅ {p['name']} ke terms set ho gaye.")

async def setbulk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    pid = _pid_arg(context)
    if not pid or len(context.args) < 2:
        await update.message.reply_text("Usage: /setbulk <id> <tiers>\nExample: /setbulk 8 3:360,5:345,10:315\nClear karne ke liye: /setbulk 8 clear"); return
    raw = " ".join(context.args[1:]).strip()
    if raw.lower() == "clear":
        tiers = ""
    else:
        if not parse_bulk(raw):
            await update.message.reply_text("Tiers galat hain. Format: 3:360,5:345 (qty:unit_price_pkr)"); return
        tiers = raw
    c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        c.close(); await update.message.reply_text("Ye ID nahi mili."); return
    c.execute("UPDATE products SET bulk=? WHERE id=?", (tiers, pid)); c.commit(); c.close()
    await update.message.reply_text(f"✅ {p['name']} ke bulk discounts: {tiers or 'clear ho gaye'}")

async def setunlimited_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    pid = _pid_arg(context)
    if not pid or len(context.args) < 2:
        await update.message.reply_text("Usage: /setunlimited <id> <on|off>"); return
    val = context.args[1].lower()
    if val not in ("on", "off"):
        await update.message.reply_text("Sirf 'on' ya 'off'."); return
    c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        c.close(); await update.message.reply_text("Ye ID nahi mili."); return
    c.execute("UPDATE products SET unlimited=? WHERE id=?", (1 if val == "on" else 0, pid)); c.commit(); c.close()
    await update.message.reply_text(f"✅ {p['name']} — unlimited display {'ON (♾️ dikhega)' if val == 'on' else 'OFF'}.")
    if val == "on":
        await update.message.reply_text("Note: ♾️ sirf display hai — auto-delivery ke liye phir bhi /addstock se accounts chahiye. Stock khatam hone pe order admin ko notify hoga.")

async def addreview_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/addreview <product_id> <name> <rating 1-5> <text> — shop ki taraf se review."""
    if not is_admin(update.effective_user.id): return
    args = context.args
    if len(args) < 4:
        await update.message.reply_text("Usage: /addreview <id> <name> <rating 1-5> <text>\nExample: /addreview 8 Ahmed 5 Best service, fast delivery!"); return
    try:
        pid = int(args[0])
    except ValueError:
        await update.message.reply_text("Product ID number hona chahiye."); return
    rest = args[1:]
    rating_idx = None
    for i, t in enumerate(rest):
        if t in ("1", "2", "3", "4", "5"):
            rating_idx = i; break
    if rating_idx is None or rating_idx == 0 or rating_idx == len(rest) - 1:
        await update.message.reply_text("Rating (1-5) aur text dono chahiye. Format: /addreview <id> <name> <rating> <text>"); return
    name = " ".join(rest[:rating_idx])
    rating = int(rest[rating_idx])
    text = " ".join(rest[rating_idx + 1:])
    c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        c.close(); await update.message.reply_text("Ye ID nahi mili."); return
    c.execute("INSERT INTO reviews(product_id,user_name,rating,text,verified,source) VALUES(?,?,?,?,0,'shop')",
              (pid, name, rating, text))
    c.commit(); c.close()
    await update.message.reply_text(f"✅ Review add ho gayi: {p['name']} — ⭐{rating} {name}: {text}")

async def topupcode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/topupcode <CODE> <amount> <currency> — wallet top-up code banao."""
    if not is_admin(update.effective_user.id): return
    try:
        code = context.args[0].strip().upper()
        amount = float(context.args[1])
        currency = context.args[2].strip().upper()
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /topupcode <CODE> <amount> <currency>\nExample: /topupcode WELCOME100 100 PKR"); return
    if currency not in ("PKR", "USDT"):
        await update.message.reply_text("Currency sirf PKR ya USDT."); return
    if amount < 1:
        await update.message.reply_text("Amount minimum 1 hona chahiye."); return
    c = db()
    if c.execute("SELECT 1 FROM topup_codes WHERE code=?", (code,)).fetchone():
        c.close(); await update.message.reply_text("Ye code pehle se hai. Dusra code use karein."); return
    c.execute("INSERT INTO topup_codes(code,amount,currency,used_by,created_at) VALUES(?,?,?,?,?)",
              (code, amount, currency, None, datetime.now().strftime("%Y-%m-%d %H:%M")))
    c.commit(); c.close()
    await update.message.reply_text(f"✅ Top-up code ban gaya: {code} — {amount} {currency}\nBuyer Wallet -> Redeem Top-up Code se use karega.")

async def reply_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/reply <user_id> <text> — support message ka jawab user ko bhejo."""
    if not is_admin(update.effective_user.id): return
    try:
        uid = int(context.args[0])
        text = " ".join(context.args[1:])
        if not text:
            raise ValueError
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /reply <user_id> <text>"); return
    c = db(); c.execute("INSERT INTO support_msgs(user_id,username,text,direction,created_at) VALUES(?,?,?,?,?)",
                        (uid, "", text, "admin", datetime.now().strftime("%Y-%m-%d %H:%M")))
    c.commit(); c.close()
    try:
        await context.bot.send_message(uid, f"💬 Support reply:\n\n{text}")
        await update.message.reply_text(f"✅ Reply user {uid} ko bhej diya.")
    except Exception as e:
        await update.message.reply_text(f"❌ Bhej nahi saka: {e}")

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
        await update.message.reply_text("Usage: /set <key> <value>\nKeys: jazzcash_number, jazzcash_name, easypaisa_number, easypaisa_name, usdt_address, tg_channel, tg_group_url, wa_channel_url, support_username"); return
    key, val = context.args[0], " ".join(context.args[1:])
    if key not in ("jazzcash_number", "jazzcash_name", "easypaisa_number", "easypaisa_name", "usdt_address",
                   "tg_channel", "tg_group_url", "wa_channel_url", "support_username"):
        await update.message.reply_text("Galat key. Keys: jazzcash_number, jazzcash_name, easypaisa_number, easypaisa_name, usdt_address, tg_channel, tg_group_url, wa_channel_url, support_username"); return
    if key == "tg_channel":
        val = val.strip()
    set_setting(key, val)
    extra = ""
    if key == "tg_channel":
        extra = "\n⚠️ Bot ko is channel me ADMIN banana zaroori hai, warna join check nahi hoga."
    await update.message.reply_text(f"✅ {key} = {val}{extra}")

# ---------- Main ----------

def main():
    global PTB_APP, PTB_LOOP
    init_db()
    start_health_server()
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN .env me nahi hai. .env.example dekho.")
        return
    if ADMIN_ID == 0:
        print("WARNING: ADMIN_ID set nahi hai — /admin kaam nahi karega. @userinfobot se apni ID le ke .env me dalo.")
    app = Application.builder().token(BOT_TOKEN).build()
    PTB_APP = app  # IPN HTTP thread ko bot chahiye hota hai (delivery msgs ke liye)

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
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("orders", orders_cmd))
    app.add_handler(CommandHandler("stock", stock_cmd))
    app.add_handler(CommandHandler("setpayments", setpayments))
    app.add_handler(CommandHandler("set", set_cmd))
    app.add_handler(CommandHandler("removeproduct", removeproduct_cmd))
    app.add_handler(CommandHandler("setkind", setkind_cmd))
    app.add_handler(CommandHandler("setdesc", setdesc_cmd))
    app.add_handler(CommandHandler("setterms", setterms_cmd))
    app.add_handler(CommandHandler("setbulk", setbulk_cmd))
    app.add_handler(CommandHandler("setunlimited", setunlimited_cmd))
    app.add_handler(CommandHandler("addreview", addreview_cmd))
    app.add_handler(CommandHandler("topupcode", topupcode_cmd))
    app.add_handler(CommandHandler("reply", reply_cmd))
    app.add_handler(CommandHandler("setprice", setprice_cmd))
    app.add_handler(CommandHandler("disableproduct", disableproduct_cmd))
    app.add_handler(CommandHandler("enableproduct", enableproduct_cmd))
    app.add_handler(CommandHandler("clearstock", clearstock_cmd))
    app.add_handler(add_prod)
    app.add_handler(add_stock)
    app.add_handler(CallbackQueryHandler(on_callback))
    # proof / text messages: text TID/hash + photo screenshot + support/coupon/search/review/topup/deposit
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, on_message))

    print("Bot chal raha hai... /start se test karo.", flush=True)
    # Keep the process alive: if polling ever raises, log it and restart
    # polling instead of letting the whole process exit (Botkeep does not
    # auto-restart a stopped server). BUT honour shutdown signals — when
    # Botkeep asks the server to stop (SIGTERM), exit cleanly instead of
    # restarting forever, otherwise the service gets stuck in "Stopping"
    # (happened 2026-11-11: the restart loop blocked a graceful stop and
    # the redeploy failed). Also use a fresh event loop per attempt: after
    # a crash PTB's loop is closed, and reusing it raises
    # "Event loop is closed" inside run_polling.
    import asyncio as _asyncio
    import signal as _signal
    import time as _time
    _shutdown = {"stop": False}

    def _ask_stop(signum, frame):
        _shutdown["stop"] = True
        log.warning("Shutdown signal %s received; stopping polling loop", signum)

    _signal.signal(_signal.SIGTERM, _ask_stop)
    _signal.signal(_signal.SIGINT, _ask_stop)
    while not _shutdown["stop"]:
        try:
            _loop = _asyncio.new_event_loop()
            _asyncio.set_event_loop(_loop)
            PTB_LOOP = _loop  # IPN thread isi loop pe deliver_order chalata hai
            app.run_polling()
            log.warning("run_polling returned cleanly; restarting polling")
        except (KeyboardInterrupt, SystemExit):
            break
        except Exception:
            log.exception("run_polling crashed; restarting in 5s")
        if _shutdown["stop"]:
            break
        _time.sleep(5)
    log.warning("Polling loop stopped cleanly; exiting")

if __name__ == "__main__":
    main()
