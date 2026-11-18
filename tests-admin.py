"""Local dry-run tests for the admin dashboard API (phone signup/login,
identifier login, admin OTP, admin stats/orders/users).
No network — Twilio/Resend senders are monkeypatched to capture the code.
Same pattern as tests-auth.py.
Run: ~/workspace/tools/bot-test-venv/bin/python tests-admin.py
"""
import os, sys, json, sqlite3, hashlib, time

os.environ["DB_PATH"] = "/tmp/test-admin.db"
os.environ["WEB_API_SECRET"] = "test-secret-123"
os.environ["PORT"] = "18099"
for f in ("/tmp/test-admin.db", "/tmp/test-admin.db-wal", "/tmp/test-admin.db-shm"):
    if os.path.exists(f):
        os.remove(f)

sys.path.insert(0, "/home/hatch/workspace/telegram-shop-bot")
import bot

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" — {extra}" if extra and not cond else ""))

H = {"X-Web-Secret": "test-secret-123"}
BAD_H = {"X-Web-Secret": "wrong"}
_ip_n = [0]
def fresh_ip():
    _ip_n[0] += 1
    return f"10.1.{_ip_n[0] // 250}.{_ip_n[0] % 250 + 1}"

def bearer(tok):
    return {"Authorization": f"Bearer {tok}"}

bot.init_db()
c = bot.db()
cols = [r[1] for r in c.execute("PRAGMA table_info(web_users)").fetchall()]
check("migration: web_users has phone + is_admin", "phone" in cols and "is_admin" in cols, str(cols))
cols = [r[1] for r in c.execute("PRAGMA table_info(web_sessions)").fetchall()]
check("migration: web_sessions has is_admin", "is_admin" in cols, str(cols))
tabs = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
check("migration: admin_otps table exists", "admin_otps" in tabs, str(tabs))
check("migration: user_email_otps table exists", "user_email_otps" in tabs, str(tabs))
wcols = [r[1] for r in c.execute("PRAGMA table_info(web_users)").fetchall()]
check("migration: web_users has email_verified", "email_verified" in wcols, str(wcols))
c.close()

ADMIN_EMAIL = "raisjunaidnazeer@gmail.com"
ADMIN_PHONE = "+923706462990"

# ---------- 1. signup with phone ----------
s, out = bot.handle_auth_signup(
    {"name": "Ali Khan", "email": "ali@example.com", "phone": "+92 300 1112222",
     "password": "secret123"}, H, fresh_ip())
check("signup: with phone 200 + phone echoed + is_admin false",
      s == 200 and out.get("ok") is True and out.get("user", {}).get("phone") == "+923001112222"
      and out.get("user", {}).get("is_admin") is False, f"got {s} {out}")
TOK_ALI = out.get("token", "")

s, out = bot.handle_auth_signup(
    {"name": "No Phone", "email": "nophone@example.com", "password": "secret123"}, H, fresh_ip())
check("signup: missing phone -> 400", s == 400, f"got {s} {out}")
s, out = bot.handle_auth_signup(
    {"name": "Bad Phone", "email": "badphone@example.com", "phone": "03001112222",
     "password": "secret123"}, H, fresh_ip())
check("signup: phone without country code -> 400", s == 400, f"got {s} {out}")
s, out = bot.handle_auth_signup(
    {"name": "Short Phone", "email": "shortphone@example.com", "phone": "+92 123",
     "password": "secret123"}, H, fresh_ip())
check("signup: too-short phone -> 400", s == 400, f"got {s} {out}")
s, out = bot.handle_auth_signup(
    {"name": "Ali Dup", "email": "ali2@example.com", "phone": "+923001112222",
     "password": "secret123"}, H, fresh_ip())
check("signup: duplicate phone -> 409", s == 409, f"got {s} {out}")
s, out = bot.handle_auth_signup(
    {"name": "Ali Dup2", "email": "ali@example.com", "phone": "+923001112333",
     "password": "secret123"}, H, fresh_ip())
check("signup: duplicate email still -> 409", s == 409, f"got {s} {out}")

# ---------- 2. login by identifier ----------
s, out = bot.handle_auth_login({"email": "ali@example.com", "password": "secret123"}, H, fresh_ip())
check("login: old {email} form still works", s == 200 and out.get("ok") is True, f"got {s} {out}")
s, out = bot.handle_auth_login({"identifier": "ali@example.com", "password": "secret123"}, H, fresh_ip())
check("login: by email identifier", s == 200, f"got {s} {out}")
s, out = bot.handle_auth_login({"identifier": "+923001112222", "password": "secret123"}, H, fresh_ip())
check("login: by phone identifier", s == 200, f"got {s} {out}")
s, out = bot.handle_auth_login({"identifier": "Ali Khan", "password": "secret123"}, H, fresh_ip())
check("login: by username identifier", s == 200, f"got {s} {out}")
s, out = bot.handle_auth_login({"identifier": "Ali Khan", "password": "wrongpass"}, H, fresh_ip())
check("login: wrong password -> 401 generic", s == 401, f"got {s} {out}")
_c = bot.db()
_lr = _c.execute("SELECT detail FROM issues WHERE kind='login_failed' AND user_email=?",
                 ("Ali Khan",)).fetchall()
_c.close()
check("issues: failed login logged (kind login_failed, password never stored)",
      len(_lr) == 1 and "wrongpass" not in (_lr[0]["detail"] or ""), f"got {[dict(r) for r in _lr]}")
s, out = bot.handle_auth_login({"identifier": "nobody@example.com", "password": "secret123"}, H, fresh_ip())
check("login: unknown identifier -> 401 generic", s == 401, f"got {s} {out}")

# ambiguous username: two users, same name
bot.handle_auth_signup({"name": "Same Name", "email": "same1@example.com",
                        "phone": "+923001114444", "password": "secret123"}, H, fresh_ip())
bot.handle_auth_signup({"name": "Same Name", "email": "same2@example.com",
                        "phone": "+923001115555", "password": "secret123"}, H, fresh_ip())
s, out = bot.handle_auth_login({"identifier": "Same Name", "password": "secret123"}, H, fresh_ip())
check("login: ambiguous username -> 401 (no guessing)", s == 401, f"got {s} {out}")

# ---------- 3. admin identity ----------
s, out = bot.handle_auth_signup(
    {"name": "Junaid Nazeer", "email": ADMIN_EMAIL, "phone": ADMIN_PHONE,
     "password": "adminpass123"}, H, fresh_ip())
check("signup: admin email+phone -> is_admin true",
      s == 200 and out.get("user", {}).get("is_admin") is True, f"got {s} {out}")
s, out = bot.handle_auth_login({"identifier": ADMIN_EMAIL, "password": "adminpass123"}, H, fresh_ip())
check("login: admin is_admin recomputed true", s == 200 and out.get("user", {}).get("is_admin") is True,
      f"got {s} {out}")
s, out = bot.handle_auth_me(bearer(out.get("token", "")))
check("me: includes is_admin true + phone",
      s == 200 and out.get("user", {}).get("is_admin") is True
      and out.get("user", {}).get("phone") == ADMIN_PHONE, f"got {s} {out}")
s, out = bot.handle_auth_me(bearer(TOK_ALI))
check("me: normal user is_admin false",
      s == 200 and out.get("user", {}).get("is_admin") is False, f"got {s} {out}")

# admin by phone only (different email, admin phone)
s, out = bot.handle_auth_signup(
    {"name": "Admin By Phone", "email": "other@example.com", "phone": ADMIN_PHONE,
     "password": "secret123"}, H, fresh_ip())
check("signup: admin phone already used -> 409 (phone unique)",
      s == 409, f"got {s} {out}")

# ---------- 4. OTP send without keys -> 503 ----------
s, out = bot.handle_admin_otp_send(
    {"identifier": ADMIN_EMAIL, "password": "adminpass123", "purpose": "login"}, H, fresh_ip())
check("otp/send: no Twilio/Resend keys -> 503 otp_not_configured",
      s == 503 and out.get("error") == "otp_not_configured", f"got {s} {out}")

# ---------- 5. OTP flow with mocked senders ----------
bot.TWILIO_ACCOUNT_SID = "ACtest"
bot.TWILIO_AUTH_TOKEN = "toktest"
bot.TWILIO_FROM_NUMBER = "+15005550006"
bot.RESEND_API_KEY = "re_test"
captured = []
orig_sms = bot._send_otp_sms
orig_email = bot._send_otp_email
bot._send_otp_sms = lambda to, code: (captured.append(("sms", to, code)), True)[1]
bot._send_otp_email = lambda to, code: (captured.append(("email", to, code)), True)[1]

s, out = bot.handle_admin_otp_send(
    {"identifier": "ali@example.com", "password": "secret123", "purpose": "login"}, H, fresh_ip())
check("otp/send: non-admin -> 403 generic (no leak)",
      s == 403 and "admin verification" in out.get("error", ""), f"got {s} {out}")
s, out = bot.handle_admin_otp_send(
    {"identifier": ADMIN_EMAIL, "password": "wrongpass", "purpose": "login"}, H, fresh_ip())
check("otp/send: wrong password -> 403 generic", s == 403, f"got {s} {out}")
s, out = bot.handle_admin_otp_send(
    {"identifier": "ghost@example.com", "password": "whatever123", "purpose": "login"}, H, fresh_ip())
check("otp/send: unknown identifier -> 403 generic", s == 403, f"got {s} {out}")
s, out = bot.handle_admin_otp_send(
    {"identifier": ADMIN_EMAIL, "password": "adminpass123", "purpose": "bogus"}, H, fresh_ip())
check("otp/send: bad purpose -> 400", s == 400, f"got {s} {out}")

captured.clear()
s, out = bot.handle_admin_otp_send(
    {"identifier": ADMIN_EMAIL, "password": "adminpass123", "purpose": "signup"}, H, fresh_ip())
check("otp/send: admin signup purpose -> 200 + both channels",
      s == 200 and out.get("ok") is True and out.get("channels") == ["sms", "email"],
      f"got {s} {out}")
check("otp/send: code went to ADMIN_PHONE + ADMIN_EMAIL (env contacts)",
      ("sms", ADMIN_PHONE, captured[0][2]) in captured
      and ("email", ADMIN_EMAIL, captured[0][2]) in captured,
      f"got {captured}")
CODE = captured[0][2]
check("otp/send: code is 6 digits, never in response",
      len(CODE) == 6 and CODE.isdigit() and CODE not in json.dumps(out), f"got {CODE!r} {out}")
cc = bot.db()
row = cc.execute("SELECT code_hash, attempts, used FROM admin_otps ORDER BY id DESC LIMIT 1").fetchone()
check("otp/send: DB stores hash not plaintext",
      row["code_hash"] == hashlib.sha256(CODE.encode()).hexdigest()
      and row["code_hash"] != CODE, f"got {dict(row)}")
cc.close()

s, out = bot.handle_admin_otp_verify(
    {"identifier": ADMIN_EMAIL, "purpose": "signup", "code": "000000"}, H)
check("otp/verify: wrong code -> 401 generic", s == 401, f"got {s} {out}")
s, out = bot.handle_admin_otp_verify(
    {"identifier": ADMIN_EMAIL, "purpose": "signup", "code": CODE}, H)
check("otp/verify: right code -> 200 + admin token + is_admin",
      s == 200 and out.get("ok") is True and len(out.get("token", "")) == 32
      and out.get("user", {}).get("is_admin") is True, f"got {s} {out}")
TOK_ADMIN = out.get("token", "")
s, out = bot.handle_admin_otp_verify(
    {"identifier": ADMIN_EMAIL, "purpose": "signup", "code": CODE}, H)
check("otp/verify: same code twice -> 401 (single-use)", s == 401, f"got {s} {out}")
# wrong code vs expired code: BOTH return the same generic 401 string by
# design (no oracle for attackers) — the site must map them identically.
# (Row is inserted directly so this test does not consume the 5/hour OTP
# send budget that section 8 counts on.)
_ADMIN_ROW = bot.db().execute("SELECT id FROM web_users WHERE email=?", (ADMIN_EMAIL,)).fetchone()
_ADMIN_ID = _ADMIN_ROW["id"]
CODE2 = "123456"
_cc = bot.db()
_cc.execute("INSERT INTO admin_otps(admin_user_id, purpose, code_hash, expires_at, attempts, used, created_at) "
            "VALUES(?, 'login', ?, '2099-01-01 00:00:00', 0, 0, ?)",
            (_ADMIN_ID, hashlib.sha256(CODE2.encode()).hexdigest(), bot._now_str()))
_cc.commit(); _cc.close()
s, out = bot.handle_admin_otp_verify(
    {"identifier": ADMIN_EMAIL, "purpose": "login", "code": "000000"}, H)
check("otp/verify: wrong code error string",
      s == 401 and out.get("error") == "code ghalat ya expire ho gaya hai", f"got {s} {out}")
cc = bot.db()
cc.execute("UPDATE admin_otps SET expires_at='2000-01-01 00:00:00' WHERE used=0 AND purpose='login'")
cc.commit(); cc.close()
s, out = bot.handle_admin_otp_verify(
    {"identifier": ADMIN_EMAIL, "purpose": "login", "code": CODE2}, H)
check("otp/verify: expired code -> same 401 string as wrong code (indistinguishable)",
      s == 401 and out.get("error") == "code ghalat ya expire ho gaya hai", f"got {s} {out}")

# ---------- 6. admin endpoints: auth ----------
s, out = bot.handle_admin_stats()
check("admin/stats: no token -> 401", s == 401, f"got {s} {out}")
s, out = bot.handle_admin_stats(bearer(TOK_ALI))
check("admin/stats: normal user token -> 403", s == 403, f"got {s} {out}")
s, out = bot.handle_admin_orders(bearer("deadbeef" * 4), {})
check("admin/orders: bad token -> 401", s == 401, f"got {s} {out}")

# ---------- 7. seed orders + check data ----------
c = bot.db()
pid = c.execute("SELECT id FROM products LIMIT 1").fetchone()["id"]
now = bot._now_str()
c.execute("INSERT INTO orders(user_id, username, product_id, payment_method, amount_text, "
          "proof, status, account_id, created_at, code, items_json, qty, amount_pkr, channel, web_contact) "
          "VALUES(0,'',?,'Zurvapay','Rs 171','', 'pending', NULL, ?, 'RS-TEST001', '[]', 1, 171, 'web', ?)",
          (pid, now, ADMIN_EMAIL))
c.execute("INSERT INTO orders(user_id, username, product_id, payment_method, amount_text, "
          "proof, status, account_id, created_at, code, items_json, qty, amount_pkr, channel, web_contact) "
          "VALUES(0,'',?,'Zurvapay','Rs 1000','', 'delivered', NULL, ?, 'RS-TEST002', '[]', 1, 1000, 'web', ?)",
          (pid, now, "ali@example.com"))
c.execute("INSERT INTO orders(user_id, username, product_id, payment_method, amount_text, "
          "proof, status, account_id, created_at, code, items_json, qty, amount_pkr, channel, web_contact) "
          "VALUES(7965730503,'JunaidTG',?,'Easypaisa','Rs 500','', 'delivered', NULL, ?, 'RS-TEST003', '[]', 1, 500, 'telegram', '')",
          (pid, now))
c.commit(); c.close()

s, out = bot.handle_admin_stats(bearer(TOK_ADMIN))
st = out.get("stats", {})
check("admin/stats: counts + revenue (paid only)",
      s == 200 and st.get("total_orders") == 3 and st.get("paid_orders") == 2
      and st.get("pending_orders") == 1 and st.get("total_revenue_pkr") == 1500
      and st.get("today_orders") == 3 and st.get("total_users") >= 4,
      f"got {s} {out}")

s, out = bot.handle_admin_orders(bearer(TOK_ADMIN), {})
check("admin/orders: 3 orders newest first",
      s == 200 and out.get("total") == 3 and len(out.get("orders", [])) == 3
      and out["orders"][0]["code"] == "RS-TEST003", f"got {s} {out}")
o1 = [o for o in out["orders"] if o["code"] == "RS-TEST001"][0]
check("admin/orders: web buyer resolved (name/email/phone/source)",
      o1["buyer"] == {"name": "Junaid Nazeer", "email": ADMIN_EMAIL,
                      "phone": ADMIN_PHONE, "source": "web"}
      and o1["delivery"] == "Unpaid" and o1["amount_pkr"] == 171, f"got {o1}")
o3 = [o for o in out["orders"] if o["code"] == "RS-TEST003"][0]
check("admin/orders: telegram buyer (no email/phone)",
      o3["buyer"] == {"name": "JunaidTG", "email": "", "phone": "", "source": "telegram"}
      and o3["delivery"] == "Delivered", f"got {o3}")
check("admin/orders: no hashes/tokens/secrets leak",
      "password_hash" not in json.dumps(out) and "token_hash" not in json.dumps(out)
      and "delivery_token" not in json.dumps(out), f"got {out}")

s, out = bot.handle_admin_orders(bearer(TOK_ADMIN), {"status": ["pending"]})
check("admin/orders: status filter", s == 200 and out.get("total") == 1
      and out["orders"][0]["code"] == "RS-TEST001", f"got {s} {out}")
s, out = bot.handle_admin_orders(bearer(TOK_ADMIN), {"q": ["TEST002"]})
check("admin/orders: q search by code", s == 200 and out.get("total") == 1, f"got {s} {out}")
s, out = bot.handle_admin_orders(bearer(TOK_ADMIN), {"limit": ["1"], "offset": ["1"]})
check("admin/orders: pagination limit/offset",
      s == 200 and len(out.get("orders", [])) == 1 and out.get("total") == 3, f"got {s} {out}")

s, out = bot.handle_admin_order_detail(bearer(TOK_ADMIN), "RS-TEST002")
check("admin/order detail: full row + items + no delivery token",
      s == 200 and out.get("order", {}).get("code") == "RS-TEST002"
      and "delivery_token" not in json.dumps(out)
      and out["order"].get("buyer", {}).get("email") == "ali@example.com", f"got {s} {out}")
s, out = bot.handle_admin_order_detail(bearer(TOK_ADMIN), "RS-NOPE")
check("admin/order detail: unknown code -> 404", s == 404, f"got {s} {out}")
# detail items carry real product_ids: items_json lines store {"pid", "qty"}
# (create_order) — the detail reader used to look for "product_id" and
# returned 0 for every line. Admin list rows already had product_id.
_c = bot.db()
_pid = _c.execute("SELECT id FROM products WHERE name='Gemini Plus 6m Links (No Card Required)'").fetchone()["id"]
_apid = _c.execute("SELECT id FROM products WHERE name='Amazon Prime 6 Months'").fetchone()["id"]
_c.close()
_cart_code = bot.gen_order_code()
bot.create_order(0, "ali@example.com", [{"pid": _pid, "qty": 1}, {"pid": _apid, "qty": 2}],
                 "zurvapay", "Rs 2077 via Zurvapay (web)", "zurvapay", 2077,
                 _cart_code, "", 0, channel="web")
s, out = bot.handle_admin_order_detail(bearer(TOK_ADMIN), _cart_code)
_items = (out.get("order") or {}).get("items", [])
check("admin/order detail: items carry real product_ids (pid key)",
      s == 200 and len(_items) == 2
      and _items[0].get("product_id") == _pid and _items[0].get("qty") == 1
      and _items[1].get("product_id") == _apid and _items[1].get("qty") == 2,
      f"got {s} {_items}")
check("admin/order detail: top-level product_id is the first line",
      (out.get("order") or {}).get("product_id") == _pid, f"got {out.get('order')}")
# Test order hata do — baad wali HTTP checks total == 3 expect karti hain.
_c = bot.db()
_c.execute("DELETE FROM orders WHERE code=?", (_cart_code,))
_c.commit(); _c.close()

s, out = bot.handle_admin_users(bearer(TOK_ADMIN), {})
check("admin/users: lists users with order counts",
      s == 200 and out.get("total", 0) >= 4
      and any(u["email"] == "ali@example.com" and u["orders"] == 1 for u in out["users"])
      and any(u["email"] == ADMIN_EMAIL and u["orders"] == 1 for u in out["users"])
      and "password_hash" not in json.dumps(out), f"got {s} {out}")
s, out = bot.handle_admin_users(bearer(TOK_ADMIN), {"q": ["ali@example"]})
check("admin/users: q search", s == 200 and out.get("total") == 1, f"got {s} {out}")

# ---------- 8. OTP lock after 10 wrong attempts ----------
captured.clear()
s, out = bot.handle_admin_otp_send(
    {"identifier": ADMIN_EMAIL, "password": "adminpass123", "purpose": "login"}, H, fresh_ip())
check("otp/send: login purpose -> 200", s == 200, f"got {s} {out}")
LOGIN_CODE = captured[0][2]
wrong = 0
for i in range(10):
    s, out = bot.handle_admin_otp_verify(
        {"identifier": ADMIN_EMAIL, "purpose": "login", "code": "111111"}, H)
    if s == 401:
        wrong += 1
check("otp/verify: 10 wrong codes -> 401 each", wrong == 10, f"wrong={wrong}")
s, out = bot.handle_admin_otp_verify(
    {"identifier": ADMIN_EMAIL, "purpose": "login", "code": LOGIN_CODE}, H)
check("otp/verify: 11th attempt after 10 fails -> 429 locked", s == 429, f"got {s} {out}")
# unlock (test-only) and verify the right code still works
bot._OTP_LOCKS.pop(bot.db().execute("SELECT id FROM web_users WHERE email=?", (ADMIN_EMAIL,)).fetchone()["id"], None)
s, out = bot.handle_admin_otp_verify(
    {"identifier": ADMIN_EMAIL, "purpose": "login", "code": LOGIN_CODE}, H)
check("otp/verify: after lock expiry right code -> 200",
      s == 200 and len(out.get("token", "")) == 32, f"got {s} {out}")

# OTP send rate limit: 5/hour per admin (already sent 2; send 3 more ok, 6th -> 429)
for i in range(3):
    s, out = bot.handle_admin_otp_send(
        {"identifier": ADMIN_EMAIL, "password": "adminpass123", "purpose": "login"}, H, fresh_ip())
    check(f"otp/send: rate limit fill {i+1}/3 -> 200", s == 200, f"got {s} {out}")
s, out = bot.handle_admin_otp_send(
    {"identifier": ADMIN_EMAIL, "password": "adminpass123", "purpose": "login"}, H, fresh_ip())
check("otp/send: 6th send in an hour -> 429", s == 429, f"got {s} {out}")

bot._send_otp_sms = orig_sms
bot._send_otp_email = orig_email

# ---------- 8b. OTP Telegram channel + best-effort fallback ----------
# Twilio Pakistan me trial nahi deta — admin OTP ko Telegram se bhi
# chalna chahiye, aur koi ek channel missing ho to 503 nahi aana chahiye.
bot._OTP_SEND_RL.clear()
bot._OTP_LOCKS.clear()
bot.ADMIN_ID = 7965730503
bot.BOT_TOKEN = "123456:TESTTOKEN"
bot.TWILIO_ACCOUNT_SID = "ACtest"
bot.TWILIO_AUTH_TOKEN = "toktest"
bot.TWILIO_FROM_NUMBER = "+15005550006"
bot.RESEND_API_KEY = "re_test"
tg_captured = []
orig_tg = bot._send_otp_telegram
bot._send_otp_sms = lambda to, code: (captured.append(("sms", to, code)), True)[1]
bot._send_otp_email = lambda to, code: (captured.append(("email", to, code)), True)[1]
bot._send_otp_telegram = lambda code: (tg_captured.append(("telegram", code)), True)[1]

captured.clear(); tg_captured.clear()
s, out = bot.handle_admin_otp_send(
    {"identifier": ADMIN_EMAIL, "password": "adminpass123", "purpose": "login"}, H, fresh_ip())
check("otp/send: telegram channel on -> channels [sms, email, telegram]",
      s == 200 and out.get("channels") == ["sms", "email", "telegram"], f"got {s} {out}")
check("otp/send: telegram code is the SAME code as sms/email",
      bool(tg_captured) and tg_captured[0][1] == captured[0][2],
      f"got {tg_captured} {captured}")

# Twilio keys missing -> no 503, email + telegram still go
bot.TWILIO_ACCOUNT_SID = ""
bot.TWILIO_AUTH_TOKEN = ""
bot.TWILIO_FROM_NUMBER = ""
captured.clear(); tg_captured.clear()
s, out = bot.handle_admin_otp_send(
    {"identifier": ADMIN_EMAIL, "password": "adminpass123", "purpose": "login"}, H, fresh_ip())
check("otp/send: no Twilio keys -> 200 via [email, telegram], no 503",
      s == 200 and out.get("channels") == ["email", "telegram"], f"got {s} {out}")

# Resend key missing too -> telegram only
bot.RESEND_API_KEY = ""
captured.clear(); tg_captured.clear()
s, out = bot.handle_admin_otp_send(
    {"identifier": ADMIN_EMAIL, "password": "adminpass123", "purpose": "login"}, H, fresh_ip())
check("otp/send: no Twilio + no Resend -> 200 via [telegram] only",
      s == 200 and out.get("channels") == ["telegram"], f"got {s} {out}")

# sms + email fail, telegram ok -> 200 via telegram only
bot.TWILIO_ACCOUNT_SID = "ACtest"
bot.TWILIO_AUTH_TOKEN = "toktest"
bot.TWILIO_FROM_NUMBER = "+15005550006"
bot.RESEND_API_KEY = "re_test"
bot._send_otp_sms = lambda to, code: False
bot._send_otp_email = lambda to, code: False
captured.clear(); tg_captured.clear()
s, out = bot.handle_admin_otp_send(
    {"identifier": ADMIN_EMAIL, "password": "adminpass123", "purpose": "login"}, H, fresh_ip())
check("otp/send: sms+email fail, telegram ok -> 200 via [telegram]",
      s == 200 and out.get("channels") == ["telegram"], f"got {s} {out}")

# all three fail -> 502, code deleted (verify with it -> 401)
bot._send_otp_telegram = lambda code: (tg_captured.append(("telegram", code)), False)[1]
tg_captured.clear()
s, out = bot.handle_admin_otp_send(
    {"identifier": ADMIN_EMAIL, "password": "adminpass123", "purpose": "login"}, H, fresh_ip())
check("otp/send: all channels fail -> 502", s == 502, f"got {s} {out}")
dead_code = tg_captured[0][1] if tg_captured else "000000"
s, out = bot.handle_admin_otp_verify(
    {"identifier": ADMIN_EMAIL, "purpose": "login", "code": dead_code}, H)
check("otp/send: 502 code is deleted (verify -> 401)", s == 401, f"got {s} {out}")
_c = bot.db()
_ar = _c.execute("SELECT COUNT(*) n FROM issues WHERE kind='admin_otp_failed' "
                 "AND user_email=?", (ADMIN_EMAIL,)).fetchone()["n"]
_c.close()
check("issues: admin OTP all-channels failure logged (kind admin_otp_failed)",
      _ar == 1, f"got {_ar}")

# no channel at all (ADMIN_ID 0 + no keys) -> 503 otp_not_configured
bot.ADMIN_ID = 0
bot.TWILIO_ACCOUNT_SID = ""
bot.TWILIO_AUTH_TOKEN = ""
bot.TWILIO_FROM_NUMBER = ""
bot.RESEND_API_KEY = ""
s, out = bot.handle_admin_otp_send(
    {"identifier": ADMIN_EMAIL, "password": "adminpass123", "purpose": "login"}, H, fresh_ip())
check("otp/send: no channel at all -> 503 otp_not_configured",
      s == 503 and out.get("error") == "otp_not_configured", f"got {s} {out}")

# restore (HTTP section needs the real senders back; config state is
# irrelevant there — it only checks 400/401 paths)
bot.ADMIN_ID = 0
bot.BOT_TOKEN = ""
bot.TWILIO_ACCOUNT_SID = "ACtest"
bot.TWILIO_AUTH_TOKEN = "toktest"
bot.TWILIO_FROM_NUMBER = "+15005550006"
bot.RESEND_API_KEY = "re_test"
bot._send_otp_telegram = orig_tg
bot._send_otp_sms = orig_sms
bot._send_otp_email = orig_email

# ---------- 8c. Admin OTP via Gmail SMTP fallback ----------
# Resend fail ho to admin OTP email SMTP se jaye. Real _send_otp_email
# (mocked nahi) + mocked low-level senders. No network.
bot._OTP_SEND_RL.clear()
bot._OTP_LOCKS.clear()
bot.ADMIN_ID = 7965730503
bot.BOT_TOKEN = "123456:TESTTOKEN"
bot.TWILIO_ACCOUNT_SID = ""
bot.TWILIO_AUTH_TOKEN = ""
bot.TWILIO_FROM_NUMBER = ""
orig_smtp_user_a = bot.SMTP_USER
orig_smtp_pass_a = bot.SMTP_PASS
orig_resend_a = bot._send_email_via_resend
orig_smtp_a = bot._send_email_via_smtp
orig_resend_key_a = bot.RESEND_API_KEY
smtp_captured_a = []
resend_captured_a = []
bot._send_email_via_resend = lambda to, subj, text: (resend_captured_a.append((to, subj, text)), True)[1]
bot._send_email_via_smtp = lambda to, subj, text: (smtp_captured_a.append((to, subj, text)), True)[1]
bot._send_otp_telegram = lambda code: True  # mocked: telegram ok

# SMTP-only: no Resend key, SMTP set -> email channel via SMTP
bot.RESEND_API_KEY = ""
bot.SMTP_USER = "smtp@example.com"
bot.SMTP_PASS = "apppass1234567890"
smtp_captured_a.clear(); resend_captured_a.clear()
s, out = bot.handle_admin_otp_send(
    {"identifier": ADMIN_EMAIL, "password": "adminpass123", "purpose": "login"}, H, fresh_ip())
check("smtp-admin: SMTP-only -> 200 channels [email, telegram]",
      s == 200 and out.get("channels") == ["email", "telegram"], f"got {s} {out}")
check("smtp-admin: code went via SMTP to ADMIN_EMAIL",
      len(smtp_captured_a) == 1 and smtp_captured_a[0][0] == ADMIN_EMAIL
      and "admin login code" in smtp_captured_a[0][1], f"got {smtp_captured_a}")

# Resend fail -> SMTP fallback, channels still [email, telegram]
bot.RESEND_API_KEY = "re_test"
bot._send_email_via_resend = lambda to, subj, text: False
smtp_captured_a.clear(); resend_captured_a.clear()
s, out = bot.handle_admin_otp_send(
    {"identifier": ADMIN_EMAIL, "password": "adminpass123", "purpose": "login"}, H, fresh_ip())
check("smtp-admin: Resend fail -> SMTP fallback, 200 [email, telegram]",
      s == 200 and out.get("channels") == ["email", "telegram"]
      and len(smtp_captured_a) == 1, f"got {s} {out}")

# SMTP-only but no Telegram (real sender, BOT_TOKEN empty -> False,
# no network call) -> email only, no 503
bot._send_otp_telegram = orig_tg
bot.BOT_TOKEN = ""
smtp_captured_a.clear()
s, out = bot.handle_admin_otp_send(
    {"identifier": ADMIN_EMAIL, "password": "adminpass123", "purpose": "login"}, H, fresh_ip())
check("smtp-admin: SMTP-only, no Telegram -> 200 [email] only",
      s == 200 and out.get("channels") == ["email"], f"got {s} {out}")

# restore
bot.BOT_TOKEN = "123456:TESTTOKEN"
bot.SMTP_USER = orig_smtp_user_a
bot.SMTP_PASS = orig_smtp_pass_a
bot._send_email_via_resend = orig_resend_a
bot._send_email_via_smtp = orig_smtp_a
bot.RESEND_API_KEY = orig_resend_key_a
bot._send_otp_telegram = orig_tg

# ---------- 9. HTTP layer (real health server, admin routes) ----------
bot.start_health_server()
time.sleep(1.0)
import requests as rq
try:
    r = rq.get("http://127.0.0.1:18099/api/admin/stats", timeout=5)
    check("HTTP: GET /api/admin/stats no token -> 401", r.status_code == 401, f"got {r.status_code}")
    r = rq.get("http://127.0.0.1:18099/api/admin/orders",
               headers={"Authorization": f"Bearer {TOK_ALI}"}, timeout=5)
    check("HTTP: GET /api/admin/orders normal token -> 403", r.status_code == 403, f"got {r.status_code}")
    r = rq.get("http://127.0.0.1:18099/api/admin/orders",
               headers={"Authorization": f"Bearer {TOK_ADMIN}"}, timeout=5)
    check("HTTP: GET /api/admin/orders admin token -> 200 with orders",
          r.status_code == 200 and r.json().get("total") == 3, f"got {r.status_code} {r.text[:160]}")
    r = rq.get("http://127.0.0.1:18099/api/admin/order/RS-TEST001",
               headers={"Authorization": f"Bearer {TOK_ADMIN}"}, timeout=5)
    check("HTTP: GET /api/admin/order/RS-TEST001 -> 200",
          r.status_code == 200 and r.json().get("order", {}).get("code") == "RS-TEST001",
          f"got {r.status_code} {r.text[:160]}")
    r = rq.get("http://127.0.0.1:18099/api/admin/users",
               headers={"Authorization": f"Bearer {TOK_ADMIN}"}, timeout=5)
    check("HTTP: GET /api/admin/users admin token -> 200",
          r.status_code == 200 and r.json().get("total", 0) >= 4, f"got {r.status_code}")
    # otp/send over HTTP with keys absent (env globals reset by the test? no — still set)
    # -> senders are restored to real ones, but Twilio envs are test values;
    # the call would hit the network, so only check the route exists via a
    # bad-purpose 400 (validated before any send).
    r = rq.post("http://127.0.0.1:18099/api/admin/otp/send",
                json={"identifier": ADMIN_EMAIL, "password": "adminpass123", "purpose": "bogus"},
                headers={"X-Web-Secret": "test-secret-123"}, timeout=5)
    check("HTTP: POST /api/admin/otp/send bad purpose -> 400",
          r.status_code == 400, f"got {r.status_code} {r.text[:160]}")
    r = rq.post("http://127.0.0.1:18099/api/admin/otp/send",
                json={"identifier": ADMIN_EMAIL, "password": "adminpass123", "purpose": "login"},
                timeout=5)
    check("HTTP: POST /api/admin/otp/send without secret -> 401",
          r.status_code == 401, f"got {r.status_code}")
    r = rq.post("http://127.0.0.1:18099/api/admin/otp/verify",
                json={"identifier": ADMIN_EMAIL, "purpose": "login", "code": "12345"},
                headers={"X-Web-Secret": "test-secret-123"}, timeout=5)
    check("HTTP: POST /api/admin/otp/verify short code -> 400",
          r.status_code == 400, f"got {r.status_code} {r.text[:160]}")
except Exception as e:
    check("HTTP admin layer reachable", False, str(e))

# ---------- 10. Dashboard v2: visits + issues ----------
from datetime import datetime as _dt, timedelta as _td
c = bot.db()
_tabs10 = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
check("v2: site_visits + issues tables exist",
      "site_visits" in _tabs10 and "issues" in _tabs10, str(_tabs10))
c.execute("DELETE FROM site_visits")
c.execute("DELETE FROM issues")
c.commit(); c.close()

# --- visits aggregates (seeded directly, deterministic) ---
_now10 = int(time.time())
_today_start = (_now10 // 86400) * 86400  # UTC midnight
c = bot.db()
for sess, page, ts in [
        ("sess-a", "/", _now10 - 100), ("sess-a", "/products", _now10 - 200),
        ("sess-b", "/", _now10 - 300), ("sess-c", "/pricing", _now10 - 86400),
        ("sess-d", "/", _now10 - 8 * 86400), ("sess-e", "/", _now10 - 31 * 86400)]:
    c.execute("INSERT INTO site_visits(session_id, page, ts) VALUES(?,?,?)", (sess, page, ts))
c.commit(); c.close()

s, out = bot.handle_admin_visits()
check("visits: no token -> 401", s == 401, f"got {s} {out}")
s, out = bot.handle_admin_visits(bearer(TOK_ALI))
check("visits: normal user token -> 403", s == 403, f"got {s} {out}")
s, out = bot.handle_admin_visits(bearer(TOK_ADMIN))
check("visits: counts (today 3, 7d 4, 30d 5)",
      s == 200 and out.get("today") == 3 and out.get("last7d") == 4
      and out.get("last30d") == 5, f"got {s} {out}")
check("visits: uniques (today 2, 7d 3, 30d 4)",
      out.get("unique_today") == 2 and out.get("unique_7d") == 3
      and out.get("unique_30d") == 4, f"got {out}")
check("visits: by_page top 10, '/' first with 3",
      out.get("by_page") and out["by_page"][0] == {"page": "/", "count": 3}
      and len(out["by_page"]) == 3, f"got {out.get('by_page')}")
check("visits: hourly24 = 24 ints, sum 3 (last 24h)",
      len(out.get("hourly24", [])) == 24 and sum(out["hourly24"]) == 3,
      f"got {out.get('hourly24')}")
check("visits: daily30 = 30 ints, sum 5, today bucket 3, yesterday 1",
      len(out.get("daily30", [])) == 30 and sum(out["daily30"]) == 5
      and out["daily30"][-1] == 3 and out["daily30"][-2] == 1,
      f"got {out.get('daily30')}")

# --- track endpoint: stores, no auth, bad input rejected ---
s, out = bot.handle_track_visit({"page": "/home", "session": "sess-home"}, {}, "203.0.113.50")
check("track: happy path 200 + no auth needed",
      s == 200 and out.get("ok") is True, f"got {s} {out}")
c = bot.db()
_tr = c.execute("SELECT COUNT(*) n FROM site_visits WHERE page='/home' AND session_id='sess-home'").fetchone()["n"]
check("track: row stored in site_visits, no IP stored",
      _tr == 1 and "session_id" in [r[1] for r in c.execute("PRAGMA table_info(site_visits)").fetchall()]
      and "ip" not in [r[1] for r in c.execute("PRAGMA table_info(site_visits)").fetchall()],
      f"got {_tr}")
c.close()
for label, body in [
        ("page without leading slash", {"page": "home", "session": "sess-x"}),
        ("page too long", {"page": "/" + "x" * 200, "session": "sess-x"}),
        ("session with bad chars", {"page": "/home", "session": "sess x!"}),
        ("session too long", {"page": "/home", "session": "s" * 65}),
        ("missing session", {"page": "/home"}),
        ("missing page", {"session": "sess-x"}),
        ("non-string page", {"page": 123, "session": "sess-x"})]:
    s, out = bot.handle_track_visit(body, {}, "203.0.113.51")
    check(f"track: {label} -> 400", s == 400, f"got {s} {out}")

# --- track rate limit: 60/hour per IP ---
bot._VISIT_RL.clear()
_rl_ip = "203.0.113.99"
_results = []
for _i in range(61):
    s, _ = bot.handle_track_visit({"page": "/rl", "session": "sess-rl"}, {}, _rl_ip)
    _results.append(s)
check("track: rate limit 60/hour per IP (61st -> 429)",
      _results[:60] == [200] * 60 and _results[60] == 429, f"got {_results[-3:]}")
s, _ = bot.handle_track_visit({"page": "/rl", "session": "sess-rl"}, {}, "203.0.113.100")
check("track: rate limit is per IP (other IP still 200)", s == 200, f"got {s}")

# --- issues: stored + computed stuck orders ---
c = bot.db()
c.execute("DELETE FROM issues")
_now_i = int(time.time())
for email, name, kind, detail, ts, status in [
        ("a@example.com", "Ann", "login_failed", "Failed login attempt", _now_i - 60, "open"),
        ("b@example.com", "Bob", "email_otp_failed", "Email verification code could not be sent", _now_i - 120, "open"),
        ("a@example.com", "Ann", "login_failed", "Failed login attempt", _now_i - 30, "resolved")]:
    c.execute("INSERT INTO issues(user_email, user_name, kind, detail, ts, status) VALUES(?,?,?,?,?,?)",
              (email, name, kind, detail, ts, status))
_cutoff = (_dt.now() - _td(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
_old2h = (_dt.now() - _td(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
_old3h = (_dt.now() - _td(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
_new10m = (_dt.now() - _td(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
_new30m = (_dt.now() - _td(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
_pid10 = c.execute("SELECT id FROM products LIMIT 1").fetchone()["id"]
_stuck_ids = {}
for code, status, created, ch, contact, uname in [
        ("RS-STUCK1", "zurva_processing", _old2h, "web", "stuck@example.com", ""),
        ("RS-STUCK2", "paid_pending", _old3h, "telegram", "", "TGUser"),
        ("RS-FRESH1", "zurva_processing", _new10m, "web", "fresh@example.com", ""),
        ("RS-FRESH2", "paid_pending", _new30m, "web", "fresh2@example.com", "")]:
    cur = c.execute("INSERT INTO orders(user_id, username, product_id, payment_method, amount_text, "
                    "proof, status, account_id, created_at, code, items_json, qty, amount_pkr, channel, web_contact) "
                    "VALUES(0,?,?,'Zurvapay','Rs 100','', ?, NULL, ?, ?, '[]', 1, 100, ?, ?)",
                    (uname, _pid10, status, created, code, ch, contact))
    _stuck_ids[code] = cur.lastrowid
c.commit(); c.close()

s, out = bot.handle_admin_issues()
check("issues: no token -> 401", s == 401, f"got {s} {out}")
s, out = bot.handle_admin_issues(bearer(TOK_ALI), {})
check("issues: normal user token -> 403", s == 403, f"got {s} {out}")
s, out = bot.handle_admin_issues(bearer(TOK_ADMIN), {})
_iss = out.get("issues", [])
check("issues: 3 stored + 2 computed stuck = 5, newest first",
      s == 200 and len(_iss) == 5 and _iss[0]["kind"] == "login_failed"
      and _iss[0]["status"] == "resolved", f"got {s} {out}")
_comp = [i for i in _iss if i["source"] == "computed"]
check("issues: computed rows have negative ids, kind order_stuck, status open",
      len(_comp) == 2 and all(i["id"] < 0 and i["kind"] == "order_stuck"
                              and i["status"] == "open" for i in _comp)
      and sorted(i["id"] for i in _comp) == sorted(-_stuck_ids[c] for c in ("RS-STUCK1", "RS-STUCK2")),
      f"got {_comp}")
check("issues: computed detail names the order code",
      all(("RS-STUCK1" in i["detail"] or "RS-STUCK2" in i["detail"]) for i in _comp),
      f"got {_comp}")
check("issues: counts per kind (login 2, email 1, stuck 2) + open 4",
      out.get("counts") == {"login_failed": 2, "email_otp_failed": 1, "order_stuck": 2}
      and out.get("open") == 4, f"got {out.get('counts')} open={out.get('open')}")
check("issues: fresh (<1h) paid-pending orders are NOT stuck",
      not any("RS-FRESH" in i["detail"] for i in _iss), f"got {_iss}")
s, out = bot.handle_admin_issues(bearer(TOK_ADMIN), {"kind": ["login_failed"]})
check("issues: kind filter", s == 200 and len(out["issues"]) == 2
      and all(i["kind"] == "login_failed" for i in out["issues"]), f"got {s} {out}")
s, out = bot.handle_admin_issues(bearer(TOK_ADMIN), {"kind": ["order_stuck"]})
check("issues: kind=order_stuck filter -> only computed",
      s == 200 and len(out["issues"]) == 2
      and all(i["source"] == "computed" for i in out["issues"]), f"got {s} {out}")
s, out = bot.handle_admin_issues(bearer(TOK_ADMIN), {"status": ["resolved"]})
check("issues: status filter", s == 200 and len(out["issues"]) == 1
      and out["issues"][0]["status"] == "resolved", f"got {s} {out}")
s, out = bot.handle_admin_issues(bearer(TOK_ADMIN), {"q": ["a@example"]})
check("issues: q filter matches email", s == 200 and len(out["issues"]) == 2, f"got {s} {out}")
s, out = bot.handle_admin_issues(bearer(TOK_ADMIN), {"q": ["RS-STUCK1"]})
check("issues: q filter matches detail (order code)",
      s == 200 and len(out["issues"]) == 1 and out["issues"][0]["id"] == -_stuck_ids["RS-STUCK1"],
      f"got {s} {out}")

# --- resolve ---
_open_id = [i["id"] for i in _iss if i["source"] == "stored" and i["status"] == "open"][0]
s, out = bot.handle_admin_issue_resolve(None, str(_open_id))
check("issues/resolve: no token -> 401", s == 401, f"got {s} {out}")
s, out = bot.handle_admin_issue_resolve(bearer(TOK_ALI), str(_open_id))
check("issues/resolve: normal user token -> 403", s == 403, f"got {s} {out}")
s, out = bot.handle_admin_issue_resolve(bearer(TOK_ADMIN), str(-_stuck_ids["RS-STUCK1"]))
check("issues/resolve: computed row -> 400", s == 400, f"got {s} {out}")
s, out = bot.handle_admin_issue_resolve(bearer(TOK_ADMIN), "999999")
check("issues/resolve: unknown id -> 404", s == 404, f"got {s} {out}")
s, out = bot.handle_admin_issue_resolve(bearer(TOK_ADMIN), str(_open_id))
check("issues/resolve: stored row -> 200", s == 200 and out.get("ok") is True, f"got {s} {out}")
c = bot.db()
_rst = c.execute("SELECT status FROM issues WHERE id=?", (_open_id,)).fetchone()["status"]
c.close()
check("issues/resolve: DB row now resolved", _rst == "resolved", f"got {_rst}")

# --- stats new fields (expected values read live from the DB) ---
c = bot.db()
_exp_today = c.execute("SELECT COUNT(*) n FROM site_visits WHERE ts>=?", (_today_start,)).fetchone()["n"]
_exp_7d = c.execute("SELECT COUNT(*) n FROM site_visits WHERE ts>=?", (_now10 - 7 * 86400,)).fetchone()["n"]
_exp_open = c.execute("SELECT COUNT(*) n FROM issues WHERE status='open'").fetchone()["n"] + 2
c.close()
s, out = bot.handle_admin_stats(bearer(TOK_ADMIN))
_st = out.get("stats", {})
check("stats: visits_today / visits_7d / open_issues present and match DB",
      s == 200 and _st.get("visits_today") == _exp_today and _st.get("visits_7d") == _exp_7d
      and _st.get("open_issues") == _exp_open,
      f"got {_st} expected today={_exp_today} 7d={_exp_7d} open={_exp_open}")

# --- HTTP: new admin + track routes ---
try:
    r = rq.get("http://127.0.0.1:18099/api/admin/visits", timeout=5)
    check("HTTP: GET /api/admin/visits no token -> 401", r.status_code == 401, f"got {r.status_code}")
    r = rq.get("http://127.0.0.1:18099/api/admin/visits",
               headers={"Authorization": f"Bearer {TOK_ADMIN}"}, timeout=5)
    check("HTTP: GET /api/admin/visits admin -> 200 with aggregates",
          r.status_code == 200 and r.json().get("today") == _exp_today
          and len(r.json().get("hourly24", [])) == 24 and len(r.json().get("daily30", [])) == 30,
          f"got {r.status_code} {r.text[:160]}")
    r = rq.get("http://127.0.0.1:18099/api/admin/issues",
               headers={"Authorization": f"Bearer {TOK_ADMIN}"}, timeout=5)
    check("HTTP: GET /api/admin/issues admin -> 200 with issues",
          r.status_code == 200 and len(r.json().get("issues", [])) == 5
          and r.json().get("counts", {}).get("order_stuck") == 2,
          f"got {r.status_code} {r.text[:160]}")
    r = rq.get("http://127.0.0.1:18099/api/admin/issues?kind=order_stuck",
               headers={"Authorization": f"Bearer {TOK_ADMIN}"}, timeout=5)
    check("HTTP: GET /api/admin/issues?kind=order_stuck -> 2 computed",
          r.status_code == 200 and len(r.json().get("issues", [])) == 2,
          f"got {r.status_code} {r.text[:160]}")
    r = rq.post("http://127.0.0.1:18099/api/track/visit",
                json={"page": "/http-test", "session": "sess-http"}, timeout=5)
    check("HTTP: POST /api/track/visit no auth -> 200",
          r.status_code == 200 and r.json().get("ok") is True,
          f"got {r.status_code} {r.text[:160]}")
    r = rq.post("http://127.0.0.1:18099/api/track/visit",
                json={"page": "no-slash", "session": "sess-http"}, timeout=5)
    check("HTTP: POST /api/track/visit bad page -> 400", r.status_code == 400, f"got {r.status_code}")
    # resolve over HTTP: fresh stored row
    c = bot.db()
    cur = c.execute("INSERT INTO issues(user_email, user_name, kind, detail, ts, status) "
                    "VALUES('http@example.com','H','login_failed','Failed login attempt',?, 'open')",
                    (int(time.time()),))
    _http_issue = cur.lastrowid
    c.commit(); c.close()
    r = rq.post(f"http://127.0.0.1:18099/api/admin/issues/{_http_issue}/resolve",
                headers={"Authorization": f"Bearer {TOK_ALI}"}, timeout=5)
    check("HTTP: POST resolve normal token -> 403", r.status_code == 403, f"got {r.status_code}")
    r = rq.post(f"http://127.0.0.1:18099/api/admin/issues/{_http_issue}/resolve",
                headers={"Authorization": f"Bearer {TOK_ADMIN}"}, timeout=5)
    check("HTTP: POST resolve admin -> 200",
          r.status_code == 200 and r.json().get("ok") is True,
          f"got {r.status_code} {r.text[:160]}")
    r = rq.post("http://127.0.0.1:18099/api/admin/issues/-123/resolve",
                headers={"Authorization": f"Bearer {TOK_ADMIN}"}, timeout=5)
    check("HTTP: POST resolve computed id -> 400", r.status_code == 400, f"got {r.status_code}")
except Exception as e:
    check("HTTP v2 layer reachable", False, str(e))

# ---------- 11. Global promo coupons (admin dashboard) ----------
# Tables + validate_promo edge cases + admin CRUD + uses + TG buy_totals.
_c = bot.db()
_tabs = [r[0] for r in _c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
check("promo: promo_codes + promo_uses + stock_events tables exist",
      all(t in _tabs for t in ("promo_codes", "promo_uses", "stock_events")), str(_tabs))
_c.close()

# validate_promo direct: har error code, isi order me.
_c = bot.db()
_c.execute("INSERT INTO promo_codes(code,pct_off,max_uses,used_count,expires_at,min_order_pkr,first_order_only,active,created_at) "
           "VALUES('SAVE10',10,NULL,0,NULL,0,0,1,'2026-11-17 10:00')")
_c.execute("INSERT INTO promo_codes(code,pct_off,max_uses,used_count,expires_at,min_order_pkr,first_order_only,active,created_at) "
           "VALUES('OFF20',20,2,2,NULL,0,0,1,'2026-11-17 10:00')")
_c.execute("INSERT INTO promo_codes(code,pct_off,max_uses,used_count,expires_at,min_order_pkr,first_order_only,active,created_at) "
           "VALUES('OLD5',5,NULL,0,'2020-01-01 00:00',0,0,1,'2026-11-17 10:00')")
_c.execute("INSERT INTO promo_codes(code,pct_off,max_uses,used_count,expires_at,min_order_pkr,first_order_only,active,created_at) "
           "VALUES('MIN50',10,NULL,0,NULL,500,0,1,'2026-11-17 10:00')")
_c.execute("INSERT INTO promo_codes(code,pct_off,max_uses,used_count,expires_at,min_order_pkr,first_order_only,active,created_at) "
           "VALUES('FIRST15',15,NULL,0,NULL,0,1,1,'2026-11-17 10:00')")
_c.execute("INSERT INTO promo_codes(code,pct_off,max_uses,used_count,expires_at,min_order_pkr,first_order_only,active,created_at) "
           "VALUES('BAND25',25,NULL,0,NULL,0,0,0,'2026-11-17 10:00')")
_c.commit(); _c.close()
check("promo: validate SAVE10 10% of 1000 = 100",
      bot.validate_promo(" save10 ", 1000, False) == (True, 10, 100, None),
      f"got {bot.validate_promo('save10', 1000, False)}")
check("promo: unknown -> coupon_not_found",
      bot.validate_promo("NOPE99", 1000, False)[3] == "coupon_not_found")
check("promo: inactive -> coupon_inactive",
      bot.validate_promo("BAND25", 1000, False)[3] == "coupon_inactive")
check("promo: expired -> coupon_expired",
      bot.validate_promo("OLD5", 1000, False)[3] == "coupon_expired")
check("promo: exhausted (used==max) -> coupon_exhausted",
      bot.validate_promo("OFF20", 1000, False)[3] == "coupon_exhausted")
check("promo: below min order -> coupon_min_order",
      bot.validate_promo("MIN50", 499, False)[3] == "coupon_min_order")
check("promo: min order met -> ok",
      bot.validate_promo("MIN50", 500, False)[0] is True)
check("promo: first-only, not first order -> coupon_first_only",
      bot.validate_promo("FIRST15", 1000, False)[3] == "coupon_first_only")
check("promo: first-only, first order -> ok 15%",
      bot.validate_promo("FIRST15", 1000, True) == (True, 15, 150, None))
check("promo: discount capped at amount (100% of 50)",
      bot.validate_promo("SAVE10", 0, False) == (True, 10, 0, None))
check("promo: failed payment does NOT consume a use (validate only)",
      bot.db().execute("SELECT used_count FROM promo_codes WHERE code='SAVE10'").fetchone()["used_count"] == 0)

# Double-use guard: ek hi order_code pe do dafa record -> ek row, count 1.
_c = bot.db()
_first = bot._record_promo_use_conn(_c, "SAVE10", "RS-GUARD1", "Test", "t@example.com")
_second = bot._record_promo_use_conn(_c, "SAVE10", "RS-GUARD1", "Test", "t@example.com")
_c.commit()
_rows = _c.execute("SELECT COUNT(*) n FROM promo_uses WHERE code='SAVE10' AND order_code='RS-GUARD1'").fetchone()["n"]
_cnt = _c.execute("SELECT used_count FROM promo_codes WHERE code='SAVE10'").fetchone()["used_count"]
_c.close()
check("promo: double-use guard (record twice -> 1 row, used_count 1)",
      _first is True and _second is False and _rows == 1 and _cnt == 1,
      f"first={_first} second={_second} rows={_rows} count={_cnt}")
# FIRST-ORDER code global table me nahi — record helper use nahi karta.
_c = bot.db()
check("promo: FIRST-ORDER is not a global code (record returns False)",
      bot._record_promo_use_conn(_c, "FIRST-ORDER", "RS-GUARD2") is False)
_c.close()

# ---------- 11b. coupon admin endpoints: auth + CRUD ----------
s, out = bot.handle_admin_coupons()
check("admin/coupons: no token -> 401", s == 401, f"got {s} {out}")
s, out = bot.handle_admin_coupons(bearer(TOK_ALI))
check("admin/coupons: normal user token -> 403", s == 403, f"got {s} {out}")
s, out = bot.handle_admin_coupons(bearer(TOK_ADMIN))
check("admin/coupons: list has seeded codes, newest first",
      s == 200 and len(out.get("coupons", [])) == 6, f"got {s} {out}")
s, out = bot.handle_admin_coupon_create(
    {"code": "save20", "pct_off": 20, "max_uses": 5, "expires_at": "2099-01-01",
     "min_order_pkr": 100, "first_order_only": 0}, bearer(TOK_ADMIN))
check("admin/coupons: create lower-case code -> 201, stored upper, date normalised",
      s == 201 and out.get("coupon", {}).get("code") == "SAVE20"
      and out["coupon"]["expires_at"] == "2099-01-01 23:59"
      and out["coupon"]["pct_off"] == 20, f"got {s} {out}")
s, out = bot.handle_admin_coupon_create(
    {"code": "SAVE20", "pct_off": 10}, bearer(TOK_ADMIN))
check("admin/coupons: duplicate code -> 400 coupon_exists",
      s == 400 and out.get("error") == "coupon_exists", f"got {s} {out}")
for _bad, _field in [({"code": "ab", "pct_off": 10}, "code"),
                     ({"code": "BAD CODE!", "pct_off": 10}, "code"),
                     ({"code": "OK10", "pct_off": 0}, "pct_off"),
                     ({"code": "OK10", "pct_off": 101}, "pct_off"),
                     ({"code": "OK10", "pct_off": 10, "max_uses": 0}, "max_uses"),
                     ({"code": "OK10", "pct_off": 10, "expires_at": "2020-01-01"}, "expires_at"),
                     ({"code": "OK10", "pct_off": 10, "expires_at": "not-a-date"}, "expires_at"),
                     ({"code": "OK10", "pct_off": 10, "min_order_pkr": -1}, "min_order_pkr")]:
    s, out = bot.handle_admin_coupon_create(_bad, bearer(TOK_ADMIN))
    check(f"admin/coupons: invalid {_field} -> 400 coupon_invalid",
          s == 400 and out.get("error") == "coupon_invalid" and out.get("field") == _field,
          f"got {s} {out}")
s, out = bot.handle_admin_coupon_create({"code": "OK10", "pct_off": 10}, {})
check("admin/coupons: create no token -> 401", s == 401, f"got {s} {out}")
s, out = bot.handle_admin_coupon_create({"code": "OK10", "pct_off": 10}, bearer(TOK_ALI))
check("admin/coupons: create normal token -> 403", s == 403, f"got {s} {out}")
s, out = bot.handle_admin_coupon_toggle(bearer(TOK_ADMIN), "SAVE20")
check("admin/coupons: toggle -> active 0",
      s == 200 and out.get("active") == 0, f"got {s} {out}")
s, out = bot.handle_coupon_validate({"code": "SAVE20", "amount_pkr": 1000}, fresh_ip())
check("admin/coupons: toggled-off code validates coupon_inactive",
      s == 400 and out.get("error") == "coupon_inactive", f"got {s} {out}")
s, out = bot.handle_admin_coupon_toggle(bearer(TOK_ADMIN), "SAVE20")
check("admin/coupons: toggle back -> active 1",
      s == 200 and out.get("active") == 1, f"got {s} {out}")
s, out = bot.handle_admin_coupon_toggle(bearer(TOK_ADMIN), "GHOST99")
check("admin/coupons: toggle unknown -> 404", s == 404, f"got {s} {out}")
s, out = bot.handle_admin_coupon_uses(bearer(TOK_ADMIN), "SAVE10")
check("admin/coupons: uses list has the guard row",
      s == 200 and len(out.get("uses", [])) == 1
      and out["uses"][0]["order_code"] == "RS-GUARD1", f"got {s} {out}")
s, out = bot.handle_admin_coupon_uses(bearer(TOK_ADMIN), "GHOST99")
check("admin/coupons: uses unknown -> 404", s == 404, f"got {s} {out}")
s, out = bot.handle_admin_coupon_delete({}, bearer(TOK_ADMIN), "SAVE10")
check("admin/coupons: delete with uses, no force -> 400 coupon_has_uses",
      s == 400 and out.get("error") == "coupon_has_uses", f"got {s} {out}")
s, out = bot.handle_admin_coupon_delete({"force": True}, bearer(TOK_ADMIN), "SAVE10")
check("admin/coupons: delete with force -> 200 + uses rows gone",
      s == 200 and out.get("deleted") is True
      and bot.db().execute("SELECT COUNT(*) n FROM promo_uses WHERE code='SAVE10'").fetchone()["n"] == 0,
      f"got {s} {out}")
s, out = bot.handle_admin_coupon_delete({}, bearer(TOK_ADMIN), "SAVE10")
check("admin/coupons: delete unknown -> 404", s == 404, f"got {s} {out}")
s, out = bot.handle_admin_coupon_delete({}, bearer(TOK_ALI), "SAVE20")
check("admin/coupons: delete normal token -> 403", s == 403, f"got {s} {out}")
s, out = bot.handle_admin_coupon_delete({}, {}, "SAVE20")
check("admin/coupons: delete no token -> 401", s == 401, f"got {s} {out}")
# Unused code deletes without force.
s, out = bot.handle_admin_coupon_delete({}, bearer(TOK_ADMIN), "BAND25")
check("admin/coupons: delete unused code (no force) -> 200",
      s == 200 and out.get("deleted") is True, f"got {s} {out}")

# ---------- 11c. POST /api/coupon/validate (public) ----------
s, out = bot.handle_coupon_validate({"code": "SAVE20", "amount_pkr": 1000, "is_first_order": False}, fresh_ip())
check("coupon/validate: happy path 20% of 1000 = 200",
      s == 200 and out == {"ok": True, "pct": 20, "discount": 200}, f"got {s} {out}")
s, out = bot.handle_coupon_validate({"code": "NOPE99", "amount_pkr": 1000}, fresh_ip())
check("coupon/validate: unknown -> 400 coupon_not_found",
      s == 400 and out.get("error") == "coupon_not_found", f"got {s} {out}")
s, out = bot.handle_coupon_validate({"code": "FIRST15", "amount_pkr": 1000, "is_first_order": False}, fresh_ip())
check("coupon/validate: first-only, not first -> 400 coupon_first_only",
      s == 400 and out.get("error") == "coupon_first_only", f"got {s} {out}")
s, out = bot.handle_coupon_validate({"code": "SAVE20", "amount_pkr": -5}, fresh_ip())
check("coupon/validate: negative amount -> 400", s == 400, f"got {s} {out}")
s, out = bot.handle_coupon_validate({"code": "SAVE20"}, fresh_ip())
check("coupon/validate: missing amount -> 400", s == 400, f"got {s} {out}")
_rl_ip = fresh_ip()
_rl = [bot.handle_coupon_validate({"code": "SAVE20", "amount_pkr": 100}, _rl_ip)[0] for _ in range(31)]
check("coupon/validate: rate limit 31st call -> 429",
      _rl[:30] == [200] * 30 and _rl[30] == 429, f"got {_rl[-3:]}")

# ---------- 11d. Telegram buy_totals: global first, FIRST-ORDER fallback ----------
from types import SimpleNamespace as _NS
_prod1 = bot.db().execute("SELECT id, price_pkr FROM products ORDER BY id LIMIT 1").fetchone()
_P1 = _prod1["id"]
_P1_PRICE = _prod1["price_pkr"]
def _ctx(code):
    return _NS(user_data={"buy_mode": "single", "buy_pid": _P1, "buy_qty": 1,
                          "buy_coupon_code": code})
_lines, _sub, _disc, _tot = bot.buy_totals(_ctx("SAVE20"), 555001)
check("buy_totals: global SAVE20 gives 20% (not FIRST-ORDER 7%)",
      _sub == _P1_PRICE and _disc == round(_P1_PRICE * 20 / 100)
      and _tot == _sub - _disc, f"got sub={_sub} disc={_disc}")
bot.ensure_first_coupon(555001)
_lines, _sub, _disc, _tot = bot.buy_totals(_ctx("FIRST-ORDER"), 555001)
check("buy_totals: FIRST-ORDER fallback still 7%",
      _disc == round(_P1_PRICE * 7 / 100), f"got disc={_disc}")
_c = bot.db()
_c.execute("INSERT INTO orders(user_id, username, product_id, payment_method, amount_text, proof, "
           "status, created_at, code, items_json, qty, amount_pkr, channel) "
           "VALUES(555002,'u',?,'Easypaisa','Rs 100','', 'delivered', ?, 'RS-FIRSTDONE', '[]', 1, 100, 'telegram')",
           (_P1, bot._now_str()))
_c.commit(); _c.close()
_lines, _sub, _disc, _tot = bot.buy_totals(_ctx("FIRST15"), 555002)
check("buy_totals: first-only global code, already ordered -> discount 0",
      _disc == 0, f"got disc={_disc}")

# ---------- 11e. TG deliver_order records global promo use (not per-user table) ----------
import asyncio as _asyncio
class _FakeBot:
    def __init__(self):
        self.sent = []
    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
        self.sent.append((chat_id, text))
        return None
_c = bot.db()
_c.execute("INSERT INTO accounts(product_id, login, password, extra, status) VALUES(?, 'tg@login.com', 'tgpass', '', 'available')", (_P1,))
_c.commit(); _c.close()
_tg_oid = bot.create_order(555003, "tgbuyer", [{"pid": _P1, "qty": 1}], "Easypaisa",
                           f"Rs {_P1}", "proof", _P1, bot.gen_order_code(), "SAVE20", 0, channel="telegram")
_fake_ctx = _NS(bot=_FakeBot())
_ok = _asyncio.run(bot.deliver_order(_fake_ctx, _tg_oid))
_c = bot.db()
_o = _c.execute("SELECT status, code FROM orders WHERE id=?", (_tg_oid,)).fetchone()
_uses = _c.execute("SELECT COUNT(*) n FROM promo_uses WHERE code='SAVE20' AND order_code=?", (_o["code"],)).fetchone()["n"]
_cnt20 = _c.execute("SELECT used_count FROM promo_codes WHERE code='SAVE20'").fetchone()["used_count"]
_c.close()
check("deliver_order: global coupon -> delivered + promo_uses row + used_count 1",
      _ok is True and _o["status"] == "delivered" and _uses == 1 and _cnt20 == 1,
      f"ok={_ok} status={_o['status'] if _o else None} uses={_uses} count={_cnt20}")
# FIRST-ORDER delivery: per-user coupons.used=1, promo_uses me kuch nahi.
_c = bot.db()
_c.execute("INSERT INTO accounts(product_id, login, password, extra, status) VALUES(?, 'tg2@login.com', 'tgpass2', '', 'available')", (_P1,))
_c.commit(); _c.close()
bot.ensure_first_coupon(555004)
_fo_oid = bot.create_order(555004, "tgbuyer2", [{"pid": _P1, "qty": 1}], "Easypaisa",
                           f"Rs {_P1}", "proof", _P1, bot.gen_order_code(), "FIRST-ORDER", 0, channel="telegram")
_ok2 = _asyncio.run(bot.deliver_order(_NS(bot=_FakeBot()), _fo_oid))
_c = bot.db()
_used = _c.execute("SELECT used FROM coupons WHERE user_id=555004 AND code='FIRST-ORDER'").fetchone()["used"]
_pu = _c.execute("SELECT COUNT(*) n FROM promo_uses WHERE code='FIRST-ORDER'").fetchone()["n"]
_c.close()
check("deliver_order: FIRST-ORDER -> per-user used=1, no promo_uses row",
      _ok2 is True and _used == 1 and _pu == 0, f"ok={_ok2} used={_used} promo_uses={_pu}")

# ---------- 12. Products admin endpoints ----------
s, out = bot.handle_admin_products()
check("admin/products: no token -> 401", s == 401, f"got {s} {out}")
s, out = bot.handle_admin_products(bearer(TOK_ALI))
check("admin/products: normal token -> 403", s == 403, f"got {s} {out}")
s, out = bot.handle_admin_products(bearer(TOK_ADMIN))
_plist = out.get("products", [])
check("admin/products: list has all products with stock/sold/ahm7 fields",
      s == 200 and len(_plist) >= 42
      and all(set(("id", "name", "price_pkr", "price_usdt", "kind", "category", "active",
                    "description", "terms",
                    "local_stock", "sold", "ahm7_mapped", "ahm7_product_id",
                    "ahm7_price_pkr", "margin_pkr")) <= set(p.keys()) for p in _plist),
      f"got {s} len={len(_plist)}")
_ama = [p for p in _plist if p["name"] == "Amazon Prime 6 Months"][0]
check("admin/products: Amazon mapped, cost 668, margin = price - cost",
      _ama["ahm7_mapped"] is True and _ama["ahm7_price_pkr"] == 668
      and _ama["margin_pkr"] == _ama["price_pkr"] - 668, f"got {_ama}")
_unmapped = [p for p in _plist if not p["ahm7_mapped"]]
check("admin/products: unmapped products have null margin",
      len(_unmapped) >= 1 and all(p["margin_pkr"] is None for p in _unmapped),
      f"got {len(_unmapped)}")
s, out = bot.handle_admin_product_create(
    {"name": "Test Product Alpha", "price_pkr": 560, "kind": "account",
     "category": "TestCat", "description": "d", "terms": "t"}, bearer(TOK_ADMIN))
check("admin/products: create -> 201 + id",
      s == 201 and isinstance(out.get("id"), int), f"got {s} {out}")
_TPID = out.get("id")
_c = bot.db()
_tp = _c.execute("SELECT price_usdt, kind, category FROM products WHERE id=?", (_TPID,)).fetchone()
_c.close()
check("admin/products: price_usdt derived like seed (560/280 = 2.0)",
      _tp["price_usdt"] == 2.0 and _tp["kind"] == "account" and _tp["category"] == "TestCat",
      f"got {dict(_tp)}")
s, out = bot.handle_admin_product_create(
    {"name": "test product alpha", "price_pkr": 100, "kind": "account", "category": "X"},
    bearer(TOK_ADMIN))
check("admin/products: duplicate name (case-insensitive) -> 400 product_exists",
      s == 400 and out.get("error") == "product_exists", f"got {s} {out}")
for _bad, _field in [({"name": "", "price_pkr": 100, "kind": "account", "category": "X"}, "name"),
                     ({"name": "B", "price_pkr": 0, "kind": "account", "category": "X"}, "price_pkr"),
                     ({"name": "B", "price_pkr": 100, "kind": "weird", "category": "X"}, "kind"),
                     ({"name": "B", "price_pkr": 100, "kind": "account", "category": ""}, "category")]:
    s, out = bot.handle_admin_product_create(_bad, bearer(TOK_ADMIN))
    check(f"admin/products: invalid {_field} -> 400",
          s == 400 and out.get("error") == "product_invalid", f"got {s} {out}")
# Price change reflects in _site_price_pkr (future checkouts).
s, out = bot.handle_admin_product_price({"price_pkr": 700}, bearer(TOK_ADMIN), _TPID)
check("admin/products: price change -> 200 + new price",
      s == 200 and out.get("price_pkr") == 700, f"got {s} {out}")
_c = bot.db()
_tp2 = _c.execute("SELECT * FROM products WHERE id=?", (_TPID,)).fetchone()
_c.close()
check("admin/products: _site_price_pkr follows new DB price (700)",
      bot._site_price_pkr(_tp2, {}) == 700 and _tp2["price_usdt"] == 2.5,
      f"got {dict(_tp2)}")
s, out = bot.handle_admin_product_price({"price_pkr": 700}, bearer(TOK_ADMIN), 999999)
check("admin/products: price unknown id -> 404", s == 404, f"got {s} {out}")
s, out = bot.handle_admin_product_price({"price_pkr": -1}, bearer(TOK_ADMIN), _TPID)
check("admin/products: price <= 0 -> 400", s == 400, f"got {s} {out}")
s, out = bot.handle_admin_product_toggle(bearer(TOK_ADMIN), _TPID)
check("admin/products: toggle -> active 0",
      s == 200 and out.get("active") == 0, f"got {s} {out}")
s, out = bot.handle_admin_product_toggle(bearer(TOK_ADMIN), _TPID)
check("admin/products: toggle back -> active 1",
      s == 200 and out.get("active") == 1, f"got {s} {out}")
s, out = bot.handle_admin_product_toggle(bearer(TOK_ADMIN), 999999)
check("admin/products: toggle unknown -> 404", s == 404, f"got {s} {out}")
s, out = bot.handle_admin_product_details(
    {"description": "new desc", "terms": "new terms"}, bearer(TOK_ADMIN), _TPID)
check("admin/products: details -> 200 + stored",
      s == 200 and out.get("ok") is True
      and bot.db().execute("SELECT description FROM products WHERE id=?", (_TPID,)).fetchone()["description"] == "new desc",
      f"got {s} {out}")
s, out = bot.handle_admin_product_details({"description": "x" * 2001}, bearer(TOK_ADMIN), _TPID)
check("admin/products: details overlong -> 400", s == 400, f"got {s} {out}")
s, out = bot.handle_admin_product_details({"description": "x"}, bearer(TOK_ADMIN), 999999)
check("admin/products: details unknown -> 404", s == 404, f"got {s} {out}")

# ---------- 12b. Admin-edited price lock: CSV sync respects admin edits ----------
# "Amazon Prime 6 Months" CSV me hai (CSV price 799, desc "Limited Deal -
# was PKR 850"); init_db ka sync iski DB price 799 pe la chuka hai.
_c = bot.db()
_ama_id = _c.execute("SELECT id FROM products WHERE name='Amazon Prime 6 Months'").fetchone()["id"]
_ama0 = _c.execute("SELECT price_pkr, price_usdt, description, terms, admin_edited FROM products WHERE id=?", (_ama_id,)).fetchone()
_ama_terms0 = _ama0["terms"]
_c.close()
check("price lock: migration adds admin_edited=0, CSV price 799 after init sync",
      _ama0["admin_edited"] == 0 and _ama0["price_pkr"] == 799, f"got {dict(_ama0)}")
# 1) Non-edited product: sync changed price ko CSV price pe wapas lata hai.
_c = bot.db()
_c.execute("UPDATE products SET price_pkr=100 WHERE id=?", (_ama_id,))
_c.commit()
bot._sync_products_from_csv(_c)
_c.commit()
_p = _c.execute("SELECT price_pkr FROM products WHERE id=?", (_ama_id,)).fetchone()
_c.close()
check("price lock: non-edited product syncs back to CSV price 799",
      _p["price_pkr"] == 799, f"got {_p['price_pkr']}")
# 2) Admin price edit (API) flag lagata hai; sync phir price ko chhoo nahi sakta.
s, out = bot.handle_admin_product_price({"price_pkr": 750}, bearer(TOK_ADMIN), _ama_id)
_c = bot.db()
_p = _c.execute("SELECT price_pkr, admin_edited FROM products WHERE id=?", (_ama_id,)).fetchone()
check("price lock: admin price edit sets admin_edited=1",
      s == 200 and _p["admin_edited"] == 1 and _p["price_pkr"] == 750, f"got {s} {dict(_p)}")
bot._sync_products_from_csv(_c)
_c.commit()
_p = _c.execute("SELECT price_pkr, description FROM products WHERE id=?", (_ama_id,)).fetchone()
_c.close()
check("price lock: admin-edited price (750) survives CSV sync",
      _p["price_pkr"] == 750, f"got {dict(_p)}")
# 3) Description edit (API) bhi lock karta hai; terms-only edit nahi.
_c = bot.db()
_c.execute("UPDATE products SET price_pkr=799, price_usdt=2.85, description='Limited Deal - was PKR 850', admin_edited=0 WHERE id=?", (_ama_id,))
_c.commit(); _c.close()
s, out = bot.handle_admin_product_details({"description": "admin wali description"}, bearer(TOK_ADMIN), _ama_id)
_c = bot.db()
_p = _c.execute("SELECT description, admin_edited FROM products WHERE id=?", (_ama_id,)).fetchone()
check("price lock: admin description edit sets admin_edited=1",
      s == 200 and _p["admin_edited"] == 1 and _p["description"] == "admin wali description", f"got {s} {dict(_p)}")
bot._sync_products_from_csv(_c)
_c.commit()
_p = _c.execute("SELECT description FROM products WHERE id=?", (_ama_id,)).fetchone()
_c.close()
check("price lock: admin-edited description survives CSV sync",
      _p["description"] == "admin wali description", f"got {dict(_p)}")
_c = bot.db()
_c.execute("UPDATE products SET description='Limited Deal - was PKR 850', admin_edited=0 WHERE id=?", (_ama_id,))
_c.commit(); _c.close()
s, out = bot.handle_admin_product_details({"terms": "sirf terms badle"}, bearer(TOK_ADMIN), _ama_id)
_c = bot.db()
_p = _c.execute("SELECT admin_edited FROM products WHERE id=?", (_ama_id,)).fetchone()
check("price lock: terms-only edit does NOT set admin_edited",
      s == 200 and _p["admin_edited"] == 0, f"got {s} {dict(_p)}")
bot._sync_products_from_csv(_c)
_c.commit()
_p = _c.execute("SELECT price_pkr FROM products WHERE id=?", (_ama_id,)).fetchone()
_c.close()
check("price lock: terms-only row still syncs (price 799)",
      _p["price_pkr"] == 799, f"got {_p['price_pkr']}")
# 4) GET /api/admin/products flag expose karta hai; phir row wapas CSV jaisi.
s, out = bot.handle_admin_products(bearer(TOK_ADMIN))
_ama = [p for p in out.get("products", []) if p["name"] == "Amazon Prime 6 Months"][0]
check("price lock: GET /api/admin/products exposes admin_edited=false",
      s == 200 and "admin_edited" in _ama and _ama["admin_edited"] is False, f"got {_ama}")
_c = bot.db()
_c.execute("UPDATE products SET price_pkr=799, price_usdt=2.85, description='Limited Deal - was PKR 850', terms=?, admin_edited=0 WHERE id=?", (_ama_terms0, _ama_id))
_c.commit(); _c.close()

# ---------- 13. Stock admin endpoints ----------
s, out = bot.handle_admin_stock_add(
    {"lines": ["stock1@login.com | pass1",
               "CODE9 | mail@login.com | https://mailbox.example",
               "badline-no-separator",
               "   ",
               "onlylogin | ",
               "comma@login.com,commapass"]},
    bearer(TOK_ADMIN), _TPID)
check("stock/add: 3 valid added, 2 malformed skipped, blank silent",
      s == 200 and out.get("added") == 3 and len(out.get("skipped", [])) == 2
      and all(x["reason"] == "format_ghalat_hai" for x in out["skipped"]),
      f"got {s} {out}")
_c = bot.db()
_accs = _c.execute("SELECT login, password, extra, status FROM accounts WHERE product_id=? ORDER BY id", (_TPID,)).fetchall()
_ev = _c.execute("SELECT action, count, by_admin FROM stock_events WHERE product_id=? ORDER BY id DESC LIMIT 1", (_TPID,)).fetchone()
_c.close()
check("stock/add: mail line stored extra=code, login=email, password=url",
      any(a["login"] == "mail@login.com" and a["extra"] == "CODE9"
          and a["password"] == "https://mailbox.example" for a in _accs),
      f"got {[dict(a) for a in _accs]}")
check("stock/add: stock_events row logged (add, count 3, admin email)",
      _ev["action"] == "add" and _ev["count"] == 3 and _ev["by_admin"] == ADMIN_EMAIL,
      f"got {dict(_ev)}")
s, out = bot.handle_admin_stock_add({"lines": []}, bearer(TOK_ADMIN), _TPID)
check("stock/add: empty lines -> 400", s == 400, f"got {s} {out}")
s, out = bot.handle_admin_stock_add({"lines": ["a@b.com | p"]}, bearer(TOK_ADMIN), 999999)
check("stock/add: unknown product -> 404", s == 404, f"got {s} {out}")
s, out = bot.handle_admin_stock_add({"lines": ["a@b.com | p"]}, {}, _TPID)
check("stock/add: no token -> 401", s == 401, f"got {s} {out}")
# Mark one account sold, then remove: sold rows never touched.
_c = bot.db()
_sold_id = _c.execute("SELECT id FROM accounts WHERE product_id=? ORDER BY id LIMIT 1", (_TPID,)).fetchone()["id"]
_c.execute("UPDATE accounts SET status='sold', sold_to=1 WHERE id=?", (_sold_id,))
_c.commit(); _c.close()
s, out = bot.handle_admin_stock_remove({"count": 99}, bearer(TOK_ADMIN), _TPID)
check("stock/remove: more than available -> 400 stock_kam_hai + available",
      s == 400 and out.get("error") == "stock_kam_hai" and out.get("available") == 2,
      f"got {s} {out}")
s, out = bot.handle_admin_stock_remove({"count": 2}, bearer(TOK_ADMIN), _TPID)
check("stock/remove: removes 2 oldest available only",
      s == 200 and out.get("removed") == 2, f"got {s} {out}")
_c = bot.db()
_left = _c.execute("SELECT id, status FROM accounts WHERE product_id=?", (_TPID,)).fetchall()
_ev2 = _c.execute("SELECT action, count FROM stock_events WHERE product_id=? ORDER BY id DESC LIMIT 1", (_TPID,)).fetchone()
_c.close()
check("stock/remove: sold row survives, available all gone",
      len(_left) == 1 and _left[0]["id"] == _sold_id and _left[0]["status"] == "sold",
      f"got {[dict(a) for a in _left]}")
check("stock/remove: stock_events row logged (remove, count 2)",
      _ev2["action"] == "remove" and _ev2["count"] == 2, f"got {dict(_ev2)}")
s, out = bot.handle_admin_stock_remove({"count": 0}, bearer(TOK_ADMIN), _TPID)
check("stock/remove: count 0 -> 400", s == 400, f"got {s} {out}")
s, out = bot.handle_admin_stock_remove({"count": 1}, bearer(TOK_ADMIN), 999999)
check("stock/remove: unknown product -> 404", s == 404, f"got {s} {out}")
s, out = bot.handle_admin_stock_events(bearer(TOK_ADMIN), {"product_id": [str(_TPID)], "limit": ["50"]})
check("stock-events: filtered by product, newest first",
      s == 200 and len(out.get("events", [])) == 2
      and out["events"][0]["action"] == "remove"
      and out["events"][0]["product_name"] == "Test Product Alpha", f"got {s} {out}")
s, out = bot.handle_admin_stock_events(bearer(TOK_ADMIN), {"limit": ["500"]})
check("stock-events: limit clamps to 200",
      s == 200 and len(out.get("events", [])) <= 200, f"got {s} {out}")
s, out = bot.handle_admin_stock_events({}, {})
check("stock-events: no token -> 401", s == 401, f"got {s} {out}")

# ---------- 13b. Product delete guards ----------
# _TPID has an order? No — but it HAS a sold account (not available), so
# delete must succeed only after... wait: sold accounts are not "available
# stock" and no order references it, so delete is allowed. Use a fresh
# product for the guards instead.
s, out = bot.handle_admin_product_create(
    {"name": "Test Product Beta", "price_pkr": 100, "kind": "account", "category": "TestCat"},
    bearer(TOK_ADMIN))
_BPID = out.get("id")
_c = bot.db()
_c.execute("INSERT INTO accounts(product_id, login, password, status) VALUES(?, 'b@login.com', 'bp', 'available')", (_BPID,))
_c.execute("INSERT INTO orders(user_id, username, product_id, payment_method, amount_text, proof, status, created_at, code, items_json, qty, amount_pkr, channel) "
           "VALUES(1,'u',?,'Easypaisa','Rs 100','', 'pending', ?, 'RS-BETA1', '[]', 1, 100, 'telegram')",
           (_BPID, bot._now_str()))
_c.commit(); _c.close()
s, out = bot.handle_admin_product_delete(bearer(TOK_ADMIN), _BPID)
check("admin/products: delete refused with orders -> 400 product_has_orders",
      s == 400 and out.get("error") == "product_has_orders", f"got {s} {out}")
_c = bot.db()
_c.execute("DELETE FROM orders WHERE code='RS-BETA1'")
_c.commit(); _c.close()
s, out = bot.handle_admin_product_delete(bearer(TOK_ADMIN), _BPID)
check("admin/products: delete refused with available stock -> 400 product_has_stock",
      s == 400 and out.get("error") == "product_has_stock", f"got {s} {out}")
_c = bot.db()
_c.execute("DELETE FROM accounts WHERE product_id=?", (_BPID,))
_c.commit(); _c.close()
s, out = bot.handle_admin_product_delete(bearer(TOK_ADMIN), _BPID)
check("admin/products: delete clean product -> 200",
      s == 200 and out.get("deleted") is True, f"got {s} {out}")
s, out = bot.handle_admin_product_delete(bearer(TOK_ADMIN), _BPID)
check("admin/products: delete unknown -> 404", s == 404, f"got {s} {out}")
s, out = bot.handle_admin_product_delete(bearer(TOK_ALI), _TPID)
check("admin/products: delete normal token -> 403", s == 403, f"got {s} {out}")
# Cart items_json reference also blocks delete.
_c = bot.db()
_c.execute("INSERT INTO orders(user_id, username, product_id, payment_method, amount_text, proof, status, created_at, code, items_json, qty, amount_pkr, channel) "
           "VALUES(1,'u',1,'Easypaisa','Rs 100','', 'pending', ?, 'RS-CARTREF', ?, 1, 100, 'telegram')",
           (bot._now_str(), json.dumps([{"pid": _TPID, "qty": 1}])))
_c.commit(); _c.close()
s, out = bot.handle_admin_product_delete(bearer(TOK_ADMIN), _TPID)
check("admin/products: delete refused when cart items_json references it",
      s == 400 and out.get("error") == "product_has_orders", f"got {s} {out}")
_c = bot.db()
_c.execute("DELETE FROM orders WHERE code='RS-CARTREF'")
_c.commit(); _c.close()

# ---------- 13c. Product create with AHM7 mapping (file backup/restore) ----------
import os as _os
_MAP_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(bot.__file__)), "ahm7-mapping.json")
_MAP_BAK = open(_MAP_PATH, "rb").read() if _os.path.isfile(_MAP_PATH) else None
_BAK_PATH = _MAP_PATH + ".bak"
_BAK_BAK = open(_BAK_PATH, "rb").read() if _os.path.isfile(_BAK_PATH) else None
try:
    s, out = bot.handle_admin_product_create(
        {"name": "Test Product Gamma", "price_pkr": 450, "kind": "mail",
         "category": "TestCat", "ahm7_product_id": "test-ahm7-id-1"}, bearer(TOK_ADMIN))
    _GPID = out.get("id")
    check("admin/products: create with ahm7_product_id -> 201",
          s == 201 and isinstance(_GPID, int), f"got {s} {out}")
    _m = bot.load_ahm7_mapping()
    check("admin/products: mapping entry written (cost null, site price set)",
          _m.get("Test Product Gamma", {}).get("ahm7_product_id") == "test-ahm7-id-1"
          and _m["Test Product Gamma"].get("cost_pkr") is None
          and _m["Test Product Gamma"].get("site_price_pkr") == 450,
          f"got {_m.get('Test Product Gamma')}")
    s, out = bot.handle_admin_products(bearer(TOK_ADMIN))
    _g = [p for p in out["products"] if p["id"] == _GPID][0]
    check("admin/products: mapped product shows ahm7_mapped + null margin",
          _g["ahm7_mapped"] is True and _g["ahm7_price_pkr"] is None and _g["margin_pkr"] is None,
          f"got {_g}")
finally:
    # Mapping file shared hai (doosri suites 26 products expect karti hain) —
    # test ke baad bilkul wapas lao.
    if _MAP_BAK is not None:
        open(_MAP_PATH, "wb").write(_MAP_BAK)
    if _BAK_BAK is not None:
        open(_BAK_PATH, "wb").write(_BAK_BAK)
    elif _os.path.isfile(_BAK_PATH):
        _os.remove(_BAK_PATH)
    bot._AHM7_MAPPING = None
    _c = bot.db()
    _c.execute("DELETE FROM products WHERE name='Test Product Gamma'")
    _c.commit(); _c.close()
check("admin/products: mapping file restored to 26 products after test",
      len(bot.load_ahm7_mapping()) == 26, f"got {len(bot.load_ahm7_mapping())}")

# ---------- 14. Public catalogue ----------
s, out = bot.handle_products_public(fresh_ip())
check("products: public, no auth, includes inactive + sold",
      s == 200 and len(out.get("products", [])) >= 42
      and any(p["name"] == "Test Product Alpha" and p["active"] == 1 for p in out["products"]),
      f"got {s} len={len(out.get('products', []))}")
_prl_ip = fresh_ip()
_prl = [bot.handle_products_public(_prl_ip)[0] for _ in range(61)]
check("products: rate limit 61st call -> 429",
      _prl[:60] == [200] * 60 and _prl[60] == 429, f"got {_prl[-3:]}")

# ---------- 15. AHM7 live endpoints (mocked requests.get — live API kabhi nahi) ----------
class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._p = payload
    def json(self):
        return self._p
_orig_get = bot.requests.get
_orig_key = bot.AHM7_API_KEY
try:
    bot.AHM7_API_KEY = ""
    s, out = bot.handle_admin_ahm7_products(bearer(TOK_ADMIN))
    check("ahm7/products: key missing -> 503 ahm7_key_missing",
          s == 503 and out.get("error") == "ahm7_key_missing", f"got {s} {out}")
    s, out = bot.handle_admin_ahm7_balance(bearer(TOK_ADMIN))
    check("ahm7/balance: key missing -> 503 ahm7_key_missing",
          s == 503 and out.get("error") == "ahm7_key_missing", f"got {s} {out}")
    s, out = bot.handle_admin_ahm7_products()
    check("ahm7/products: no token -> 401 (key check se pehle auth)",
          s == 401, f"got {s} {out}")
    bot.AHM7_API_KEY = "test-key"
    bot.requests.get = lambda url, headers=None, timeout=None: _FakeResp(401, {"error": "unauthorized"})
    s, out = bot.handle_admin_ahm7_products(bearer(TOK_ADMIN))
    check("ahm7/products: AHM7 401 -> 502 ahm7_error",
          s == 502 and out.get("error") == "ahm7_error", f"got {s} {out}")
    def _boom(url, headers=None, timeout=None):
        raise Exception("network down")
    bot.requests.get = _boom
    s, out = bot.handle_admin_ahm7_balance(bearer(TOK_ADMIN))
    check("ahm7/balance: timeout/exception -> 502 ahm7_error",
          s == 502 and out.get("error") == "ahm7_error", f"got {s} {out}")
    bot.requests.get = lambda url, headers=None, timeout=None: _FakeResp(200, {
        "ok": True, "products": [
            {"id": "6ad26662f22317997032fe0c", "name": "Amazon Prime 6 Month",
             "prices": {"pkr": 700}, "discountedPrices": {"pkr": 668},
             "inStock": True, "stock": 5},
            {"id": "unmapped-1", "name": "Something New",
             "prices": {"pkr": 100}, "inStock": False}]})
    s, out = bot.handle_admin_ahm7_products(bearer(TOK_ADMIN))
    _ap = out.get("products", [])
    check("ahm7/products: mapped row joins site (price 668, margin = site - 668)",
          s == 200 and len(_ap) == 2 and _ap[0]["ahm7_id"] == "6ad26662f22317997032fe0c"
          and _ap[0]["price_pkr"] == 668 and _ap[0]["stock"] == 5
          and _ap[0]["site_price_pkr"] == 799 and _ap[0]["margin_pkr"] == 131
          and isinstance(_ap[0]["site_id"], int) and out.get("fetched_at"),
          f"got {s} {out}")
    check("ahm7/products: unmapped row -> site_id/margin null",
          _ap[1]["site_id"] is None and _ap[1]["margin_pkr"] is None
          and _ap[1]["price_pkr"] == 100, f"got {_ap[1]}")
    bot.requests.get = lambda url, headers=None, timeout=None: _FakeResp(200, {
        "balances": {"crypto": 10.5, "pkr": 2000, "inr": 0}})
    s, out = bot.handle_admin_ahm7_balance(bearer(TOK_ADMIN))
    check("ahm7/balance: balances object pass-through",
          s == 200 and out.get("balances") == {"crypto": 10.5, "pkr": 2000, "inr": 0},
          f"got {s} {out}")
finally:
    bot.requests.get = _orig_get
    bot.AHM7_API_KEY = _orig_key

# ---------- 16. HTTP dispatch for the new routes ----------
try:
    r = rq.get("http://127.0.0.1:18099/api/products", timeout=5)
    check("HTTP: GET /api/products -> 200 public",
          r.status_code == 200 and r.json().get("ok") is True
          and len(r.json().get("products", [])) >= 42, f"got {r.status_code} {r.text[:120]}")
    r = rq.post("http://127.0.0.1:18099/api/coupon/validate",
                json={"code": "SAVE20", "amount_pkr": 1000}, timeout=5)
    check("HTTP: POST /api/coupon/validate -> 200 with discount",
          r.status_code == 200 and r.json().get("discount") == 200,
          f"got {r.status_code} {r.text[:120]}")
    r = rq.get("http://127.0.0.1:18099/api/admin/coupons", timeout=5)
    check("HTTP: GET /api/admin/coupons no token -> 401", r.status_code == 401, f"got {r.status_code}")
    r = rq.get("http://127.0.0.1:18099/api/admin/coupons",
               headers={"Authorization": f"Bearer {TOK_ADMIN}"}, timeout=5)
    check("HTTP: GET /api/admin/coupons admin -> 200",
          r.status_code == 200 and isinstance(r.json().get("coupons"), list),
          f"got {r.status_code} {r.text[:120]}")
    r = rq.post("http://127.0.0.1:18099/api/admin/coupons",
                json={"code": "HTTP10", "pct_off": 10},
                headers={"Authorization": f"Bearer {TOK_ADMIN}"}, timeout=5)
    check("HTTP: POST /api/admin/coupons admin -> 201",
          r.status_code == 201 and r.json().get("coupon", {}).get("code") == "HTTP10",
          f"got {r.status_code} {r.text[:120]}")
    r = rq.post("http://127.0.0.1:18099/api/admin/coupons",
                json={"code": "HTTP11", "pct_off": 10}, timeout=5)
    check("HTTP: POST /api/admin/coupons no token -> 401", r.status_code == 401, f"got {r.status_code}")
    r = rq.post("http://127.0.0.1:18099/api/admin/coupons/HTTP10/toggle",
                json={}, headers={"Authorization": f"Bearer {TOK_ADMIN}"}, timeout=5)
    check("HTTP: POST /api/admin/coupons/:code/toggle admin -> 200",
          r.status_code == 200 and r.json().get("active") == 0,
          f"got {r.status_code} {r.text[:120]}")
    r = rq.delete("http://127.0.0.1:18099/api/admin/coupons/HTTP10",
                  json={}, headers={"Authorization": f"Bearer {TOK_ADMIN}"}, timeout=5)
    check("HTTP: DELETE /api/admin/coupons/:code admin -> 200",
          r.status_code == 200 and r.json().get("deleted") is True,
          f"got {r.status_code} {r.text[:120]}")
    r = rq.delete("http://127.0.0.1:18099/api/admin/coupons/HTTP10",
                  json={}, headers={"Authorization": f"Bearer {TOK_ADMIN}"}, timeout=5)
    check("HTTP: DELETE unknown coupon -> 404", r.status_code == 404, f"got {r.status_code}")
    r = rq.get("http://127.0.0.1:18099/api/admin/ahm7/products",
               headers={"Authorization": f"Bearer {TOK_ADMIN}"}, timeout=5)
    check("HTTP: GET /api/admin/ahm7/products (no key in env) -> 503",
          r.status_code == 503 and r.json().get("error") == "ahm7_key_missing",
          f"got {r.status_code} {r.text[:120]}")
    r = rq.post("http://127.0.0.1:18099/api/admin/products",
                json={"name": "HTTP Product", "price_pkr": 123, "kind": "account", "category": "T"},
                headers={"Authorization": f"Bearer {TOK_ALI}"}, timeout=5)
    check("HTTP: POST /api/admin/products normal token -> 403", r.status_code == 403, f"got {r.status_code}")
except Exception as e:
    check("HTTP new-routes layer reachable", False, str(e))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
