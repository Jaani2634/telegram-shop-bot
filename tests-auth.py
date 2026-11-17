"""Local dry-run tests for web accounts (signup/login/sessions/orders).
No network, no real passwords stored. Same pattern as tests-web-checkout.py.
Run: ~/workspace/tools/bot-test-venv/bin/python tests-auth.py
(system python3 has no telegram/requests modules — the venv python is the
same one the existing checkout tests use.)
"""
import os, sys, json, sqlite3, hashlib, time

os.environ["DB_PATH"] = "/tmp/test-auth.db"
os.environ["WEB_API_SECRET"] = "test-secret-123"
os.environ["PORT"] = "18098"
for f in ("/tmp/test-auth.db", "/tmp/test-auth.db-wal", "/tmp/test-auth.db-shm"):
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
    return f"10.0.{_ip_n[0] // 250}.{_ip_n[0] % 250 + 1}"

def bearer(tok):
    return {"Authorization": f"Bearer {tok}"}

bot.init_db()
c = bot.db()
print("test DB products:", c.execute("SELECT COUNT(*) n FROM products").fetchone()["n"])
c.close()

def pid_by_name(name):
    cc = bot.db()
    r = cc.execute("SELECT id FROM products WHERE name=?", (name,)).fetchone()
    cc.close()
    return r["id"] if r else None

# ---------- 1. signup ----------
s, out = bot.handle_auth_signup(
    {"name": "Ali Khan", "email": "Ali@Example.COM", "phone": "+92 300 1110001", "password": "secret123"}, H, fresh_ip())
check("signup: happy path 200 + token + user",
      s == 200 and out.get("ok") is True and out.get("user", {}).get("email") == "ali@example.com"
      and out.get("user", {}).get("name") == "Ali Khan",
      f"got {s} {out}")
TOK_ALI = out.get("token", "")
check("signup: token is 32 hex chars",
      len(TOK_ALI) == 32 and all(ch in "0123456789abcdef" for ch in TOK_ALI), f"got {TOK_ALI!r}")
check("signup: response leaks no hashes",
      "password_hash" not in json.dumps(out) and "token_hash" not in json.dumps(out)
      and "secret123" not in json.dumps(out), f"got {out}")

s, out = bot.handle_auth_signup(
    {"name": "Ali Again", "email": "ali@example.com", "phone": "+92 300 1110002", "password": "secret123"}, H, fresh_ip())
check("signup: duplicate email -> 409", s == 409, f"got {s} {out}")
s, out = bot.handle_auth_signup(
    {"name": "Ali Again", "email": "ALI@EXAMPLE.COM", "phone": "+92 300 1110003", "password": "secret123"}, H, fresh_ip())
check("signup: duplicate email (other case) -> 409", s == 409, f"got {s} {out}")

for label, data in [
    ("bad email (no @)", {"name": "A", "email": "not-an-email", "phone": "+92 300 1110004", "password": "secret123"}),
    ("bad email (no domain)", {"name": "A", "email": "a@", "phone": "+92 300 1110004", "password": "secret123"}),
    ("short password (7)", {"name": "A", "email": "a2@example.com", "phone": "+92 300 1110004", "password": "short7!"}),
    ("empty name", {"name": "", "email": "a3@example.com", "phone": "+92 300 1110004", "password": "secret123"}),
    ("name 61 chars", {"name": "x" * 61, "email": "a4@example.com", "phone": "+92 300 1110004", "password": "secret123"}),
    ("missing password", {"name": "A", "email": "a5@example.com", "phone": "+92 300 1110004", "password": ""}),
    ("missing phone", {"name": "A", "email": "a6b@example.com", "password": "secret123"}),
    ("phone without country code", {"name": "A", "email": "a7@example.com", "phone": "03001110004", "password": "secret123"}),
]:
    s, out = bot.handle_auth_signup(data, H, fresh_ip())
    check(f"signup: {label} -> 400", s == 400, f"got {s} {out}")

s, out = bot.handle_auth_signup(
    {"name": "A", "email": "a6@example.com", "phone": "+92 300 1110005", "password": "secret123"}, BAD_H, fresh_ip())
check("signup: wrong secret -> 401", s == 401, f"got {s} {out}")
s, out = bot.handle_auth_signup(
    {"name": "A", "email": "a6@example.com", "phone": "+92 300 1110005", "password": "secret123"}, {}, fresh_ip())
check("signup: no secret -> 401", s == 401, f"got {s} {out}")

# ---------- 2. password storage (hashed, scrypt verifies) ----------
c = bot.db()
u = c.execute("SELECT * FROM web_users WHERE email='ali@example.com'").fetchone()
c.close()
check("storage: user row exists, email lowercased",
      u and u["email"] == "ali@example.com" and u["name"] == "Ali Khan", f"got {dict(u) if u else None}")
stored = u["password_hash"] if u else ""
check("storage: password NOT stored in plain text",
      stored and stored != "secret123" and "secret123" not in stored, f"got {stored!r}")
check("storage: hash format salt_hex$hash_hex",
      "$" in stored and len(stored.split("$")[0]) == 32 and len(stored.split("$")[1]) == 64,
      f"got {stored!r}")
check("storage: scrypt verifies right password", bot._verify_password("secret123", stored))
check("storage: scrypt rejects wrong password", not bot._verify_password("wrongpass", stored))
check("storage: scrypt rejects garbage stored value",
      not bot._verify_password("secret123", "not-a-hash"))
c = bot.db()
sess = c.execute("SELECT * FROM web_sessions WHERE user_id=?", (u["id"],)).fetchone()
c.close()
check("storage: session stores SHA-256 digest, not the token",
      sess and sess["token_hash"] == hashlib.sha256(TOK_ALI.encode()).hexdigest()
      and sess["token_hash"] != TOK_ALI, f"got {dict(sess) if sess else None}")
check("storage: session expires ~30 days out",
      sess and sess["expires_at"] > bot._now_str(), f"got {dict(sess) if sess else None}")

# ---------- 3. login ----------
s, out = bot.handle_auth_login(
    {"email": "ali@example.com", "password": "secret123"}, H, fresh_ip())
check("login: happy path 200 + new token + user",
      s == 200 and out.get("ok") is True and len(out.get("token", "")) == 32
      and out.get("user", {}).get("email") == "ali@example.com",
      f"got {s} {out}")
TOK_ALI2 = out.get("token", "")
check("login: second session token differs from signup token",
      TOK_ALI2 and TOK_ALI2 != TOK_ALI, f"got {TOK_ALI2!r} vs {TOK_ALI!r}")
check("login: response leaks no hashes",
      "password_hash" not in json.dumps(out) and "token_hash" not in json.dumps(out),
      f"got {out}")

s, wrong_out = bot.handle_auth_login(
    {"email": "ali@example.com", "password": "wrongpass"}, H, fresh_ip())
check("login: wrong password -> 401", s == 401, f"got {s} {wrong_out}")
s, unknown_out = bot.handle_auth_login(
    {"email": "nobody@example.com", "password": "wrongpass"}, H, fresh_ip())
check("login: unknown email -> 401", s == 401, f"got {s} {unknown_out}")
check("login: wrong-password and unknown-email errors are IDENTICAL (no leak)",
      wrong_out.get("error") == unknown_out.get("error") == "email ya password ghalat hai",
      f"got {wrong_out!r} vs {unknown_out!r}")
s, out = bot.handle_auth_login({"email": "ali@example.com", "password": ""}, H, fresh_ip())
check("login: missing password -> 400", s == 400, f"got {s} {out}")
s, out = bot.handle_auth_login({"email": "", "password": "secret123"}, H, fresh_ip())
check("login: missing email -> 400", s == 400, f"got {s} {out}")
s, out = bot.handle_auth_login(
    {"email": "ali@example.com", "password": "secret123"}, BAD_H, fresh_ip())
check("login: wrong secret -> 401", s == 401, f"got {s} {out}")

# ---------- 4. me ----------
s, out = bot.handle_auth_me(bearer(TOK_ALI))
check("me: valid token -> 200 + user", s == 200 and out.get("user", {}).get("email") == "ali@example.com",
      f"got {s} {out}")
s, out = bot.handle_auth_me(bearer(TOK_ALI2))
check("me: second session token also valid", s == 200, f"got {s} {out}")
s, out = bot.handle_auth_me(bearer("00" * 16))
check("me: wrong token -> 401", s == 401, f"got {s} {out}")
s, out = bot.handle_auth_me({})
check("me: no Authorization header -> 401", s == 401, f"got {s} {out}")
s, out = bot.handle_auth_me({"Authorization": "Basic abc"})
check("me: non-Bearer auth -> 401", s == 401, f"got {s} {out}")
s, out = bot.handle_auth_me(H)
check("me: X-Web-Secret alone is NOT accepted -> 401", s == 401, f"got {s} {out}")
# expired session: seed one with a past expiry
EXP_TOK = "ee" * 16
c = bot.db()
c.execute("INSERT INTO web_sessions(user_id, token_hash, expires_at, created_at) VALUES(?,?,?,?)",
          (u["id"], hashlib.sha256(EXP_TOK.encode()).hexdigest(), "2020-01-01 00:00:00", "2020-01-01 00:00:00"))
c.commit(); c.close()
s, out = bot.handle_auth_me(bearer(EXP_TOK))
check("me: expired token -> 401", s == 401, f"got {s} {out}")
c = bot.db()
gone = c.execute("SELECT 1 FROM web_sessions WHERE token_hash=?",
                 (hashlib.sha256(EXP_TOK.encode()).hexdigest(),)).fetchone()
c.close()
check("me: expired session row deleted on use", gone is None)

# ---------- 5. logout ----------
s, out = bot.handle_auth_logout(bearer(TOK_ALI2))
check("logout: valid token -> 200", s == 200 and out.get("ok") is True, f"got {s} {out}")
s, out = bot.handle_auth_me(bearer(TOK_ALI2))
check("logout: token revoked (me -> 401)", s == 401, f"got {s} {out}")
s, out = bot.handle_auth_me(bearer(TOK_ALI))
check("logout: other session still valid", s == 200, f"got {s} {out}")
s, out = bot.handle_auth_logout(bearer("00" * 16))
check("logout: invalid token still -> 200 (no validity leak)", s == 200, f"got {s} {out}")
s, out = bot.handle_auth_logout({})
check("logout: no token -> 200", s == 200, f"got {s} {out}")

# ---------- 6. orders (isolation) ----------
gem_pid = pid_by_name("Gemini Plus 6m Links (No Card Required)")
ama_pid = pid_by_name("Amazon Prime 6 Months")
s, out = bot.handle_auth_signup(
    {"name": "Bilal", "email": "bilal@example.com", "phone": "+92 300 1110006", "password": "secret123"}, H, fresh_ip())
TOK_BILAL = out.get("token", "")
def seed_web_order(email, items, amount, status="delivered"):
    code = bot.gen_order_code()
    tok = bot._gen_delivery_token()
    oid = bot.create_order(0, email, items, "zurvapay", f"Rs {amount} via Zurvapay (web)",
                           "zurvapay", amount, code, "", 0, channel="web")
    c2 = bot.db()
    c2.execute("UPDATE orders SET delivery_token=?, web_contact=?, success_base=?, status=? WHERE id=?",
               (tok, email, "https://retailshop.pages.dev", status, oid))
    c2.commit(); c2.close()
    return code, tok
code_a1, tok_a1 = seed_web_order("ali@example.com", [{"pid": gem_pid, "qty": 1}], 479)
code_a2, tok_a2 = seed_web_order("ali@example.com",
                                 [{"pid": gem_pid, "qty": 1}, {"pid": ama_pid, "qty": 2}], 2077, "pending")
code_b1, tok_b1 = seed_web_order("bilal@example.com", [{"pid": ama_pid, "qty": 1}], 799)
# telegram-channel order with Ali's email as contact — must NOT show on web
tg_code = bot.gen_order_code()
bot.create_order(999, "ali@example.com", [{"pid": gem_pid, "qty": 1}], "zurvapay",
                 "Rs 479 via Zurvapay", "zurvapay", 479, tg_code, "", 0, channel="telegram")
c = bot.db()
c.execute("UPDATE orders SET web_contact='ali@example.com' WHERE code=?", (tg_code,))
c.commit(); c.close()

s, out = bot.handle_auth_orders(bearer(TOK_ALI))
codes = [o["code"] for o in out.get("orders", [])]
check("orders: Ali sees only his 2 web orders",
      s == 200 and sorted(codes) == sorted([code_a1, code_a2]),
      f"got {s} {out}")
check("orders: newest first", codes == [code_a2, code_a1], f"got {codes}")
o1 = next(o for o in out["orders"] if o["code"] == code_a1)
check("orders: single-item product name + amount + status + created",
      o1["product"] == "Gemini Plus 6m Links (No Card Required)"
      and o1["amount_pkr"] == 479 and o1["status"] == "delivered" and o1["created"],
      f"got {o1}")
check("orders: order_url has code + delivery token",
      o1["order_url"] == f"https://retailshop.pages.dev/order.html?code={code_a1}&token={tok_a1}",
      f"got {o1['order_url']!r}")
o2 = next(o for o in out["orders"] if o["code"] == code_a2)
check("orders: multi-item shows (+1 more)",
      o2["product"] == "Gemini Plus 6m Links (No Card Required) (+1 more)"
      and o2["amount_pkr"] == 2077 and o2["status"] == "pending", f"got {o2}")
check("orders: telegram order with same contact NOT listed",
      tg_code not in codes, f"got {codes}")
s, out = bot.handle_auth_orders(bearer(TOK_BILAL))
check("orders: Bilal sees only his own order",
      s == 200 and [o["code"] for o in out.get("orders", [])] == [code_b1],
      f"got {s} {out}")
s, out = bot.handle_auth_orders(bearer("00" * 16))
check("orders: invalid token -> 401", s == 401, f"got {s} {out}")
s, out = bot.handle_auth_orders({})
check("orders: no token -> 401", s == 401, f"got {s} {out}")

# ---------- 7. delete ----------
s, out = bot.handle_auth_delete(bearer(TOK_BILAL))
check("delete: valid token -> 200", s == 200 and out.get("ok") is True, f"got {s} {out}")
c = bot.db()
gone_u = c.execute("SELECT 1 FROM web_users WHERE email='bilal@example.com'").fetchone()
gone_s = c.execute("SELECT 1 FROM web_sessions WHERE token_hash=?",
                   (hashlib.sha256(TOK_BILAL.encode()).hexdigest(),)).fetchone()
c.close()
check("delete: user row removed", gone_u is None)
check("delete: sessions removed", gone_s is None)
s, out = bot.handle_auth_me(bearer(TOK_BILAL))
check("delete: token dead after delete (me -> 401)", s == 401, f"got {s} {out}")
s, out = bot.handle_auth_login(
    {"email": "bilal@example.com", "password": "secret123"}, H, fresh_ip())
check("delete: login after delete -> 401 (generic)", s == 401, f"got {s} {out}")
s, out = bot.handle_auth_delete(bearer("00" * 16))
check("delete: invalid token -> 401", s == 401, f"got {s} {out}")
# Ali still intact after Bilal's delete
s, out = bot.handle_auth_me(bearer(TOK_ALI))
check("delete: other user unaffected", s == 200, f"got {s} {out}")

# ---------- 8. bot-side rate limits ----------
bot._AUTH_SIGNUP_RL.clear()
rl_ip = "203.0.113.1"
results = []
for i in range(11):
    s, _ = bot.handle_auth_signup(
        {"name": "RL", "email": f"rl{i}@example.com", "phone": f"+9230090000{i:02d}", "password": "secret123"}, H, rl_ip)
    results.append(s)
check("rate limit: signup 10/hour per IP (11th -> 429)",
      results[:10] == [200] * 10 and results[10] == 429, f"got {results}")

bot._AUTH_LOGIN_RL.clear()
rl_ip2 = "203.0.113.2"
results = []
for i in range(21):
    s, _ = bot.handle_auth_login(
        {"email": "rl-login@example.com", "password": "wrongpass"}, H, rl_ip2)
    results.append(s)
check("rate limit: login 20/hour per IP+email (21st -> 429)",
      results[:20] == [401] * 20 and results[20] == 429, f"got {results}")
# same IP, different email: not blocked (key is IP+email)
s, _ = bot.handle_auth_login(
    {"email": "other@example.com", "password": "wrongpass"}, H, rl_ip2)
check("rate limit: login limit is per IP+email (other email still 401, not 429)",
      s == 401, f"got {s}")

# ---------- 8b. user email verification (Resend) ----------
# Signup auto-send: mocked sender captures the code; send fail must NOT
# block signup. Verify send/confirm + lock + rate limit.
import re as _re
email_captured = []
orig_send_email = bot._send_email_via_resend
bot.RESEND_API_KEY = "re_test"
bot._send_email_via_resend = lambda to, subj, text: (email_captured.append((to, subj, text)), True)[1]

c = bot.db()
_tabs = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
_cols = [r[1] for r in c.execute("PRAGMA table_info(web_users)").fetchall()]
c.close()
check("email-verify: user_email_otps table exists", "user_email_otps" in _tabs, str(_tabs))
check("email-verify: web_users has email_verified", "email_verified" in _cols, str(_cols))

email_captured.clear()
s, out = bot.handle_auth_signup(
    {"name": "Verify Me", "email": "verify@example.com", "phone": "+92 300 1110010", "password": "secret123"}, H, fresh_ip())
check("email-verify: signup 200 + email_verified false + verification_sent true",
      s == 200 and out.get("user", {}).get("email_verified") is False
      and out.get("user", {}).get("verification_sent") is True, f"got {s} {out}")
TOK_V = out.get("token", "")
check("email-verify: code emailed to the signup address",
      len(email_captured) == 1 and email_captured[0][0] == "verify@example.com"
      and "verification code" in email_captured[0][1], f"got {email_captured}")
_m = _re.search(r"(\d{6})", email_captured[0][2])
VCODE = _m.group(1) if _m else ""
check("email-verify: code is 6 digits, never in the signup response",
      len(VCODE) == 6 and VCODE not in json.dumps(out), f"got {VCODE!r} {out}")

# send fails -> signup still 200, verification_sent false, login works
bot._send_email_via_resend = lambda to, subj, text: False
s, out = bot.handle_auth_signup(
    {"name": "No Mail", "email": "nomail@example.com", "phone": "+92 300 1110011", "password": "secret123"}, H, fresh_ip())
check("email-verify: send failure does NOT block signup (200, sent=false)",
      s == 200 and out.get("user", {}).get("verification_sent") is False, f"got {s} {out}")
TOK_NM = out.get("token", "")
s, out = bot.handle_auth_login({"email": "nomail@example.com", "password": "secret123"}, H, fresh_ip())
check("email-verify: unverified user can still log in", s == 200, f"got {s} {out}")
s, out = bot.handle_auth_me(bearer(TOK_NM))
check("email-verify: me shows email_verified false",
      s == 200 and out.get("user", {}).get("email_verified") is False, f"got {s} {out}")
bot._send_email_via_resend = lambda to, subj, text: (email_captured.append((to, subj, text)), True)[1]

# send endpoint: no token -> 401
s, out = bot.handle_auth_verify_email_send({})
check("email-verify: send without token -> 401", s == 401, f"got {s} {out}")
# send endpoint: resend
email_captured.clear()
s, out = bot.handle_auth_verify_email_send(bearer(TOK_V))
check("email-verify: send -> 200 channels [email]",
      s == 200 and out.get("channels") == ["email"], f"got {s} {out}")
_m = _re.search(r"(\d{6})", email_captured[0][2]) if email_captured else None
VCODE2 = _m.group(1) if _m else ""
check("email-verify: resend issues a fresh code",
      len(VCODE2) == 6 and VCODE2 != VCODE, f"got {VCODE2!r} vs {VCODE!r}")

# confirm: wrong code -> 401, right code -> 200 + verified
s, out = bot.handle_auth_verify_email_confirm({"code": "000000"}, bearer(TOK_V))
check("email-verify: confirm wrong code -> 401", s == 401, f"got {s} {out}")
s, out = bot.handle_auth_verify_email_confirm({"code": VCODE2}, bearer(TOK_V))
check("email-verify: confirm right code -> 200 email_verified true",
      s == 200 and out.get("email_verified") is True, f"got {s} {out}")
s, out = bot.handle_auth_me(bearer(TOK_V))
check("email-verify: me shows email_verified true after confirm",
      s == 200 and out.get("user", {}).get("email_verified") is True, f"got {s} {out}")
s, out = bot.handle_auth_verify_email_confirm({"code": VCODE2}, bearer(TOK_V))
check("email-verify: same code twice -> 401 (single-use)", s == 401, f"got {s} {out}")
# send when already verified -> 200 already_verified, no new email
email_captured.clear()
s, out = bot.handle_auth_verify_email_send(bearer(TOK_V))
check("email-verify: send when verified -> 200 already_verified, no email",
      s == 200 and out.get("already_verified") is True and not email_captured,
      f"got {s} {out}")

# lock after 10 wrong attempts (fresh user: nomail)
email_captured.clear()
s, out = bot.handle_auth_verify_email_send(bearer(TOK_NM))
check("email-verify: send for lock test -> 200", s == 200, f"got {s} {out}")
_m = _re.search(r"(\d{6})", email_captured[0][2]) if email_captured else None
NMCODE = _m.group(1) if _m else ""
wrong = 0
for i in range(10):
    s, out = bot.handle_auth_verify_email_confirm({"code": "111111"}, bearer(TOK_NM))
    if s == 401:
        wrong += 1
check("email-verify: 10 wrong codes -> 401 each", wrong == 10, f"wrong={wrong}")
s, out = bot.handle_auth_verify_email_confirm({"code": NMCODE}, bearer(TOK_NM))
check("email-verify: 11th attempt after 10 fails -> 429 locked", s == 429, f"got {s} {out}")
# unlock (test-only) and confirm the right code still works
_c = bot.db()
_nm_id = _c.execute("SELECT id FROM web_users WHERE email='nomail@example.com'").fetchone()["id"]
_c.close()
bot._OTP_LOCKS.pop(f"email:{_nm_id}", None)
s, out = bot.handle_auth_verify_email_confirm({"code": NMCODE}, bearer(TOK_NM))
check("email-verify: after lock expiry right code -> 200",
      s == 200 and out.get("email_verified") is True, f"got {s} {out}")

# rate limit: 5 sends/hour per user (fresh user; clear the counter first
# so the signup auto-send does not count toward this user's endpoint limit)
s, out = bot.handle_auth_signup(
    {"name": "RL Mail", "email": "rlmail@example.com", "phone": "+92 300 1110012", "password": "secret123"}, H, fresh_ip())
TOK_RL = out.get("token", "")
bot._OTP_SEND_RL.clear()
results = []
for i in range(6):
    s, _ = bot.handle_auth_verify_email_send(bearer(TOK_RL))
    results.append(s)
check("email-verify: send rate limit 5/hour (6th -> 429)",
      results[:5] == [200] * 5 and results[5] == 429, f"got {results}")

# no Resend key -> signup still works, send -> 503 email_not_configured
bot.RESEND_API_KEY = ""
s, out = bot.handle_auth_signup(
    {"name": "No Key", "email": "nokey@example.com", "phone": "+92 300 1110013", "password": "secret123"}, H, fresh_ip())
check("email-verify: signup without Resend key -> 200, sent=false",
      s == 200 and out.get("user", {}).get("verification_sent") is False, f"got {s} {out}")
TOK_NK = out.get("token", "")
s, out = bot.handle_auth_verify_email_send(bearer(TOK_NK))
check("email-verify: send without Resend key -> 503 email_not_configured",
      s == 503 and out.get("error") == "email_not_configured", f"got {s} {out}")

bot.RESEND_API_KEY = ""
bot._send_email_via_resend = orig_send_email

# ---------- 8c. Gmail SMTP fallback for email OTP ----------
# Resend free tier sirf owner email ko bhej sakta hai (403) — SMTP
# fallback: pehle Resend, fail ho to Gmail SMTP. Mocked senders, no network.
orig_smtp_user = bot.SMTP_USER
orig_smtp_pass = bot.SMTP_PASS
orig_send_smtp = bot._send_email_via_smtp
orig_resend_key2 = bot.RESEND_API_KEY
smtp_captured = []
resend_captured = []
bot._send_email_via_resend = lambda to, subj, text: (resend_captured.append((to, subj, text)), True)[1]
bot._send_email_via_smtp = lambda to, subj, text: (smtp_captured.append((to, subj, text)), True)[1]

# unit: Resend ok -> SMTP kabhi call nahi hota
bot.RESEND_API_KEY = "re_test"
bot.SMTP_USER = "smtp@example.com"
bot.SMTP_PASS = "apppass1234567890"
resend_captured.clear(); smtp_captured.clear()
check("smtp: _send_email Resend ok -> True, SMTP not tried",
      bot._send_email("u@example.com", "s", "t") is True
      and len(resend_captured) == 1 and len(smtp_captured) == 0)
# unit: Resend fail -> SMTP fallback ok
bot._send_email_via_resend = lambda to, subj, text: False
resend_captured.clear(); smtp_captured.clear()
check("smtp: _send_email Resend fail -> SMTP fallback True",
      bot._send_email("u@example.com", "s", "t") is True
      and len(smtp_captured) == 1, f"got resend={resend_captured} smtp={smtp_captured}")
# unit: dono fail -> False
bot._send_email_via_smtp = lambda to, subj, text: False
check("smtp: _send_email both fail -> False",
      bot._send_email("u@example.com", "s", "t") is False)
# unit: koi configured nahi -> False
bot.RESEND_API_KEY = ""
bot.SMTP_USER = ""
bot.SMTP_PASS = ""
check("smtp: _send_email neither configured -> False",
      bot._send_email("u@example.com", "s", "t") is False)
check("smtp: _email_configured false jab dono missing",
      bot._email_configured() is False)

# unit: _send_email_via_smtp — mocked smtplib.SMTP, no network.
# STARTTLS + login (spaces hata kar) + send_message verify karo.
class _FakeSMTP:
    instances = []
    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls = []
        _FakeSMTP.instances.append(self)
    def starttls(self):
        self.calls.append("starttls")
    def login(self, user, pw):
        self.calls.append(("login", user, pw))
    def send_message(self, msg):
        self.calls.append(("send", msg["From"], msg["To"], msg["Subject"]))
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
orig_smtplib_smtp = bot.smtplib.SMTP
bot.smtplib.SMTP = _FakeSMTP
bot._send_email_via_smtp = orig_send_smtp  # real sender (upar mock tha)
bot.SMTP_USER = "shop@gmail.com"
bot.SMTP_PASS = "abcd efgh ijkl mnop"
_FakeSMTP.instances.clear()
check("smtp: _send_email_via_smtp mocked SMTP -> True",
      bot._send_email_via_smtp("u@example.com", "Code", "body") is True)
_f = _FakeSMTP.instances[0] if _FakeSMTP.instances else None
check("smtp: host smtp.gmail.com:587 + starttls",
      _f is not None and _f.host == "smtp.gmail.com" and _f.port == 587
      and "starttls" in _f.calls,
      f"got {_f.host if _f else None}:{_f.port if _f else None} {_f.calls if _f else None}")
check("smtp: login with user + password spaces removed",
      _f is not None and ("login", "shop@gmail.com", "abcdefghijklmnop") in _f.calls,
      f"got {_f.calls if _f else None}")
check("smtp: send_message from 'Pheonix Retail Shop <SMTP_USER>' to recipient",
      _f is not None and ("send", "Pheonix Retail Shop <shop@gmail.com>", "u@example.com", "Code") in _f.calls,
      f"got {_f.calls if _f else None}")
bot.smtplib.SMTP = orig_smtplib_smtp
# SMTP not configured -> real sender returns False, no SMTP object made
bot.SMTP_USER = ""
bot.SMTP_PASS = ""
_FakeSMTP.instances.clear()
bot.smtplib.SMTP = _FakeSMTP
check("smtp: _send_email_via_smtp unconfigured -> False, no connect",
      bot._send_email_via_smtp("u@example.com", "Code", "body") is False
      and len(_FakeSMTP.instances) == 0)
bot.smtplib.SMTP = orig_smtplib_smtp

# flow: SMTP-only — signup auto-send + verify-email send endpoint
bot.RESEND_API_KEY = ""
bot.SMTP_USER = "smtp@example.com"
bot.SMTP_PASS = "apppass1234567890"
bot._send_email_via_resend = lambda to, subj, text: False
bot._send_email_via_smtp = lambda to, subj, text: (smtp_captured.append((to, subj, text)), True)[1]
smtp_captured.clear()
s, out = bot.handle_auth_signup(
    {"name": "SMTP Only", "email": "smtponly@example.com", "phone": "+92 300 1110014", "password": "secret123"}, H, fresh_ip())
check("smtp: SMTP-only signup -> 200 verification_sent true",
      s == 200 and out.get("user", {}).get("verification_sent") is True, f"got {s} {out}")
TOK_SM = out.get("token", "")
check("smtp: SMTP-only signup code went via SMTP to signup address",
      len(smtp_captured) == 1 and smtp_captured[0][0] == "smtponly@example.com",
      f"got {smtp_captured}")
smtp_captured.clear()
s, out = bot.handle_auth_verify_email_send(bearer(TOK_SM))
check("smtp: SMTP-only verify-email send -> 200 channels [email]",
      s == 200 and out.get("channels") == ["email"], f"got {s} {out}")

# flow: Resend fail -> SMTP fallback (endpoint level)
bot.RESEND_API_KEY = "re_test"
bot._send_email_via_resend = lambda to, subj, text: False
smtp_captured.clear()
s, out = bot.handle_auth_verify_email_send(bearer(TOK_SM))
check("smtp: Resend fail -> SMTP fallback, send -> 200",
      s == 200 and len(smtp_captured) == 1, f"got {s} {out}")

# flow: dono fail -> send 502; signup verification_sent false
bot._send_email_via_smtp = lambda to, subj, text: False
s, out = bot.handle_auth_verify_email_send(bearer(TOK_SM))
check("smtp: both senders fail -> send 502",
      s == 502, f"got {s} {out}")
s, out = bot.handle_auth_signup(
    {"name": "Both Fail", "email": "bothfail@example.com", "phone": "+92 300 1110015", "password": "secret123"}, H, fresh_ip())
check("smtp: both fail -> signup still 200, sent=false",
      s == 200 and out.get("user", {}).get("verification_sent") is False, f"got {s} {out}")

# flow: neither configured -> send 503 email_not_configured
bot.RESEND_API_KEY = ""
bot.SMTP_USER = ""
bot.SMTP_PASS = ""
s, out = bot.handle_auth_verify_email_send(bearer(TOK_SM))
check("smtp: neither configured -> send 503 email_not_configured",
      s == 503 and out.get("error") == "email_not_configured", f"got {s} {out}")

# restore
bot.RESEND_API_KEY = orig_resend_key2
bot.SMTP_USER = orig_smtp_user
bot.SMTP_PASS = orig_smtp_pass
bot._send_email_via_smtp = orig_send_smtp
bot._send_email_via_resend = orig_send_email

# ---------- 9. HTTP layer (real health server) ----------
bot.start_health_server()
time.sleep(1.0)
import requests as rq
try:
    r = rq.post("http://127.0.0.1:18098/api/auth/signup",
                json={"name": "HTTP", "email": "http@example.com", "phone": "+92 300 1110007", "password": "secret123"},
                headers={"X-Web-Secret": "test-secret-123"}, timeout=5)
    j = r.json()
    check("HTTP: POST /api/auth/signup -> 200 + token",
          r.status_code == 200 and len(j.get("token", "")) == 32,
          f"got {r.status_code} {r.text[:160]}")
    http_tok = j.get("token", "")
    r = rq.post("http://127.0.0.1:18098/api/auth/signup",
                json={"name": "HTTP", "email": "http@example.com", "phone": "+92 300 1110007", "password": "secret123"},
                timeout=5)
    check("HTTP: signup without secret -> 401", r.status_code == 401, f"got {r.status_code}")
    r = rq.get("http://127.0.0.1:18098/api/auth/me",
               headers={"Authorization": f"Bearer {http_tok}"}, timeout=5)
    check("HTTP: GET /api/auth/me Bearer -> 200",
          r.status_code == 200 and r.json().get("user", {}).get("email") == "http@example.com",
          f"got {r.status_code} {r.text[:160]}")
    r = rq.get("http://127.0.0.1:18098/api/auth/me", timeout=5)
    check("HTTP: GET /api/auth/me no token -> 401", r.status_code == 401, f"got {r.status_code}")
    r = rq.get("http://127.0.0.1:18098/api/auth/orders",
               headers={"Authorization": f"Bearer {http_tok}"}, timeout=5)
    check("HTTP: GET /api/auth/orders -> 200 empty list",
          r.status_code == 200 and r.json().get("orders") == [],
          f"got {r.status_code} {r.text[:160]}")
    r = rq.post("http://127.0.0.1:18098/api/auth/login",
                json={"email": "http@example.com", "password": "secret123"},
                headers={"X-Web-Secret": "test-secret-123"}, timeout=5)
    check("HTTP: POST /api/auth/login -> 200",
          r.status_code == 200 and len(r.json().get("token", "")) == 32,
          f"got {r.status_code} {r.text[:160]}")
    # verify-email over HTTP (mocked sender; http@example.com is unverified)
    bot.RESEND_API_KEY = "re_test"
    bot._send_email_via_resend = lambda to, subj, text: (email_captured.append((to, subj, text)), True)[1]
    r = rq.post("http://127.0.0.1:18098/api/auth/verify-email/send",
                headers={"Authorization": f"Bearer {http_tok}"}, timeout=5)
    check("HTTP: POST /api/auth/verify-email/send -> 200 channels [email]",
          r.status_code == 200 and r.json().get("channels") == ["email"],
          f"got {r.status_code} {r.text[:160]}")
    _m = _re.search(r"(\d{6})", email_captured[-1][2]) if email_captured else None
    http_code = _m.group(1) if _m else ""
    r = rq.post("http://127.0.0.1:18098/api/auth/verify-email/confirm",
                json={"code": "000000"},
                headers={"Authorization": f"Bearer {http_tok}"}, timeout=5)
    check("HTTP: verify-email/confirm wrong code -> 401", r.status_code == 401, f"got {r.status_code}")
    r = rq.post("http://127.0.0.1:18098/api/auth/verify-email/confirm",
                json={"code": http_code},
                headers={"Authorization": f"Bearer {http_tok}"}, timeout=5)
    check("HTTP: verify-email/confirm right code -> 200",
          r.status_code == 200 and r.json().get("email_verified") is True,
          f"got {r.status_code} {r.text[:160]}")
    r = rq.get("http://127.0.0.1:18098/api/auth/me",
               headers={"Authorization": f"Bearer {http_tok}"}, timeout=5)
    check("HTTP: me after verify -> email_verified true",
          r.status_code == 200 and r.json().get("user", {}).get("email_verified") is True,
          f"got {r.status_code} {r.text[:160]}")
    r = rq.post("http://127.0.0.1:18098/api/auth/verify-email/send",
                timeout=5)
    check("HTTP: verify-email/send without token -> 401", r.status_code == 401, f"got {r.status_code}")
    bot._send_email_via_resend = orig_send_email
    r = rq.post("http://127.0.0.1:18098/api/auth/logout",
                headers={"Authorization": f"Bearer {http_tok}"}, timeout=5)
    check("HTTP: POST /api/auth/logout -> 200", r.status_code == 200, f"got {r.status_code}")
    r = rq.get("http://127.0.0.1:18098/api/auth/me",
               headers={"Authorization": f"Bearer {http_tok}"}, timeout=5)
    check("HTTP: me after logout -> 401", r.status_code == 401, f"got {r.status_code}")
    r = rq.post("http://127.0.0.1:18098/api/auth/delete",
                headers={"Authorization": f"Bearer {http_tok}"}, timeout=5)
    check("HTTP: delete with revoked token -> 401", r.status_code == 401, f"got {r.status_code}")
except Exception as e:
    check("HTTP layer reachable", False, str(e))

print(f"\n==== {len(PASS)} passed, {len(FAIL)} failed ====")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
