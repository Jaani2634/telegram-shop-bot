"""Local dry-run tests for web checkout + AHM7 fulfillment (no real money).
Same-channel delivery: website orders deliver ON the website (order page
+ delivery token); Telegram DM is NOT used for web orders.
Run: ~/workspace/tools/bot-test-venv/bin/python /tmp/test-web-checkout.py
"""
import os, sys, json, sqlite3, threading, time

os.environ["DB_PATH"] = "/tmp/test-shop.db"
os.environ["WEB_API_SECRET"] = "test-secret-123"
os.environ["PORT"] = "18099"
for f in ("/tmp/test-shop.db", "/tmp/test-shop.db-wal", "/tmp/test-shop.db-shm"):
    if os.path.exists(f):
        os.remove(f)

sys.path.insert(0, "/home/hatch/workspace/telegram-shop-bot")
import bot

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" — {extra}" if extra and not cond else ""))

# ---------- setup ----------
bot.init_db()
c = bot.db()
n = c.execute("SELECT COUNT(*) n FROM products").fetchone()["n"]
print(f"test DB products: {n}")
def pid_by_name(name):
    cc = bot.db()
    r = cc.execute("SELECT id FROM products WHERE name=?", (name,)).fetchone()
    cc.close()
    return r["id"] if r else None
c.close()

# ---------- 1. mapping ----------
m = bot.load_ahm7_mapping()
check("mapping has 26 products", len(m) == 26, f"got {len(m)}")
mapped = [v for v in m.values() if v["ahm7_product_id"]]
check("mapping: 21 mapped", len(mapped) == 21, f"got {len(mapped)}")
check("mapping: 8 profitable at site prices",
      sum(1 for v in mapped if (v["margin_pkr"] or 0) > 0) == 8)
check("mapping: 13 loss/breakeven (guard blocks)",
      sum(1 for v in mapped if (v["margin_pkr"] or 0) <= 0) == 13)
check("mapping: 5 no-match", sum(1 for v in m.values() if not v["ahm7_product_id"]) == 5)
for name, want in [("Bolt .new Pro 1 Month", -504), ("500$ API CLAUDE 30D (FW)", -171),
                   ("Muse Ai 31B Token (NW)", -165), ("Muse Ai 1B Credits", -2),
                   ("Gemini Plus 6m Links (No Card Required)", 113),
                   ("Amazon Prime 6 Months", 31)]:
    check(f"margin {name} = {want}", m[name]["margin_pkr"] == want, f"got {m[name]['margin_pkr']}")

# ---------- 2. auth ----------
orig_secret = bot.WEB_API_SECRET
bot.WEB_API_SECRET = ""
s, out = bot.handle_web_checkout({"product_id": 1}, {})
check("checkout: no secret -> 401 (fail-closed)", s == 401, f"got {s} {out}")
s, out = bot.handle_order_status("RS-000000", "x" * 32, {})
check("status: no secret -> 404 (no leak)", s == 404, f"got {s} {out}")
bot.WEB_API_SECRET = orig_secret
s, out = bot.handle_web_checkout({"product_id": 1}, {"X-Web-Secret": "wrong"})
check("checkout: wrong secret -> 401", s == 401, f"got {s} {out}")
s, out = bot.handle_ahm7_retry({"order_code": "RS-000000"}, {"X-Web-Secret": "wrong"})
check("retry: wrong secret -> 401", s == 401, f"got {s} {out}")
H = {"X-Web-Secret": "test-secret-123"}

# ---------- 3. validation ----------
s, out = bot.handle_web_checkout({"product_id": 99999}, H)
check("checkout: bad product -> 404", s == 404, f"got {s} {out}")

# ---------- 4. happy path (mocked initiate; NO Telegram needed) ----------
calls = {"initiate": 0, "place": 0}
real_initiate = bot.zurvapay_initiate
init_args = {}
def _initiate_mock(code, total, details, name="", email="", success_url=None, cancel_url=None):
    calls["initiate"] += 1
    init_args["success_url"] = success_url
    init_args["cancel_url"] = cancel_url
    return "https://checkout.test/pay/xyz", None
bot.zurvapay_initiate = _initiate_mock
gem_pid = pid_by_name("Gemini Plus 6m Links (No Card Required)")
s, out = bot.handle_web_checkout({"product_id": gem_pid, "contact": "buyer@example.com"}, H)
check("checkout: happy path 200 + checkout_url (no telegram needed)",
      s == 200 and out.get("checkout_url", "").startswith("https://"),
      f"got {s} {out}")
code = out.get("order_code", "")
tok = out.get("delivery_token", "")
check("checkout: delivery_token is 32 hex chars",
      len(tok) == 32 and all(ch in "0123456789abcdef" for ch in tok), f"got {tok!r}")
check("checkout: order_url has code+token",
      out.get("order_url", "").startswith("https://retailshop.pages.dev/order.html?code=")
      and f"code={code}" in out.get("order_url", "") and f"token={tok}" in out.get("order_url", ""),
      f"got {out.get('order_url')!r}")
check("checkout: success_url/cancel_url = order page",
      (init_args.get("success_url") or "").startswith("https://retailshop.pages.dev/order.html?code=")
      and init_args.get("cancel_url") == init_args.get("success_url"),
      f"got {init_args}")
c = bot.db()
o = c.execute("SELECT * FROM orders WHERE code=?", (code,)).fetchone()
check("checkout: order in DB, channel=web, amount=site price 379, uid=0",
      o and o["channel"] == "web" and o["amount_pkr"] == 379 and o["user_id"] == 0,
      f"got {dict(o) if o else None}")
check("checkout: delivery_token + web_contact stored",
      o and o["delivery_token"] == tok and o["web_contact"] == "buyer@example.com",
      f"got {dict(o) if o else None}")
check("checkout: order status pending", o and o["status"] == "pending")
c.close()

# contact optional: checkout with no contact at all must also work
s2, out2 = bot.handle_web_checkout({"product_id": gem_pid}, H)
check("checkout: no contact field still works", s2 == 200 and out2.get("order_code"), f"got {s2} {out2}")

# ---------- 5. margin guard (Bolt = loss) ----------
bolt_pid = pid_by_name("Bolt .new Pro 1 Month")
bolt_code = bot.gen_order_code()
bolt_oid = bot.create_order(0, "", [{"pid": bolt_pid, "qty": 1}], "zurvapay",
                            "Rs 399 via Zurvapay (web)", "zurvapay", 399, bolt_code, "", 0, channel="web")
c = bot.db(); c.execute("UPDATE orders SET status='zurva_processing' WHERE id=?", (bolt_oid,)); c.commit(); c.close()
real_place = bot.ahm7_place_order
def _place_guard(*a, **k):
    calls["place"] += 1
    raise AssertionError("AHM7 buy must NOT be called for a loss-maker")
bot.ahm7_place_order = _place_guard
ok, msg = bot.fulfill_web_order(bolt_oid)
check("margin guard: Bolt returns False, no AHM7 call", ok is False and calls["place"] == 0, f"ok={ok} msg={msg}")
c = bot.db(); o = c.execute("SELECT status, ahm7_note FROM orders WHERE id=?", (bolt_oid,)).fetchone(); c.close()
check("margin guard: status ahm7_pending + note",
      o["status"] == "ahm7_pending" and "margin guard" in (o["ahm7_note"] or ""), f"got {dict(o)}")

# ---------- 6. 402 path (Gemini, profitable) ----------
def _place_402(pid, ref):
    calls["place"] += 1
    return None, "AHM7 error HTTP 402: insufficient_balance (needed: 300, balances: {'pkr': 0})"
bot.ahm7_place_order = _place_402
gem_oid = bot.create_order(0, "", [{"pid": gem_pid, "qty": 1}], "zurvapay",
                           "Rs 379 via Zurvapay (web)", "zurvapay", 379, bot.gen_order_code(), "", 0, channel="web")
c = bot.db(); c.execute("UPDATE orders SET status='zurva_processing' WHERE id=?", (gem_oid,)); c.commit(); c.close()
ok, msg = bot.fulfill_web_order(gem_oid)
check("402 path: returns False, no crash", ok is False, f"ok={ok} msg={msg}")
c = bot.db(); o = c.execute("SELECT status, ahm7_note FROM orders WHERE id=?", (gem_oid,)).fetchone(); c.close()
check("402 path: status ahm7_pending, note has 402",
      o["status"] == "ahm7_pending" and "402" in (o["ahm7_note"] or ""), f"got {dict(o)}")

# ---------- 7. success path (mocked buy; delivery = order page, no DM) ----------
def _place_ok(pid, ref):
    calls["place"] += 1
    return ({"orderId": "AHM7-TEST123", "status": "approved", "totalCharged": 266,
             "currency": "pkr", "credentials": ["test@example.com | Pass123"],
             "product": {"instructions": "Login with the email."}}, None)
bot.ahm7_place_order = _place_ok
check("no Telegram DM path anymore (function removed)",
      not hasattr(bot, "_ahm7_deliver_dm") and not hasattr(bot, "_resolve_telegram_chat_id"))
ok, msg = bot.fulfill_web_order(gem_oid)
check("success path: delivered True", ok is True, f"ok={ok} msg={msg}")
c = bot.db(); o = c.execute("SELECT status, ahm7_order_id, ahm7_credentials FROM orders WHERE id=?", (gem_oid,)).fetchone(); c.close()
check("success path: status delivered, AHM7 order + credentials stored",
      o["status"] == "delivered" and o["ahm7_order_id"] == "AHM7-TEST123"
      and "Pass123" in (o["ahm7_credentials"] or ""), f"got {dict(o)}")

# ---------- 8. no double-buy (retry with stored creds) ----------
calls["place"] = 0
bot.ahm7_place_order = _place_guard  # must NOT be called
c = bot.db(); c.execute("UPDATE orders SET status='ahm7_pending' WHERE id=?", (gem_oid,)); c.commit(); c.close()
ok, msg = bot.fulfill_web_order(gem_oid)
check("no double-buy: retry marks delivered, place not called",
      ok is True and calls["place"] == 0, f"ok={ok} msg={msg}")
c = bot.db(); o = c.execute("SELECT status FROM orders WHERE id=?", (gem_oid,)).fetchone(); c.close()
check("no double-buy: status delivered again", o["status"] == "delivered")

# ---------- 9. retry endpoint ----------
s, out = bot.handle_ahm7_retry({"order_code": code}, H)  # the happy-path order is still 'pending'
check("retry: pending order rejected", s == 400, f"got {s} {out}")
c = bot.db(); c.execute("UPDATE orders SET status='ahm7_pending' WHERE id=?", (gem_oid,)); c.commit(); c.close()
c = bot.db(); grow = c.execute("SELECT code FROM orders WHERE id=?", (gem_oid,)).fetchone()["code"]; c.close()
s, out = bot.handle_ahm7_retry({"order_code": grow}, H)
check("retry: ahm7_pending -> delivered via stored creds", s == 200 and out.get("ok") is True, f"got {s} {out}")

# ---------- 10. order status endpoint (function level) ----------
GEM_TOKEN = "aa" * 16
BOLT_TOKEN = "bb" * 16
c = bot.db()
c.execute("UPDATE orders SET delivery_token=? WHERE id=?", (GEM_TOKEN, gem_oid))
c.execute("UPDATE orders SET delivery_token=? WHERE id=?", (BOLT_TOKEN, bolt_oid))
c.commit(); c.close()
s, out = bot.handle_order_status(code, tok, H, "1.2.3.4")
check("status: pending web order -> processing, no credentials",
      s == 200 and out.get("status") == "processing" and "credentials" not in out,
      f"got {s} {out}")
s, out = bot.handle_order_status(grow, GEM_TOKEN, H, "1.2.3.4")
check("status: delivered order -> credentials returned",
      s == 200 and out.get("status") == "delivered"
      and any("Pass123" in str(x) for x in out.get("credentials", [])),
      f"got {s} {out}")
s, out = bot.handle_order_status(bolt_code, BOLT_TOKEN, H, "1.2.3.4")
check("status: ahm7_pending order -> manual (honest message)",
      s == 200 and out.get("status") == "manual" and "manual" in (out.get("message") or ""),
      f"got {s} {out}")
s, out = bot.handle_order_status(code, "00" * 16, H, "1.2.3.4")
check("status: wrong token -> 404, no leak", s == 404 and out.get("error") == "not found", f"got {s} {out}")
s, out = bot.handle_order_status(code, "", H, "1.2.3.4")
check("status: missing token -> 404", s == 404, f"got {s} {out}")
s, out = bot.handle_order_status(code, tok, {"X-Web-Secret": "wrong"}, "1.2.3.4")
check("status: wrong secret -> 404, no leak", s == 404, f"got {s} {out}")
tg_oid = bot.create_order(999, "tguser", [{"pid": gem_pid, "qty": 1}], "zurvapay",
                          "Rs 379 via Zurvapay", "zurvapay", 379, bot.gen_order_code(), "", 0, channel="telegram")
c = bot.db(); tg_code = c.execute("SELECT code FROM orders WHERE id=?", (tg_oid,)).fetchone()["code"]; c.close()
s, out = bot.handle_order_status(tg_code, GEM_TOKEN, H, "1.2.3.4")
check("status: telegram order -> 404 (web only)", s == 404, f"got {s} {out}")
# rate limit: 30/min per IP
rl = [bot.handle_order_status(grow, GEM_TOKEN, H, "9.9.9.9")[0] for _ in range(31)]
check("status: rate limit 31st call -> 429", rl[:30] == [200] * 30 and rl[30] == 429,
      f"got {rl[-3:]}")

# ---------- 11. IPN integration (mocked signature verify) ----------
bot.ahm7_place_order = _place_402
ipn_oid = bot.create_order(0, "", [{"pid": gem_pid, "qty": 1}], "zurvapay",
                           "Rs 379 via Zurvapay (web)", "zurvapay", 379, bot.gen_order_code(), "", 0, channel="web")
c = bot.db(); icode = c.execute("SELECT code FROM orders WHERE id=?", (ipn_oid,)).fetchone()["code"]; c.close()
real_verify = bot.verify_zurvapay_ipn
bot.verify_zurvapay_ipn = lambda data, raw_body=b"", headers=None: (True, "test")
f = {"identifier": icode, "amount": "379", "status": "success", "signature": "x",
     "signatures": ["x"], "txid": "TESTTX1"}
ok, msg = bot._zurva_handle_order_ipn(icode, f)
check("IPN: web order -> paid-pending (402), IPN handled True", ok is True and "paid-pending" in msg, f"ok={ok} msg={msg}")
c = bot.db(); o = c.execute("SELECT status FROM orders WHERE id=?", (ipn_oid,)).fetchone(); c.close()
check("IPN: order status ahm7_pending", o["status"] == "ahm7_pending", f"got {o['status']}")
ok2, msg2 = bot._zurva_handle_order_ipn(icode, f)
check("IPN: duplicate IPN does not re-process", ok2 is True and "pehle hi" in msg2, f"ok={ok2} msg={msg2}")
bot.verify_zurvapay_ipn = real_verify

# ---------- 12. HTTP layer (real health server) ----------
bot.zurvapay_initiate = real_initiate
bot.ahm7_place_order = real_place
bot.start_health_server()
time.sleep(1.0)
import requests as rq
http_code = http_tok = None
try:
    r = rq.get("http://127.0.0.1:18099/", timeout=5)
    check("HTTP: GET / 200", r.status_code == 200, f"got {r.status_code}")
    r = rq.post("http://127.0.0.1:18099/api/web-checkout",
                json={"product_id": gem_pid}, timeout=5)
    check("HTTP: wrong secret -> 401", r.status_code == 401, f"got {r.status_code} {r.text[:120]}")
    r = rq.post("http://127.0.0.1:18099/api/web-checkout",
                json={"product_id": gem_pid, "contact": "a@b.com"},
                headers={"X-Web-Secret": "test-secret-123"}, timeout=10)
    # test env me ZURVAPAY_PUBLIC_KEY nahi hai -> initiate fail -> 502,
    # lekin order + delivery token ban jane chahiye (order kabhi lose nahi hota)
    j = r.json()
    http_code, http_tok = j.get("order_code"), j.get("delivery_token")
    check("HTTP: checkout no Telegram needed -> 502 (no Zurvapay key) + order+token",
          r.status_code == 502 and http_code and len(http_tok or "") == 32,
          f"got {r.status_code} {r.text[:200]}")
    r = rq.post("http://127.0.0.1:18099/api/web-checkout",
                json={"product_id": 99999},
                headers={"X-Web-Secret": "test-secret-123"}, timeout=5)
    check("HTTP: bad product -> 404", r.status_code == 404, f"got {r.status_code}")
    r = rq.post("http://127.0.0.1:18099/api/ahm7-retry",
                json={"order_code": "RS-000000"},
                headers={"X-Web-Secret": "wrong"}, timeout=5)
    check("HTTP: retry wrong secret -> 401", r.status_code == 401, f"got {r.status_code}")
    if http_code and http_tok:
        r = rq.get(f"http://127.0.0.1:18099/api/order/{http_code}?token={http_tok}",
                   headers={"X-Web-Secret": "test-secret-123"}, timeout=5)
        check("HTTP: GET /api/order valid token -> 200 processing",
              r.status_code == 200 and r.json().get("status") == "processing",
              f"got {r.status_code} {r.text[:160]}")
        r = rq.get(f"http://127.0.0.1:18099/api/order/{http_code}?token={'00' * 16}",
                   headers={"X-Web-Secret": "test-secret-123"}, timeout=5)
        check("HTTP: GET /api/order wrong token -> 404", r.status_code == 404, f"got {r.status_code}")
        r = rq.get(f"http://127.0.0.1:18099/api/order/{http_code}?token={http_tok}", timeout=5)
        check("HTTP: GET /api/order no secret -> 404", r.status_code == 404, f"got {r.status_code}")
    else:
        check("HTTP: order status checks (skipped — no code/token)", False, "no http_code/http_tok")
except Exception as e:
    check("HTTP layer reachable", False, str(e))

print(f"\n==== {len(PASS)} passed, {len(FAIL)} failed ====")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
