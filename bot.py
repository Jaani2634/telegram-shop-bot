"""
Telegram Shop Bot - Digital Accounts Seller
Junaid ke liye - Manual (JazzCash/Easypaisa) + USDT (TRC20) dono

Flow:
 Buyer: /start -> channel gate -> Products -> product detail
   (quantity + / - + terms + I Agree, sab ek screen pe) -> order summary
   -> payment (Zurvapay 1st / Easypaisa 2nd / JazzCash 3rd / USDT 4th /
   wallet) -> proof -> admin approve / USDT auto-verify -> auto delivery
 Language: per-user Roman Urdu (default) ya English — main menu se
   "🌐 Language / زبان" se badlo. Products products.csv se startup pe
   auto-sync hote hain (prices + naye products).
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
import csv
import json
import random
import re
import secrets
import time
import sqlite3
import smtplib
import logging
import requests
import threading
import asyncio
import hashlib
import hmac
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
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
# bot_id — LAZMI (2026-11-12 live test se sabit): Zurvapay approval sirf
# registered Telegram bot ke liye valid hai; initiate call me bot_id bhejna
# zaroori hai warna "domain_not_allowed" error aata hai. Default BOT_USERNAME.
ZURVAPAY_BOT_ID = os.getenv("ZURVAPAY_BOT_ID", BOT_USERNAME)

# ---------- Web checkout (site -> Cloudflare Worker -> bot server) + AHM7 reseller ----------
# Site se direct purchase: Worker POST /api/checkout bhejta hai, bot server
# POST /api/web-checkout pe order banata hai + Zurvapay initiate karta hai.
# Shared secret — kabhi code me nahi, sirf env se (Botkeep .env me set karo).
WEB_API_SECRET = os.getenv("WEB_API_SECRET", "")
# AHM7 reseller API — payment confirm hone ke baad bot server yahan se auto-buy
# karta hai aur credentials buyer ko Telegram DM me bhejta hai. Key sirf env se.
# Wallet me balance hona lazmi hai, warna buy 402 dega aur order paid-pending
# reh jayega (kabhi lose nahi hota — retry / manual handling se deliver hoga).
AHM7_API_KEY = os.getenv("AHM7_API_KEY", "")
AHM7_BASE_URL = os.getenv("AHM7_BASE_URL", "https://api.ahm7.tech/v1").rstrip("/")

# ---------- Web admin dashboard + OTP (admin login) ----------
# Admin kaun hai: email ya phone env se match kare (Junaid). OTP teen
# channels se ja sakta hai — Telegram (bot khud ADMIN_ID ko bhejta hai),
# Twilio SMS (phone) + Resend email. Har channel best-effort aur alag hai:
# Twilio sirf tab jab uski keys set hon, Resend sirf tab jab uski key set
# ho, Gmail SMTP sirf tab jab SMTP_USER + SMTP_PASS set hon, Telegram
# hamesha (admin ke liye). Koi bhi channel available na ho to OTP send
# 503 deta hai (otp_not_configured). Keys kabhi code me nahi —
# sirf env se (Botkeep .env me set karo).
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "raisjunaidnazeer@gmail.com").strip().lower()
ADMIN_PHONE = os.getenv("ADMIN_PHONE", "+923706462990").strip()
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM = os.getenv("RESEND_FROM", "Pheonix Retail Shop <onboarding@resend.dev>")
# Gmail SMTP fallback — Resend free tier sirf owner email ko bhej sakta
# hai (403 domain restriction), is liye SMTP_USER ek Gmail address aur
# SMTP_PASS uska app password hai (16 chars; spaces hon to hata diye
# jate hain). Dono Botkeep env se aate hain, value kabhi log nahi hoti.
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASS = os.getenv("SMTP_PASS", "")

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
    # Do processes (Telegram bot + WhatsApp bot) ek hi DB likhte hain —
    # WAL + busy_timeout se "database is locked" nahi aata.
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=10000")
    except Exception:
        pass
    return c

def _add_col(c, table, col, ddl):
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")

def _sync_products_from_csv(c):
    """products.csv (bot.py ke saath wali directory) se products upsert karo:
    naam se match karke price_pkr/price_usdt/description UPDATE, naye INSERT.
    Stock aur active flags ko haath nahi lagata. CSV na mile to skip."""
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "products.csv")
    if not os.path.isfile(csv_path):
        return
    added = updated = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = (row.get("name") or "").strip()
            if not name:
                continue
            try:
                pkr = int(float((row.get("price_pkr") or "0").strip()))
                usdt = float((row.get("price_usdt") or "0").strip())
            except ValueError:
                continue
            desc = (row.get("description") or "").strip()
            cur = c.execute("SELECT id, price_pkr, price_usdt, description FROM products WHERE name=?", (name,)).fetchone()
            if cur:
                if cur["price_pkr"] != pkr or cur["price_usdt"] != usdt or (cur["description"] or "") != desc:
                    c.execute("UPDATE products SET description=?, price_pkr=?, price_usdt=? WHERE id=?",
                              (desc, pkr, usdt, cur["id"]))
                    updated += 1
            else:
                c.execute("INSERT INTO products(name,description,price_pkr,price_usdt) VALUES(?,?,?,?)",
                          (name, desc, pkr, usdt))
                added += 1
    if added or updated:
        log.info("products.csv sync: %d added, %d updated", added, updated)

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
    CREATE TABLE IF NOT EXISTS reviews(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        rating INTEGER NOT NULL,
        text TEXT NOT NULL,
        created_at INTEGER NOT NULL
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
    CREATE TABLE IF NOT EXISTS outbox(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel TEXT NOT NULL,
        recipient TEXT NOT NULL,
        text TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at TEXT,
        sent_at TEXT
    );
    CREATE TABLE IF NOT EXISTS support_msgs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        username TEXT,
        text TEXT,
        direction TEXT DEFAULT 'user',
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS user_prefs(
        user_id INTEGER PRIMARY KEY,
        lang TEXT DEFAULT 'ur'
    );
    CREATE TABLE IF NOT EXISTS web_users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS web_sessions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token_hash TEXT UNIQUE NOT NULL,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS admin_otps(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_user_id INTEGER NOT NULL,
        purpose TEXT NOT NULL,
        code_hash TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        attempts INTEGER DEFAULT 0,
        used INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS user_email_otps(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        code_hash TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        attempts INTEGER DEFAULT 0,
        used INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS site_visits(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        page TEXT,
        ts INTEGER
    );
    CREATE TABLE IF NOT EXISTS issues(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_email TEXT,
        user_name TEXT,
        kind TEXT,
        detail TEXT,
        ts INTEGER,
        status TEXT DEFAULT 'open'
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
                     ("coupon_code", "TEXT DEFAULT ''"), ("amount_pkr", "INTEGER DEFAULT 0"),
                     ("channel", "TEXT DEFAULT 'telegram'"),
                     ("ahm7_order_id", "TEXT DEFAULT ''"),
                     ("ahm7_credentials", "TEXT DEFAULT ''"),
                     ("ahm7_note", "TEXT DEFAULT ''"),
                     ("delivery_token", "TEXT DEFAULT ''"),
                     ("web_contact", "TEXT DEFAULT ''"),
                     ("success_base", "TEXT DEFAULT ''"),
                     ("checkout_url", "TEXT DEFAULT ''")):
        _add_col(c, "orders", col, ddl)
    # Reviews: purani DB me reviews table purane schema (user_name, verified,
    # source) se bani hoti hai — web reviews ko `name` + `created_at` chahiye.
    # Bina migration ke INSERT/SELECT "no such column" se fail hota hai
    # (live DB pe yahi hoga jab naya code pehli dafa chalega).
    _add_col(c, "reviews", "name", "TEXT NOT NULL DEFAULT ''")
    _add_col(c, "reviews", "created_at", "INTEGER NOT NULL DEFAULT 0")
    # Web accounts: phone (country code ke saath) + admin flag; sessions:
    # admin session ka flag (admin endpoints sirf admin sessions se khulte hain).
    _add_col(c, "web_users", "phone", "TEXT DEFAULT ''")
    _add_col(c, "web_users", "is_admin", "INTEGER DEFAULT 0")
    _add_col(c, "web_users", "email_verified", "INTEGER DEFAULT 0")
    _add_col(c, "web_sessions", "is_admin", "INTEGER DEFAULT 0")
    try:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_web_users_phone "
                  "ON web_users(phone) WHERE phone <> ''")
    except Exception:
        pass
    # Purani Telegram reviews (user_name) ka naam naye `name` column me bhar do.
    try:
        c.execute("UPDATE reviews SET name = user_name WHERE (name IS NULL OR name = '') "
                  "AND user_name IS NOT NULL AND user_name <> ''")
    except Exception:
        pass
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
    # Product guides bharo (sirf jahan description/terms khaali hain —
    # admin ki /setdesc ya /setterms wali change overwrite nahi hogi).
    for row in c.execute("SELECT id, name, description, terms FROM products").fetchall():
        g = PRODUCT_GUIDES.get(row["name"])
        if not g:
            continue
        short_desc, terms = g
        if (not (row["description"] or "").strip()) and short_desc:
            c.execute("UPDATE products SET description=? WHERE id=?", (short_desc, row["id"]))
        if (not (row["terms"] or "").strip()) and terms:
            c.execute("UPDATE products SET terms=? WHERE id=?", (terms, row["id"]))
    # products.csv se auto-sync (16 Nov 2026): har restart pe CSV ki prices
    # aur naye products DB me aa jate hain — stock/active flags ko haath
    # nahi lagata. CSV na mile to khamoshi se skip (crash nahi hota).
    try:
        _sync_products_from_csv(c)
    except Exception:
        log.exception("products.csv sync failed (skipping)")
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

# Har product ki delivery instructions (How to Use + warranty) — SubPk.Store
# WhatsApp bot ke style me: buyer ko account ke sath ye instructions milti
# hain (delivery message me, aur checkout par Terms me bhi).
# 4 products ki text seller ki apni hai (video se li gayi); baaki 22 usi
# style me likhi gayi hain — Junaid /setterms se kisi bhi product ki badal
# sakta hai. (name -> (short description, terms))
PRODUCT_GUIDES = {
    "Amazon Prime 6 Months": (
        "Limited Deal - was PKR 850",
        "How to Use: You will receive Mail | Password. Login on primevideo.com or the Amazon app with the same mail and password. Use only the given profile — do not change the mail or password, or the account may stop working. Check it right after login — first login warranty only. If there is any issue, contact Support immediately.",
    ),
    "Udemy Personal Plan 1M": (
        "Limited Deal - was PKR 669",
        "28,000+ Top-rated Courses, Unlimited Access. Practice Activities and Tests Included. Certificate In Your Name. Download Every Certificate as a PDF Before Your Plan Expires. Check Certificate: https://www.udemy.com/certificate/UC-YOUR-CERTIFICATE-NUMBER/ — Update Name and Account Details With Your Own Information. 2 Days Warranty. Warranty will be void if you change the mail.",
    ),
    "Capcut Pro 1 Month": (
        "Premium Service",
        "CapCut Pro Team – 1 Month private account. Features: Ready-made private account, Full Pro Team access, Instant delivery after payment. Warranty Rules: Warranty only covers account going back non-Pro. Warranty not accepted if account hits max-attempts issues. How to Use: You will receive: Mail | Capcut Password | Mail Password. Copy exactly and login at CapCut. Login on max 2 devices (1 mobile + 1 PC). Do not logout and login repeatedly.",
    ),
    "HMA Vpn Monthly": (
        "Premium Service",
        "How to Use: You will receive Mail | Password. Download the HMA VPN app and login with the same mail and password. Use on 1 device only. Do not change the password. Check it right after login — first login warranty only. If there is any issue, contact Support.",
    ),
    "Microsoft 365 Plus 1 Year": (
        "Premium Service",
        "How to Use: You will receive Mail | Password. Login on office.com with the same mail and activate Office apps on your device. Do not change the mail or password. Check it right after login — first login warranty only. If there is any issue, contact Support.",
    ),
    "Muse Ai 1B Credits": (
        "Premium Service",
        "How to Use: You will receive Mail and mailbox URL. Put the mail on muse.ai, then get the OTP from the mailbox site and paste it on muse.ai to complete login. Use on only 1 device, and every time you open muse.ai use it with USA IP. Credits will be 1 Billion. First Login Warranty Only (login within 30 mins of purchase). Accounts are NON WARRANTY — take the risk if you buy it!",
    ),
    "Windows 10/11 Pro": (
        "Premium Service",
        "How to Use: You will receive a Windows activation key. Go to Windows Settings, then Activation, enter the key and activate. The key works on 1 PC. Activate it as soon as you receive it. If there is any issue, contact Support.",
    ),
    "Muse Ai 31B Token (NW)": (
        "Premium Service",
        "Muse AI – 31 Billion Tokens Account. First Login Warranty Only (Login Within 30 Mins Of Purchase). How to Use: You will receive Mail and mailbox url. Put mail on muse.ai and then get otp from mailbox url. Note: Credits Can Be 30 or 31 Billion. Note: All accounts will be used different domains. All accounts will be add a USA phone numbers! Anyway, the accounts are NON WARRANTY, take the risk if you buy it! To avoid any issues use it on only 1 Device. And every time you open muse ai use it with usa ip.",
    ),
    "Capcut Pro 7D 500 credits": (
        "CapCut Pro — 7 days, 500 credits",
        "How to Use: You will receive Mail | Password. Login on the CapCut app with the same mail and password — you get 500 credits for 7 days. Use on 1 device only, do not change the password. Check it right after login. If there is any issue, contact Support.",
    ),
    "500$ API CLAUDE 30D (FW)": (
        "Claude API — $500 credits, 30 days",
        "How to Use: You will receive Mail | Password (or API key/link). Login on the Claude console and use the $500 API credits — valid for 30 days. Use on 1 device/IP only, do not share the credentials. Check it right after login — first login warranty only. If there is any issue, contact Support.",
    ),
    "100$ API Claude 30D (FW)": (
        "Claude API — $100 credits, 30 days",
        "How to Use: You will receive Mail | Password (or API key/link). Login on the Claude console and use the $100 API credits — valid for 30 days. Use on 1 device/IP only, do not share the credentials. Check it right after login — first login warranty only. If there is any issue, contact Support.",
    ),
    "Adobe Express 12M": (
        "Adobe Express Premium — 12 months",
        "How to Use: You will receive Mail | Password. Login on adobe.com with the same mail and use Express Premium — valid for 12 months. Do not change the password. Use on 1 device. Check it right after login. If there is any issue, contact Support.",
    ),
    "Gemini Plus 6m Links (No Card Required)": (
        "Gemini Plus — 6 months, redeem link, no card needed",
        "GEMINI PLUS – 6 MONTHS. Simple Redeem-Link Activation. No Credit Card Required. Add Up to 5 Accounts. Gemini Pro and Flash Models. Gemini Omni 1.1 Flash. Anti Gravity Access. Nano Banana 2. 400 GB Google Storage. 250 Google Flow Points. 12-Hour Link Warranty. Instant Delivery. No Warranty After Activation — Only Activation Warranty. Note: We sell official Gemini Plus Plan. You activate it by yourself on your own mail. After activation, plus features are all from Google, not from our side. If you think you haven't got any feature then contact Google. Anti-gravity and Flow ai etc. — everything is from Google.",
    ),
    "MUSE AI 1B TOKEN (W2D)": (
        "Muse AI — 1 Billion tokens account",
        "How to Use: You will receive Mail and mailbox URL. Put the mail on muse.ai, then get the OTP from the mailbox site and paste it on muse.ai to complete login. Use on only 1 device, and every time you open muse.ai use it with USA IP. Credits will be 1 Billion (W2D). First Login Warranty Only (login within 30 mins of purchase). Accounts are NON WARRANTY.",
    ),
    "Outlook IMAP": (
        "Outlook mail account (IMAP)",
        "How to Use: You will receive Mail | Password. Login with IMAP in Outlook or any mail app. This is a mail account only. Check it right after login. If there is any issue, contact Support immediately.",
    ),
    "Hotmail Mail Accounts": (
        "Hotmail mail account",
        "How to Use: You will receive Mail | Password. Login on outlook.com / Hotmail with the same mail and password. This is a mail account only. Check it right after login. If there is any issue, contact Support immediately.",
    ),
    "Owl Proxy 200 Mb": (
        "Owl Proxy — 200 MB data",
        "How to Use: You will receive proxy details (host, port, username, password). Put them in your app or browser proxy settings to use it. You get 200 MB data. Do not share the details. If there is any issue, contact Support.",
    ),
    "iLovePdf Premium 1 Yr": (
        "iLovePDF Premium — 1 year",
        "How to Use: You will receive Mail | Password. Login on ilovepdf.com with the same mail and use Premium tools — valid for 1 year. Do not change the password. Check it right after login. If there is any issue, contact Support.",
    ),
    "Chatgpt Plus 1M (25DW)": (
        "ChatGPT Plus — 1 month",
        "How to Use: You will receive Mail | Password. Login on chatgpt.com with the same mail and use Plus features — valid for 1 month. Use on 1 device only, do not change the password. Check it right after login. If there is any issue, contact Support.",
    ),
    "Coursera Plus 7 Days Trial": (
        "Coursera Plus — 7 days trial",
        "How to Use: You will receive Mail | Password. Login on coursera.org with the same mail — you get a 7-day trial. Finish your work before the trial ends. Do not change the password. Check it right after login. If there is any issue, contact Support.",
    ),
    "Bolt .new Pro 1 Month": (
        "Bolt.new Pro — 1 month",
        "How to Use: You will receive Mail | Password. Login on bolt.new with the same mail and use Pro features — valid for 1 month. Use on 1 device, do not change the password. Check it right after login. If there is any issue, contact Support.",
    ),
    "3 in 1 VIP Group": (
        "3 in 1 VIP group access",
        "How to Use: After payment you will receive a VIP group invite link. Join the group with the link. Read and follow the group rules. Do not share the link. If there is any issue, contact Support.",
    ),
    "Canva Edu 3 Years Invite On Client Mail": (
        "Invite on client mail",
        "How to Use: Give your mail ID — the Canva Edu invite (3 years) will be sent to your mail. Accept the invite and use Canva Edu. Accept the invite as soon as you receive it. If there is any issue, contact Support.",
    ),
    "CrunchyRoll 1 Year Mega Fan Profile (FW)": (
        "Crunchyroll Mega Fan — 1 year",
        "How to Use: You will receive Mail | Password. Login on crunchyroll.com with the same mail and use the Mega Fan profile — valid for 1 year. Use only the given profile — do not change the mail or password. Check it right after login. If there is any issue, contact Support.",
    ),
    "Hostinger Unlimited Plan 12 Months (FW)": (
        "Hostinger Unlimited — 12 months",
        "How to Use: You will receive Mail | Password. Login on hostinger.com with the same mail — you get the Unlimited plan for 12 months. Manage your website/hosting on the same account. Do not change the password. Check it right after login. If there is any issue, contact Support.",
    ),
    "Muse Ai Token On Client Account": (
        "Muse AI token — added to your own account",
        "How to Use: Give your own Muse account details — the token will be added to your own account. After that, login on muse.ai with your own account and check it. Use on only 1 device, and every time you open muse.ai use it with USA IP. Check it right after login. If there is any issue, contact Support.",
    ),
}

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
            if self.path.startswith("/api/reviews/"):
                # Public read — product reviews + real sold count.
                try:
                    parsed = urlparse(self.path)
                    pid = parsed.path[len("/api/reviews/"):].strip("/")
                    status, out = handle_get_reviews(pid)
                except Exception as e:
                    log.exception("Reviews handler crashed")
                    status, out = 500, {"ok": False, "error": f"handler error: {e}"}
                body = json.dumps(out, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/api/auth/me") or self.path.startswith("/api/auth/orders"):
                # Web account: apni profile / apne orders (Bearer session token).
                try:
                    parsed = urlparse(self.path)
                    if parsed.path.startswith("/api/auth/me"):
                        status, out = handle_auth_me(self.headers)
                    else:
                        status, out = handle_auth_orders(self.headers)
                except Exception as e:
                    log.exception("Auth handler crashed")
                    status, out = 500, {"ok": False, "error": f"handler error: {e}"}
                body = json.dumps(out, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/api/admin/"):
                # Admin dashboard (Bearer admin token — admin session).
                try:
                    parsed = urlparse(self.path)
                    qs = parse_qs(parsed.query)
                    p = parsed.path
                    if p == "/api/admin/stats":
                        status, out = handle_admin_stats(self.headers)
                    elif p == "/api/admin/visits":
                        status, out = handle_admin_visits(self.headers)
                    elif p == "/api/admin/issues":
                        status, out = handle_admin_issues(self.headers, qs)
                    elif p == "/api/admin/orders":
                        status, out = handle_admin_orders(self.headers, qs)
                    elif p.startswith("/api/admin/order/"):
                        status, out = handle_admin_order_detail(
                            self.headers, p[len("/api/admin/order/"):].strip("/"))
                    elif p == "/api/admin/users":
                        status, out = handle_admin_users(self.headers, qs)
                    else:
                        status, out = 404, {"ok": False, "error": "not found"}
                except Exception as e:
                    log.exception("Admin handler crashed")
                    status, out = 500, {"ok": False, "error": f"handler error: {e}"}
                body = json.dumps(out, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/api/order/"):
                # Website order status (Worker se, X-Web-Secret + delivery token).
                try:
                    parsed = urlparse(self.path)
                    code = parsed.path[len("/api/order/"):].strip("/")
                    qs = parse_qs(parsed.query)
                    token = (qs.get("token") or [""])[0]
                    ip = self.client_address[0] if self.client_address else ""
                    status, out = handle_order_status(code, token, self.headers, ip)
                except Exception as e:
                    log.exception("Order status handler crashed")
                    status, out = 500, {"ok": False, "error": f"handler error: {e}"}
                body = json.dumps(out, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/zurvapay/success") or self.path.startswith("/zurvapay/cancel"):
                # Zurvapay ke baad customer yahan aata hai (approved domain) —
                # site ke order page pe 302 redirect (code + token DB se).
                try:
                    parsed = urlparse(self.path)
                    qs = parse_qs(parsed.query)
                    code = (qs.get("code") or [""])[0]
                    kind = "success" if parsed.path.startswith("/zurvapay/success") else "cancel"
                    target = zurvapay_redirect_target(kind, code)
                    log.info("Zurvapay %s: code=%s, redirecting to order page", kind, code)
                except Exception:
                    log.exception("Zurvapay redirect handler crashed")
                    target = "https://retailshop.pages.dev/"
                self.send_response(302)
                self.send_header("Location", target)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
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
            if self.path.startswith("/api/web-checkout") or self.path.startswith("/api/ahm7-retry") or self.path.startswith("/api/review") or self.path.startswith("/api/auth/signup") or self.path.startswith("/api/auth/login") or self.path.startswith("/api/auth/logout") or self.path.startswith("/api/auth/delete") or self.path.startswith("/api/auth/verify-email/send") or self.path.startswith("/api/auth/verify-email/confirm") or self.path.startswith("/api/admin/otp/send") or self.path.startswith("/api/admin/otp/verify") or self.path.startswith("/api/track/visit") or self.path.startswith("/api/admin/issues/"):
                # Site checkout / review (Cloudflare Worker se) — JSON in, JSON out.
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                raw = self.rfile.read(length) if length > 0 else b""
                try:
                    data = json.loads(raw.decode("utf-8") or "{}")
                    if not isinstance(data, dict):
                        data = {}
                except Exception:
                    data = {}
                try:
                    if self.path.startswith("/api/web-checkout"):
                        status, out = handle_web_checkout(data, self.headers)
                    elif self.path.startswith("/api/review/delete"):
                        status, out = handle_delete_review(data, self.headers)
                    elif self.path.startswith("/api/review"):
                        # Worker CF-Connecting-IP forward karta hai (X-Forwarded-For);
                        # warna saari site reviews ko bot ek hi IP samajhta hai aur
                        # 5/hour rate limit sab pe lag jata hai. Header sirf
                        # X-Web-Secret wali requests pe mana jata hai.
                        ip = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
                        if not ip:
                            ip = self.client_address[0] if self.client_address else ""
                        status, out = handle_submit_review(data, self.headers, ip)
                    elif self.path.startswith("/api/auth/signup"):
                        ip = self.client_address[0] if self.client_address else ""
                        status, out = handle_auth_signup(data, self.headers, ip)
                    elif self.path.startswith("/api/auth/login"):
                        ip = self.client_address[0] if self.client_address else ""
                        status, out = handle_auth_login(data, self.headers, ip)
                    elif self.path.startswith("/api/auth/logout"):
                        status, out = handle_auth_logout(self.headers)
                    elif self.path.startswith("/api/auth/delete"):
                        status, out = handle_auth_delete(self.headers)
                    elif self.path.startswith("/api/auth/verify-email/send"):
                        status, out = handle_auth_verify_email_send(self.headers)
                    elif self.path.startswith("/api/auth/verify-email/confirm"):
                        status, out = handle_auth_verify_email_confirm(data, self.headers)
                    elif self.path.startswith("/api/admin/otp/send"):
                        ip = self.client_address[0] if self.client_address else ""
                        status, out = handle_admin_otp_send(data, self.headers, ip)
                    elif self.path.startswith("/api/admin/otp/verify"):
                        status, out = handle_admin_otp_verify(data, self.headers)
                    elif self.path.startswith("/api/track/visit"):
                        ip = self.client_address[0] if self.client_address else ""
                        status, out = handle_track_visit(data, self.headers, ip)
                    elif self.path.startswith("/api/admin/issues/"):
                        # POST /api/admin/issues/:id/resolve
                        _pp = urlparse(self.path).path.strip("/").split("/")
                        _iid = _pp[3] if len(_pp) >= 5 and _pp[4] == "resolve" else ""
                        status, out = handle_admin_issue_resolve(self.headers, _iid)
                    else:
                        status, out = handle_ahm7_retry(data, self.headers)
                except Exception as e:
                    log.exception("Web API handler crashed")
                    status, out = 500, {"ok": False, "error": f"handler error: {e}"}
                body = json.dumps(out, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
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

# ---------- Language (Roman Urdu default, English optional) ----------

def get_lang(uid):
    """User ki language: 'ur' (Roman Urdu, default) ya 'en' (English)."""
    try:
        c = db(); r = c.execute("SELECT lang FROM user_prefs WHERE user_id=?", (uid,)).fetchone(); c.close()
        return r["lang"] if r and r["lang"] in ("ur", "en") else "ur"
    except Exception:
        return "ur"

def set_lang(uid, lang):
    if lang not in ("ur", "en"):
        lang = "ur"
    c = db()
    c.execute("INSERT INTO user_prefs(user_id,lang) VALUES(?,?) "
              "ON CONFLICT(user_id) DO UPDATE SET lang=excluded.lang", (uid, lang))
    c.commit(); c.close()

def t(uid, key, **kw):
    """Customer-facing string user ki language me. Roman Urdu ('ur') text
    bilkul waisa hi hai jaisa pehle tha; 'en' uska English tarjuma hai."""
    lang = get_lang(uid) if isinstance(uid, int) else "ur"
    s = STRINGS.get(lang, {}).get(key)
    if s is None:
        s = STRINGS["ur"].get(key, key)
    if kw:
        try:
            return s.format(**kw)
        except Exception:
            return s
    return s

# Customer-facing strings: 'ur' = Roman Urdu (pehle wala text, bilkul
# waisa hi), 'en' = English. Admin commands/messages Roman Urdu me hi
# rehte hain (admin = owner). Product ke terms/descriptions data hain,
# is liye wo translate nahi hote.
STRINGS = {
"ur": {
    # Main menu
    "menu_welcome": "👋 Welcome to RetailShop!",
    "menu_currency": "💱 Currency: {cur}",
    "menu_coupon": "🎁 First Order Coupon: {code} — {pct}% off your first order! (Checkout pe apply karein)",
    "menu_choose": "Neeche se choose karein:",
    "menu_lang_btn": "🌐 Language / زبان",
    # Language menu
    "lang_title": "🌐 Language / زبان\n\nApni language choose karein:",
    "lang_set": "✅ Language {name} set ho gayi.",
    # Products list
    "prod_choose": "🛍️ Products — koi ek choose karein:  (Page {page}/{total})",
    "prod_prev": "⬅ Prev",
    "prod_next": "Next ➡",
    "prod_cart": "🛒 View Cart",
    "prod_refresh": "🔄 Refresh Stock",
    "prod_search": "🔍 Search Products",
    "prod_request": "📝 Request Products",
    "prod_back_menu": "⬅ Main Menu",
    "prod_stock": "Stock: {n}",
    "prod_out_stock": "Out of Stock",
    # Product detail
    "prod_not_found": "Product nahi mila.",
    "prod_no_reviews": "⭐ No reviews yet",
    "prod_reviews": "⭐ {avg} ({n} reviews)",
    "prod_get_free": "🎁 Get Free",
    "prod_price": "💰 Price: {price}",
    "prod_bulk": "💸 Bulk discounts: {tiers}",
    "prod_qty_line": "{qty} × {price}",
    "prod_custom_qty": "🔢 Custom Quantity",
    "prod_agree": "✅ I Agree — Continue to Payment",
    "prod_add_cart": "🛒 Add to Cart",
    "prod_reviews_btn": "⭐ Reviews",
    "prod_back_products": "⬅ Back to Products",
    "prod_oos_msg": "❌ Is product ka stock khatam hai. Dusra product choose karein.",
    "prod_oos_alert": "Out of stock hai.",
    "prod_oos_note": "🔴 Out of Stock — ye product abhi available nahi hai. Stock aate hi wapas milega.",
    "prod_qty_info": "Quantity: {qty} — Total: {total}",
    # Quantity
    "qty_prompt": "🔢 Quantity kitni chahiye? Number bhejein (1–99):",
    "qty_only_stock": "Sirf {n} stock hai.",
    "qty_number_only": "Sirf number bhejein (1–99):",
    "qty_range": "Quantity 1 se 99 ke beech honi chahiye.",
    # Terms
    "terms_title": "📜 Terms & Conditions",
    # Summary
    "sum_line": "• {name} × {qty} — {line} ({unit} each)",
    "sum_subtotal": "Subtotal: {amount}",
    "sum_coupon": "Coupon {code} (-{pct}%): -{amount}",
    "sum_total": "Total: {amount}",
    "sum_wallet": "👛 Wallet balance: Rs {pkr} | ${usdt} USDT",
    "sum_coupon_btn": "🎟️ Apply Coupon Code",
    "sum_pay_zurva": "💳 Pay with Zurvapay",
    "sum_pay_easypaisa": "📱 Easypaisa",
    "sum_pay_jazzcash": "📱 JazzCash",
    "sum_pay_usdt": "₮ USDT (TRC20)",
    "sum_pay_wallet": "👛 Pay from Wallet",
    "sum_change_qty": "🔢 Change Quantity",
    "sum_cancel": "❌ Cancel",
    "sum_expired": "Session expire ho gayi. /start se dobara karein.",
    # Payment
    "pay_zurva_only_pkr": "⚡ Zurvapay sirf PKR deposits ke liye hai. USDT ke liye USDT option use karein.",
    "pay_zurva_fail_dep": "⚠️ Zurvapay payment start nahi ho saki: {err}\nDeposit #{id} pending hai — dobara try karein ya admin se rabta karein.",
    "pay_zurva_fail_order": "⚠️ Zurvapay payment start nahi ho saki: {err}\nOrder #{oid} ({code}) pending hai — dobara try karein ya admin se rabta karein.",
    "pay_zurva_paynow": "💳 Pay Now — JazzCash / Easypaisa / Raast QR",
    "pay_zurva_dep": "⚡ Zurvapay Deposit\n\nAmount: Rs {amount}\nDeposit #{id}\n\nNeeche button se payment karein — JazzCash, Easypaisa ya Raast QR. Payment confirm hote hi wallet auto credit ho jayega.",
    "pay_manual_dep": "💚 {label} Deposit\n\nAmount: {amount} {currency}\n\nSend to:\nNumber: `{num}`\nName: {name}\n\nPayment karke **Transaction ID (TID)** yahan bhejein, ya screenshot bhejein.\nAdmin approve karte hi wallet credit ho jayega.",
    "pay_usdt_dep": "🪙 USDT (TRC20) Deposit\n\nAmount: **${amount} USDT**\n\nSend to (TRC20 only):\n`{addr}`\n\nPayment karke **Transaction Hash** yahan bhejein. Bot khud verify karega.",
    "pay_zurva_order": "⚡ Zurvapay Payment\n\nProduct: {label}\nAmount: Rs {total}\nOrder #{oid} ({code})\n\nNeeche button se payment karein — JazzCash, Easypaisa ya Raast QR. Payment confirm hote hi account isi chat me auto deliver ho jayega.",
    "pay_manual_order": "💚 {label} Payment\n\nProduct: {label2}\nAmount: Rs {total}\n\nSend to:\nNumber: `{num}`\nName: {name}\n\nPayment karke **Transaction ID (TID)** yahan bhejein, ya screenshot bhejein.\nAdmin approve karte hi account auto deliver ho jayega.",
    "pay_usdt_order": "🪙 USDT (TRC20) Payment\n\nProduct: {label}\nAmount: **${amount} USDT**\n\nSend to (TRC20 only):\n`{addr}`\n\nPayment karke **Transaction Hash** yahan bhejein. Bot khud verify karega, phir account auto deliver ho jayega.",
    "pay_pkr_title": "💳 Pay via PKR — Total: Rs {total}\n\nMethod choose karein:",
    # Order status
    "status_pending": "⏳ PAID — admin review",
    "status_delivered": "✅ DELIVERED",
    "status_rejected": "❌ CANCELLED",
    "status_oos": "❌ CANCELLED (stock khatam)",
    # My Orders
    "orders_none": "📦 Aapki koi order nahi hai.",
    "orders_title": "📦 My Orders — detail ke liye order pe tap karein:",
    "order_not_found": "Order nahi mili.",
    "order_buy_again": "🔁 Buy Again",
    "order_write_review": "⭐ Write Review",
    "order_back": "⬅ My Orders",
    # Wallet
    "wallet_deposit": "💰 Deposit",
    "wallet_topup": "🎟️ Redeem Top-up Code",
    "wallet_stmt": "📄 Wallet Statement",
    "wallet_stmt_none": "Koi entry nahi hai.",
    "wallet_stmt_dl": "⬇️ Download Statement (.txt)",
    "wallet_stmt_caption": "📄 Aapki wallet statement.",
    # Deposit
    "dep_title": "💰 Deposit\n\nCurrency choose karein (minimum amount 1):",
    "dep_pkr": "💚 Deposit in PKR",
    "dep_usdt": "🪙 Deposit in USDT",
    "dep_back_wallet": "⬅ Wallet",
    "dep_amount": "💰 Deposit ({cur})\n\nAmount bhejein — minimum 1 {cur}:",
    "dep_method_pkr": "💰 Deposit Rs {amount} — payment method choose karein:",
    "dep_method_usdt": "💰 Deposit ${amount} USDT — payment method choose karein:",
    "dep_back": "⬅ Back",
    # Help
    "help_text": ("❓ Help — Kaise khareedein?\n\n"
        "1️⃣ Products kholein, category ya search se product choose karein.\n"
        "2️⃣ Product page pe quantity choose karein (bulk discount ho to kam price milegi).\n"
        "3️⃣ Terms & Conditions parh ke I Agree dabayein.\n"
        "4️⃣ Order Summary check karein — coupon ho to Apply Coupon Code dabayein.\n"
        "5️⃣ Payment karein: Easypaisa / JazzCash (TID ya screenshot bhejein), USDT TRC20 (TX hash bhejein), ya Zurvapay se JazzCash/Easypaisa/Raast QR — auto verify, account khud aa jayega. Wallet balance ho to Pay from Wallet bhi kar sakte hain.\n"
        "6️⃣ Admin verify karte hi account isi chat me auto deliver ho jayega.\n\n"
        "💰 Wallet me Deposit karke bhi pay kar sakte hain.\n"
        "🎁 Pehli order pe FIRST-ORDER coupon se 7% off!"),
    # Support
    "support_title": "💬 Support\n\nKoi issue ho — payment, account kaam na kare, ya order na mile — to neeche se admin ko message bhejein.",
    "support_send": "✉️ Send Message to Admin",
    "support_tg": "💬 Message Admin (Telegram)",
    "support_prompt": "✉️ Apna message likhein — admin ko forward kar dunga.\n\n(Cancel ke liye /cancel likhein)",
    "support_sent": "✅ Aapka message admin ko bhej diya gaya hai. Jawab isi chat me aayega.",
    # Giveaways
    "giv_share": "Apna link share karein — jab aapka dost channel join karke pehli order complete karega, referral verified hoga.\n",
    "giv_mandatory": "\n⚠️ Mandatory: hamara Telegram channel @retail_shop_official aur WhatsApp channel join karna zaroori hai.",
    "giv_verify_btn": "✅ I Have Joined (Verify Membership)",
    # Channels / Join Us
    "channels_title": "📢 Hamare Channels\n\nTelegram channel join karna zaroori hai — bot khud check karta hai.\nWhatsApp channel pe naye products aur offers milte hain.",
    "joinus_title": "🤝 Join Us\n\nHamare channels join karein — naye products, offers aur giveaways sabse pehle yahin milte hain.",
    # Channel gate
    "gate_join_tg": "🔒 Pehle hamara Telegram channel join karein, phir products dikhenge.\n\nChannel join karein, phir neeche **Check Join Status** dabayein.",
    "gate_check_fail": "⚠️ Channel check nahi ho saka. Admin ne bot ko Telegram channel me admin nahi banaya hoga. Thodi der me dobara /start karein.",
    "gate_join_wa": "💬 Hamara WhatsApp channel bhi join kar lein — wahan naye products aur offers aate hain.\n\nJoin karke neeche **Maine Join Kar Liya** dabayein.",
    "gate_joined_tg": "✅ Telegram channel joined! Ab WhatsApp channel bhi join kar lein.",
    "gate_thanks": "✅ Shukriya! ",
    # Cart
    "cart_empty": "🛒 Your Cart khaali hai.",
    "cart_added": "🛒 Cart me add ho gaya!",
    "cart_empty_alert": "Cart khaali hai.",
    "cart_stock_alert": "{name} ka sirf {n} stock hai.",
    "cart_checkout": "✅ Checkout",
    "cart_clear": "🧹 Clear Cart",
    "cart_continue": "🛍️ Continue Shopping",
    # Reviews
    "rev_only_delivered": "Review sirf delivered order pe likhi ja sakti hai.",
    "rev_rating": "⭐ Rating: {rating}/5\n\nAb apni review likhein (text bhejein):",
    "rev_saved": "✅ Shukriya! Aapki review save ho gayi.",
    # Search / Request
    "search_prompt": "🔍 Product search karein — keyword bhejein (jaise 'muse' ya 'capcut'):",
    "search_none": "🔍 '{kw}' — koi product nahi mila.",
    "req_prompt": "📝 Konsa product chahiye? Naam + details bhejein — admin ko forward kar dunga:",
    "req_sent": "✅ Aapki request admin ko bhej di gayi hai. Shukriya!",
    # Coupon
    "coupon_prompt": "🎟️ Coupon code bhejein (jaise FIRST-ORDER):",
    "coupon_applied": "✅ Coupon {code} apply ho gaya! {pct}% off — summary dobara dikha raha hun...",
    "coupon_wrong": "Ye coupon code galat hai.",
    "coupon_not_yours": "Aapke paas ye coupon nahi hai.",
    "coupon_used": "Ye coupon use ho chuka hai.",
    "coupon_first_only": "Ye coupon sirf pehli order ke liye hai.",
    # Proof / buy
    "buy_free": "🎁 FREE product! Order #{oid} — account abhi bhej raha hun...",
    "buy_order_received": "✅ Order #{oid} ({code}) mil gayi! Admin verify kar raha hai. Approve hote hi account isi chat me aa jayega.",
    "buy_hash_short": "Ye hash lagta nahi. Poora Transaction Hash bhejein (TronScan link se copy karein).",
    "buy_usdt_verified": "✅ USDT verified! {reason}\nOrder #{oid} ({code}) — account abhi bhej raha hun...",
    "buy_usdt_fail": "⚠️ Auto-verify nahi hui: {reason}\nOrder #{oid} ({code}) admin ko bhej di hai — wo manually check karke deliver karega.",
    "buy_wallet_paid": "✅ Wallet se payment ho gayi! Order #{oid} — account abhi bhej raha hun...",
    "buy_wallet_refund": "⚠️ Delivery nahi ho saki (stock khatam) — wallet refund kar diya gaya hai.",
    "buy_wallet_low": "Wallet balance kam hai. Pehle Deposit karein.",
    # Deposit proof
    "dep_received": "✅ Deposit #{id} mil gayi! Admin verify kar raha hai. Approve hote hi wallet credit ho jayega.",
    "dep_usdt_verified": "✅ USDT verified! {reason}\nDeposit #{id} — {amount} {currency} aapke wallet me add ho gaye.",
    "dep_usdt_fail": "⚠️ Auto-verify nahi hui: {reason}\nDeposit #{id} admin ko bhej di hai — wo manually check karke credit karega.",
    # Top-up
    "topup_prompt": "🎟️ Top-up code bhejein:",
    "topup_wrong": "❌ Ye top-up code galat hai.",
    "topup_used": "❌ Ye code pehle hi use ho chuka hai.",
    "topup_ok": "✅ Top-up successful! {amount} {currency} aapke wallet me add ho gaye.",
    # Misc
    "misc_expired": "Session expire ho gayi. /start se dobara karein.",
    "misc_only_number": "Sirf number bhejein (jaise 500):",
    "misc_min_deposit": "Minimum deposit 1 hai. Dobara amount bhejein:",
    "misc_giv_verified": "✅ Verified! Aap giveaways ke liye eligible hain.",
    "misc_giv_check_fail": "Check nahi ho saka — thodi der me dobara try karein.",
    "misc_giv_join_first": "Pehle Telegram channel join karein, phir verify dabayein.",
    "misc_join_check": "Abhi join nahi hua — pehle channel join karein, phir dobara check karein.",
    "misc_join_check_fail": "Check nahi ho saka — bot channel me admin nahi hai.",
},
"en": {
    # Main menu
    "menu_welcome": "👋 Welcome to RetailShop!",
    "menu_currency": "💱 Currency: {cur}",
    "menu_coupon": "🎁 First Order Coupon: {code} — {pct}% off your first order! (Apply it at checkout)",
    "menu_choose": "Choose below:",
    "menu_lang_btn": "🌐 Language / زبان",
    # Language menu
    "lang_title": "🌐 Language\n\nChoose your language:",
    "lang_set": "✅ Language set to {name}.",
    # Products list
    "prod_choose": "🛍️ Products — choose one:  (Page {page}/{total})",
    "prod_prev": "⬅ Prev",
    "prod_next": "Next ➡",
    "prod_cart": "🛒 View Cart",
    "prod_refresh": "🔄 Refresh Stock",
    "prod_search": "🔍 Search Products",
    "prod_request": "📝 Request Products",
    "prod_back_menu": "⬅ Main Menu",
    "prod_stock": "Stock: {n}",
    "prod_out_stock": "Out of Stock",
    # Product detail
    "prod_not_found": "Product not found.",
    "prod_no_reviews": "⭐ No reviews yet",
    "prod_reviews": "⭐ {avg} ({n} reviews)",
    "prod_get_free": "🎁 Get Free",
    "prod_price": "💰 Price: {price}",
    "prod_bulk": "💸 Bulk discounts: {tiers}",
    "prod_qty_line": "{qty} × {price}",
    "prod_custom_qty": "🔢 Custom Quantity",
    "prod_agree": "✅ I Agree — Continue to Payment",
    "prod_add_cart": "🛒 Add to Cart",
    "prod_reviews_btn": "⭐ Reviews",
    "prod_back_products": "⬅ Back to Products",
    "prod_oos_msg": "❌ This product is out of stock. Please choose another product.",
    "prod_oos_alert": "This product is out of stock.",
    "prod_oos_note": "🔴 Out of Stock — this product is not available right now. It will be back once restocked.",
    "prod_qty_info": "Quantity: {qty} — Total: {total}",
    # Quantity
    "qty_prompt": "🔢 How many do you want? Send a number (1–99):",
    "qty_only_stock": "Only {n} in stock.",
    "qty_number_only": "Please send only a number (1–99):",
    "qty_range": "Quantity must be between 1 and 99.",
    # Terms
    "terms_title": "📜 Terms & Conditions",
    # Summary
    "sum_line": "• {name} × {qty} — {line} ({unit} each)",
    "sum_subtotal": "Subtotal: {amount}",
    "sum_coupon": "Coupon {code} (-{pct}%): -{amount}",
    "sum_total": "Total: {amount}",
    "sum_wallet": "👛 Wallet balance: Rs {pkr} | ${usdt} USDT",
    "sum_coupon_btn": "🎟️ Apply Coupon Code",
    "sum_pay_zurva": "💳 Pay with Zurvapay",
    "sum_pay_easypaisa": "📱 Easypaisa",
    "sum_pay_jazzcash": "📱 JazzCash",
    "sum_pay_usdt": "₮ USDT (TRC20)",
    "sum_pay_wallet": "👛 Pay from Wallet",
    "sum_change_qty": "🔢 Change Quantity",
    "sum_cancel": "❌ Cancel",
    "sum_expired": "Session expired. Please start again with /start.",
    # Payment
    "pay_zurva_only_pkr": "⚡ Zurvapay is only for PKR deposits. For USDT, please use the USDT option.",
    "pay_zurva_fail_dep": "⚠️ Zurvapay payment could not be started: {err}\nDeposit #{id} is pending — please try again or contact the admin.",
    "pay_zurva_fail_order": "⚠️ Zurvapay payment could not be started: {err}\nOrder #{oid} ({code}) is pending — please try again or contact the admin.",
    "pay_zurva_paynow": "💳 Pay Now — JazzCash / Easypaisa / Raast QR",
    "pay_zurva_dep": "⚡ Zurvapay Deposit\n\nAmount: Rs {amount}\nDeposit #{id}\n\nPay using the button below — JazzCash, Easypaisa or Raast QR. Your wallet will be credited automatically once the payment is confirmed.",
    "pay_manual_dep": "💚 {label} Deposit\n\nAmount: {amount} {currency}\n\nSend to:\nNumber: `{num}`\nName: {name}\n\nAfter payment, send the **Transaction ID (TID)** here, or send a screenshot.\nYour wallet will be credited once the admin approves it.",
    "pay_usdt_dep": "🪙 USDT (TRC20) Deposit\n\nAmount: **${amount} USDT**\n\nSend to (TRC20 only):\n`{addr}`\n\nAfter payment, send the **Transaction Hash** here. The bot will verify it automatically.",
    "pay_zurva_order": "⚡ Zurvapay Payment\n\nProduct: {label}\nAmount: Rs {total}\nOrder #{oid} ({code})\n\nPay using the button below — JazzCash, Easypaisa or Raast QR. Your account will be delivered automatically in this chat once the payment is confirmed.",
    "pay_manual_order": "💚 {label} Payment\n\nProduct: {label2}\nAmount: Rs {total}\n\nSend to:\nNumber: `{num}`\nName: {name}\n\nAfter payment, send the **Transaction ID (TID)** here, or send a screenshot.\nYour account will be delivered automatically once the admin approves it.",
    "pay_usdt_order": "🪙 USDT (TRC20) Payment\n\nProduct: {label}\nAmount: **${amount} USDT**\n\nSend to (TRC20 only):\n`{addr}`\n\nAfter payment, send the **Transaction Hash** here. The bot will verify it, then your account will be delivered automatically.",
    "pay_pkr_title": "💳 Pay via PKR — Total: Rs {total}\n\nChoose a method:",
    # Order status
    "status_pending": "⏳ PAID — admin review",
    "status_delivered": "✅ DELIVERED",
    "status_rejected": "❌ CANCELLED",
    "status_oos": "❌ CANCELLED (out of stock)",
    # My Orders
    "orders_none": "📦 You have no orders.",
    "orders_title": "📦 My Orders — tap an order for details:",
    "order_not_found": "Order not found.",
    "order_buy_again": "🔁 Buy Again",
    "order_write_review": "⭐ Write Review",
    "order_back": "⬅ My Orders",
    # Wallet
    "wallet_deposit": "💰 Deposit",
    "wallet_topup": "🎟️ Redeem Top-up Code",
    "wallet_stmt": "📄 Wallet Statement",
    "wallet_stmt_none": "No entries yet.",
    "wallet_stmt_dl": "⬇️ Download Statement (.txt)",
    "wallet_stmt_caption": "📄 Your wallet statement.",
    # Deposit
    "dep_title": "💰 Deposit\n\nChoose a currency (minimum amount 1):",
    "dep_pkr": "💚 Deposit in PKR",
    "dep_usdt": "🪙 Deposit in USDT",
    "dep_back_wallet": "⬅ Wallet",
    "dep_amount": "💰 Deposit ({cur})\n\nSend the amount — minimum 1 {cur}:",
    "dep_method_pkr": "💰 Deposit Rs {amount} — choose a payment method:",
    "dep_method_usdt": "💰 Deposit ${amount} USDT — choose a payment method:",
    "dep_back": "⬅ Back",
    # Help
    "help_text": ("❓ Help — How to buy?\n\n"
        "1️⃣ Open Products, choose a product by category or search.\n"
        "2️⃣ Choose the quantity on the product page (bulk discounts give a lower price).\n"
        "3️⃣ Read the Terms & Conditions and tap I Agree.\n"
        "4️⃣ Check the Order Summary — tap Apply Coupon Code if you have one.\n"
        "5️⃣ Pay: Easypaisa / JazzCash (send TID or screenshot), USDT TRC20 (send TX hash), or Zurvapay (JazzCash/Easypaisa/Raast QR) — auto-verified, your account arrives by itself. If you have wallet balance, you can also Pay from Wallet.\n"
        "6️⃣ Once the admin verifies it, your account is delivered automatically in this chat.\n\n"
        "💰 You can also pay by depositing into your Wallet.\n"
        "🎁 Get 7% off your first order with the FIRST-ORDER coupon!"),
    # Support
    "support_title": "💬 Support\n\nIf you have any issue — payment, an account that doesn't work, or an order that didn't arrive — send a message to the admin below.",
    "support_send": "✉️ Send Message to Admin",
    "support_tg": "💬 Message Admin (Telegram)",
    "support_prompt": "✉️ Write your message — I'll forward it to the admin.\n\n(To cancel, send /cancel)",
    "support_sent": "✅ Your message has been sent to the admin. The reply will come in this chat.",
    # Giveaways
    "giv_share": "Share your link — when your friend joins the channel and completes their first order, your referral will be verified.\n",
    "giv_mandatory": "\n⚠️ Mandatory: you must join our Telegram channel @retail_shop_official and WhatsApp channel.",
    "giv_verify_btn": "✅ I Have Joined (Verify Membership)",
    # Channels / Join Us
    "channels_title": "📢 Our Channels\n\nJoining the Telegram channel is required — the bot checks it automatically.\nNew products and offers are posted on the WhatsApp channel.",
    "joinus_title": "🤝 Join Us\n\nJoin our channels — new products, offers and giveaways appear here first.",
    # Channel gate
    "gate_join_tg": "🔒 Please join our Telegram channel first, then the products will appear.\n\nJoin the channel, then tap **Check Join Status** below.",
    "gate_check_fail": "⚠️ The channel check could not be completed. The admin may not have made the bot an admin of the Telegram channel. Please try /start again in a bit.",
    "gate_join_wa": "💬 Please also join our WhatsApp channel — new products and offers are posted there.\n\nAfter joining, tap **Maine Join Kar Liya** below.",
    "gate_joined_tg": "✅ Telegram channel joined! Now please also join the WhatsApp channel.",
    "gate_thanks": "✅ Thank you! ",
    # Cart
    "cart_empty": "🛒 Your cart is empty.",
    "cart_added": "🛒 Added to cart!",
    "cart_empty_alert": "Your cart is empty.",
    "cart_stock_alert": "Only {n} of {name} is in stock.",
    "cart_checkout": "✅ Checkout",
    "cart_clear": "🧹 Clear Cart",
    "cart_continue": "🛍️ Continue Shopping",
    # Reviews
    "rev_only_delivered": "You can only review a delivered order.",
    "rev_rating": "⭐ Rating: {rating}/5\n\nNow write your review (send it as text):",
    "rev_saved": "✅ Thank you! Your review has been saved.",
    # Search / Request
    "search_prompt": "🔍 Search for a product — send a keyword (e.g. 'muse' or 'capcut'):",
    "search_none": "🔍 '{kw}' — no product found.",
    "req_prompt": "📝 Which product do you need? Send the name + details — I'll forward it to the admin:",
    "req_sent": "✅ Your request has been sent to the admin. Thank you!",
    # Coupon
    "coupon_prompt": "🎟️ Send your coupon code (e.g. FIRST-ORDER):",
    "coupon_applied": "✅ Coupon {code} applied! {pct}% off — showing the summary again...",
    "coupon_wrong": "This coupon code is not correct.",
    "coupon_not_yours": "You don't have this coupon.",
    "coupon_used": "This coupon has already been used.",
    "coupon_first_only": "This coupon is only for your first order.",
    # Proof / buy
    "buy_free": "🎁 FREE product! Order #{oid} — sending your account now...",
    "buy_order_received": "✅ Order #{oid} ({code}) received! The admin is verifying it. Your account will arrive in this chat once approved.",
    "buy_hash_short": "That doesn't look like a hash. Please send the full Transaction Hash (copy it from the TronScan link).",
    "buy_usdt_verified": "✅ USDT verified! {reason}\nOrder #{oid} ({code}) — sending your account now...",
    "buy_usdt_fail": "⚠️ Auto-verification failed: {reason}\nOrder #{oid} ({code}) has been sent to the admin — they will check it manually and deliver.",
    "buy_wallet_paid": "✅ Paid from wallet! Order #{oid} — sending your account now...",
    "buy_wallet_refund": "⚠️ Delivery failed (out of stock) — your wallet has been refunded.",
    "buy_wallet_low": "Your wallet balance is too low. Please deposit first.",
    # Deposit proof
    "dep_received": "✅ Deposit #{id} received! The admin is verifying it. Your wallet will be credited once approved.",
    "dep_usdt_verified": "✅ USDT verified! {reason}\nDeposit #{id} — {amount} {currency} has been added to your wallet.",
    "dep_usdt_fail": "⚠️ Auto-verification failed: {reason}\nDeposit #{id} has been sent to the admin — they will check it manually and credit your wallet.",
    # Top-up
    "topup_prompt": "🎟️ Send your top-up code:",
    "topup_wrong": "❌ This top-up code is not correct.",
    "topup_used": "❌ This code has already been used.",
    "topup_ok": "✅ Top-up successful! {amount} {currency} has been added to your wallet.",
    # Misc
    "misc_expired": "Session expired. Please start again with /start.",
    "misc_only_number": "Please send only a number (e.g. 500):",
    "misc_min_deposit": "The minimum deposit is 1. Please send the amount again:",
    "misc_giv_verified": "✅ Verified! You are eligible for giveaways.",
    "misc_giv_check_fail": "The check could not be completed — please try again in a bit.",
    "misc_giv_join_first": "Please join the Telegram channel first, then tap verify.",
    "misc_join_check": "Not joined yet — please join the channel first, then check again.",
    "misc_join_check_fail": "The check could not be completed — the bot is not an admin of the channel.",
},
}

# ---------- Currency / prices ----------

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

def stock_tag(p, uid=None):
    if p["unlimited"]:
        return "♾️"
    n = stock_count(p["id"])
    if uid is None:
        return f"Stock: {n}" if n else "Out of Stock"
    return t(uid, "prod_stock", n=n) if n else t(uid, "prod_out_stock")

def product_btn_label(p, uid):
    """Uniform product button: 🟢/🔴 <Name> — <Price>. Price hamesha poori
    dikhti hai; bohat lamba naam end se chhota kar diya jata hai."""
    try:
        unlimited = bool(p["unlimited"])
    except Exception:
        unlimited = False
    in_stock = unlimited or stock_count(p["id"]) > 0
    dot = "🟢" if in_stock else "🔴"
    price = price_text(p, uid)
    name = p["name"]
    suffix = f" — {price}"
    max_total = 58
    if len(dot) + 1 + len(name) + len(suffix) > max_total:
        keep = max_total - len(dot) - 1 - len(suffix) - 1
        name = name[:max(keep, 8)] + "…"
    return f"{dot} {name}{suffix}"

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
        return False, t(uid, "coupon_wrong")
    c = db(); r = c.execute("SELECT * FROM coupons WHERE user_id=? AND code=?", (uid, FIRST_COUPON_CODE)).fetchone()
    if not r:
        c.close(); return False, t(uid, "coupon_not_yours")
    if r["used"]:
        c.close(); return False, t(uid, "coupon_used")
    done = c.execute("SELECT COUNT(*) n FROM orders WHERE user_id=? AND status='delivered'", (uid,)).fetchone()["n"]
    c.close()
    if done:
        return False, t(uid, "coupon_first_only")
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

def create_order(uid, username, items, payment_method, amount_text, proof, amount_pkr, code, coupon_code, discount_pkr, channel="telegram"):
    """items: list of {pid, qty}. Returns order id.
    channel: 'telegram' ya 'whatsapp' ya 'web' (site checkout).
    WhatsApp orders me uid = buyer ka phone number (integer, digits only)
    hota hai. Web orders me uid = 0 hota hai (koi Telegram identity nahi —
    delivery website ke order page pe hoti hai, delivery_token se)."""
    c = db()
    first_pid = items[0]["pid"] if items else 0
    total_qty = sum(i["qty"] for i in items)
    cur = c.execute(
        "INSERT INTO orders(user_id,username,product_id,payment_method,amount_text,proof,status,created_at,code,items_json,qty,discount_pkr,coupon_code,amount_pkr,channel) "
        "VALUES(?,?,?,?,?,?, 'pending', ?,?,?,?,?,?,?,?)",
        (uid, username, first_pid, payment_method, amount_text, proof,
         datetime.now().strftime("%Y-%m-%d %H:%M"), code, json.dumps(items), total_qty,
         discount_pkr, coupon_code or "", amount_pkr, channel))
    oid = cur.lastrowid; c.commit(); c.close()
    return oid

# ---------- Outbox (Telegram bot <-> WhatsApp bot message bus) ----------
# Dono processes ek hi shop.db share karte hain. Python bot WhatsApp buyer
# ko seedha message nahi bhej sakta — wo outbox me daalta hai, Node (Baileys)
# poll karke bhejta hai. Ulta: WA bot admin ko Telegram pe notify karne ke
# liye outbox (channel='telegram') me daalta hai, Python ka poller bhejta hai.

def queue_outbox(channel, recipient, text):
    """Outbox me message daalo. Returns row id."""
    c = db()
    cur = c.execute("INSERT INTO outbox(channel,recipient,text,status,created_at) VALUES(?,?,?, 'pending', ?)",
                    (channel, str(recipient), text, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    oid = cur.lastrowid; c.commit(); c.close()
    return oid

def _outbox_send_telegram(row):
    """Ek outbox row (channel='telegram') Telegram pe bhejo. Returns True/False."""
    try:
        _zurva_run_on_loop(PTB_APP.bot.send_message(int(row["recipient"]), row["text"]), timeout=30)
        return True
    except Exception as e:
        log.warning("Outbox telegram send fail (row %s): %s", row["id"], e)
        return False

def _outbox_poller_loop():
    """Background thread: pending 'telegram' outbox rows bhejte raho.
    PTB loop ready hone ka intezar karta hai; kabhi crash nahi hota."""
    import time as _time
    while True:
        try:
            _time.sleep(10)
            if PTB_APP is None or PTB_LOOP is None or PTB_LOOP.is_closed():
                continue
            c = db()
            rows = c.execute("SELECT * FROM outbox WHERE channel='telegram' AND status='pending' ORDER BY id LIMIT 20").fetchall()
            c.close()
            for row in rows:
                c = db()
                cur = c.execute("UPDATE outbox SET status='sending' WHERE id=? AND status='pending'", (row["id"],))
                claimed = cur.rowcount; c.commit(); c.close()
                if not claimed:
                    continue
                ok = _outbox_send_telegram(row)
                c = db()
                if ok:
                    c.execute("UPDATE outbox SET status='sent', sent_at=? WHERE id=?",
                              (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), row["id"]))
                else:
                    c.execute("UPDATE outbox SET status='pending' WHERE id=?", (row["id"],))
                c.commit(); c.close()
        except Exception:
            log.exception("Outbox poller error (retry in 10s)")

def start_outbox_poller():
    threading.Thread(target=_outbox_poller_loop, daemon=True).start()
    log.info("Outbox poller started (telegram rows)")

def order_status_label(status, uid=None):
    key = {"pending": "status_pending",
           "delivered": "status_delivered",
           "rejected": "status_rejected",
           "out_of_stock": "status_oos"}.get(status)
    if key is None:
        return status
    if uid is None:
        return STRINGS["ur"][key]
    return t(uid, key)

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
    uid = user_id or 0
    lines = [t(uid, "menu_welcome")]
    if user_id:
        lines.append(f"\n{t(uid, 'menu_currency', cur=user_currency(user_id))}")
        ensure_first_coupon(user_id)
        c = db()
        r = c.execute("SELECT used FROM coupons WHERE user_id=? AND code=?", (user_id, FIRST_COUPON_CODE)).fetchone()
        done = c.execute("SELECT COUNT(*) n FROM orders WHERE user_id=? AND status='delivered'", (user_id,)).fetchone()["n"]
        c.close()
        if r and not r["used"] and not done:
            lines.append(t(uid, "menu_coupon", code=FIRST_COUPON_CODE, pct=FIRST_COUPON_PCT))
    lines.append("\n" + t(uid, "menu_choose"))
    return "\n".join(lines)

def menu_kb(uid=None):
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
        [InlineKeyboardButton(t(uid or 0, "menu_lang_btn"), callback_data="lang_menu")],
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
        kb.append([InlineKeyboardButton(product_btn_label(p, uid), callback_data=f"prod_{p['id']}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(t(uid, "prod_prev"), callback_data=f"{base}_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(t(uid, "prod_next"), callback_data=f"{base}_{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(t(uid, "prod_cart"), callback_data="cart"),
               InlineKeyboardButton(t(uid, "prod_refresh"), callback_data=f"{base}_{page}")])
    kb.append([InlineKeyboardButton(t(uid, "prod_search"), callback_data="search_prod"),
               InlineKeyboardButton(t(uid, "prod_request"), callback_data="req_prod")])
    kb.append([InlineKeyboardButton(t(uid, "prod_back_menu"), callback_data="back_menu")])
    return InlineKeyboardMarkup(kb), total_pages

def products_view_text(uid, page, cat_idx, total_pages):
    parts = [PROMO_BANNER, ""]
    if cat_idx is not None:
        parts.append(f"📂 {CATEGORIES[cat_idx]}")
    parts.append(t(uid, "prod_choose", page=page + 1, total=total_pages))
    return "\n".join(parts)

async def render_products_list(q, context, page=0, cat_idx=None):
    kb, total_pages = products_view_kb(q.from_user.id, page, cat_idx)
    context.user_data["last_list"] = f"cat_{cat_idx}_{page}" if cat_idx is not None else f"plist_{page}"
    await q.edit_message_text(products_view_text(q.from_user.id, page, cat_idx, total_pages), reply_markup=kb)

async def render_product_detail(q, context, pid):
    """Product detail — quantity selector + terms + I Agree, sab ek hi
    screen pe (naya simplified flow). Out of stock ho to agree button
    nahi dikhta, sirf out-of-stock note."""
    uid = q.from_user.id
    c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone(); c.close()
    if not p:
        await q.edit_message_text(t(uid, "prod_not_found")); return
    # Buy state: isi product ki quantity yahan se set hoti hai.
    if context.user_data.get("buy_pid") != pid:
        context.user_data["buy_pid"] = pid
        context.user_data["buy_mode"] = "single"
        context.user_data["buy_qty"] = 1
        context.user_data.pop("buy_items", None)
    if not context.user_data.get("buy_code"):
        context.user_data["buy_code"] = gen_order_code()
    qty = max(1, min(99, int(context.user_data.get("buy_qty", 1) or 1)))
    context.user_data["buy_qty"] = qty
    n, avg = review_stats(pid)
    rating_line = t(uid, "prod_reviews", avg=avg, n=n) if n else t(uid, "prod_no_reviews")
    is_free = p["price_pkr"] == 0 and p["price_usdt"] == 0
    try:
        unlimited = bool(p["unlimited"])
    except Exception:
        unlimited = False
    in_stock = unlimited or stock_count(pid) > 0
    bulk = bulk_text(p, uid)
    desc = p["description"] or "No description."
    terms = (p["terms"] or "").strip() if "terms" in p.keys() else ""
    text = (f"📦 {p['name']}\n\n{desc}\n\n"
            f"{t(uid, 'prod_price', price=price_text(p, uid))}\n"
            f"📦 {stock_tag(p, uid)}\n{rating_line}\n")
    if terms:
        text += f"\n📖 How to Use:\n{terms}\n"
    if bulk:
        text += bulk + "\n"
    kb_rows = []
    if not in_stock and not is_free:
        text += f"\n{t(uid, 'prod_oos_note')}\n"
    else:
        unit = unit_price_pkr(p, qty)
        kb_rows.append([
            InlineKeyboardButton("➖", callback_data=f"qtydec_{pid}"),
            InlineKeyboardButton(t(uid, "prod_qty_line", qty=qty, price=fmt_money(unit * qty, uid)), callback_data="qty_info"),
            InlineKeyboardButton("➕", callback_data=f"qtyinc_{pid}"),
        ])
        kb_rows.append([InlineKeyboardButton(t(uid, "prod_custom_qty"), callback_data=f"qtycustom_{pid}")])
        if is_free:
            kb_rows.append([InlineKeyboardButton(t(uid, "prod_get_free"), callback_data=f"free_{pid}")])
        else:
            kb_rows.append([InlineKeyboardButton(t(uid, "prod_agree"), callback_data="detail_agree")])
    kb_rows.append([InlineKeyboardButton(t(uid, "prod_add_cart"), callback_data=f"cartadd_{pid}")])
    kb_rows.append([InlineKeyboardButton(t(uid, "prod_cart"), callback_data="cart"),
                    InlineKeyboardButton(t(uid, "prod_reviews_btn"), callback_data=f"rev_{pid}_0")])
    kb_rows.append([InlineKeyboardButton(t(uid, "prod_back_products"), callback_data="back_list")])
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb_rows))

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
            t(user.id, "gate_join_tg"),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return False
    if member is None:
        await update.message.reply_text(t(user.id, "gate_check_fail"))
        return False
    if wa_channel_url() and not wa_joined_flag(user.id):
        kb = [[InlineKeyboardButton("💬 Join WhatsApp Channel", url=wa_channel_url())],
              [InlineKeyboardButton("✅ Maine Join Kar Liya", callback_data="wa_joined")]]
        await update.message.reply_text(
            t(user.id, "gate_join_wa"),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return False
    await update.message.reply_text(await show_menu_text(user.id), reply_markup=menu_kb(user.id))
    return True

# ---------- Auto Delivery ----------

def _md_escape(text):
    """Telegram legacy Markdown ke liye escape — delivery message me
    instructions daalte waqt formatting na toote."""
    return (text.replace("\\", "\\\\").replace("`", "\\`").replace("*", "\\*")
                .replace("_", "\\_").replace("[", "\\[").replace("]", "\\]"))

def delivery_instructions(product):
    """Product ki How-to-Use instructions (terms, warna description) —
    delivery message me lagane ke liye. Khali ho to ''."""
    terms = ""
    try:
        terms = (product["terms"] or "").strip() if "terms" in product.keys() else ""
    except Exception:
        terms = ""
    if not terms:
        try:
            terms = (product["description"] or "").strip() if "description" in product.keys() else ""
        except Exception:
            terms = ""
    if not terms:
        return ""
    return "\n\n📖 *How to Use*\n" + _md_escape(terms)

def wa_delivery_text(product, accs, order, date_txt):
    """WhatsApp buyer ke liye PLAIN-TEXT delivery message (koi Markdown nahi).
    Telegram wale message jaisa hi, sirf formatting nahi."""
    order_id = order["id"]
    try:
        terms = (product["terms"] or "").strip() if "terms" in product.keys() else ""
    except Exception:
        terms = ""
    if not terms:
        try:
            terms = (product["description"] or "").strip() if "description" in product.keys() else ""
        except Exception:
            terms = ""
    howto = f"\n\nHow to Use:\n{terms}" if terms else ""
    kind = (product["kind"] or "account") if "kind" in product.keys() else "account"
    if kind == "mail":
        lines = []
        for acc in accs:
            extra = (acc["extra"] or "") if "extra" in acc.keys() else ""
            lines.append(f"{extra} | {acc['login']} | {acc['password']}" if extra else f"{acc['login']} | {acc['password']}")
        return (
            f"✅ Payment Verified\n"
            f"Product: {product['name']}\nOrder ID: PAY-{order_id}\n"
            f"Paid: {order['amount_text']}\nDate: {date_txt}\n\n"
            f"Account\n" + "\n".join(lines) + "\n\n"
            f"Order Delivery Note\n"
            f"You have received mail and mailbox url.\n"
            f"Copy the mail and paste it on muse.ai.\n\n"
            f"Then go to mailbox site, get otp and paste on muse.ai to complete login.\n\n"
            f"OTP na mile ya koi issue ho to Support se rabta karein — code admin bhej dega."
            + howto
        )
    if len(accs) == 1:
        acc = accs[0]
        return (
            f"✅ Payment confirmed! Aapka account mil gaya:\n\n"
            f"Product: {product['name']}\n"
            f"Login / Email: {acc['login']}\n"
            f"Password: {acc['password']}\n\n"
            f"Order #{order_id} (PAY-{order_id})\n"
            f"Account ko save kar lein. Koi issue ho to admin se rabta karein."
            + howto
        )
    lines = []
    for i, acc in enumerate(accs, 1):
        lines.append(f"{i}. Login: {acc['login']}\n   Password: {acc['password']}")
    return (
        f"✅ Payment confirmed! Aapke {len(accs)} accounts mil gaye:\n\n"
        f"Product: {product['name']}\n\n" + "\n".join(lines) +
        f"\n\nOrder #{order_id} (PAY-{order_id})\n"
        f"Accounts ko save kar lein. Koi issue ho to admin se rabta karein."
        + howto
    )

async def send_to_buyer(context, order, text, parse_mode=None):
    """Buyer ko message bhejo — channel ke hisaab se route karo.
    Telegram: seedha send. WhatsApp: outbox me daalo (Node bot bhejega)."""
    channel = ""
    try:
        channel = (order["channel"] or "telegram") if "channel" in order.keys() else "telegram"
    except Exception:
        channel = "telegram"
    if channel == "whatsapp":
        jid = f"{order['user_id']}@s.whatsapp.net"
        queue_outbox("whatsapp", jid, text)
        return True
    await context.bot.send_message(order["user_id"], text, parse_mode=parse_mode)
    return True

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
            await send_to_buyer(context, order, f"Sorry, {product['name']} ka stock khatam ho gaya. Admin se rabta karein, refund / replacement milega.")
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
    channel = ""
    try:
        channel = (order["channel"] or "telegram") if "channel" in order.keys() else "telegram"
    except Exception:
        channel = "telegram"
    for product, accs in plan:
        if channel == "whatsapp":
            # WhatsApp buyer: plain-text delivery outbox me — Node (Baileys) bhejega.
            queue_outbox("whatsapp", f"{order['user_id']}@s.whatsapp.net",
                         wa_delivery_text(product, accs, order, date_txt))
            continue
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
                + delivery_instructions(product)
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
                + delivery_instructions(product)
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
                + delivery_instructions(product)
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

def zurvapay_initiate(identifier, amount_pkr, details, customer_name="", customer_email="",
                      success_url=None, cancel_url=None):
    """POST /payment/initiate (form-encoded, homepage sample ke mutabiq).
    Returns (checkout_url, None) ya (None, error). success_url/cancel_url
    sirf web orders ke liye diye jate hain — hamesha BOT-SERVER URLs
    (approved domain, {PUBLIC_BASE_URL}/zurvapay/success?code=...), kyunke
    Zurvapay site domain (retailshop.pages.dev) ko reject karta hai.
    Telegram orders pe default bot-server URLs lagte hain."""
    if not ZURVAPAY_PUBLIC_KEY:
        return None, "ZURVAPAY_PUBLIC_KEY set nahi hai"
    ipn = zurvapay_ipn_url()
    if not ipn:
        return None, "IPN URL nahi bani — PUBLIC_BASE_URL ya ZURVAPAY_IPN_URL set karein"
    base = _zurvapay_public_base()
    payload = {
        "public_key": ZURVAPAY_PUBLIC_KEY,
        "bot_id": ZURVAPAY_BOT_ID,
        "identifier": identifier,
        "currency": "PKR",
        "amount": int(amount_pkr),
        "details": details,
        "ipn_url": ipn,
        "success_url": success_url or f"{base}/zurvapay/success",
        "cancel_url": cancel_url or f"{base}/zurvapay/cancel",
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
    log.warning("Zurvapay initiate failed for %s: %s", identifier, err or "unknown")
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
    alag-alag ho sakte hain, is liye aliases check karte hain.
    Zurvapay ka asal IPN (13 Nov 2026, RS-467791) nested form fields bhejta
    hai: amount `data[amount]` me ('20.00000000') aur signature
    `status_signature` field me aati hai — ye dono aliases zaroori hain."""
    d = {str(k).lower(): v for k, v in (data or {}).items()}

    def pick(*names):
        for n in names:
            if n in d and d[n] not in (None, ""):
                return str(d[n])
        return ""

    sigs = []
    for n in ("status_signature", "signature", "hash", "sign", "checksum",
              "data[signature]", "data[status_signature]"):
        if n in d and d[n] not in (None, ""):
            v = str(d[n]).strip().lower()
            if v and v not in sigs:
                sigs.append(v)
    return {
        "identifier": pick("identifier", "order_id", "merchant_order_id", "reference", "ref", "orderid"),
        "amount": pick("amount", "amount_pkr", "total", "price", "data[amount]", "data[total]"),
        "status": pick("status", "payment_status", "transaction_status", "txn_status").lower(),
        "signature": sigs[0] if sigs else "",
        "signatures": sigs,
        "txid": pick("transaction_id", "payment_id", "zurva_txn_id", "txn_id", "data[payment_trx]", "payment_trx"),
    }

def _hmac_hex(secret, msg, algo="sha256"):
    return hmac.new(secret.encode(), msg.encode(), getattr(hashlib, algo)).hexdigest()

def verify_zurvapay_ipn(data, raw_body=b"", headers=None):
    """Signed IPN verify karo. Returns (True, scheme_name) ya (False, reason).
    'auto' mode tamam candidate schemes try karta hai — har candidate secret
    se keyed HMAC hai, is liye baghair secret ke koi bhi forge nahi ho sakta.
    ZURVAPAY_IPN_MODE se ek scheme pin ki ja sakti hai.
    13 Nov 2026 ka asal IPN (RS-467791) se seekha: signature `status_signature`
    field me aati hai aur amount `data[amount]` ('20.00000000') me — is liye
    tamam signature values aur amount formats try karte hain."""
    secret = ZURVAPAY_SECRET_KEY
    if not secret:
        return False, "secret key set nahi hai"
    f = _zurva_ipn_fields(data)
    sigs = list(f.get("signatures") or [])
    if f.get("signature") and f["signature"] not in sigs:
        sigs.append(f["signature"])
    if headers:
        for h in ("X-Zurvapay-Signature", "X-Signature", "X-Zurva-Signature", "Signature"):
            if headers.get(h):
                v = str(headers.get(h)).strip().lower()
                if v and v not in sigs:
                    sigs.append(v)
    if not sigs:
        return False, "IPN me signature nahi mili"
    ident = f.get("identifier", "")
    amount = f.get("amount", "")
    status = f.get("status", "")
    txid = f.get("txid", "")
    # Amount ke formats: IPN '20.00000000' bhejta hai; scheme '20' ya '20.00' bhi ho sakti hai.
    amounts = []
    for a in (amount,):
        if a and a not in amounts:
            amounts.append(a)
        try:
            v = float(str(a))
            for norm in (str(int(v)) if v == int(v) else str(v), f"{v:.2f}"):
                if norm not in amounts:
                    amounts.append(norm)
        except Exception:
            pass
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
    # Naye candidates — status/trx wale aur normalized amount wale (13 Nov IPN ke baad add hue).
    msgs = []
    for a in amounts:
        if ident and a:
            msgs += [f"{ident}|{a}", f"{ident}{a}"]
            if status:
                msgs += [f"{ident}|{a}|{status}", f"{status}|{ident}|{a}",
                         f"{ident}{a}{status}", f"{status}{ident}{a}"]
            if txid:
                msgs += [f"{ident}|{a}|{txid}"]
        if status and txid and a:
            msgs += [f"{status}|{txid}|{a}"]
    if ident and status:
        msgs += [f"{ident}|{status}", f"{status}|{ident}"]
    if status:
        msgs += [status]
    if txid:
        msgs += [txid]
    seen = set()
    for m in msgs:
        if m and m not in seen:
            seen.add(m)
            candidates.append((f"hmac_msg:{m}", _hmac_hex(secret, m)))
    for a in amounts:
        if ident and a and status:
            candidates += [
                ("sha256_sec_status", hashlib.sha256(f"{secret}{status}{ident}{a}".encode()).hexdigest()),
                ("sha256_sec_ident", hashlib.sha256(f"{secret}{ident}{a}{status}".encode()).hexdigest()),
                ("sha256_status_sec", hashlib.sha256(f"{status}{ident}{a}{secret}".encode()).hexdigest()),
                ("sha256_ident_sec", hashlib.sha256(f"{ident}{a}{secret}".encode()).hexdigest()),
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
    try:
        # Sorted canon — signature jaisi keys ko substring se bhi nikalo
        # (status_signature bhi signature hi hai, canon me nahi honi chahiye).
        items = sorted((k, v) for k, v in data.items()
                       if not any(s in str(k).lower() for s in ("signature", "sign", "hash", "checksum")))
        canon = "&".join(f"{k}={v}" for k, v in items)
        candidates += [
            ("hmac_sorted_nosig_sha256", _hmac_hex(secret, canon)),
            ("hmac_sorted_nosig_sha512", _hmac_hex(secret, canon, "sha512")),
        ]
    except Exception:
        pass
    mode = (ZURVAPAY_IPN_MODE or "auto").strip()
    if mode != "auto":
        candidates = [c for c in candidates if c[0] == mode or c[0].split(":")[0] == mode]
        if not candidates:
            return False, f"unknown ZURVAPAY_IPN_MODE: {mode}"
    for name, expected in candidates:
        for sig in sigs:
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
        try:
            ch = (o["channel"] or "telegram") if "channel" in o.keys() else "telegram"
        except Exception:
            ch = "telegram"
        if ch == "web":
            # Web order — apne stock se NAHI, AHM7 reseller se fulfill karo.
            ok, fmsg = fulfill_web_order(o["id"])
            if ok:
                return True, "delivered via AHM7"
            # Paid-pending: payment mil gayi, fulfillment baad me (manual/retry).
            # IPN handle ho gaya — Zurvapay ko retry nahi karwana.
            return True, f"paid-pending: {fmsg}"
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

# ---------- Web checkout (site) + AHM7 reseller fulfillment ----------
# RetailShop AHM7 ka reseller hai: customer RetailShop ko pay karta hai,
# hum commission rakhte hain, AHM7 se asal cost pe auto-buy karte hain aur
# AHM7 ki credentials buyer ko Telegram DM me bhejte hain. Apne stock se
# web orders deliver NAHI hote — sirf Telegram bot ke andar ki orders
# (channel='telegram') purane deliver_order path se chalti hain.

_AHM7_MAPPING = None

def load_ahm7_mapping():
    """ahm7-mapping.json (bot.py ke saath wali directory) load karo.
    Key: site product name. Value: ahm7_product_id, cost_pkr, site_price_pkr.
    Mapping na mile to {} — web checkout phir har product pe error dega."""
    global _AHM7_MAPPING
    if _AHM7_MAPPING is None:
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ahm7-mapping.json")
            with open(path, encoding="utf-8") as f:
                _AHM7_MAPPING = json.load(f).get("products", {}) or {}
        except Exception as e:
            log.warning("ahm7-mapping.json load fail: %s", e)
            _AHM7_MAPPING = {}
    return _AHM7_MAPPING

def _web_auth(headers):
    """X-Web-Secret check (constant-time). Returns True ya False.
    Secret set hi nahi hai to har request reject (fail-closed)."""
    if not WEB_API_SECRET:
        return False
    try:
        given = (headers.get("X-Web-Secret") or "").strip()
    except Exception:
        given = ""
    return bool(given) and hmac.compare_digest(given, WEB_API_SECRET)

def _gen_delivery_token():
    """Website order ke liye random delivery token (32 hex chars = 128 bit).
    Order code ke saath ye token hi credentials dekhne ki chabi hai —
    token ke baghair order page kuch nahi dikhata."""
    c = db()
    for _ in range(10):
        tok = secrets.token_hex(16)
        if not c.execute("SELECT 1 FROM orders WHERE delivery_token=?", (tok,)).fetchone():
            c.close()
            return tok
    c.close()
    return secrets.token_hex(16)

# GET /api/order rate limit: 30 requests / 60s per IP (in-memory, best-effort).
_ORDER_STATUS_RL = {}
_ORDER_STATUS_RL_LOCK = threading.Lock()

def _order_status_rate_limited(ip):
    now = time.time()
    ip = ip or "unknown"
    with _ORDER_STATUS_RL_LOCK:
        if len(_ORDER_STATUS_RL) > 2000:
            _ORDER_STATUS_RL.clear()
        start, count = _ORDER_STATUS_RL.get(ip, (now, 0))
        if now - start >= 60:
            start, count = now, 0
        count += 1
        _ORDER_STATUS_RL[ip] = (start, count)
        return count > 30

def zurvapay_redirect_target(kind, code):
    """Zurvapay success/cancel ke baad customer ko kahan bhejna hai.
    Returns the site order-page URL (code + delivery token DB se), ya shop
    home jab order na mile. kind 'success' ya 'cancel' — dono order page
    pe jate hain, taake customer apna order code + status dekh sake."""
    code = str(code or "").strip().upper()
    home = "https://retailshop.pages.dev/"
    if not code:
        return home
    try:
        c = db()
        o = c.execute("SELECT delivery_token, success_base FROM orders WHERE code=?", (code,)).fetchone()
        c.close()
    except Exception:
        return home
    if not o:
        return home
    try:
        token = o["delivery_token"] or ""
    except Exception:
        token = ""
    try:
        base = (o["success_base"] or "").strip().rstrip("/")
    except Exception:
        base = ""
    if base not in ("https://retailshop.pages.dev", "https://retail-shop.online"):
        base = "https://retailshop.pages.dev"
    if not token:
        return home
    return f"{base}/order.html?code={code}&token={token}"


# POST /api/review rate limit: 5 reviews / 60 min per IP (in-memory).
_REVIEW_RL = {}
_REVIEW_RL_LOCK = threading.Lock()

def _review_rate_limited(ip):
    now = time.time()
    ip = ip or "unknown"
    with _REVIEW_RL_LOCK:
        if len(_REVIEW_RL) > 2000:
            _REVIEW_RL.clear()
        start, count = _REVIEW_RL.get(ip, (now, 0))
        if now - start >= 3600:
            start, count = now, 0
        count += 1
        _REVIEW_RL[ip] = (start, count)
        return count > 5


def handle_submit_review(data, headers=None, client_ip=""):
    """POST /api/review (Worker se, X-Web-Secret). Stores a real customer
    review — koi fake/seeded review nahi. Returns (http_status, dict)."""
    if not _web_auth(headers or {}):
        return 401, {"ok": False, "error": "unauthorized"}
    if _review_rate_limited(client_ip):
        return 429, {"ok": False, "error": "too many reviews — thodi der me dobara try karein"}
    data = data or {}
    try:
        product_id = int(data.get("product_id"))
    except Exception:
        return 400, {"ok": False, "error": "product_id ghalat hai"}
    try:
        rating = int(data.get("rating"))
    except Exception:
        return 400, {"ok": False, "error": "rating 1 se 5 tak honi chahiye"}
    if rating < 1 or rating > 5:
        return 400, {"ok": False, "error": "rating 1 se 5 tak honi chahiye"}
    name = str(data.get("name") or "").strip()
    text = str(data.get("text") or "").strip()
    if not name or len(name) > 40:
        return 400, {"ok": False, "error": "naam 1 se 40 characters tak ho"}
    if not text or len(text) > 500:
        return 400, {"ok": False, "error": "review 1 se 500 characters tak ho"}
    low = (name + " " + text).lower()
    if "http://" in low or "https://" in low or "www." in low:
        return 400, {"ok": False, "error": "review me link nahi ho sakta"}
    c = db()
    prod = c.execute("SELECT id FROM products WHERE id=?", (product_id,)).fetchone()
    c.close()
    if not prod:
        return 404, {"ok": False, "error": "product nahi mila"}
    now = int(time.time())
    c = db()
    cur = c.execute("INSERT INTO reviews(product_id, name, rating, text, created_at) VALUES(?,?,?,?,?)",
              (product_id, name, rating, text, now))
    rid = cur.lastrowid
    c.commit(); c.close()
    log.info("Review added: product %s, rating %s", product_id, rating)
    return 200, {"ok": True, "review": {"id": rid, "product_id": product_id, "name": name,
                                         "rating": rating, "text": text, "created_at": now}}


def handle_delete_review(data, headers=None):
    """POST /api/review/delete (Worker se, X-Web-Secret). Test reviews
    hatane ke liye — site par koi delete button nahi hai. Returns
    (http_status, dict)."""
    if not _web_auth(headers or {}):
        return 401, {"ok": False, "error": "unauthorized"}
    data = data or {}
    try:
        rid = int(data.get("id"))
    except Exception:
        return 400, {"ok": False, "error": "review id ghalat hai"}
    c = db()
    cur = c.execute("DELETE FROM reviews WHERE id=?", (rid,))
    n = cur.rowcount
    c.commit(); c.close()
    if not n:
        return 404, {"ok": False, "error": "review nahi mili"}
    log.info("Review deleted: id %s", rid)
    return 200, {"ok": True, "deleted": n}


def _sold_count(c, product_id):
    """Real sold count: paid/delivered/pending-fulfillment orders jin me ye
    product ho (single product_id ya cart items_json). Har order ek dafa
    gina jata hai, chahe qty zyada ho."""
    sold = 0
    try:
        rows = c.execute(
            "SELECT product_id, items_json FROM orders "
            "WHERE status IN ('zurva_processing','ahm7_pending','delivered')").fetchall()
    except Exception:
        return 0
    for r in rows:
        try:
            if r["product_id"] == product_id:
                sold += 1
                continue
            items = json.loads(r["items_json"] or "[]")
            if isinstance(items, list) and any(
                    isinstance(it, dict) and it.get("pid") == product_id for it in items):
                sold += 1
        except Exception:
            continue
    return sold


def handle_get_reviews(product_id):
    """GET /api/reviews/<product_id> — public read. Returns the product's
    reviews (newest first) + real 'sold' count from the orders table."""
    try:
        product_id = int(product_id)
    except Exception:
        return 400, {"ok": False, "error": "product_id ghalat hai"}
    c = db()
    prod = c.execute("SELECT id FROM products WHERE id=?", (product_id,)).fetchone()
    if not prod:
        c.close()
        return 404, {"ok": False, "error": "product nahi mila"}
    rows = c.execute(
        "SELECT name, rating, text, created_at FROM reviews "
        "WHERE product_id=? ORDER BY id DESC LIMIT 50", (product_id,)).fetchall()
    sold = _sold_count(c, product_id)
    c.close()
    return 200, {"ok": True, "product_id": product_id, "sold": sold,
                 "reviews": [{"name": r["name"], "rating": r["rating"],
                              "text": r["text"], "created_at": r["created_at"]} for r in rows]}


def handle_order_status(code, token, headers=None, client_ip=""):
    """GET /api/order/<code>?token=... (Worker se aata hai). Returns
    (http_status, dict). Security: order code + delivery token dono chahiye.
    Ghalat/missing token, non-web order, ya missing WEB_API_SECRET — sab pe
    404 'not found' (koi information leak nahi). Credentials sirf tab
    milte hain jab status='delivered' ho."""
    if not _web_auth(headers or {}):
        return 404, {"ok": False, "error": "not found"}
    if _order_status_rate_limited(client_ip):
        return 429, {"ok": False, "error": "too many requests — thodi der me dobara try karein"}
    code = str(code or "").strip().upper()
    token = str(token or "").strip()
    if not code or not token:
        return 404, {"ok": False, "error": "not found"}
    c = db(); o = c.execute("SELECT * FROM orders WHERE code=?", (code,)).fetchone(); c.close()
    if not o:
        return 404, {"ok": False, "error": "not found"}
    try:
        ch = (o["channel"] or "telegram") if "channel" in o.keys() else "telegram"
    except Exception:
        ch = "telegram"
    if ch != "web":
        return 404, {"ok": False, "error": "not found"}
    try:
        stored = o["delivery_token"] or ""
    except Exception:
        stored = ""
    if not stored or not hmac.compare_digest(token, stored):
        return 404, {"ok": False, "error": "not found"}
    c = db(); p = c.execute("SELECT name FROM products WHERE id=?", (o["product_id"],)).fetchone(); c.close()
    pname = p["name"] if p else ""
    try:
        _lines = json.loads(o["items_json"] or "[]")
        _n = len(_lines) if isinstance(_lines, list) else 0
    except Exception:
        _n = 0
    if _n > 1 and pname:
        pname = f"{pname} (+{_n - 1} more)"
    out = {"ok": True, "code": code, "product": pname, "amount_pkr": o["amount_pkr"]}
    status = o["status"]
    if status == "delivered":
        creds = []
        try:
            creds = json.loads(o["ahm7_credentials"] or "[]")
        except Exception:
            creds = []
        out["status"] = "delivered"
        out["credentials"] = creds if isinstance(creds, list) else []
        return 200, out
    if status == "ahm7_pending":
        out["status"] = "manual"
        out["message"] = ("Payment mil gayi hai — order manual process ho raha hai. "
                          "Apna order code save rakhein aur thodi der me dobara check karein.")
        return 200, out
    out["status"] = "processing"
    out["message"] = "Payment confirm ho rahi hai — order process ho raha hai."
    if status == "pending":
        # Payment abhi start nahi hui — order page "Pay now" dikhaye.
        # (zurva_processing = payment mil gayi, dobara pay nahi karwana.)
        try:
            _cu = o["checkout_url"] or ""
        except Exception:
            _cu = ""
        if _cu:
            out["checkout_url"] = _cu
    return 200, out

# ---------- Web accounts (site signup/login — REAL accounts) ----------
# Site (Worker) -> bot server. Signup/login ko X-Web-Secret chahiye (Worker
# se aate hain); me/logout/delete/orders ko Bearer session token chahiye
# (customer ka apna token, Authorization header me — Worker forward karta
# hai). Password kabhi plain-text store/log nahi hota: hashlib.scrypt
# (per-user random salt, n=16384, r=8, p=1, dklen=32), stored
# "salt_hex$hash_hex". Session token sirf SHA-256 digest store hota hai;
# token khud sirf signup/login response me ek dafa milta hai.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_AUTH_SESSION_DAYS = 30
_AUTH_GENERIC_ERROR = "email ya password ghalat hai"

# Bot-side rate limits (in-memory, _ORDER_STATUS_RL jaisa pattern):
# signup 10/hour per IP, login 20/hour per (IP + email).
_AUTH_SIGNUP_RL = {}
_AUTH_LOGIN_RL = {}
_AUTH_RL_LOCK = threading.Lock()

def _auth_rate_limited(store, key, window_s, limit):
    now = time.time()
    key = key or "unknown"
    with _AUTH_RL_LOCK:
        if len(store) > 2000:
            store.clear()
        start, count = store.get(key, (now, 0))
        if now - start >= window_s:
            start, count = now, 0
        count += 1
        store[key] = (start, count)
        return count > limit

def _hash_password(password):
    """scrypt hash banao. Returns 'salt_hex$hash_hex'."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                        n=16384, r=8, p=1, dklen=32)
    return salt.hex() + "$" + dk.hex()

def _verify_password(password, stored):
    """Constant-time scrypt verify. Stored format ghalat ho to False."""
    try:
        salt_hex, hash_hex = (stored or "").split("$", 1)
        salt = bytes.fromhex(salt_hex)
        want = bytes.fromhex(hash_hex)
    except Exception:
        return False
    try:
        got = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                             n=16384, r=8, p=1, dklen=32)
    except Exception:
        return False
    return hmac.compare_digest(got, want)

def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _create_web_session(user_id, is_admin=0):
    """Naya session banao (30 din). Returns plaintext token (sirf caller
    ko wapas dena hai — DB me sirf SHA-256 digest jata hai).
    is_admin=1 sirf admin OTP verify se banta hai (admin dashboard)."""
    token = secrets.token_hex(16)  # 32 hex chars
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = _now_str()
    expires = (datetime.now() + timedelta(days=_AUTH_SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    c = db()
    c.execute("INSERT INTO web_sessions(user_id, token_hash, expires_at, created_at, is_admin) "
              "VALUES(?,?,?,?,?)", (user_id, token_hash, expires, now, 1 if is_admin else 0))
    c.commit(); c.close()
    return token

def _bearer_token(headers):
    """Authorization: Bearer <token> se token nikalo, warna None."""
    try:
        auth = (headers.get("Authorization") or "").strip()
    except Exception:
        return None
    if not auth or auth.split(None, 1)[0].lower() != "bearer":
        return None
    parts = auth.split(None, 1)
    return parts[1].strip() if len(parts) == 2 and parts[1].strip() else None

def _auth_session_user(headers):
    """Bearer token se logged-in web user nikalo.
    Returns (user_row_dict, None) ya (None, (status, error_dict)).
    Expired session mile to usay delete kar do."""
    token = _bearer_token(headers or {})
    if not token:
        return None, (401, {"ok": False, "error": "unauthorized"})
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    c = db()
    s = c.execute("SELECT * FROM web_sessions WHERE token_hash=?", (token_hash,)).fetchone()
    if not s:
        c.close()
        return None, (401, {"ok": False, "error": "unauthorized"})
    if (s["expires_at"] or "") <= _now_str():
        c.execute("DELETE FROM web_sessions WHERE id=?", (s["id"],))
        c.commit(); c.close()
        return None, (401, {"ok": False, "error": "unauthorized"})
    u = c.execute("SELECT * FROM web_users WHERE id=?", (s["user_id"],)).fetchone()
    c.close()
    if not u:
        return None, (401, {"ok": False, "error": "unauthorized"})
    return u, None

def _public_web_user(u):
    """Site ko wapas dene wala user object — kabhi password hash ya
    token nahi. Phone + is_admin admin dashboard / header ke liye;
    email_verified profile banner ke liye."""
    try:
        phone = u["phone"] or ""
    except Exception:
        phone = ""
    try:
        is_admin = bool(u["is_admin"])
    except Exception:
        is_admin = False
    try:
        email_verified = bool(u["email_verified"])
    except Exception:
        email_verified = False
    return {"name": u["name"], "email": u["email"],
            "phone": phone, "is_admin": is_admin,
            "email_verified": email_verified}

def _normalize_phone(raw):
    """Phone normalize karo: spaces/dashes/parens hatao; '00' -> '+'.
    Sirf +countrycode + 7-15 digits valid. Galat ho to None."""
    s = re.sub(r"[\s\-().]", "", str(raw or "").strip())
    if s.startswith("00"):
        s = "+" + s[2:]
    if not re.match(r"^\+\d{7,15}$", s):
        return None
    return s


def _is_admin_identity(email, phone):
    """Admin kaun hai: email ya phone env (ADMIN_EMAIL / ADMIN_PHONE)
    se match kare — ye Junaid ka account hai."""
    try:
        em = (email or "").strip().lower()
    except Exception:
        em = ""
    try:
        ph = (phone or "").strip()
    except Exception:
        ph = ""
    return bool((em and em == ADMIN_EMAIL) or (ph and ph == ADMIN_PHONE))


def _find_web_user_by_identifier(c, ident):
    """web_users me identifier dhoondo: email (@ ho) YA phone (+ ho)
    YA username (name). Naam ek se zyada users ka ho to None (ambiguous
    — galat account me login nahi karwana)."""
    ident = (ident or "").strip()
    if not ident:
        return None
    if "@" in ident:
        return c.execute("SELECT * FROM web_users WHERE email=?", (ident.lower(),)).fetchone()
    ph = _normalize_phone(ident)
    if ph:
        return c.execute("SELECT * FROM web_users WHERE phone=?", (ph,)).fetchone()
    rows = c.execute("SELECT * FROM web_users WHERE name=?", (ident,)).fetchall()
    if len(rows) == 1:
        return rows[0]
    return None


def handle_auth_signup(data, headers=None, client_ip=""):
    """POST /api/auth/signup (Worker se, X-Web-Secret). Returns (status, dict).
    Naya web account + session banao. Token sirf is response me milta hai."""
    if not _web_auth(headers or {}):
        return 401, {"ok": False, "error": "unauthorized"}
    if _auth_rate_limited(_AUTH_SIGNUP_RL, client_ip, 3600, 10):
        return 429, {"ok": False, "error": "too many signups — thodi der me dobara try karein"}
    data = data or {}
    name = str(data.get("name") or "").strip()
    email = str(data.get("email") or "").strip().lower()
    password = str(data.get("password") or "")
    phone = _normalize_phone(data.get("phone") or "")
    if not name or len(name) > 60:
        return 400, {"ok": False, "error": "naam 1 se 60 characters tak ho"}
    if not email or not _EMAIL_RE.match(email):
        return 400, {"ok": False, "error": "email ghalat hai"}
    if not phone:
        return 400, {"ok": False, "error": "phone number country code ke saath likhein (masalan +92 370 6462990)"}
    if len(password) < 8:
        return 400, {"ok": False, "error": "password kam se kam 8 characters ka ho"}
    c = db()
    exists = c.execute("SELECT id FROM web_users WHERE email=?", (email,)).fetchone()
    if exists:
        c.close()
        log.info("Auth signup: email=%s result=duplicate", email)
        return 409, {"ok": False, "error": "is email se account pehle se hai"}
    exists = c.execute("SELECT id FROM web_users WHERE phone=? AND phone <> ''", (phone,)).fetchone()
    if exists:
        c.close()
        log.info("Auth signup: phone=%s result=duplicate", phone)
        return 409, {"ok": False, "error": "is phone number se account pehle se hai"}
    is_admin = 1 if _is_admin_identity(email, phone) else 0
    cur = c.execute("INSERT INTO web_users(email, name, password_hash, created_at, phone, is_admin) "
                    "VALUES(?,?,?,?,?,?)", (email, name, _hash_password(password), _now_str(), phone, is_admin))
    uid = cur.lastrowid
    c.commit(); c.close()
    token = _create_web_session(uid)
    # Email verification code — best-effort, signup kabhi block nahi karta:
    # Resend free tier (sirf owner email) ya SMTP dono fail hon to bhi
    # account banta hai (unverified), login chalta hai, profile me banner
    # + resend button dikhta hai. Verification tab enforce hogi jab send
    # theek se kaam karne lage.
    verification_sent = False
    if _email_configured():
        try:
            _code = _issue_email_otp(uid)
            if _code:
                verification_sent = bool(_send_email(
                    email, "Pheonix Retail Shop — email verification code",
                    ("Assalam-o-Alaikum,\n\nAapka Pheonix Retail Shop email verification code: "
                     f"{_code}\nYe code 10 minute ke liye valid hai. Kisi ko na dein.\n\n"
                     "— Pheonix Retail Shop")))
        except Exception as e:
            log.warning("Auth signup: verification email failed for %s: %s", email, e)
            verification_sent = False
    log.info("Auth signup: email=%s result=ok verification_sent=%s", email, verification_sent)
    return 200, {"ok": True, "token": token,
                 "user": {"name": name, "email": email, "phone": phone, "is_admin": bool(is_admin),
                          "email_verified": False, "verification_sent": verification_sent}}

def handle_auth_login(data, headers=None, client_ip=""):
    """POST /api/auth/login (Worker se, X-Web-Secret). Returns (status, dict).
    Identifier = username (name) YA email YA phone. Ghalat identifier ya
    ghalat password — dono pe ek hi generic 401 (ye nahi batate ke
    identifier exist karta hai ya nahi)."""
    if not _web_auth(headers or {}):
        return 401, {"ok": False, "error": "unauthorized"}
    data = data or {}
    ident = str(data.get("identifier") or data.get("email") or "").strip()
    password = str(data.get("password") or "")
    if _auth_rate_limited(_AUTH_LOGIN_RL, f"{client_ip}|{ident.lower()}", 3600, 20):
        return 429, {"ok": False, "error": "too many logins — thodi der me dobara try karein"}
    if not ident or not password:
        return 400, {"ok": False, "error": "username, email ya phone + password chahiye"}
    c = db()
    u = _find_web_user_by_identifier(c, ident)
    if not u or not _verify_password(password, u["password_hash"]):
        c.close()
        log.info("Auth login: identifier=%s result=fail", ident)
        _log_issue(ident, "", "login_failed", "Failed login attempt")
        return 401, {"ok": False, "error": _AUTH_GENERIC_ERROR}
    # Admin flag dobara check karo (env badla ho to flag bhi badle).
    is_admin = 1 if _is_admin_identity(u["email"], u["phone"] if "phone" in u.keys() else "") else 0
    try:
        cur_admin = u["is_admin"] if "is_admin" in u.keys() else 0
    except Exception:
        cur_admin = 0
    if bool(cur_admin) != bool(is_admin):
        c.execute("UPDATE web_users SET is_admin=? WHERE id=?", (is_admin, u["id"]))
        c.commit()
    c.close()
    token = _create_web_session(u["id"])
    log.info("Auth login: identifier=%s result=ok", ident)
    return 200, {"ok": True, "token": token, "user": _public_web_user(u)}

def handle_auth_me(headers=None):
    """GET /api/auth/me — Bearer session token. X-Web-Secret NAHI chahiye."""
    u, err = _auth_session_user(headers or {})
    if err:
        return err
    return 200, {"ok": True, "user": _public_web_user(u)}

def handle_auth_logout(headers=None):
    """POST /api/auth/logout — Bearer token wala session delete karo.
    Token pehle se invalid ho to bhi 200 (validity leak nahi karni)."""
    token = _bearer_token(headers or {})
    if token:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        c = db()
        c.execute("DELETE FROM web_sessions WHERE token_hash=?", (token_hash,))
        c.commit(); c.close()
        log.info("Auth logout: result=ok")
    return 200, {"ok": True}

def handle_auth_delete(headers=None):
    """POST /api/auth/delete — Bearer token: user + uske tamam sessions
    delete karo (test accounts hatane ke liye)."""
    u, err = _auth_session_user(headers or {})
    if err:
        return err
    c = db()
    c.execute("DELETE FROM web_sessions WHERE user_id=?", (u["id"],))
    c.execute("DELETE FROM web_users WHERE id=?", (u["id"],))
    c.commit(); c.close()
    log.info("Auth delete: email=%s result=ok", u["email"])
    return 200, {"ok": True}

def handle_auth_orders(headers=None):
    """GET /api/auth/orders — Bearer token. Sirf is user ke apne web orders
    (channel='web' AND web_contact = user email), newest first. Dusre user
    ke orders kabhi nahi milte. order_url me code + delivery token hota hai
    (owner apna order page khol sakta hai)."""
    u, err = _auth_session_user(headers or {})
    if err:
        return err
    email = u["email"]
    c = db()
    rows = c.execute(
        "SELECT * FROM orders WHERE channel='web' AND web_contact=? "
        "ORDER BY id DESC", (email,)).fetchall()
    orders = []
    for o in rows:
        try:
            p = c.execute("SELECT name FROM products WHERE id=?", (o["product_id"],)).fetchone()
        except Exception:
            p = None
        pname = p["name"] if p else ""
        try:
            lines = json.loads(o["items_json"] or "[]")
            n = len(lines) if isinstance(lines, list) else 0
        except Exception:
            n = 0
        if n > 1 and pname:
            pname = f"{pname} (+{n - 1} more)"
        try:
            base = (o["success_base"] or "").strip().rstrip("/")
        except Exception:
            base = ""
        if base not in ("https://retailshop.pages.dev", "https://retail-shop.online"):
            base = "https://retailshop.pages.dev"
        try:
            token = o["delivery_token"] or ""
        except Exception:
            token = ""
        try:
            created = o["created_at"] or ""
        except Exception:
            created = ""
        orders.append({
            "code": o["code"],
            "product": pname,
            "amount_pkr": o["amount_pkr"],
            "status": o["status"],
            "created": created,
            "order_url": f"{base}/order.html?code={o['code']}&token={token}" if token else "",
        })
    c.close()
    return 200, {"ok": True, "orders": orders}


# ---------- Admin dashboard + OTP (admin login) ----------
# Admin session: web_sessions row jis ka is_admin=1 HO aur user ka
# is_admin bhi 1 ho. OTP: 6-digit code, ek hi code teen channels pe —
# Telegram DM (ADMIN_ID, bot khud bhejta hai), Twilio SMS (ADMIN_PHONE),
# Resend email (ADMIN_EMAIL). Har channel best-effort: jo available ho
# us pe jata hai, baaki skip. Code kabhi log ya response me nahi aata
# (sirf SHA-256 hash DB me).

_OTP_TTL_S = 600            # code 10 minute valid
_OTP_MAX_SENDS_HOUR = 5     # per admin, per hour
_OTP_MAX_ATTEMPTS = 10      # phir 15 minute lock
_OTP_LOCK_S = 900
_OTP_SEND_RL = {}           # admin_user_id -> (window_start, count)
_OTP_LOCKS = {}             # admin_user_id -> locked_until epoch
_OTP_RL_LOCK = threading.Lock()
_PAID_ORDER_STATUSES = ("zurva_processing", "ahm7_pending", "delivered", "out_of_stock")
_ADMIN_GENERIC_ERROR = "admin verification available nahi hai"


def _twilio_configured():
    """Twilio SMS tab jab SID + token + from number teeno set hon."""
    return bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER)


def _resend_configured():
    return bool(RESEND_API_KEY)


def _smtp_configured():
    """Gmail SMTP tab jab SMTP_USER + SMTP_PASS dono set hon."""
    return bool(SMTP_USER and SMTP_PASS)


def _email_configured():
    """Koi email sender available ho: Resend ya Gmail SMTP."""
    return bool(_resend_configured() or _smtp_configured())


def _otp_configured():
    """Admin OTP tab jab koi ek channel available ho: Twilio (keys set),
    Resend (key set), Gmail SMTP (user + app password set), ya Telegram
    (ADMIN_ID set — bot khud bhejta hai). Twilio Pakistan me trial nahi
    deta, is liye koi ek channel bhi missing ho to 503 nahi — sirf tab
    503 jab kuch bhi available na ho."""
    return bool(_twilio_configured() or _resend_configured()
                or _smtp_configured() or (ADMIN_ID and BOT_TOKEN))


def _otp_send_allowed(admin_id):
    now = time.time()
    with _OTP_RL_LOCK:
        if len(_OTP_SEND_RL) > 2000:
            _OTP_SEND_RL.clear()
        start, count = _OTP_SEND_RL.get(admin_id, (now, 0))
        if now - start >= 3600:
            start, count = now, 0
        count += 1
        _OTP_SEND_RL[admin_id] = (start, count)
        return count <= _OTP_MAX_SENDS_HOUR


def _otp_locked(admin_id):
    until = _OTP_LOCKS.get(admin_id, 0)
    if until and time.time() < until:
        return True
    if until:
        _OTP_LOCKS.pop(admin_id, None)
    return False


def _send_otp_sms(to_phone, code):
    """Twilio SMS. Code kabhi log nahi hota. Returns True/False."""
    try:
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json",
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            data={"To": to_phone, "From": TWILIO_FROM_NUMBER,
                  "Body": f"Pheonix Retail Shop admin code: {code}. 10 minute ke liye valid hai. Kisi ko na dein."},
            timeout=15)
        if r.status_code in (200, 201):
            return True
        log.warning("Twilio OTP SMS failed: HTTP %s", r.status_code)
        return False
    except Exception as e:
        log.warning("Twilio OTP SMS error: %s", e)
        return False


def _send_email_via_resend(to_email, subject, text):
    """Resend se plain email bhejo. Koi code kabhi log nahi hota.
    Returns True/False (HTTP status bhi log me, body kabhi nahi)."""
    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}",
                     "Content-Type": "application/json"},
            json={"from": RESEND_FROM, "to": [to_email],
                  "subject": subject, "text": text},
            timeout=15)
        if r.status_code in (200, 201):
            return True
        log.warning("Resend email failed: HTTP %s", r.status_code)
        return False
    except Exception as e:
        log.warning("Resend email error: %s", e)
        return False


def _send_email_via_smtp(to_email, subject, text):
    """Gmail SMTP se plain email bhejo (smtp.gmail.com:587, STARTTLS).
    App password kabhi log nahi hota. Returns True/False."""
    if not _smtp_configured():
        return False
    try:
        msg = EmailMessage()
        msg["From"] = f"Pheonix Retail Shop <{SMTP_USER}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(text)
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
            server.starttls()
            # Gmail app password 16 chars, groups me spaces ke saath
            # dikhaya jata hai — login se pehle spaces hata do.
            server.login(SMTP_USER, SMTP_PASS.replace(" ", "").strip())
            server.send_message(msg)
        return True
    except Exception as e:
        log.warning("SMTP email failed: %s", e)
        return False


def _send_email(to_email, subject, text):
    """Email bhejo — pehle Resend, fail ho to Gmail SMTP fallback.
    Returns True jab koi ek sender kaam kar jaye, False jab dono fail
    hon ya koi configured na ho. Code kabhi log nahi hota."""
    if _resend_configured() and _send_email_via_resend(to_email, subject, text):
        return True
    if _smtp_configured() and _send_email_via_smtp(to_email, subject, text):
        return True
    return False


def _send_otp_email(to_email, code):
    """Admin OTP email — Resend, fail ho to Gmail SMTP fallback.
    Code kabhi log nahi hota. Returns True/False."""
    return _send_email(
        to_email, "Pheonix Retail Shop — admin login code",
        ("Assalam-o-Alaikum,\n\nAapka Pheonix Retail Shop admin code: "
         f"{code}\nYe code 10 minute ke liye valid hai. Kisi ko na dein.\n\n"
         "— Pheonix Retail Shop"))


def _send_otp_telegram(code):
    """Telegram DM (admin OTP) — bot khud ADMIN_ID ko bhejta hai, seedha
    Bot API se (PTB loop ki zaroorat nahi — HTTP thread se bhi chalta hai).
    Code kabhi log nahi hota. Returns True/False."""
    if not BOT_TOKEN or not ADMIN_ID:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_ID,
                  "text": (f"Pheonix Retail Shop admin code: {code}. "
                           "10 minute ke liye valid hai. Kisi ko na dein.")},
            timeout=15)
        if r.status_code == 200:
            return True
        log.warning("Telegram OTP send failed: HTTP %s", r.status_code)
        return False
    except Exception as e:
        log.warning("Telegram OTP send error: %s", e)
        return False


def _admin_session_user(headers):
    """Bearer admin session token se admin user nikalo.
    Session ka is_admin=1 HONA chahiye AUR user ka is_admin bhi 1 ho.
    Returns (user_row, None) ya (None, (status, error_dict))."""
    token = _bearer_token(headers or {})
    if not token:
        return None, (401, {"ok": False, "error": "unauthorized"})
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    c = db()
    s = c.execute("SELECT * FROM web_sessions WHERE token_hash=?", (token_hash,)).fetchone()
    if not s:
        c.close()
        return None, (401, {"ok": False, "error": "unauthorized"})
    if (s["expires_at"] or "") <= _now_str():
        c.execute("DELETE FROM web_sessions WHERE id=?", (s["id"],))
        c.commit(); c.close()
        return None, (401, {"ok": False, "error": "unauthorized"})
    try:
        sess_admin = bool(s["is_admin"])
    except Exception:
        sess_admin = False
    if not sess_admin:
        c.close()
        return None, (403, {"ok": False, "error": "admin only"})
    u = c.execute("SELECT * FROM web_users WHERE id=?", (s["user_id"],)).fetchone()
    c.close()
    if not u:
        return None, (401, {"ok": False, "error": "unauthorized"})
    try:
        user_admin = bool(u["is_admin"])
    except Exception:
        user_admin = False
    if not user_admin:
        return None, (403, {"ok": False, "error": "admin only"})
    return u, None


def handle_admin_otp_send(data, headers=None, client_ip=""):
    """POST /api/admin/otp/send {identifier, password, purpose}.
    Sirf admin account ke liye: password check + is_admin check ke baad
    ek hi 6-digit code Twilio SMS (ADMIN_PHONE) + Resend email
    (ADMIN_EMAIL) dono pe bhejo. Account exist karta hai ya nahi, password
    ghalat hai ya admin nahi — sab pe ek hi generic 403 (leak nahi)."""
    if not _web_auth(headers or {}):
        return 401, {"ok": False, "error": "unauthorized"}
    if not _otp_configured():
        return 503, {"ok": False, "error": "otp_not_configured"}
    data = data or {}
    ident = str(data.get("identifier") or "").strip()
    password = str(data.get("password") or "")
    purpose = str(data.get("purpose") or "").strip()
    if purpose not in ("signup", "login"):
        return 400, {"ok": False, "error": "purpose ghalat hai"}
    if not ident or not password:
        return 400, {"ok": False, "error": "identifier aur password chahiye"}
    c = db()
    u = _find_web_user_by_identifier(c, ident)
    if not u or not _verify_password(password, u["password_hash"]):
        c.close()
        return 403, {"ok": False, "error": _ADMIN_GENERIC_ERROR}
    try:
        user_admin = bool(u["is_admin"])
    except Exception:
        user_admin = False
    if not user_admin:
        c.close()
        return 403, {"ok": False, "error": _ADMIN_GENERIC_ERROR}
    admin_id = u["id"]
    if not _otp_send_allowed(admin_id):
        c.close()
        return 429, {"ok": False, "error": "bohat zyada koshishein — 1 ghante baad try karein"}
    # Purane unused codes isi purpose ke liye invalid kar do.
    c.execute("UPDATE admin_otps SET used=1 WHERE admin_user_id=? AND purpose=? AND used=0",
              (admin_id, purpose))
    code = str(secrets.randbelow(900000) + 100000)
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    now = datetime.now()
    expires = (now + timedelta(seconds=_OTP_TTL_S)).strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT INTO admin_otps(admin_user_id, purpose, code_hash, expires_at, attempts, used, created_at) "
              "VALUES(?,?,?,?,0,0,?)", (admin_id, purpose, code_hash, expires, _now_str()))
    c.commit(); c.close()
    # Har channel best-effort aur alag: Twilio sirf jab keys set hon
    # (Pakistan me trial nahi — keys missing hon to skip, fail nahi),
    # email (Resend, fail ho to Gmail SMTP fallback) sirf jab koi email
    # sender configured ho, Telegram hamesha (admin ke liye).
    channels = []
    if _twilio_configured() and _send_otp_sms(ADMIN_PHONE, code):
        channels.append("sms")
    if _email_configured() and _send_otp_email(ADMIN_EMAIL, code):
        channels.append("email")
    if _send_otp_telegram(code):
        channels.append("telegram")
    if not channels:
        # Dono channels fail — code bekaar hai, delete kar do taake retry fresh ho.
        _log_issue(ident, u["name"] if u else "", "admin_otp_failed",
                   "Admin OTP could not be sent via any channel")
        c = db()
        c.execute("DELETE FROM admin_otps WHERE admin_user_id=? AND purpose=? AND code_hash=? AND used=0",
                  (admin_id, purpose, code_hash))
        c.commit(); c.close()
        return 502, {"ok": False, "error": "OTP bheja nahi ja saka — dobara try karein"}
    log.info("Admin OTP send: purpose=%s channels=%s", purpose, ",".join(channels))
    return 200, {"ok": True, "channels": channels, "expires_in": _OTP_TTL_S}


def handle_admin_otp_verify(data, headers=None):
    """POST /api/admin/otp/verify {identifier, purpose, code}.
    Code theek ho to admin session token wapas do. Ghalat code pe
    generic 401; 10 ghalat koshishon ke baad 15 minute lock (429)."""
    if not _web_auth(headers or {}):
        return 401, {"ok": False, "error": "unauthorized"}
    data = data or {}
    ident = str(data.get("identifier") or "").strip()
    purpose = str(data.get("purpose") or "").strip()
    code = str(data.get("code") or "").strip()
    if purpose not in ("signup", "login") or not ident or not re.match(r"^\d{6}$", code):
        return 400, {"ok": False, "error": "identifier, purpose aur 6-digit code chahiye"}
    c = db()
    u = _find_web_user_by_identifier(c, ident)
    if not u:
        c.close()
        return 401, {"ok": False, "error": "code ghalat ya expire ho gaya hai"}
    try:
        user_admin = bool(u["is_admin"])
    except Exception:
        user_admin = False
    if not user_admin:
        c.close()
        return 401, {"ok": False, "error": "code ghalat ya expire ho gaya hai"}
    admin_id = u["id"]
    if _otp_locked(admin_id):
        c.close()
        return 429, {"ok": False, "error": "bohat zyada ghalat koshishein — 15 minute baad try karein"}
    row = c.execute(
        "SELECT * FROM admin_otps WHERE admin_user_id=? AND purpose=? AND used=0 "
        "ORDER BY id DESC LIMIT 1", (admin_id, purpose)).fetchone()
    if not row or (row["expires_at"] or "") <= _now_str():
        c.close()
        return 401, {"ok": False, "error": "code ghalat ya expire ho gaya hai"}
    want = hashlib.sha256(code.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(want, row["code_hash"] or ""):
        attempts = (row["attempts"] or 0) + 1
        c.execute("UPDATE admin_otps SET attempts=? WHERE id=?", (attempts, row["id"]))
        c.commit()
        if attempts >= _OTP_MAX_ATTEMPTS:
            _OTP_LOCKS[admin_id] = time.time() + _OTP_LOCK_S
        c.close()
        return 401, {"ok": False, "error": "code ghalat ya expire ho gaya hai"}
    c.execute("UPDATE admin_otps SET used=1 WHERE id=?", (row["id"],))
    c.commit(); c.close()
    token = _create_web_session(admin_id, is_admin=1)
    log.info("Admin OTP verify: purpose=%s result=ok", purpose)
    return 200, {"ok": True, "token": token, "user": _public_web_user(u)}


# ---------- User email verification (Resend) ----------
# Signup ke baad user ko 6-digit code email se jata hai. Resend free tier
# reportedly sirf account owner ke apne email pe bhejta hai jab tak domain
# verify na ho — is liye send fail hona signup/login ko KABHI block nahi
# karta: account unverified banta hai, login chalta hai, profile me banner
# + resend button dikhta hai. Verification enforce tab hogi jab send
# theek se kaam karne lage (domain verify hone ke baad).
_EMAIL_OTP_SUBJECT = "Pheonix Retail Shop — email verification code"


def _issue_email_otp(user_id):
    """User ke liye naya email OTP row banao (purane unused invalid).
    Returns plaintext code (sirf caller ko — DB me sirf hash), ya None
    jab rate limit (5/hour) khatam ho."""
    if not _otp_send_allowed(f"email:{user_id}"):
        return None
    c = db()
    c.execute("UPDATE user_email_otps SET used=1 WHERE user_id=? AND used=0", (user_id,))
    code = str(secrets.randbelow(900000) + 100000)
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    expires = (datetime.now() + timedelta(seconds=_OTP_TTL_S)).strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT INTO user_email_otps(user_id, code_hash, expires_at, attempts, used, created_at) "
              "VALUES(?,?,?,0,0,?)", (user_id, code_hash, expires, _now_str()))
    c.commit(); c.close()
    return code


def handle_auth_verify_email_send(headers=None):
    """POST /api/auth/verify-email/send (Bearer session token).
    Logged-in user ko uske apne email pe verification code bhejo."""
    u, err = _auth_session_user(headers or {})
    if err:
        return err
    try:
        if u["email_verified"]:
            return 200, {"ok": True, "already_verified": True}
    except Exception:
        pass
    if not _email_configured():
        return 503, {"ok": False, "error": "email_not_configured"}
    code = _issue_email_otp(u["id"])
    if not code:
        return 429, {"ok": False, "error": "bohat zyada koshishein — 1 ghante baad try karein"}
    sent = _send_email(
        u["email"], _EMAIL_OTP_SUBJECT,
        ("Assalam-o-Alaikum,\n\nAapka Pheonix Retail Shop email verification code: "
         f"{code}\nYe code 10 minute ke liye valid hai. Kisi ko na dein.\n\n"
         "— Pheonix Retail Shop"))
    if not sent:
        _log_issue(u["email"], u["name"], "email_otp_failed",
                   "Email verification code could not be sent")
        return 502, {"ok": False, "error": "email bheja nahi ja saka — dobara try karein"}
    log.info("Email verify send: user_id=%s result=ok", u["id"])
    return 200, {"ok": True, "channels": ["email"], "expires_in": _OTP_TTL_S}


def handle_auth_verify_email_confirm(data, headers=None):
    """POST /api/auth/verify-email/confirm {code} (Bearer session token).
    Code theek ho to web_users.email_verified=1. Ghalat code pe generic
    401; 10 ghalat koshishon ke baad 15 minute lock (429)."""
    u, err = _auth_session_user(headers or {})
    if err:
        return err
    data = data or {}
    code = str(data.get("code") or "").strip()
    if not re.match(r"^\d{6}$", code):
        return 400, {"ok": False, "error": "6-digit code chahiye"}
    user_id = u["id"]
    lock_key = f"email:{user_id}"
    if _otp_locked(lock_key):
        return 429, {"ok": False, "error": "bohat zyada ghalat koshishein — 15 minute baad try karein"}
    c = db()
    row = c.execute(
        "SELECT * FROM user_email_otps WHERE user_id=? AND used=0 "
        "ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
    if not row or (row["expires_at"] or "") <= _now_str():
        c.close()
        return 401, {"ok": False, "error": "code ghalat ya expire ho gaya hai"}
    want = hashlib.sha256(code.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(want, row["code_hash"] or ""):
        attempts = (row["attempts"] or 0) + 1
        c.execute("UPDATE user_email_otps SET attempts=? WHERE id=?", (attempts, row["id"]))
        c.commit()
        if attempts >= _OTP_MAX_ATTEMPTS:
            _OTP_LOCKS[lock_key] = time.time() + _OTP_LOCK_S
        c.close()
        return 401, {"ok": False, "error": "code ghalat ya expire ho gaya hai"}
    c.execute("UPDATE user_email_otps SET used=1 WHERE id=?", (row["id"],))
    c.execute("UPDATE web_users SET email_verified=1 WHERE id=?", (user_id,))
    c.commit(); c.close()
    log.info("Email verify confirm: user_id=%s result=ok", user_id)
    return 200, {"ok": True, "email_verified": True}


def _admin_order_buyer(c, o):
    """Order row -> buyer {name, email, phone, source}."""
    try:
        ch = (o["channel"] or "telegram") or "telegram"
    except Exception:
        ch = "telegram"
    if ch == "web":
        email = ""
        try:
            email = (o["web_contact"] or "").strip()
        except Exception:
            email = ""
        w = None
        if email:
            w = c.execute("SELECT name, email, phone FROM web_users WHERE email=?", (email,)).fetchone()
        if w:
            return {"name": w["name"], "email": w["email"], "phone": w["phone"] or "", "source": "web"}
        return {"name": "", "email": email, "phone": "", "source": "web"}
    try:
        uname = o["username"] or ""
    except Exception:
        uname = ""
    return {"name": uname, "email": "", "phone": "", "source": ch}


def _admin_order_product(c, o):
    """Product naam (+N more jahan cart me kayi items hon)."""
    try:
        p = c.execute("SELECT name FROM products WHERE id=?", (o["product_id"],)).fetchone()
    except Exception:
        p = None
    pname = p["name"] if p else ""
    try:
        lines = json.loads(o["items_json"] or "[]")
        n = len(lines) if isinstance(lines, list) else 0
    except Exception:
        n = 0
    if n > 1 and pname:
        pname = f"{pname} (+{n - 1} more)"
    return pname


def _admin_delivery_label(status):
    return {"delivered": "Delivered",
            "zurva_processing": "Paid — delivering",
            "ahm7_pending": "Paid — waiting for supplier",
            "pending": "Unpaid",
            "out_of_stock": "Paid — stock issue",
            "rejected": "Rejected"}.get(status or "", status or "")


def _admin_order_row(c, o):
    try:
        ch = (o["channel"] or "telegram") or "telegram"
    except Exception:
        ch = "telegram"
    return {"code": o["code"], "created_at": o["created_at"] or "",
            "product": _admin_order_product(c, o),
            "product_id": o["product_id"],
            "qty": o["qty"] or 1,
            "amount_pkr": o["amount_pkr"] or 0,
            "amount_text": o["amount_text"] or "",
            "payment_method": o["payment_method"],
            "status": o["status"],
            "delivery": _admin_delivery_label(o["status"]),
            "channel": ch,
            "buyer": _admin_order_buyer(c, o)}


def handle_admin_stats(headers=None):
    """GET /api/admin/stats — Bearer admin token."""
    u, err = _admin_session_user(headers or {})
    if err:
        return err
    c = db()
    total = c.execute("SELECT COUNT(*) n FROM orders").fetchone()["n"]
    paid = c.execute("SELECT COUNT(*) n FROM orders WHERE status IN (?,?,?,?)",
                     _PAID_ORDER_STATUSES).fetchone()["n"]
    pending = c.execute("SELECT COUNT(*) n FROM orders WHERE status='pending'").fetchone()["n"]
    rev = c.execute("SELECT COALESCE(SUM(amount_pkr),0) s FROM orders WHERE status IN (?,?,?,?)",
                    _PAID_ORDER_STATUSES).fetchone()["s"]
    users = c.execute("SELECT COUNT(*) n FROM web_users").fetchone()["n"]
    today = datetime.now().strftime("%Y-%m-%d")
    today_n = c.execute("SELECT COUNT(*) n FROM orders WHERE substr(created_at,1,10)=?",
                        (today,)).fetchone()["n"]
    # Dashboard v2: visits + open issues (stored open + computed stuck).
    _now_utc = datetime.now(timezone.utc)
    _today_start = int(datetime(_now_utc.year, _now_utc.month, _now_utc.day,
                                tzinfo=timezone.utc).timestamp())
    _now_ts = int(time.time())
    visits_today = c.execute("SELECT COUNT(*) n FROM site_visits WHERE ts>=?",
                             (_today_start,)).fetchone()["n"]
    visits_7d = c.execute("SELECT COUNT(*) n FROM site_visits WHERE ts>=?",
                          (_now_ts - 7 * 86400,)).fetchone()["n"]
    stored_open = c.execute("SELECT COUNT(*) n FROM issues WHERE status='open'").fetchone()["n"]
    open_issues = stored_open + len(_computed_stuck_issues(c))
    c.close()
    return 200, {"ok": True, "stats": {"total_orders": total, "paid_orders": paid,
                                        "pending_orders": pending,
                                        "total_revenue_pkr": rev,
                                        "total_users": users, "today_orders": today_n,
                                        "visits_today": visits_today,
                                        "visits_7d": visits_7d,
                                        "open_issues": open_issues}}


def handle_admin_orders(headers=None, query=None):
    """GET /api/admin/orders?status=&method=&q=&limit=&offset= — Bearer admin token.
    Sab orders (web + Telegram), newest first. q code/username/web_contact me dhoondta hai."""
    u, err = _admin_session_user(headers or {})
    if err:
        return err
    query = query or {}
    status = (query.get("status") or [""])[0].strip()
    method = (query.get("method") or [""])[0].strip()
    q = (query.get("q") or [""])[0].strip()
    try:
        limit = max(1, min(100, int((query.get("limit") or ["50"])[0])))
    except Exception:
        limit = 50
    try:
        offset = max(0, int((query.get("offset") or ["0"])[0]))
    except Exception:
        offset = 0
    where, args = [], []
    if status:
        where.append("status=?"); args.append(status)
    if method:
        where.append("LOWER(payment_method)=LOWER(?)"); args.append(method)
    if q:
        where.append("(code LIKE ? OR username LIKE ? OR web_contact LIKE ?)")
        args += [f"%{q}%", f"%{q}%", f"%{q}%"]
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    c = db()
    total = c.execute(f"SELECT COUNT(*) n FROM orders{wsql}", args).fetchone()["n"]
    rows = c.execute(f"SELECT * FROM orders{wsql} ORDER BY id DESC LIMIT ? OFFSET ?",
                     args + [limit, offset]).fetchall()
    orders = [_admin_order_row(c, o) for o in rows]
    c.close()
    return 200, {"ok": True, "orders": orders, "total": total,
                 "limit": limit, "offset": offset}


def handle_admin_order_detail(headers=None, code=""):
    """GET /api/admin/order/:code — Bearer admin token. Full detail.
    Delivery token kabhi nahi milta; delivered account credentials
    (login/password) admin ko milte hain (ye shop ka apna data hai)."""
    u, err = _admin_session_user(headers or {})
    if err:
        return err
    code = (code or "").strip()
    if not code:
        return 404, {"ok": False, "error": "order nahi mila"}
    c = db()
    o = c.execute("SELECT * FROM orders WHERE code=?", (code,)).fetchone()
    if not o:
        c.close()
        return 404, {"ok": False, "error": "order nahi mila"}
    out = _admin_order_row(c, o)
    try:
        lines = json.loads(o["items_json"] or "[]")
        if not isinstance(lines, list):
            lines = []
    except Exception:
        lines = []
    items = []
    for ln in lines:
        try:
            pid = int((ln or {}).get("product_id") or 0)
            qty = int((ln or {}).get("qty") or 1)
        except Exception:
            continue
        p = c.execute("SELECT name FROM products WHERE id=?", (pid,)).fetchone()
        items.append({"product_id": pid, "product": p["name"] if p else "",
                      "qty": qty})
    out["items"] = items
    out["proof"] = o["proof"] or ""
    out["discount_pkr"] = o["discount_pkr"] or 0
    out["coupon_code"] = o["coupon_code"] or ""
    out["account_id"] = o["account_id"]
    try:
        out["ahm7_order_id"] = o["ahm7_order_id"] or ""
    except Exception:
        out["ahm7_order_id"] = ""
    try:
        out["ahm7_note"] = o["ahm7_note"] or ""
    except Exception:
        out["ahm7_note"] = ""
    out["credentials"] = None
    if o["account_id"]:
        a = c.execute("SELECT login, password FROM accounts WHERE id=?",
                      (o["account_id"],)).fetchone()
        if a:
            out["credentials"] = {"login": a["login"], "password": a["password"]}
    c.close()
    return 200, {"ok": True, "order": out}


def handle_admin_users(headers=None, query=None):
    """GET /api/admin/users?q=&limit=&offset= — Bearer admin token.
    Web users + unke web orders ki count."""
    u, err = _admin_session_user(headers or {})
    if err:
        return err
    query = query or {}
    q = (query.get("q") or [""])[0].strip()
    try:
        limit = max(1, min(100, int((query.get("limit") or ["50"])[0])))
    except Exception:
        limit = 50
    try:
        offset = max(0, int((query.get("offset") or ["0"])[0]))
    except Exception:
        offset = 0
    where, args = "", []
    if q:
        where = " WHERE (name LIKE ? OR email LIKE ? OR phone LIKE ?)"
        args = [f"%{q}%", f"%{q}%", f"%{q}%"]
    c = db()
    total = c.execute(f"SELECT COUNT(*) n FROM web_users{where}", args).fetchone()["n"]
    rows = c.execute(f"SELECT * FROM web_users{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                     args + [limit, offset]).fetchall()
    users = []
    for w in rows:
        n = c.execute("SELECT COUNT(*) n FROM orders WHERE channel='web' AND web_contact=?",
                      (w["email"],)).fetchone()["n"]
        users.append({"id": w["id"], "name": w["name"], "email": w["email"],
                      "phone": w["phone"] or "", "created_at": w["created_at"] or "",
                      "is_admin": bool(w["is_admin"]) if "is_admin" in w.keys() else False,
                      "orders": n})
    c.close()
    return 200, {"ok": True, "users": users, "total": total,
                 "limit": limit, "offset": offset}


# ---------- Dashboard v2: site visits + issues ----------
# site_visits: public page-view counter (no auth, no IP stored).
# issues: best-effort failure log (email/admin OTP, payment, login) +
# computed "order_stuck" rows derived from orders at read time.

_VISIT_RL = {}
_VISIT_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_STUCK_ORDER_STATUSES = ("zurva_processing", "paid_pending")


def _log_issue(user_email, user_name, kind, detail):
    """Best-effort insert into issues — kabhi main flow nahi torta.
    Kabhi OTP/code/password log nahi karta (callers sirf safe detail bhejte hain)."""
    try:
        c = db()
        c.execute("INSERT INTO issues(user_email, user_name, kind, detail, ts, status) "
                  "VALUES(?,?,?,?,?, 'open')",
                  (str(user_email or "")[:120], str(user_name or "")[:120],
                   str(kind or "")[:64], str(detail or "")[:500], int(time.time())))
        c.commit(); c.close()
    except Exception:
        pass


def _computed_stuck_issues(c):
    """Orders zurva_processing / paid_pending me 1 ghante se zyada —
    read time pe derive hote hain, store NAHI hote. id = -order_id."""
    cutoff = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        rows = c.execute(
            "SELECT * FROM orders WHERE status IN (?,?) AND created_at <> '' "
            "AND created_at < ? ORDER BY id DESC",
            (_STUCK_ORDER_STATUSES[0], _STUCK_ORDER_STATUSES[1], cutoff)).fetchall()
    except Exception:
        return []
    out = []
    for o in rows:
        try:
            ts = int(datetime.strptime(o["created_at"], "%Y-%m-%d %H:%M:%S").timestamp())
        except Exception:
            ts = int(time.time()) - 3600
        try:
            ch = (o["channel"] or "telegram") if "channel" in o.keys() else "telegram"
        except Exception:
            ch = "telegram"
        email = (o["web_contact"] or "") if ch == "web" else ""
        try:
            name = o["username"] or ""
        except Exception:
            name = ""
        out.append({"id": -o["id"], "user_email": email, "user_name": name,
                    "kind": "order_stuck",
                    "detail": f"Order {o['code']} stuck in status {o['status']} "
                              f"for over 1 hour (Rs {o['amount_pkr'] or 0})",
                    "ts": ts, "status": "open", "source": "computed"})
    return out


def handle_track_visit(data, headers=None, client_ip=""):
    """POST /api/track/visit {page, session} — public, no auth.
    Rate limit 60/hour per client IP (in-memory). IP store NAHI hota."""
    if _auth_rate_limited(_VISIT_RL, client_ip, 3600, 60):
        return 429, {"ok": False, "error": "too many visits — thodi der me dobara try karein"}
    data = data or {}
    page = data.get("page")
    session = data.get("session")
    if not isinstance(page, str) or not page.startswith("/") or len(page) > 200:
        return 400, {"ok": False, "error": "page '/' se shuru hona chahiye (max 200 chars)"}
    if not isinstance(session, str) or not _VISIT_SESSION_RE.match(session):
        return 400, {"ok": False, "error": "session token ghalat hai"}
    c = db()
    c.execute("INSERT INTO site_visits(session_id, page, ts) VALUES(?,?,?)",
              (session, page, int(time.time())))
    c.commit(); c.close()
    return 200, {"ok": True}


def handle_admin_visits(headers=None):
    """GET /api/admin/visits — Bearer admin token. 'Today' = current UTC day;
    uniques = distinct session_id. by_page = top 10 (last 30d)."""
    u, err = _admin_session_user(headers or {})
    if err:
        return err
    now_utc = datetime.now(timezone.utc)
    now_ts = int(time.time())
    today_start = int(datetime(now_utc.year, now_utc.month, now_utc.day,
                               tzinfo=timezone.utc).timestamp())
    start_7d = now_ts - 7 * 86400
    start_30d = now_ts - 30 * 86400
    c = db()
    today = c.execute("SELECT COUNT(*) n FROM site_visits WHERE ts>=?",
                      (today_start,)).fetchone()["n"]
    last7d = c.execute("SELECT COUNT(*) n FROM site_visits WHERE ts>=?",
                       (start_7d,)).fetchone()["n"]
    last30d = c.execute("SELECT COUNT(*) n FROM site_visits WHERE ts>=?",
                        (start_30d,)).fetchone()["n"]
    unique_today = c.execute("SELECT COUNT(DISTINCT session_id) n FROM site_visits "
                             "WHERE ts>=?", (today_start,)).fetchone()["n"]
    unique_7d = c.execute("SELECT COUNT(DISTINCT session_id) n FROM site_visits "
                          "WHERE ts>=?", (start_7d,)).fetchone()["n"]
    unique_30d = c.execute("SELECT COUNT(DISTINCT session_id) n FROM site_visits "
                           "WHERE ts>=?", (start_30d,)).fetchone()["n"]
    by_page = [{"page": r["page"], "count": r["c"]} for r in c.execute(
        "SELECT page, COUNT(*) c FROM site_visits WHERE ts>=? "
        "GROUP BY page ORDER BY c DESC, page ASC LIMIT 10", (start_30d,)).fetchall()]
    # hourly24: last 24 hour-buckets, oldest -> newest.
    cur_hour = (now_ts // 3600) * 3600
    hour_counts = {}
    for r in c.execute("SELECT ts FROM site_visits WHERE ts>=?",
                       (now_ts - 24 * 3600,)).fetchall():
        b = (r["ts"] // 3600) * 3600
        hour_counts[b] = hour_counts.get(b, 0) + 1
    hourly24 = [hour_counts.get(cur_hour - (23 - i) * 3600, 0) for i in range(24)]
    # daily30: last 30 UTC days, oldest -> newest.
    day_counts = {}
    for r in c.execute("SELECT ts FROM site_visits WHERE ts>=?",
                       (start_30d,)).fetchall():
        b = (r["ts"] // 86400) * 86400
        day_counts[b] = day_counts.get(b, 0) + 1
    daily30 = [day_counts.get(today_start - (29 - i) * 86400, 0) for i in range(30)]
    c.close()
    return 200, {"ok": True, "today": today, "last7d": last7d, "last30d": last30d,
                 "unique_today": unique_today, "unique_7d": unique_7d,
                 "unique_30d": unique_30d, "by_page": by_page,
                 "hourly24": hourly24, "daily30": daily30}


def handle_admin_issues(headers=None, query=None):
    """GET /api/admin/issues?kind=&status=&q= — Bearer admin token.
    Stored rows + computed order_stuck rows, newest first. counts/open
    poori (unfiltered) list pe hain; filters sirf list pe lagte hain."""
    u, err = _admin_session_user(headers or {})
    if err:
        return err
    query = query or {}
    kind_f = (query.get("kind") or [""])[0].strip()
    status_f = (query.get("status") or [""])[0].strip()
    q_f = (query.get("q") or [""])[0].strip().lower()
    c = db()
    merged = []
    for r in c.execute("SELECT * FROM issues ORDER BY id DESC").fetchall():
        merged.append({"id": r["id"], "user_email": r["user_email"] or "",
                       "user_name": r["user_name"] or "", "kind": r["kind"] or "",
                       "detail": r["detail"] or "", "ts": r["ts"] or 0,
                       "status": r["status"] or "open", "source": "stored"})
    merged += _computed_stuck_issues(c)
    c.close()
    counts = {}
    open_n = 0
    for it in merged:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
        if it["status"] == "open":
            open_n += 1
    if kind_f:
        merged = [it for it in merged if it["kind"] == kind_f]
    if status_f:
        merged = [it for it in merged if it["status"] == status_f]
    if q_f:
        merged = [it for it in merged
                  if q_f in (it["user_email"] or "").lower()
                  or q_f in (it["user_name"] or "").lower()
                  or q_f in (it["detail"] or "").lower()]
    merged.sort(key=lambda it: (it["ts"], it["id"]), reverse=True)
    return 200, {"ok": True, "issues": merged, "counts": counts, "open": open_n}


def handle_admin_issue_resolve(headers=None, issue_id=""):
    """POST /api/admin/issues/:id/resolve — Bearer admin token.
    Sirf stored rows resolve hote hain; computed rows (id < 0) -> 400."""
    u, err = _admin_session_user(headers or {})
    if err:
        return err
    try:
        iid = int(str(issue_id or "").strip())
    except Exception:
        return 400, {"ok": False, "error": "issue id ghalat hai"}
    if iid < 0:
        return 400, {"ok": False, "error": "computed issue resolve nahi ho sakta"}
    if iid == 0:
        return 400, {"ok": False, "error": "issue id ghalat hai"}
    c = db()
    cur = c.execute("UPDATE issues SET status='resolved' WHERE id=?", (iid,))
    c.commit()
    if cur.rowcount == 0:
        c.close()
        return 404, {"ok": False, "error": "issue nahi mila"}
    c.close()
    return 200, {"ok": True}


def ahm7_place_order(product_id, customer_ref, quantity=1):
    """AHM7 POST /v1/orders (currency pkr, Idempotency-Key = customer_ref —
    retry kabhi double-buy nahi karta). quantity: usi product ki kitni
    units (1–100). Cart orders me har distinct product ki apni call hoti
    hai, is liye customer_ref har line ke liye alag hona chahiye
    (maslan f"{order_code}-{pid}"). Returns (order_dict, None)
    ya (None, error_string). API-level failures kabhi raise nahi karte."""
    if not AHM7_API_KEY:
        return None, "AHM7_API_KEY set nahi hai (Botkeep env me add karein)"
    try:
        quantity = int(quantity)
    except Exception:
        quantity = 1
    quantity = max(1, min(100, quantity))
    try:
        r = requests.post(
            f"{AHM7_BASE_URL}/orders",
            json={"productId": product_id, "quantity": quantity, "currency": "pkr",
                  "customerRef": customer_ref},
            headers={"X-Api-Key": AHM7_API_KEY, "Idempotency-Key": customer_ref,
                     "Content-Type": "application/json"},
            timeout=30)
    except Exception as e:
        log.exception("AHM7 order error")
        return None, f"AHM7 se rabta nahi ho saka: {e}"
    try:
        d = r.json()
    except Exception:
        return None, f"AHM7 ka jawab samajh nahi aaya (HTTP {r.status_code})"
    if r.status_code == 201 and isinstance(d, dict) and d.get("ok") and isinstance(d.get("order"), dict):
        return d["order"], None
    msg = ""
    if isinstance(d, dict):
        msg = str(d.get("message") or d.get("error") or d)
        if "needed" in d:
            msg += f" (needed: {d.get('needed')}, balances: {d.get('balances')})"
    return None, f"AHM7 error HTTP {r.status_code}: {msg[:300] or 'unknown'}"

def _ahm7_mark_pending(order_id, note):
    """Paid order ko manual/retry queue me daalo — kabhi lose nahi karna."""
    c = db()
    c.execute("UPDATE orders SET status='ahm7_pending', ahm7_note=? WHERE id=?",
              (str(note)[:500], order_id))
    c.commit(); c.close()

def fulfill_web_order(order_id):
    """Paid web order -> AHM7 auto-buy -> credentials order row me save.
    Delivery website ke order page pe hoti hai (GET /api/order/<code> +
    delivery token) — Telegram DM NAHI (same-channel rule).
    Cart orders me items_json ki har line alag AHM7 buy hoti hai
    (Idempotency-Key = f"{code}-{pid}", is liye retry kabhi double-buy
    nahi karta). Koi line fail ho to poora order 'ahm7_pending' me rehta
    hai — pehle se khareedi lines ki credentials save rehti hain aur
    retry sirf baaki lines khareedta hai. Returns (True, msg) jab
    delivered, (False, reason) jab paid-pending. Business failures kabhi
    raise nahi karte — order hamesha 'ahm7_pending' me safe rehta hai."""
    c = db()
    o = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    c.close()
    if not o:
        return False, "order nahi mila"
    if o["status"] == "delivered":
        return True, "already delivered"
    if o["status"] not in ("zurva_processing", "ahm7_pending"):
        return False, f"order status {o['status']} hai — fulfillment nahi ho sakti"
    # --- Lines nikalo: cart items_json, warna legacy single product_id.
    try:
        lines = json.loads(o["items_json"] or "[]")
    except Exception:
        lines = []
    if not isinstance(lines, list) or not lines:
        lines = [{"pid": o["product_id"], "qty": o["qty"] or 1}]
    norm = []
    for ln in lines:
        try:
            pid = int((ln or {}).get("pid") or (ln or {}).get("product_id") or 0)
            qty = int((ln or {}).get("qty") or 1)
        except Exception:
            continue
        if pid > 0:
            norm.append({"pid": pid, "qty": max(1, min(100, qty))})
    if not norm:
        note = "order me koi product line nahi — manual handling"
        _ahm7_mark_pending(order_id, note)
        return False, note
    # --- Pehle se khareedi lines (creds me pid tag hota hai).
    try:
        stored_creds = json.loads(o["ahm7_credentials"] or "[]")
    except Exception:
        stored_creds = []
    if not isinstance(stored_creds, list):
        stored_creds = []
    try:
        stored_id = o["ahm7_order_id"] or ""
    except Exception:
        stored_id = ""
    bought_pids = {cr.get("pid") for cr in stored_creds
                   if isinstance(cr, dict) and isinstance(cr.get("pid"), int)}
    # Legacy single-order creds (pid tag nahi) — dobara buy nahi karna.
    if stored_id and stored_creds and len(norm) == 1 and not bought_pids:
        c = db(); c.execute("UPDATE orders SET status='delivered' WHERE id=?", (order_id,)); c.commit(); c.close()
        return True, "already bought — credentials order page pe available (dobara buy nahi hua)"
    combined = list(stored_creds)
    ahm7_ids = [s for s in (stored_id.split(",") if stored_id else []) if s]
    failures = []
    mapping_all = load_ahm7_mapping()
    for ln in norm:
        pid, qty = ln["pid"], ln["qty"]
        if pid in bought_pids:
            continue
        c = db()
        prod = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        c.close()
        if not prod:
            failures.append(f"product nahi mila (id={pid})")
            continue
        mapping = mapping_all.get(prod["name"])
        if not mapping or not mapping.get("ahm7_product_id"):
            failures.append(f"AHM7 mapping nahi hai ({prod['name']})")
            continue
        cost = mapping.get("cost_pkr")
        site_price = mapping.get("site_price_pkr") or 0
        if cost is None or cost * qty >= site_price * qty:
            failures.append(
                f"margin guard: {prod['name']} cost Rs {cost} >= site price Rs {site_price} — auto-buy nahi hua")
            continue
        order_obj, err = ahm7_place_order(mapping["ahm7_product_id"], f"{o['code']}-{pid}",
                                          quantity=qty)
        if err:
            failures.append(f"AHM7 buy fail ({prod['name']}): {err}")
            continue
        creds = order_obj.get("credentials") or []
        if not creds:
            failures.append(f"AHM7 buy OK ({order_obj.get('orderId')}) lekin credentials khaali mile ({prod['name']})")
            continue
        ahm7_ids.append(str(order_obj.get("orderId") or ""))
        for cred in creds:
            if isinstance(cred, dict):
                tagged = dict(cred)
                tagged["pid"] = pid
                tagged["label"] = prod["name"]
                combined.append(tagged)
            else:
                combined.append({"pid": pid, "label": prod["name"], "login": str(cred)})
    if failures:
        note = "; ".join(failures)[:500]
        c = db()
        c.execute("UPDATE orders SET ahm7_order_id=?, ahm7_credentials=?, ahm7_note=?, status='ahm7_pending' WHERE id=?",
                  (",".join(ahm7_ids), json.dumps(combined, ensure_ascii=False), note, order_id))
        c.commit(); c.close()
        _zurva_send(ADMIN_ID, f"⚠️ Web order {o['code']} paid-pending: {note}. Manual deliver karein ya retry karein.")
        return False, note
    c = db()
    c.execute("UPDATE orders SET ahm7_order_id=?, ahm7_credentials=?, ahm7_note=?, status='delivered' WHERE id=?",
              (",".join(ahm7_ids), json.dumps(combined, ensure_ascii=False),
               f"AHM7 {','.join(ahm7_ids)}", order_id))
    c.commit(); c.close()
    return True, f"delivered via AHM7 {','.join(ahm7_ids)} — credentials order page pe available"

def handle_web_checkout(data, headers=None):
    """POST /api/web-checkout (Worker se aata hai). Returns (http_status, dict).
    Website order banata hai (channel='web', uid=0 — koi Telegram identity
    nahi) + Zurvapay initiate karta hai. Delivery website ke order page pe
    hoti hai: order_code + random delivery token milte hain; payment ke
    baad customer order.html?code=...&token=... pe credentials dekhta hai.
    Do shapes: single {product_id} ya cart {items: [{product_id, qty}]}.
    Amount hamesha server-side mapping ki site price hoti hai — client jo
    bhejta hai us pe bharosa nahi (tamper-proof). contact optional hai
    (email ya Telegram) — sirf manual follow-up ke liye, kabhi required nahi."""
    if not _web_auth(headers or {}):
        return 401, {"ok": False, "error": "unauthorized (WEB_API_SECRET ghalat ya set nahi)"}
    data = data or {}
    log.info("Web checkout request: product_id=%s items=%s has_contact=%s",
             data.get("product_id"), isinstance(data.get("items"), list),
             bool(str(data.get("contact") or "").strip()))
    # --- Items normalize karo: cart [{product_id, qty}] ya single product_id.
    raw_items = data.get("items")
    lines = []
    if isinstance(raw_items, list) and raw_items:
        if len(raw_items) > 26:
            return 400, {"ok": False, "error": "cart me zyada items hain"}
        merged = {}
        for it in raw_items:
            try:
                pid = int((it or {}).get("product_id"))
                _q = (it or {}).get("qty")
                qty = 1 if _q is None else int(_q)
            except Exception:
                return 400, {"ok": False, "error": "cart items ghalat hain"}
            if pid <= 0 or qty < 1 or qty > 10:
                return 400, {"ok": False, "error": "cart items ghalat hain"}
            merged[pid] = merged.get(pid, 0) + qty
        lines = [{"pid": pid, "qty": min(10, q)} for pid, q in merged.items()]
    else:
        try:
            product_id = int(data.get("product_id"))
        except Exception:
            return 400, {"ok": False, "error": "product_id ghalat hai"}
        if product_id <= 0:
            return 400, {"ok": False, "error": "product_id ghalat hai"}
        lines = [{"pid": product_id, "qty": 1}]
    contact = str(data.get("contact") or "").strip()[:120]
    # --- Har line validate karo + total server-side nikalo.
    mapping_all = load_ahm7_mapping()
    total = 0
    labels = []
    for ln in lines:
        c = db(); prod = c.execute("SELECT * FROM products WHERE id=?", (ln["pid"],)).fetchone(); c.close()
        if not prod:
            return 404, {"ok": False, "error": "product nahi mila"}
        mapping = mapping_all.get(prod["name"])
        if not mapping:
            return 400, {"ok": False, "error": f"ye product web checkout pe available nahi hai ({prod['name']})"}
        site_price = mapping.get("site_price_pkr") or 0
        if site_price <= 0:
            return 400, {"ok": False, "error": "product ki price set nahi hai"}
        ln["name"] = prod["name"]
        ln["unit_price"] = site_price
        total += site_price * ln["qty"]
        labels.append(f"{prod['name']} x{ln['qty']}" if ln["qty"] > 1 else prod["name"])
    if total <= 0:
        return 400, {"ok": False, "error": "total price set nahi hai"}
    code = gen_order_code()
    token = _gen_delivery_token()
    oid = create_order(0, contact, [{"pid": ln["pid"], "qty": ln["qty"]} for ln in lines],
                       "zurvapay", f"Rs {total} via Zurvapay (web)", "zurvapay",
                       total, code, "", 0, channel="web")
    success_base = str(data.get("success_base") or "").strip().rstrip("/")
    if success_base not in ("https://retailshop.pages.dev", "https://retail-shop.online"):
        success_base = "https://retailshop.pages.dev"
    c = db()
    c.execute("UPDATE orders SET delivery_token=?, web_contact=?, success_base=? WHERE id=?",
              (token, contact, success_base, oid))
    c.commit(); c.close()
    order_url = f"{success_base}/order.html?code={code}&token={token}"
    details = f"RetailShop order {code} — " + ", ".join(labels)
    # Zurvapay sirf approved domain (bot server) ki success/cancel URLs
    # accept karta hai — site ka order-page URL yahan NAHI dena, warna
    # "domain not allowed" error aata hai. Bot-server handler phir 302 se
    # customer ko order page pe bhej deta hai (code + token DB se).
    zp_base = _zurvapay_public_base()
    zp_success = f"{zp_base}/zurvapay/success?code={code}" if zp_base else None
    zp_cancel = f"{zp_base}/zurvapay/cancel?code={code}" if zp_base else None
    url, err = zurvapay_initiate(code, total, details[:200],
                                 "RetailShop Customer", "",
                                 success_url=zp_success, cancel_url=zp_cancel)
    if err:
        log.warning("Web checkout %s: Zurvapay initiate failed: %s", code, err)
        _log_issue(contact if "@" in contact else "", "", "payment_failed",
                   f"Zurvapay initiate failed for order {code}: {err}")
        return 502, {"ok": False, "error": f"Payment start nahi ho saki: {err}",
                     "order_code": code, "delivery_token": token, "order_url": order_url}
    # Checkout URL store karo — order page ka "Pay now" button isi se
    # payment dobara kholta hai (popup block hone par safety net).
    c = db()
    c.execute("UPDATE orders SET checkout_url=? WHERE id=?", (url, oid))
    c.commit(); c.close()
    log.info("Web checkout %s: total Rs %s, checkout_url ok", code, total)
    return 200, {"ok": True, "checkout_url": url, "order_code": code,
                 "delivery_token": token, "order_url": order_url}

def handle_ahm7_retry(data, headers=None):
    """POST /api/ahm7-retry — paid-pending web order dobara fulfill karo
    (wallet top-up ke baad). Returns (http_status, dict)."""
    if not _web_auth(headers or {}):
        return 401, {"ok": False, "error": "unauthorized (WEB_API_SECRET ghalat ya set nahi)"}
    code = str((data or {}).get("order_code") or "").strip()
    if not code:
        return 400, {"ok": False, "error": "order_code chahiye"}
    c = db(); o = c.execute("SELECT * FROM orders WHERE code=?", (code,)).fetchone(); c.close()
    if not o:
        return 404, {"ok": False, "error": "order nahi mila"}
    try:
        ch = (o["channel"] or "telegram") if "channel" in o.keys() else "telegram"
    except Exception:
        ch = "telegram"
    if ch != "web":
        return 400, {"ok": False, "error": "ye web order nahi hai"}
    if o["status"] not in ("ahm7_pending", "zurva_processing"):
        return 400, {"ok": False, "error": f"order status {o['status']} hai — retry sirf paid-pending orders pe hota hai"}
    ok, msg = fulfill_web_order(o["id"])
    c = db(); o2 = c.execute("SELECT status FROM orders WHERE id=?", (o["id"],)).fetchone(); c.close()
    return 200, {"ok": ok, "message": msg, "order_code": code,
                 "status": o2["status"] if o2 else o["status"]}

# ---------- Buy flow renders ----------

def summary_text(uid, context):
    lines, subtotal, discount, total = buy_totals(context, uid)
    code = context.user_data.get("buy_code") or gen_order_code()
    context.user_data["buy_code"] = code
    parts = [f"🧾 Order Summary — {code}\n"]
    for l in lines:
        parts.append(t(uid, "sum_line", name=l['product']['name'], qty=l['qty'],
                       line=fmt_money(l['line_pkr'], uid), unit=fmt_money(l['unit_pkr'], uid)))
    parts.append(f"\n{t(uid, 'sum_subtotal', amount=fmt_money(subtotal, uid))}")
    if discount:
        parts.append(t(uid, "sum_coupon", code=context.user_data.get('buy_coupon_code'),
                       pct=FIRST_COUPON_PCT, amount=fmt_money(discount, uid)))
    parts.append(t(uid, "sum_total", amount=fmt_money(total, uid)))
    parts.append(f"\n{t(uid, 'sum_wallet', pkr=get_wallet(uid, 'PKR'), usdt=get_wallet(uid, 'USDT'))}")
    return "\n".join(parts)

def summary_kb(uid, context):
    """Payment order: 1st Zurvapay, 2nd Easypaisa, 3rd JazzCash, 4th USDT.
    (Coupon 7% pehli order pe summary me hi apply hota hai.)"""
    _lines, _sub, _disc, total = buy_totals(context, uid)
    kb = []
    if not context.user_data.get("buy_coupon_code"):
        kb.append([InlineKeyboardButton(t(uid, "sum_coupon_btn"), callback_data="coupon_apply")])
    if zurvapay_enabled():
        kb.append([InlineKeyboardButton(t(uid, "sum_pay_zurva"), callback_data="pay_zurvapay")])
    kb.append([InlineKeyboardButton(t(uid, "sum_pay_easypaisa"), callback_data="pay_easypaisa")])
    kb.append([InlineKeyboardButton(t(uid, "sum_pay_jazzcash"), callback_data="pay_jazzcash")])
    kb.append([InlineKeyboardButton(t(uid, "sum_pay_usdt"), callback_data="pay_usdt")])
    cur = user_currency(uid)
    bal = get_wallet(uid, cur)
    need = total if cur == "PKR" else round(total / USDT_RATE, 2)
    if total > 0 and bal >= need:
        kb.append([InlineKeyboardButton(t(uid, "sum_pay_wallet"), callback_data="pay_wallet")])
    kb.append([InlineKeyboardButton(t(uid, "sum_change_qty"), callback_data="qty_change"),
               InlineKeyboardButton(t(uid, "sum_cancel"), callback_data="cancel_order")])
    return InlineKeyboardMarkup(kb)

async def render_summary(q, context):
    uid = q.from_user.id
    await q.edit_message_text(summary_text(uid, context), reply_markup=summary_kb(uid, context))

# ---------- Cart / wallet / orders / giveaways renders ----------

async def render_cart(q, context):
    uid = q.from_user.id
    lines = cart_lines_db(uid)
    if not lines:
        text = t(uid, "cart_empty")
    else:
        parts = ["🛒 Your Cart\n"]
        for l in lines:
            parts.append(t(uid, "sum_line", name=l['product']['name'], qty=l['qty'],
                           line=fmt_money(l['line_pkr'], uid), unit=fmt_money(l['unit_pkr'], uid)))
        parts.append(f"\n{t(uid, 'sum_total', amount=fmt_money(sum(l['line_pkr'] for l in lines), uid))}")
        text = "\n".join(parts)
    kb = []
    for l in lines:
        kb.append([InlineKeyboardButton(f"❌ Remove {_short_name(l['product']['name'], 30)}", callback_data=f"cart_rm_{l['pid']}")])
    if lines:
        kb.append([InlineKeyboardButton(t(uid, "cart_checkout"), callback_data="cart_checkout"),
                   InlineKeyboardButton(t(uid, "cart_clear"), callback_data="cart_clear")])
    kb.append([InlineKeyboardButton(t(uid, "cart_continue"), callback_data="back_list")])
    kb.append([InlineKeyboardButton(t(uid, "prod_back_menu"), callback_data="back_menu")])
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
        [InlineKeyboardButton(t(uid, "wallet_deposit"), callback_data="menu_deposit"),
         InlineKeyboardButton(t(uid, "wallet_topup"), callback_data="wal_topup")],
        [InlineKeyboardButton(t(uid, "wallet_stmt"), callback_data="wal_stmt")],
        [InlineKeyboardButton(t(uid, "prod_back_menu"), callback_data="back_menu")],
    ])
    await q.edit_message_text(text, reply_markup=kb)

async def render_orders_list(q, context):
    uid = q.from_user.id
    c = db(); rows = c.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (uid,)).fetchall(); c.close()
    if not rows:
        await q.edit_message_text(t(uid, "orders_none"),
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(uid, "prod_back_menu"), callback_data="back_menu")]]))
        return
    kb = []
    for o in rows:
        c = db(); p = c.execute("SELECT name FROM products WHERE id=?", (o["product_id"],)).fetchone(); c.close()
        kb.append([InlineKeyboardButton(f"#{o['id']} {_short_name(p['name'], 28) if p else '?'} — {order_status_label(o['status'], uid)}",
                                        callback_data=f"ord_{o['id']}")])
    kb.append([InlineKeyboardButton(t(uid, "prod_back_menu"), callback_data="back_menu")])
    await q.edit_message_text(t(uid, "orders_title"), reply_markup=InlineKeyboardMarkup(kb))

async def render_order_detail(q, context, oid):
    uid = q.from_user.id
    c = db(); o = c.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (oid, uid)).fetchone(); c.close()
    if not o:
        await q.edit_message_text(t(uid, "order_not_found")); return
    c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (o["product_id"],)).fetchone(); c.close()
    text = (f"📦 Order #{o['id']} ({o['code'] or '—'})\n\n"
            f"Product: {p['name'] if p else '?'}\n"
            f"Qty: {o['qty']}\n"
            f"Amount: {o['amount_text']}\n"
            f"Payment: {o['payment_method']}\n"
            f"Status: {order_status_label(o['status'], uid)}\n"
            f"Date: {o['created_at']}")
    kb = []
    if p:
        kb.append([InlineKeyboardButton(t(uid, "order_buy_again"), callback_data=f"buyagain_{p['id']}")])
    if o["status"] == "delivered":
        kb.append([InlineKeyboardButton(t(uid, "order_write_review"), callback_data=f"wrev_{o['id']}")])
    kb.append([InlineKeyboardButton(t(uid, "order_back"), callback_data="menu_orders")])
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def render_reviews(q, pid, page):
    uid = q.from_user.id
    c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    total = c.execute("SELECT COUNT(*) n FROM reviews WHERE product_id=?", (pid,)).fetchone()["n"]
    rows = c.execute("SELECT * FROM reviews WHERE product_id=? ORDER BY id DESC LIMIT 5 OFFSET ?", (pid, page * 5)).fetchall()
    c.close()
    n, avg = review_stats(pid)
    parts = [f"⭐ Reviews — {p['name'] if p else ''}\n"]
    parts.append(t(uid, "prod_reviews", avg=avg, n=n) + "\n" if n else t(uid, "prod_no_reviews") + "\n")
    for r in rows:
        tag = " (Verified purchase)" if r["verified"] else (" (Added by shop)" if r["source"] == "shop" else "")
        parts.append(f"⭐{r['rating']} — {r['user_name'] or 'User'}{tag}\n{r['text'] or ''}\n")
    total_pages = max(1, (total + 4) // 5)
    kb = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(t(uid, "prod_prev"), callback_data=f"rev_{pid}_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(t(uid, "prod_next"), callback_data=f"rev_{pid}_{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(t(uid, "prod_back_products"), callback_data=f"prod_{pid}")])
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
             t(uid, "giv_share"),
             "🎁 Active Giveaways:"]
    for i, (name, desc) in enumerate(GIVEAWAYS, 1):
        parts.append(f"{i}. {name} — {desc}")
    parts.append(t(uid, "giv_mandatory"))
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(t(uid, "giv_verify_btn"), callback_data="giv_verify")],
        [InlineKeyboardButton(t(uid, "prod_back_menu"), callback_data="back_menu")],
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
    """method: jazzcash/easypaisa/usdt/zurvapay. Buy ya deposit context ke
    hisaab se payment instructions dikhao aur awaiting set karo."""
    uid = q.from_user.id
    dep = context.user_data.get("deposit")
    if dep:
        dep["method"] = method
        if method == "zurvapay":
            # Zurvapay sirf PKR karta hai.
            if dep["currency"] != "PKR":
                await q.edit_message_text(t(uid, "pay_zurva_only_pkr"))
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
                await q.edit_message_text(t(uid, "pay_zurva_fail_dep", err=err, id=dep_id))
                return
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(t(uid, "pay_zurva_paynow"), url=url)]])
            await q.edit_message_text(
                t(uid, "pay_zurva_dep", amount=dep['amount'], id=dep_id),
                reply_markup=kb)
            return
        if method in ("jazzcash", "easypaisa"):
            num = get_setting(f"{method}_number"); name = get_setting(f"{method}_name")
            label = "JazzCash" if method == "jazzcash" else "Easypaisa"
            await q.edit_message_text(
                t(uid, "pay_manual_dep", label=label, amount=dep['amount'], currency=dep['currency'], num=num, name=name),
                parse_mode="Markdown")
            context.user_data["awaiting"] = "dep_tid"
        else:
            addr = get_setting("usdt_address")
            await q.edit_message_text(
                t(uid, "pay_usdt_dep", amount=dep['amount'], addr=addr),
                parse_mode="Markdown")
            context.user_data["awaiting"] = "dep_hash"
        return
    lines, _sub, _disc, total = buy_totals(context, uid)
    if not lines:
        await q.edit_message_text(t(uid, "sum_expired")); return
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
            await q.edit_message_text(t(uid, "pay_zurva_fail_order", err=err, oid=oid, code=code))
            return
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(t(uid, "pay_zurva_paynow"), url=url)]])
        await q.edit_message_text(
            t(uid, "pay_zurva_order", label=label, total=total, oid=oid, code=code),
            reply_markup=kb)
        return
    if method in ("jazzcash", "easypaisa"):
        num = get_setting(f"{method}_number"); name = get_setting(f"{method}_name")
        mlabel = "JazzCash" if method == "jazzcash" else "Easypaisa"
        await q.edit_message_text(
            t(uid, "pay_manual_order", label=mlabel, label2=label, total=total, num=num, name=name),
            parse_mode="Markdown")
        context.user_data["awaiting"] = "tid"
    else:
        addr = get_setting("usdt_address")
        total_usdt = round(total / USDT_RATE, 2)
        await q.edit_message_text(
            t(uid, "pay_usdt_order", label=label, amount=total_usdt, addr=addr),
            parse_mode="Markdown")
        context.user_data["awaiting"] = "hash"

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = q.data
    uid = q.from_user.id

    # ---------- Main menu ----------
    if data == "back_menu":
        await q.edit_message_text(await show_menu_text(uid), reply_markup=menu_kb(uid))
        return

    if data == "curr_toggle":
        new = "USDT" if user_currency(uid) == "PKR" else "PKR"
        set_user_currency(uid, new)
        await q.answer(f"Currency ab {new} hai.", show_alert=False)
        await q.edit_message_text(await show_menu_text(uid), reply_markup=menu_kb(uid))
        return

    # ---------- Language ----------
    if data == "lang_menu":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🇵🇰 Roman Urdu", callback_data="lang_ur"),
             InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
            [InlineKeyboardButton(t(uid, "prod_back_menu"), callback_data="back_menu")],
        ])
        await q.edit_message_text(t(uid, "lang_title"), reply_markup=kb)
        return

    if data in ("lang_ur", "lang_en"):
        lang = "ur" if data == "lang_ur" else "en"
        set_lang(uid, lang)
        name = "Roman Urdu" if lang == "ur" else "English"
        await q.answer(t(uid, "lang_set", name=name), show_alert=False)
        await q.edit_message_text(await show_menu_text(uid), reply_markup=menu_kb(uid))
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
        kb.append([InlineKeyboardButton(t(uid, "prod_back_menu"), callback_data="back_menu")])
        await q.edit_message_text(
            t(uid, "channels_title"),
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
        kb.append([InlineKeyboardButton(t(uid, "prod_back_menu"), callback_data="back_menu")])
        await q.edit_message_text(
            t(uid, "joinus_title"),
            reply_markup=InlineKeyboardMarkup(kb))
        return

    if data == "menu_orders":
        await render_orders_list(q, context)
        return

    if data == "menu_support":
        sup = get_setting("support_username")
        kb = [[InlineKeyboardButton(t(uid, "support_send"), callback_data="support_send")]]
        if sup:
            kb.append([InlineKeyboardButton(t(uid, "support_tg"), url=f"https://t.me/{sup.lstrip('@')}")])
        kb.append([InlineKeyboardButton(t(uid, "prod_back_menu"), callback_data="back_menu")])
        await q.edit_message_text(
            t(uid, "support_title"),
            reply_markup=InlineKeyboardMarkup(kb))
        return

    if data == "support_send":
        context.user_data["awaiting"] = "support"
        await q.edit_message_text(t(uid, "support_prompt"))
        return

    if data == "menu_help":
        await q.edit_message_text(
            t(uid, "help_text"),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(uid, "prod_back_menu"), callback_data="back_menu")]]))
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
            await q.answer(t(uid, "misc_giv_verified"), show_alert=True)
        elif member is None:
            await q.answer(t(uid, "misc_giv_check_fail"), show_alert=True)
        else:
            await q.answer(t(uid, "misc_giv_join_first"), show_alert=True)
        return

    # ---------- Deposit ----------
    if data == "menu_deposit":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(uid, "dep_pkr"), callback_data="dep_cur_PKR"),
             InlineKeyboardButton(t(uid, "dep_usdt"), callback_data="dep_cur_USDT")],
            [InlineKeyboardButton(t(uid, "dep_back_wallet"), callback_data="menu_wallet")],
        ])
        await q.edit_message_text(t(uid, "dep_title"), reply_markup=kb)
        return

    if data.startswith("dep_cur_"):
        cur = data.split("_")[2]
        context.user_data["deposit_currency"] = cur
        context.user_data["awaiting"] = "dep_amount"
        await q.edit_message_text(t(uid, "dep_amount", cur=cur))
        return

    if data == "wal_topup":
        context.user_data["awaiting"] = "topup"
        await q.edit_message_text(t(uid, "topup_prompt"))
        return

    if data == "wal_stmt":
        entries = wallet_statement_entries(uid, limit=20)
        text = "📄 Wallet Statement (last 20)\n\n" + ("\n".join(entries) if entries else t(uid, "wallet_stmt_none"))
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(uid, "wallet_stmt_dl"), callback_data="wal_stmt_dl")],
            [InlineKeyboardButton(t(uid, "dep_back_wallet"), callback_data="menu_wallet")],
        ])
        await q.edit_message_text(text, reply_markup=kb)
        return

    if data == "wal_stmt_dl":
        entries = wallet_statement_entries(uid, limit=200)
        txt = "RetailShop — Wallet Statement\n" + "\n".join(entries) if entries else "RetailShop — Wallet Statement\n\n" + t(uid, "wallet_stmt_none")
        await context.bot.send_document(
            uid, document=InputFile(io.BytesIO(txt.encode("utf-8")), filename="wallet_statement.txt"),
            caption=t(uid, "wallet_stmt_caption"))
        return

    # ---------- Channel gate ----------
    if data == "check_join":
        member, _err = await tg_is_member(context.bot, uid)
        if member is True:
            if wa_channel_url() and not wa_joined_flag(uid):
                kb = [[InlineKeyboardButton("💬 Join WhatsApp Channel", url=wa_channel_url())],
                      [InlineKeyboardButton("✅ Maine Join Kar Liya", callback_data="wa_joined")]]
                await q.edit_message_text(
                    t(uid, "gate_joined_tg"),
                    reply_markup=InlineKeyboardMarkup(kb))
                return
            await q.edit_message_text(t(uid, "gate_thanks") + await show_menu_text(uid), reply_markup=menu_kb(uid))
            return
        if member is None:
            await q.answer(t(uid, "misc_join_check_fail"), show_alert=True)
            return
        await q.answer(t(uid, "misc_join_check"), show_alert=True)
        return

    if data == "wa_joined":
        # NOTE: WhatsApp channel join ko Telegram bot verify NAHI kar sakta —
        # WhatsApp ka koi public API nahi hai. Ye button user ke bharose pe hai.
        set_wa_joined(uid)
        await q.edit_message_text(t(uid, "gate_thanks") + await show_menu_text(uid), reply_markup=menu_kb(uid))
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
        await q.edit_message_text(t(uid, "search_prompt"))
        return

    if data == "req_prod":
        context.user_data["awaiting"] = "request"
        await q.edit_message_text(t(uid, "req_prompt"))
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
        pid = int(data.split("_")[1])
        c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone(); c.close()
        if p:
            try:
                unlimited = bool(p["unlimited"])
            except Exception:
                unlimited = False
            if not unlimited and stock_count(pid) == 0:
                await q.answer(t(uid, "prod_oos_alert"), show_alert=True)
        await render_product_detail(q, context, pid)
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
        await q.answer(t(uid, "cart_added"), show_alert=False)
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
            await q.answer(t(uid, "cart_empty_alert"), show_alert=True); return
        for l in lines:
            if not l["product"]["unlimited"] and l["qty"] > stock_count(l["pid"]):
                await q.answer(t(uid, "cart_stock_alert", name=l['product']['name'], n=stock_count(l['pid'])), show_alert=True); return
        context.user_data["buy_mode"] = "cart"
        context.user_data["buy_items"] = [{"pid": l["pid"], "qty": l["qty"]} for l in lines]
        context.user_data["buy_code"] = gen_order_code()
        context.user_data.pop("buy_coupon_code", None)
        context.user_data.pop("buy_pid", None)
        await render_summary(q, context)
        return

    # ---------- Buy flow (naya simplified flow) ----------
    # category -> product detail (quantity + terms + agree) -> payment -> pay
    if data.startswith("buy_") or data.startswith("buyagain_"):
        pid = int(data.split("_")[1])
        c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone(); c.close()
        if not p:
            await q.edit_message_text(t(uid, "prod_not_found")); return
        if not p["unlimited"] and stock_count(pid) == 0:
            await q.edit_message_text(t(uid, "prod_oos_msg"))
            return
        if p["price_pkr"] == 0 and p["price_usdt"] == 0:
            # FREE product — no payment, instant delivery
            code = gen_order_code()
            oid = create_order(uid, q.from_user.username or "", [{"pid": pid, "qty": 1}],
                               "free", "FREE", "free", 0, code, "", 0)
            await q.edit_message_text(t(uid, "buy_free", oid=oid))
            await deliver_order(context, oid)
            return
        context.user_data["buy_pid"] = pid
        context.user_data["buy_mode"] = "single"
        context.user_data["buy_qty"] = 1
        context.user_data["buy_code"] = gen_order_code()
        context.user_data.pop("buy_coupon_code", None)
        context.user_data.pop("buy_items", None)
        await render_product_detail(q, context, pid)
        return

    # Quantity + / − (detail screen pe)
    if data.startswith("qtyinc_") or data.startswith("qtydec_"):
        pid = int(data.split("_")[1])
        c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone(); c.close()
        if not p:
            return
        cur = int(context.user_data.get("buy_qty", 1) or 1)
        if data.startswith("qtyinc_"):
            new_qty = min(cur + 1, 99)
            if not p["unlimited"] and new_qty > stock_count(pid):
                await q.answer(t(uid, "qty_only_stock", n=stock_count(pid)), show_alert=True); return
        else:
            new_qty = max(cur - 1, 1)
        context.user_data["buy_pid"] = pid
        context.user_data["buy_mode"] = "single"
        context.user_data["buy_qty"] = new_qty
        if not context.user_data.get("buy_code"):
            context.user_data["buy_code"] = gen_order_code()
        await render_product_detail(q, context, pid)
        return

    if data == "qty_info":
        pid = context.user_data.get("buy_pid")
        qty = int(context.user_data.get("buy_qty", 1) or 1)
        if pid:
            c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone(); c.close()
            if p:
                await q.answer(t(uid, "prod_qty_info", qty=qty, total=fmt_money(unit_price_pkr(p, qty) * qty, uid)), show_alert=False)
        return

    # FREE product — detail screen se seedha delivery
    if data.startswith("free_"):
        pid = int(data.split("_")[1])
        c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone(); c.close()
        if not p:
            await q.edit_message_text(t(uid, "prod_not_found")); return
        if not (p["price_pkr"] == 0 and p["price_usdt"] == 0):
            await render_product_detail(q, context, pid); return
        code = gen_order_code()
        oid = create_order(uid, q.from_user.username or "", [{"pid": pid, "qty": 1}],
                           "free", "FREE", "free", 0, code, "", 0)
        await q.edit_message_text(t(uid, "buy_free", oid=oid))
        await deliver_order(context, oid)
        return

    # I Agree — detail screen se order summary (payment) pe jao
    if data == "detail_agree" or data == "terms_ok":
        await render_summary(q, context)
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
            await q.answer(t(uid, "qty_only_stock", n=stock_count(pid)), show_alert=True); return
        context.user_data["buy_pid"] = pid
        context.user_data["buy_mode"] = "single"
        context.user_data["buy_qty"] = qty
        if not context.user_data.get("buy_code"):
            context.user_data["buy_code"] = gen_order_code()
        await render_product_detail(q, context, pid)
        return

    if data.startswith("qtycustom_"):
        pid = int(data.split("_")[1])
        context.user_data["custom_pid"] = pid
        context.user_data["awaiting"] = "custom_qty"
        await q.message.reply_text(t(uid, "qty_prompt"))
        return

    if data.startswith("qtycur_"):
        pid = int(data.split("_")[1])
        new = "USDT" if user_currency(uid) == "PKR" else "PKR"
        set_user_currency(uid, new)
        await render_product_detail(q, context, pid)
        return

    if data == "cancel_order":
        clear_buy_state(context)
        context.user_data.pop("awaiting", None)
        await q.edit_message_text(await show_menu_text(uid), reply_markup=menu_kb(uid))
        return

    if data == "qty_change":
        if context.user_data.get("buy_mode") == "cart":
            await render_cart(q, context)
        else:
            pid = context.user_data.get("buy_pid")
            if pid:
                await render_product_detail(q, context, pid)
            else:
                await render_products_list(q, context, 0, None)
        return

    if data == "coupon_apply":
        context.user_data["awaiting"] = "coupon"
        await q.message.reply_text(t(uid, "coupon_prompt"))
        return

    if data == "paypkr":
        lines, _sub, _disc, total = buy_totals(context, uid)
        if not lines:
            await q.edit_message_text(t(uid, "sum_expired")); return
        kb_rows = []
        if zurvapay_enabled():
            kb_rows.append([InlineKeyboardButton(t(uid, "sum_pay_zurva"), callback_data="pay_zurvapay")])
        kb_rows.append([InlineKeyboardButton(t(uid, "sum_pay_easypaisa"), callback_data="pay_easypaisa")])
        kb_rows.append([InlineKeyboardButton(t(uid, "sum_pay_jazzcash"), callback_data="pay_jazzcash")])
        kb_rows.append([InlineKeyboardButton(t(uid, "sum_cancel"), callback_data="summary_back")])
        kb = InlineKeyboardMarkup(kb_rows)
        await q.edit_message_text(t(uid, "pay_pkr_title", total=total), reply_markup=kb)
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
            await q.edit_message_text(t(uid, "sum_expired")); return
        cur = user_currency(uid)
        need = total if cur == "PKR" else round(total / USDT_RATE, 2)
        bal = get_wallet(uid, cur)
        if bal < need:
            await q.answer(t(uid, "buy_wallet_low"), show_alert=True); return
        code = context.user_data.get("buy_code") or gen_order_code()
        coupon_code = context.user_data.get("buy_coupon_code") or ""
        items = [{"pid": l["pid"], "qty": l["qty"]} for l in lines]
        amount_text = f"{fmt_money(total, uid)} via wallet"
        add_wallet(uid, cur, -need)
        oid = create_order(uid, q.from_user.username or "", items, "wallet", amount_text, "wallet", total, code, coupon_code, discount)
        if context.user_data.get("buy_mode") == "cart":
            c = db(); c.execute("DELETE FROM carts WHERE user_id=?", (uid,)); c.commit(); c.close()
        clear_buy_state(context)
        await q.edit_message_text(t(uid, "buy_wallet_paid", oid=oid))
        ok = await deliver_order(context, oid)
        if not ok:
            add_wallet(uid, cur, need)  # refund — stock khatam thi
            await context.bot.send_message(uid, t(uid, "buy_wallet_refund"))
        return

    if data.startswith("pay_"):
        method = data.split("_")[1]  # zurvapay / easypaisa / jazzcash / usdt
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
            await q.answer(t(uid, "rev_only_delivered"), show_alert=True); return
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
        await q.message.reply_text(t(uid, "rev_rating", rating=rating))
        return

    # ---------- Admin approve / reject ----------
    if data.startswith("approve_"):
        if not is_admin(uid):
            await q.answer("Sirf admin.", show_alert=True); return
        oid = int(data.split("_")[1])
        c = db(); _o = c.execute("SELECT channel FROM orders WHERE id=?", (oid,)).fetchone(); c.close()
        if _o:
            try:
                _ch = _o["channel"] or "telegram"
            except Exception:
                _ch = "telegram"
            if _ch == "web":
                await q.edit_message_text(f"⚠️ Order #{oid} web order hai — ye AHM7 fulfillment + order page se deliver hoti hai, yahan se approve nahi hoti.")
                return
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
                await update.message.reply_text(t(user.id, "buy_hash_short")); return
            addr = get_setting("usdt_address")
            ok, reason = verify_usdt(tx, addr, float(amount))
            if ok:
                c = db(); cur = c.execute(
                    "INSERT INTO deposits(user_id,username,currency,amount,payment_method,proof,status,created_at) VALUES(?,?,?,?,?,?, 'approved', ?)",
                    (user.id, user.username or "", currency, amount, "usdt", tx, datetime.now().strftime("%Y-%m-%d %H:%M")))
                dep_id = cur.lastrowid; c.commit(); c.close()
                add_wallet(user.id, currency, amount)
                context.user_data.clear()
                await update.message.reply_text(t(user.id, "dep_usdt_verified", reason=reason, id=dep_id, amount=amount, currency=currency))
                return
            c = db(); cur = c.execute(
                "INSERT INTO deposits(user_id,username,currency,amount,payment_method,proof,status,created_at) VALUES(?,?,?,?,?,?, 'pending', ?)",
                (user.id, user.username or "", currency, amount, "usdt", tx, datetime.now().strftime("%Y-%m-%d %H:%M")))
            dep_id = cur.lastrowid; c.commit(); c.close()
            context.user_data.clear()
            await update.message.reply_text(t(user.id, "dep_usdt_fail", reason=reason, id=dep_id))
            await notify_admin_deposit(context, dep_id)
            return
        # dep_tid (manual PKR)
        c = db(); cur = c.execute(
            "INSERT INTO deposits(user_id,username,currency,amount,payment_method,proof,status,created_at) VALUES(?,?,?,?,?,?, 'pending', ?)",
            (user.id, user.username or "", currency, amount, method, proof, datetime.now().strftime("%Y-%m-%d %H:%M")))
        dep_id = cur.lastrowid; c.commit(); c.close()
        context.user_data.clear()
        await update.message.reply_text(t(user.id, "dep_received", id=dep_id))
        await notify_admin_deposit(context, dep_id)
        return

    # --- buy proof ---
    lines, _sub, discount, total = buy_totals(context, user.id)
    method = context.user_data.get("pay_method", "?")
    if not lines:
        context.user_data.clear()
        await update.message.reply_text(t(user.id, "misc_expired"))
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
        await update.message.reply_text(t(user.id, "buy_order_received", oid=oid, code=code))
        await notify_admin_pending(context, oid)

    elif awaiting == "hash":
        tx = (update.message.text or "").strip()
        if len(tx) < 20:
            await update.message.reply_text(t(user.id, "buy_hash_short")); return
        addr = get_setting("usdt_address")
        total_usdt = round(total / USDT_RATE, 2)
        ok, reason = verify_usdt(tx, addr, total_usdt)
        if ok:
            oid = create_order(user.id, user.username or "", items, "usdt", f"${total_usdt} USDT", tx, total, code, coupon_code, discount)
            if context.user_data.get("buy_mode") == "cart":
                c = db(); c.execute("DELETE FROM carts WHERE user_id=?", (user.id,)); c.commit(); c.close()
            context.user_data.clear()
            await update.message.reply_text(t(user.id, "buy_usdt_verified", reason=reason, oid=oid, code=code))
            await deliver_order(context, oid)
        else:
            # verify fail -> pending for manual admin check, buyer ko batao
            oid = create_order(user.id, user.username or "", items, "usdt", f"${total_usdt} USDT", tx, total, code, coupon_code, discount)
            if context.user_data.get("buy_mode") == "cart":
                c = db(); c.execute("DELETE FROM carts WHERE user_id=?", (user.id,)); c.commit(); c.close()
            context.user_data.clear()
            await update.message.reply_text(t(user.id, "buy_usdt_fail", reason=reason, oid=oid, code=code))
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
        await update.message.reply_text(t(user.id, "support_sent"))
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
            t(user.id, "coupon_applied", code=code.strip().upper(), pct=FIRST_COUPON_PCT),
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
            await update.message.reply_text(t(user.id, "search_none", kw=kw), reply_markup=menu_kb(user.id))
            return
        kb = []
        for p in prods:
            kb.append([InlineKeyboardButton(product_btn_label(p, user.id),
                                            callback_data=f"prod_{p['id']}")])
        kb.append([InlineKeyboardButton("⬅ Products", callback_data="menu_products")])
        await update.message.reply_text(f"🔍 Search results for '{kw}':", reply_markup=InlineKeyboardMarkup(kb))
        return

    if awaiting == "request":
        text = (update.message.text or "").strip()
        context.user_data.pop("awaiting", None)
        if not text:
            return
        await update.message.reply_text(t(user.id, "req_sent"))
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
        await update.message.reply_text(t(user.id, "rev_saved"))
        return

    if awaiting == "topup":
        code = (update.message.text or "").strip().upper()
        context.user_data.pop("awaiting", None)
        if not code:
            return
        c = db(); r = c.execute("SELECT * FROM topup_codes WHERE code=?", (code,)).fetchone()
        if not r:
            c.close(); await update.message.reply_text(t(user.id, "topup_wrong")); return
        if r["used_by"]:
            c.close(); await update.message.reply_text(t(user.id, "topup_used")); return
        c.execute("UPDATE topup_codes SET used_by=? WHERE code=?", (user.id, code)); c.commit(); c.close()
        add_wallet(user.id, r["currency"], r["amount"])
        await update.message.reply_text(t(user.id, "topup_ok", amount=r['amount'], currency=r['currency']))
        return

    if awaiting == "dep_amount":
        cur = context.user_data.get("deposit_currency", "PKR")
        try:
            amount = float((update.message.text or "").strip())
        except ValueError:
            await update.message.reply_text(t(user.id, "misc_only_number")); return
        if amount < 1:
            await update.message.reply_text(t(user.id, "misc_min_deposit")); return
        context.user_data["deposit"] = {"currency": cur, "amount": amount}
        context.user_data.pop("awaiting", None)
        if cur == "PKR":
            kb_rows = []
            if zurvapay_enabled():
                kb_rows.append([InlineKeyboardButton(t(user.id, "sum_pay_zurva"), callback_data="pay_zurvapay")])
            kb_rows.append([InlineKeyboardButton(t(user.id, "sum_pay_easypaisa"), callback_data="pay_easypaisa"),
                            InlineKeyboardButton(t(user.id, "sum_pay_jazzcash"), callback_data="pay_jazzcash")])
            kb_rows.append([InlineKeyboardButton(t(user.id, "dep_back"), callback_data="menu_deposit")])
            kb = InlineKeyboardMarkup(kb_rows)
            await update.message.reply_text(t(user.id, "dep_method_pkr", amount=amount), reply_markup=kb)
        else:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(t(user.id, "sum_pay_usdt"), callback_data="pay_usdt")],
                [InlineKeyboardButton(t(user.id, "dep_back"), callback_data="menu_deposit")],
            ])
            await update.message.reply_text(t(user.id, "dep_method_usdt", amount=amount), reply_markup=kb)
        return

    if awaiting == "custom_qty":
        pid = context.user_data.get("custom_pid")
        context.user_data.pop("awaiting", None)
        try:
            qty = int((update.message.text or "").strip())
        except ValueError:
            await update.message.reply_text(t(user.id, "qty_number_only")); return
        if qty < 1 or qty > 99:
            await update.message.reply_text(t(user.id, "qty_range")); return
        c = db(); p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone(); c.close()
        if not p:
            return
        if not p["unlimited"] and qty > stock_count(pid):
            await update.message.reply_text(t(user.id, "qty_only_stock", n=stock_count(pid))); return
        context.user_data["buy_pid"] = pid
        context.user_data["buy_mode"] = "single"
        context.user_data["buy_qty"] = qty
        if not context.user_data.get("buy_code"):
            context.user_data["buy_code"] = gen_order_code()
        # Naya flow: quantity set ho gayi — user ab I Agree dabake payment pe
        # ja sakta hai (detail screen pe wapas jaane ki zaroorat nahi).
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(user.id, "prod_agree"), callback_data="detail_agree")],
            [InlineKeyboardButton(t(user.id, "prod_back_products"), callback_data=f"prod_{pid}")],
        ])
        await update.message.reply_text(
            t(user.id, "prod_qty_info", qty=qty, total=fmt_money(unit_price_pkr(p, qty) * qty, user.id)),
            reply_markup=kb)
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
    c = db(); rows = c.execute("SELECT * FROM orders WHERE status='pending' AND channel != 'web' ORDER BY id DESC LIMIT 10").fetchall(); c.close()
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
    start_outbox_poller()  # WhatsApp bot ki taraf se Telegram admin notify + WA delivery queue

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
