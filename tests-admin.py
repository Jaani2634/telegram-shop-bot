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

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
