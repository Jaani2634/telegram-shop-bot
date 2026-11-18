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
bot.PUBLIC_BASE_URL = "http://node19.botkeep.cloud:61495"  # prod value, for URL assertions

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
check("mapping: 18 profitable at site prices (+100)",
      sum(1 for v in mapped if (v["margin_pkr"] or 0) > 0) == 18,
      f"got {sum(1 for v in mapped if (v['margin_pkr'] or 0) > 0)}")
check("mapping: 3 loss/breakeven (guard blocks)",
      sum(1 for v in mapped if (v["margin_pkr"] or 0) <= 0) == 3)
check("mapping: Muse Ai 1B Credits site price is 171 (+100)",
      m["Muse Ai 1B Credits"]["site_price_pkr"] == 171)
check("mapping: 5 no-match", sum(1 for v in m.values() if not v["ahm7_product_id"]) == 5)
for name, want in [("Bolt .new Pro 1 Month", -404), ("500$ API CLAUDE 30D (FW)", -71),
                   ("Muse Ai 31B Token (NW)", -65), ("Muse Ai 1B Credits", 98),
                   ("Gemini Plus 6m Links (No Card Required)", 213),
                   ("Amazon Prime 6 Months", 131)]:
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
check("checkout: success_url/cancel_url = bot-server URLs (approved domain, ?code=)",
      (init_args.get("success_url") or "").startswith("http://node19.botkeep.cloud:61495/zurvapay/success?code=")
      and (init_args.get("cancel_url") or "").startswith("http://node19.botkeep.cloud:61495/zurvapay/cancel?code=")
      and "retailshop.pages.dev" not in (init_args.get("success_url") or "")
      and f"code={code}" in (init_args.get("success_url") or ""),
      f"got {init_args}")
c = bot.db()
o = c.execute("SELECT * FROM orders WHERE code=?", (code,)).fetchone()
check("checkout: order in DB, channel=web, amount=site price 479, uid=0",
      o and o["channel"] == "web" and o["amount_pkr"] == 479 and o["user_id"] == 0,
      f"got {dict(o) if o else None}")
check("checkout: delivery_token + web_contact stored",
      o and o["delivery_token"] == tok and o["web_contact"] == "buyer@example.com",
      f"got {dict(o) if o else None}")
check("checkout: order status pending", o and o["status"] == "pending")
check("checkout: checkout_url stored in DB (Pay-now safety net)",
      o and o["checkout_url"] == "https://checkout.test/pay/xyz",
      f"got {dict(o) if o else None}")
c.close()

# contact optional: checkout with no contact at all must also work
s2, out2 = bot.handle_web_checkout({"product_id": gem_pid}, H)
check("checkout: no contact field still works", s2 == 200 and out2.get("order_code"), f"got {s2} {out2}")

# Zurvapay initiate failure -> 502 + payment_failed issue (order code in
# detail, no credentials anywhere in the issue row).
bot.zurvapay_initiate = lambda *a, **k: (None, "Zurvapay error: test gateway down")
s, out = bot.handle_web_checkout(
    {"product_id": gem_pid, "contact": "buyer2@example.com"}, H)
check("checkout: initiate failure -> 502 + order_code",
      s == 502 and out.get("order_code"), f"got {s} {out}")
fail_code = out.get("order_code", "")
c = bot.db()
_pr = c.execute("SELECT * FROM issues WHERE kind='payment_failed' AND user_email=?",
                ("buyer2@example.com",)).fetchall()
c.close()
check("issues: payment failure logged (order code in detail, no secrets)",
      len(_pr) == 1 and fail_code in (_pr[0]["detail"] or "")
      and "secret" not in json.dumps(dict(_pr[0])).lower()
      and "password" not in json.dumps(dict(_pr[0])).lower(),
      f"got {[dict(r) for r in _pr]}")
bot.zurvapay_initiate = _initiate_mock

# ---------- 4c. charged price == products.csv (site == CSV rule) ----------
# Web checkout used to charge ahm7-mapping.json site_price_pkr, which still
# holds pre-sync prices for 11 of the 26 site products (undercharging them
# vs the CSV). Now the charged amount must equal products.csv for every
# mapped product. (Initiate is still the mock — no real payment; orders
# stay pending, so the later sold-count check is unaffected.)
import csv as _csv
_csv_prices = {}
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "products.csv"), encoding="utf-8") as _f:
    for _row in _csv.DictReader(_f):
        _csv_prices[_row["name"]] = int(_row["price_pkr"])
_csv_checked = 0
_csv_bad = []
for _name, _want in sorted(_csv_prices.items()):
    if _name not in m:
        continue  # site pe nahi — sirf 26 mapped products check hote hain
    _pid = pid_by_name(_name)
    _s, _out = bot.handle_web_checkout({"product_id": _pid}, H)
    _cc = bot.db()
    _o = _cc.execute("SELECT amount_pkr FROM orders WHERE code=?", (_out.get("order_code", ""),)).fetchone()
    _cc.close()
    _csv_checked += 1
    if not (_s == 200 and _o and _o["amount_pkr"] == _want):
        _csv_bad.append((_name, _want, _s, dict(_o) if _o else None))
check("price: charged amount == products.csv for all 26 mapped products",
      _csv_checked == 26 and not _csv_bad, f"checked={_csv_checked} bad={_csv_bad}")
# The 11 that used to be undercharged (mapping < CSV) — spot-check the worst:
for _name, _want in [("Bolt .new Pro 1 Month", 1003), ("500$ API CLAUDE 30D (FW)", 2770),
                     ("Muse Ai 31B Token (NW)", 634), ("Muse Ai Token On Client Account", 181),
                     ("MUSE AI 1B TOKEN (W2D)", 261), ("Udemy Personal Plan 1M", 662)]:
    _pid = pid_by_name(_name)
    _s, _out = bot.handle_web_checkout({"product_id": _pid}, H)
    _cc = bot.db()
    _o = _cc.execute("SELECT amount_pkr FROM orders WHERE code=?", (_out.get("order_code", ""),)).fetchone()
    _cc.close()
    check(f"price: {_name} charges Rs {_want} (was Rs {m[_name]['site_price_pkr']} in mapping)",
          _s == 200 and _o and _o["amount_pkr"] == _want,
          f"got {_s} {dict(_o) if _o else None}")
# Cart qty multiplies the CSV price: Bolt x2 = 2006, not 2x the old 499.
_bolt_pid = pid_by_name("Bolt .new Pro 1 Month")
_s, _out = bot.handle_web_checkout({"items": [{"product_id": _bolt_pid, "qty": 2}]}, H)
_cc = bot.db()
_o = _cc.execute("SELECT amount_pkr FROM orders WHERE code=?", (_out.get("order_code", ""),)).fetchone()
_cc.close()
check("price: cart Bolt x2 charges 2 x 1003 = 2006",
      _s == 200 and _o and _o["amount_pkr"] == 2006, f"got {_s} {dict(_o) if _o else None}")

# ---------- 4b. Zurvapay redirect targets (bot-server -> order page) ----------
tgt = bot.zurvapay_redirect_target("success", code)
check("redirect: success -> order page with code+token",
      tgt == f"https://retailshop.pages.dev/order.html?code={code}&token={tok}", f"got {tgt}")
tgt = bot.zurvapay_redirect_target("cancel", code)
check("redirect: cancel -> order page too",
      tgt == f"https://retailshop.pages.dev/order.html?code={code}&token={tok}", f"got {tgt}")
check("redirect: unknown code -> shop home",
      bot.zurvapay_redirect_target("success", "RS-000000") == "https://retailshop.pages.dev/")
check("redirect: empty code -> shop home",
      bot.zurvapay_redirect_target("success", "") == "https://retailshop.pages.dev/")

# ---------- 5. margin guard (Bolt = loss at a below-cost CHARGED price) ----------
# Guard compares AHM7 cost against the price actually charged (products.csv
# price), NOT the stale mapping site_price_pkr: at the CSV price (1003)
# Bolt is profitable (cost 903), so the guard only fires when the charged
# price is below cost. Simulate that by lowering the DB price for this
# test, then restore it to the CSV price.
bolt_pid = pid_by_name("Bolt .new Pro 1 Month")
c = bot.db(); c.execute("UPDATE products SET price_pkr=499 WHERE id=?", (bolt_pid,)); c.commit(); c.close()
bolt_code = bot.gen_order_code()
bolt_oid = bot.create_order(0, "", [{"pid": bolt_pid, "qty": 1}], "zurvapay",
                            "Rs 499 via Zurvapay (web)", "zurvapay", 499, bolt_code, "", 0, channel="web")
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
c = bot.db(); c.execute("UPDATE products SET price_pkr=1003 WHERE id=?", (bolt_pid,)); c.commit(); c.close()
c = bot.db(); _bp = c.execute("SELECT * FROM products WHERE id=?", (bolt_pid,)).fetchone(); c.close()
check("margin guard: Bolt charged price restored to CSV 1003 (guard passes there)",
      _bp and _bp["price_pkr"] == 1003
      and bot._site_price_pkr(_bp, m["Bolt .new Pro 1 Month"]) == 1003,
      f"got {dict(_bp) if _bp else None}")

# ---------- 6. 402 path (Gemini, profitable) ----------
def _place_402(pid, ref, quantity=1):
    calls["place"] += 1
    return None, "AHM7 error HTTP 402: insufficient_balance (needed: 300, balances: {'pkr': 0})"
bot.ahm7_place_order = _place_402
gem_oid = bot.create_order(0, "", [{"pid": gem_pid, "qty": 1}], "zurvapay",
                           "Rs 479 via Zurvapay (web)", "zurvapay", 479, bot.gen_order_code(), "", 0, channel="web")
c = bot.db(); c.execute("UPDATE orders SET status='zurva_processing' WHERE id=?", (gem_oid,)); c.commit(); c.close()
ok, msg = bot.fulfill_web_order(gem_oid)
check("402 path: returns False, no crash", ok is False, f"ok={ok} msg={msg}")
c = bot.db(); o = c.execute("SELECT status, ahm7_note FROM orders WHERE id=?", (gem_oid,)).fetchone(); c.close()
check("402 path: status ahm7_pending, note has 402",
      o["status"] == "ahm7_pending" and "402" in (o["ahm7_note"] or ""), f"got {dict(o)}")

# ---------- 7. success path (mocked buy; delivery = order page, no DM) ----------
def _place_ok(pid, ref, quantity=1):
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
check("status: pending web order -> checkout_url returned (Pay-now safety net)",
      s == 200 and out.get("checkout_url") == "https://checkout.test/pay/xyz",
      f"got {s} {out}")
s, out = bot.handle_order_status(grow, GEM_TOKEN, H, "1.2.3.4")
check("status: delivered order -> credentials returned",
      s == 200 and out.get("status") == "delivered"
      and any("Pass123" in str(x) for x in out.get("credentials", [])),
      f"got {s} {out}")
check("status: delivered order -> no checkout_url (already paid)",
      "checkout_url" not in out, f"got {out}")
s, out = bot.handle_order_status(bolt_code, BOLT_TOKEN, H, "1.2.3.4")
check("status: ahm7_pending order -> manual (honest message)",
      s == 200 and out.get("status") == "manual" and "manual" in (out.get("message") or ""),
      f"got {s} {out}")
check("status: ahm7_pending order -> no checkout_url (already paid)",
      "checkout_url" not in out, f"got {out}")
s, out = bot.handle_order_status(code, "00" * 16, H, "1.2.3.4")
check("status: wrong token -> 404, no leak", s == 404 and out.get("error") == "not found", f"got {s} {out}")
s, out = bot.handle_order_status(code, "", H, "1.2.3.4")
check("status: missing token -> 404", s == 404, f"got {s} {out}")
s, out = bot.handle_order_status(code, tok, {"X-Web-Secret": "wrong"}, "1.2.3.4")
check("status: wrong secret -> 404, no leak", s == 404, f"got {s} {out}")
tg_oid = bot.create_order(999, "tguser", [{"pid": gem_pid, "qty": 1}], "zurvapay",
                          "Rs 479 via Zurvapay", "zurvapay", 479, bot.gen_order_code(), "", 0, channel="telegram")
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
                           "Rs 479 via Zurvapay (web)", "zurvapay", 479, bot.gen_order_code(), "", 0, channel="web")
c = bot.db(); icode = c.execute("SELECT code FROM orders WHERE id=?", (ipn_oid,)).fetchone()["code"]; c.close()
real_verify = bot.verify_zurvapay_ipn
bot.verify_zurvapay_ipn = lambda data, raw_body=b"", headers=None: (True, "test")
f = {"identifier": icode, "amount": "479", "status": "success", "signature": "x",
     "signatures": ["x"], "txid": "TESTTX1"}
ok, msg = bot._zurva_handle_order_ipn(icode, f)
check("IPN: web order -> paid-pending (402), IPN handled True", ok is True and "paid-pending" in msg, f"ok={ok} msg={msg}")
c = bot.db(); o = c.execute("SELECT status FROM orders WHERE id=?", (ipn_oid,)).fetchone(); c.close()
check("IPN: order status ahm7_pending", o["status"] == "ahm7_pending", f"got {o['status']}")
ok2, msg2 = bot._zurva_handle_order_ipn(icode, f)
check("IPN: duplicate IPN does not re-process", ok2 is True and "pehle hi" in msg2, f"ok={ok2} msg={msg2}")
bot.verify_zurvapay_ipn = real_verify


# ---------- 13. cart checkout (multi-item) ----------
ama_pid = pid_by_name("Amazon Prime 6 Months")
muse_pid = pid_by_name("Muse Ai 1B Credits")
# validation
s, out = bot.handle_web_checkout({"items": []}, H)
check("cart: empty items -> 400", s == 400, f"got {s} {out}")
s, out = bot.handle_web_checkout({"items": [{"product_id": gem_pid, "qty": 0}]}, H)
check("cart: qty 0 -> 400", s == 400, f"got {s} {out}")
s, out = bot.handle_web_checkout({"items": [{"product_id": gem_pid, "qty": 11}]}, H)
check("cart: qty 11 -> 400", s == 400, f"got {s} {out}")
s, out = bot.handle_web_checkout({"items": [{"product_id": 99999, "qty": 1}]}, H)
check("cart: unknown product -> 404", s == 404, f"got {s} {out}")
s, out = bot.handle_web_checkout({"items": [{"product_id": gem_pid, "qty": 1},
                                           {"product_id": ama_pid, "qty": 2}]}, H)
check("cart: checkout 200 + checkout_url",
      s == 200 and out.get("checkout_url", "").startswith("https://"),
      f"got {s} {out}")
cart_code = out.get("order_code", "")
c = bot.db()
o = c.execute("SELECT * FROM orders WHERE code=?", (cart_code,)).fetchone()
c.close()
check("cart: order total = 479 + 799*2 = 2077",
      o and o["amount_pkr"] == 2077, f"got {dict(o) if o else None}")
check("cart: items_json has both lines",
      o and len(json.loads(o["items_json"])) == 2, f"got {o['items_json'] if o else None}")
# multi-line fulfillment (mocked buy, per-line creds with labels)
def _place_multi(pid, ref, quantity=1):
    calls["place"] += 1
    return ({"orderId": f"AHM7-{pid}", "status": "approved", "totalCharged": 100,
             "currency": "pkr",
             "credentials": [{"email": f"user-{pid}@example.com", "password": f"P{pid}"}],
             "product": {}}, None)
bot.ahm7_place_order = _place_multi
calls["place"] = 0
c = bot.db(); c.execute("UPDATE orders SET status='zurva_processing' WHERE id=?", (o["id"],)); c.commit(); c.close()
ok, msg = bot.fulfill_web_order(o["id"])
check("cart fulfill: delivered True, 2 AHM7 calls", ok is True and calls["place"] == 2, f"ok={ok} msg={msg} calls={calls['place']}")
c = bot.db(); o2 = c.execute("SELECT status, ahm7_order_id, ahm7_credentials FROM orders WHERE id=?", (o["id"],)).fetchone(); c.close()
creds2 = json.loads(o2["ahm7_credentials"] or "[]")
check("cart fulfill: status delivered, both AHM7 ids stored",
      o2["status"] == "delivered"
      and len((o2["ahm7_order_id"] or "").split(",")) == 2
      and all(x.startswith("AHM7-") for x in (o2["ahm7_order_id"] or "").split(",")),
      f"got {dict(o2)}")
check("cart fulfill: creds carry product labels",
      len(creds2) == 2 and any(x.get("label", "").startswith("Gemini") for x in creds2)
      and any(x.get("label", "").startswith("Amazon") for x in creds2), f"got {creds2}")
# cart with a loss-maker: profitable line delivers, loss line blocks, order stays pending
# (Bolt's charged price is lowered below its AHM7 cost for this test — at
# its CSV price 1003 it is profitable and the guard would NOT fire.)
c = bot.db(); c.execute("UPDATE products SET price_pkr=499 WHERE id=?", (bolt_pid,)); c.commit(); c.close()
cart2_code = bot.gen_order_code()
cart2_oid = bot.create_order(0, "", [{"pid": gem_pid, "qty": 1}, {"pid": bolt_pid, "qty": 1}],
                             "zurvapay", "Rs 978 via Zurvapay (web)", "zurvapay", 978,
                             cart2_code, "", 0, channel="web")
c = bot.db(); c.execute("UPDATE orders SET status='zurva_processing' WHERE id=?", (cart2_oid,)); c.commit(); c.close()
ok, msg = bot.fulfill_web_order(cart2_oid)
c = bot.db(); o3 = c.execute("SELECT status, ahm7_credentials, ahm7_note FROM orders WHERE id=?", (cart2_oid,)).fetchone(); c.close()
creds3 = json.loads(o3["ahm7_credentials"] or "[]")
check("cart margin guard: pending, Gemini creds saved, note names Bolt",
      ok is False and o3["status"] == "ahm7_pending"
      and len(creds3) == 1 and creds3[0].get("label", "").startswith("Gemini")
      and "margin guard" in (o3["ahm7_note"] or "") and "Bolt" in (o3["ahm7_note"] or ""),
      f"ok={ok} msg={msg} got {dict(o3)}")
# retry must not re-buy the delivered Gemini line
calls["place"] = 0
bot.ahm7_place_order = _place_guard
ok, msg = bot.fulfill_web_order(cart2_oid)
check("cart retry: no re-buy of delivered line, still pending on Bolt",
      ok is False and calls["place"] == 0, f"ok={ok} msg={msg} calls={calls['place']}")
bot.ahm7_place_order = real_place
c = bot.db(); c.execute("UPDATE products SET price_pkr=1003 WHERE id=?", (bolt_pid,)); c.commit(); c.close()

# ---------- 14. reviews (real, no fakes) ----------
s, out = bot.handle_submit_review({"product_id": gem_pid, "name": "Ali", "rating": 5, "text": "Bohat acha, instant delivery!"}, H, "5.5.5.5")
check("review: happy path 200 + id returned", s == 200 and out.get("ok") is True and isinstance((out.get("review") or {}).get("id"), int), f"got {s} {out}")
s, out = bot.handle_submit_review({"product_id": gem_pid, "name": "Ali", "rating": 5, "text": "x"}, {"X-Web-Secret": "wrong"}, "5.5.5.5")
check("review: wrong secret -> 401", s == 401, f"got {s} {out}")
s, out = bot.handle_submit_review({"product_id": gem_pid, "name": "Ali", "rating": 6, "text": "x"}, H, "5.5.5.6")
check("review: rating 6 -> 400", s == 400, f"got {s} {out}")
s, out = bot.handle_submit_review({"product_id": gem_pid, "name": "Ali", "rating": 5, "text": "see https://spam.example"}, H, "5.5.5.7")
check("review: link in text -> 400", s == 400, f"got {s} {out}")
s, out = bot.handle_submit_review({"product_id": gem_pid, "name": "Ali", "rating": 5, "text": "x" * 501}, H, "5.5.5.8")
check("review: 501 chars -> 400", s == 400, f"got {s} {out}")
s, out = bot.handle_submit_review({"product_id": 99999, "name": "Ali", "rating": 5, "text": "ok"}, H, "5.5.5.9")
check("review: unknown product -> 404", s == 404, f"got {s} {out}")
s, out = bot.handle_get_reviews(gem_pid)
# sold for Gemini here: delivered gem order (1) + delivered cart (1) +
# ahm7_pending cart2 (1) + ahm7_pending IPN order (1) = 4 real orders.
rev_id = (out.get("reviews") or [{}])[0].get("id")
check("reviews GET: 1 review, sold counts real orders",
      s == 200 and len(out.get("reviews", [])) == 1 and out.get("sold") == 4, f"got {s} {out}")
check("reviews GET: review carries its id (admin delete needs it)",
      isinstance(rev_id, int), f"got {out}")
s, out = bot.handle_get_reviews(99999)
check("reviews GET: unknown product -> 404", s == 404, f"got {s} {out}")
# rate limit: 5/hour per IP
for i in range(5):
    bot.handle_submit_review({"product_id": gem_pid, "name": "R", "rating": 4, "text": f"ok {i}"}, H, "6.6.6.6")
s, out = bot.handle_submit_review({"product_id": gem_pid, "name": "R", "rating": 4, "text": "one more"}, H, "6.6.6.6")
check("review: 6th in an hour -> 429", s == 429, f"got {s} {out}")
# overlong name/text: rejected with machine-readable codes (Worker used to
# silently slice them to 40/500 — slicing is removed, so these now reach
# the bot and must come back 400, not stored truncated).
s, out = bot.handle_submit_review({"product_id": gem_pid, "name": "N" * 41, "rating": 5, "text": "ok"}, H, "5.5.5.10")
check("review: 41-char name -> 400 name_too_long",
      s == 400 and out.get("error") == "name_too_long" and "40" in (out.get("message") or ""),
      f"got {s} {out}")
s, out = bot.handle_submit_review({"product_id": gem_pid, "name": "N" * 40, "rating": 5, "text": "ok"}, H, "5.5.5.11")
check("review: 40-char name -> 200 (boundary)", s == 200, f"got {s} {out}")
s, out = bot.handle_submit_review({"product_id": gem_pid, "name": "Ali", "rating": 5, "text": "x" * 500}, H, "5.5.5.12")
check("review: 500-char text -> 200 (boundary)", s == 200, f"got {s} {out}")
s, out = bot.handle_submit_review({"product_id": gem_pid, "name": "Ali", "rating": 5, "text": "x" * 501}, H, "5.5.5.13")
check("review: 501-char text -> 400 text_too_long",
      s == 400 and out.get("error") == "text_too_long" and "500" in (out.get("message") or ""),
      f"got {s} {out}")
s, out = bot.handle_submit_review({"product_id": gem_pid, "name": "Ali", "rating": 5, "text": "see https://spam.example"}, H, "5.5.5.14")
check("review: https link in text -> 400 link_not_allowed",
      s == 400 and out.get("error") == "link_not_allowed", f"got {s} {out}")
s, out = bot.handle_submit_review({"product_id": gem_pid, "name": "Ali", "rating": 5, "text": "see www.spam.example"}, H, "5.5.5.15")
check("review: www link in text -> 400 link_not_allowed",
      s == 400 and out.get("error") == "link_not_allowed", f"got {s} {out}")
# delete: ADMIN ONLY now. Pehle sirf X-Web-Secret chahiye tha — Worker
# endpoint public hai, is liye web se koi bhi kisi bhi review ko delete
# kar sakta tha. Ab admin dashboard session (Bearer <redacted> token) chahiye;
# koi UI isay call nahi karta, sirf test-review cleanup ke liye hai.
s, out = bot.handle_submit_review({"product_id": gem_pid, "name": "Del", "rating": 5, "text": "delete me"}, H, "8.8.8.8")
_rid = (out.get("review") or {}).get("id")
check("review delete: submit returns id", s == 200 and isinstance(_rid, int), f"got {s} {out}")
_cc = bot.db()
_cc.execute("INSERT INTO web_users(email,name,password_hash,created_at,phone,is_admin) "
            "VALUES('admin-del@example.com','Admin','x',?,'',1)", (bot._now_str(),))
_admin_uid = _cc.execute("SELECT id FROM web_users WHERE email='admin-del@example.com'").fetchone()["id"]
_cc.execute("INSERT INTO web_users(email,name,password_hash,created_at,phone,is_admin) "
            "VALUES('user-del@example.com','User','x',?,'',0)", (bot._now_str(),))
_user_uid = _cc.execute("SELECT id FROM web_users WHERE email='user-del@example.com'").fetchone()["id"]
_cc.commit(); _cc.close()
_ADMIN_DEL_TOK = bot._create_web_session(_admin_uid, is_admin=1)
_USER_DEL_TOK = bot._create_web_session(_user_uid, is_admin=0)
def _bearer(_tok):
    return {"Authorization": f"Bearer {_tok}"}
s, out = bot.handle_delete_review({"id": _rid}, {})
check("review delete: no token -> 401", s == 401, f"got {s} {out}")
s, out = bot.handle_delete_review({"id": _rid}, {"X-Web-Secret": "test-secret-123"})
check("review delete: X-Web-Secret alone no longer enough -> 401", s == 401, f"got {s} {out}")
s, out = bot.handle_delete_review({"id": _rid}, _bearer(_USER_DEL_TOK))
check("review delete: normal user -> 403 admin only",
      s == 403 and out.get("error") == "admin only", f"got {s} {out}")
s, out = bot.handle_delete_review({"id": 999999}, _bearer(_ADMIN_DEL_TOK))
check("review delete: unknown id -> 404", s == 404, f"got {s} {out}")
s, out = bot.handle_delete_review({"id": "abc"}, _bearer(_ADMIN_DEL_TOK))
check("review delete: bad id -> 400", s == 400, f"got {s} {out}")
s, out = bot.handle_delete_review({"id": _rid}, _bearer(_ADMIN_DEL_TOK))
check("review delete: admin happy path 200", s == 200 and out.get("deleted") == 1, f"got {s} {out}")
s, out = bot.handle_get_reviews(gem_pid)
check("review delete: review gone from GET", s == 200 and all(r.get("text") != "delete me" for r in out.get("reviews", [])), f"got {s} {out}")

# ---------- 14b. reviews migration (purani live DB, purana schema) ----------
import sqlite3 as _sq
_old_db = "/tmp/test-shop-old.db"
for _f in (_old_db, _old_db + "-wal", _old_db + "-shm"):
    if os.path.exists(_f):
        os.remove(_f)
_c = _sq.connect(_old_db)
_c.execute("CREATE TABLE products(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT DEFAULT '', price_pkr INTEGER NOT NULL, price_usdt REAL NOT NULL, active INTEGER DEFAULT 1, kind TEXT DEFAULT 'account', category TEXT DEFAULT '', bulk TEXT DEFAULT '', terms TEXT DEFAULT '', unlimited INTEGER DEFAULT 0)")
_c.execute("INSERT INTO products(name,description,price_pkr,price_usdt) VALUES('Old Prod','',100,1.0)")
_c.execute("CREATE TABLE reviews(id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL, user_name TEXT, rating INTEGER NOT NULL, text TEXT, verified INTEGER DEFAULT 0, source TEXT DEFAULT 'user')")
_c.execute("INSERT INTO reviews(product_id,user_name,rating,text,verified,source) VALUES(1,'Old User',5,'purani telegram review',1,'user')")
_c.commit(); _c.close()
_main_db = bot.DB_PATH
bot.DB_PATH = _old_db
bot.init_db()
_c = bot.db()
_cols = [r[1] for r in _c.execute("PRAGMA table_info(reviews)").fetchall()]
check("migration: old reviews table gains name + created_at", "name" in _cols and "created_at" in _cols, f"cols={_cols}")
_r = _c.execute("SELECT name FROM reviews WHERE user_name='Old User'").fetchone()
check("migration: old review name backfilled from user_name", _r and _r["name"] == "Old User", f"got {dict(_r) if _r else None}")
_c.close()
s, out = bot.handle_submit_review({"product_id": 1, "name": "Mig", "rating": 4, "text": "migration ke baad review"}, H, "7.7.7.7")
check("migration: submit works on migrated old DB", s == 200 and out.get("ok") is True, f"got {s} {out}")
s, out = bot.handle_get_reviews(1)
check("migration: GET returns old + new review", s == 200 and len(out.get("reviews", [])) == 2, f"got {s} {out}")
bot.DB_PATH = _main_db

# ---------- 14c. stock API (GET /api/stock) ----------
c = bot.db()
for _i in range(3):
    c.execute("INSERT INTO accounts(product_id,login,password,extra,status) VALUES(?,?,?,?, 'available')",
              (gem_pid, f"g{_i}@x.com", "p", ""))
c.execute("INSERT INTO accounts(product_id,login,password,extra,status) VALUES(?,?,?,?, 'sold')",
          (gem_pid, "gs@x.com", "p", ""))
for _i in range(2):
    c.execute("INSERT INTO accounts(product_id,login,password,extra,status) VALUES(?,?,?,?, 'available')",
              (ama_pid, f"a{_i}@x.com", "p", ""))
c.commit(); c.close()
s, out = bot.handle_stock("7.7.7.1")
check("stock: 200 + ok", s == 200 and out.get("ok") is True, f"got {s} {out}")
_st = out.get("stock", {})
check("stock: Gemini count 3 (sold row not counted)", _st.get(str(gem_pid)) == 3,
      f"got {_st.get(str(gem_pid))}")
check("stock: Amazon count 2", _st.get(str(ama_pid)) == 2, f"got {_st.get(str(ama_pid))}")
check("stock: zero-stock product present as 0 (Muse 1B)", _st.get(str(muse_pid)) == 0,
      f"got {_st.get(str(muse_pid))}")
check("stock: all 26 mapped products present",
      all(str(pid_by_name(n)) in _st for n in m),
      f"missing={[n for n in m if str(pid_by_name(n)) not in _st]}")
check("stock: keys are strings, values are ints",
      all(isinstance(k, str) and isinstance(v, int) for k, v in _st.items()),
      f"got {list(_st.items())[:3]}")
# source: "ahm7" jahan mapping me truthy ahm7_product_id hai (web
# checkout wala hi predicate), "local" jahan koi AHM7 mapping nahi.
_src = out.get("source", {})
check("stock: source present for all products",
      all(str(pid_by_name(n)) in _src for n in m),
      f"missing={[n for n in m if str(pid_by_name(n)) not in _src]}")
check("stock: AHM7-mapped product source is 'ahm7' (Amazon)",
      _src.get(str(ama_pid)) == "ahm7", f"got {_src.get(str(ama_pid))}")
_unmapped_name = next(n for n, v in m.items() if not v.get("ahm7_product_id"))
_unmapped_pid = pid_by_name(_unmapped_name)
check("stock: unmapped product source is 'local' (%s)" % _unmapped_name,
      _src.get(str(_unmapped_pid)) == "local", f"got {_src.get(str(_unmapped_pid))}")
check("stock: source values only 'ahm7'/'local'",
      set(_src.values()) <= {"ahm7", "local"}, f"got {set(_src.values())}")
# rate limit: 30/min per IP
_rl = [bot.handle_stock("7.7.7.2")[0] for _ in range(31)]
check("stock: rate limit 31st call -> 429", _rl[:30] == [200] * 30 and _rl[30] == 429,
      f"got {_rl[-3:]}")
# no auth required: the handler takes no headers at all (public endpoint)
s, out = bot.handle_stock("7.7.7.3")
check("stock: no auth required", s == 200 and out.get("ok") is True, f"got {s} {out}")

# ---------- 14d. web-checkout vs local stock ----------
# Web orders are fulfilled via AHM7, NOT from the local accounts table —
# so zero local stock must NOT block checkout. (A local-stock gate here
# would block every web sale: AHM7 products have no local accounts.)
check("stock: Muse 1B has zero local accounts", bot.stock_count(muse_pid) == 0)
s, out = bot.handle_web_checkout({"product_id": muse_pid}, H)
check("checkout: zero local stock still starts payment (AHM7 fulfillment, by design)",
      s == 200 and out.get("checkout_url", "").startswith("https://"), f"got {s} {out}")

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
        r = rq.get(f"http://127.0.0.1:18099/zurvapay/success?code={http_code}", allow_redirects=False, timeout=5)
        check("HTTP: GET /zurvapay/success -> 302 to order page",
              r.status_code == 302 and r.headers.get("Location", "").startswith("https://retailshop.pages.dev/order.html?code="),
              f"got {r.status_code} {r.headers.get('Location')}")
    else:
        check("HTTP: order status checks (skipped — no code/token)", False, "no http_code/http_tok")
    r = rq.get("http://127.0.0.1:18099/zurvapay/cancel?code=RS-000000", allow_redirects=False, timeout=5)
    check("HTTP: GET /zurvapay/cancel unknown code -> 302 home",
          r.status_code == 302 and r.headers.get("Location") == "https://retailshop.pages.dev/",
          f"got {r.status_code} {r.headers.get('Location')}")
    r = rq.post("http://127.0.0.1:18099/api/review",
                json={"product_id": gem_pid, "name": "HTTP", "rating": 5, "text": "http test"},
                headers={"X-Web-Secret": "test-secret-123"}, timeout=5)
    check("HTTP: POST /api/review -> 200", r.status_code == 200 and r.json().get("ok") is True,
          f"got {r.status_code} {r.text[:120]}")
    r = rq.get(f"http://127.0.0.1:18099/api/reviews/{gem_pid}", timeout=5)
    check("HTTP: GET /api/reviews/:id -> 200 with reviews",
          r.status_code == 200 and len(r.json().get("reviews", [])) >= 1,
          f"got {r.status_code} {r.text[:160]}")
    r = rq.get("http://127.0.0.1:18099/api/stock", timeout=5)
    check("HTTP: GET /api/stock -> 200 with counts incl. 0-stock",
          r.status_code == 200 and r.json().get("ok") is True
          and str(gem_pid) in r.json().get("stock", {})
          and r.json()["stock"].get(str(gem_pid)) == 3
          and r.json()["stock"].get(str(muse_pid)) == 0,
          f"got {r.status_code} {r.text[:160]}")
    check("HTTP: GET /api/stock -> source map present (ahm7/local)",
          r.status_code == 200 and r.json().get("source", {}).get(str(ama_pid)) == "ahm7",
          f"got {r.status_code} {r.text[:160]}")
    r = rq.get("http://127.0.0.1:18099/api/stock/extra", timeout=5)
    check("HTTP: GET /api/stock/extra -> 404 (malformed)", r.status_code == 404,
          f"got {r.status_code}")
    r = rq.post("http://127.0.0.1:18099/api/stock", json={}, timeout=5)
    check("HTTP: POST /api/stock -> 404 (GET only)", r.status_code == 404,
          f"got {r.status_code}")
    r = rq.post("http://127.0.0.1:18099/api/review/delete",
                json={"id": 1}, timeout=5)
    check("HTTP: POST /api/review/delete without admin token -> 401",
          r.status_code == 401, f"got {r.status_code} {r.text[:120]}")
except Exception as e:
    check("HTTP layer reachable", False, str(e))

# ---------- 15. Global promo at web checkout + IPN use recording ----------
# Promo rows seedhe DB me (admin handler ka test tests-admin.py me hai).
_c = bot.db()
_c.execute("INSERT INTO promo_codes(code,pct_off,max_uses,used_count,expires_at,min_order_pkr,first_order_only,active,created_at) "
           "VALUES('WEB10',10,NULL,0,NULL,0,0,1,'2026-11-17 10:00')")
_c.execute("INSERT INTO promo_codes(code,pct_off,max_uses,used_count,expires_at,min_order_pkr,first_order_only,active,created_at) "
           "VALUES('WEBOLD',10,NULL,0,'2020-01-01 00:00',0,0,1,'2026-11-17 10:00')")
_c.execute("INSERT INTO promo_codes(code,pct_off,max_uses,used_count,expires_at,min_order_pkr,first_order_only,active,created_at) "
           "VALUES('WEBFIRST',15,NULL,0,NULL,0,1,1,'2026-11-17 10:00')")
_c.commit(); _c.close()
# Initiate mock jo total bhi capture kare (discounted total check).
_cap = {}
def _initiate_cap(code, total, details, name="", email="", success_url=None, cancel_url=None):
    _cap["code"] = code
    _cap["total"] = total
    return "https://checkout.test/pay/xyz", None
bot.zurvapay_initiate = _initiate_cap
# Gemini 479, WEB10 10% -> discount 48, charged 431.
s, out = bot.handle_web_checkout({"product_id": gem_pid, "coupon": " web10 "}, H)
check("checkout+coupon: 200, discounted total charged (479 - 48 = 431)",
      s == 200 and _cap.get("total") == 431, f"got {s} {out} cap={_cap}")
_c = bot.db()
_o = _c.execute("SELECT amount_pkr, discount_pkr, coupon_code FROM orders WHERE code=?",
                (out.get("order_code"),)).fetchone()
_c.close()
check("checkout+coupon: order stores coupon + discount + discounted amount",
      _o and _o["amount_pkr"] == 431 and _o["discount_pkr"] == 48 and _o["coupon_code"] == "WEB10",
      f"got {dict(_o) if _o else None}")
check("checkout+coupon: validate-only — used_count still 0 before payment",
      bot.db().execute("SELECT used_count FROM promo_codes WHERE code='WEB10'").fetchone()["used_count"] == 0)
_coupon_code = out.get("order_code", "")
s, out = bot.handle_web_checkout({"product_id": gem_pid, "coupon": "NOPE99"}, H)
check("checkout+coupon: unknown code -> 400 coupon_not_found",
      s == 400 and out.get("error") == "coupon_not_found", f"got {s} {out}")
s, out = bot.handle_web_checkout({"product_id": gem_pid, "coupon": "WEBOLD"}, H)
check("checkout+coupon: expired code -> 400 coupon_expired",
      s == 400 and out.get("error") == "coupon_expired", f"got {s} {out}")
s, out = bot.handle_web_checkout({"product_id": gem_pid, "coupon": "WEBFIRST"}, H)
check("checkout+coupon: first-only code on web -> 400 coupon_first_only (by design, web has no identity)",
      s == 400 and out.get("error") == "coupon_first_only", f"got {s} {out}")
# Cart + coupon: Bolt x2 = 2006, 10% = 201 (round), charged 1805.
s, out = bot.handle_web_checkout({"items": [{"product_id": _bolt_pid, "qty": 2}], "coupon": "WEB10"}, H)
check("checkout+coupon: cart total discounted (2006 - 201 = 1805)",
      s == 200 and _cap.get("total") == 1805, f"got {s} {out} cap={_cap}")
# IPN paid transition records the use (web order — deliver_order kabhi nahi chalta).
bot.ahm7_place_order = _place_402
bot.verify_zurvapay_ipn = lambda data, raw_body=b"", headers=None: (True, "test")
_f = {"identifier": _coupon_code, "amount": "431", "status": "success",
      "signature": "x", "signatures": ["x"], "txid": "TESTTX-COUPON"}
ok, msg = bot._zurva_handle_order_ipn(_coupon_code, _f)
_c = bot.db()
_uses = _c.execute("SELECT COUNT(*) n FROM promo_uses WHERE code='WEB10' AND order_code=?", (_coupon_code,)).fetchone()["n"]
_cnt = _c.execute("SELECT used_count FROM promo_codes WHERE code='WEB10'").fetchone()["used_count"]
_c.close()
check("IPN+coupon: paid transition records 1 promo use",
      ok is True and _uses == 1 and _cnt == 1, f"ok={ok} msg={msg} uses={_uses} count={_cnt}")
ok2, msg2 = bot._zurva_handle_order_ipn(_coupon_code, _f)
_c = bot.db()
_cnt2 = _c.execute("SELECT used_count FROM promo_codes WHERE code='WEB10'").fetchone()["used_count"]
_c.close()
check("IPN+coupon: duplicate IPN does not double-count the use",
      ok2 is True and _cnt2 == 1, f"ok={ok2} msg={msg2} count={_cnt2}")
bot.verify_zurvapay_ipn = real_verify
bot.zurvapay_initiate = real_initiate
bot.ahm7_place_order = real_place

# ---------- 16. HTTP: new public routes ----------
try:
    r = rq.get("http://127.0.0.1:18099/api/products", timeout=5)
    check("HTTP: GET /api/products -> 200 public catalogue",
          r.status_code == 200 and r.json().get("ok") is True
          and len(r.json().get("products", [])) >= 26
          and all("sold" in p for p in r.json()["products"]),
          f"got {r.status_code} {r.text[:160]}")
    r = rq.post("http://127.0.0.1:18099/api/coupon/validate",
                json={"code": "WEB10", "amount_pkr": 1000}, timeout=5)
    check("HTTP: POST /api/coupon/validate -> 200 discount 100",
          r.status_code == 200 and r.json().get("discount") == 100,
          f"got {r.status_code} {r.text[:160]}")
    r = rq.post("http://127.0.0.1:18099/api/coupon/validate",
                json={"code": "NOPE99", "amount_pkr": 1000}, timeout=5)
    check("HTTP: POST /api/coupon/validate unknown -> 400 coupon_not_found",
          r.status_code == 400 and r.json().get("error") == "coupon_not_found",
          f"got {r.status_code} {r.text[:160]}")
    r = rq.get("http://127.0.0.1:18099/api/admin/coupons", timeout=5)
    check("HTTP: GET /api/admin/coupons no token -> 401", r.status_code == 401, f"got {r.status_code}")
    r = rq.post("http://127.0.0.1:18099/api/products/extra", json={}, timeout=5)
    check("HTTP: POST /api/products/extra -> 404", r.status_code == 404, f"got {r.status_code}")
except Exception as e:
    check("HTTP new-routes layer reachable", False, str(e))

print(f"\n==== {len(PASS)} passed, {len(FAIL)} failed ====")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
