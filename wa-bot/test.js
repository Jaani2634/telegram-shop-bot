/**
 * wa-bot.js tests — TEMP DB ke against ONLY (kabhi live shop.db nahi).
 * Run: node test.js   (wa-bot/ dir se)
 *
 * Env (require se PEHLE set hota hai, warna wa-bot.js module-level
 * env parh lega): DB_PATH=temp DB, dummy Zurvapay key, ADMIN_ID test.
 * Network wale tests (Zurvapay initiate, Tronscan) real endpoints ko
 * dummy/garbage values se call karte hain — clean error expect karte
 * hain, crash nahi.
 */
"use strict";

const path = require("path");
const fs = require("fs");

const TEST_DB = "/tmp/wa-test/shop-test.db";
const BUYER = 923460777483; // test buyer phone (digits only)
const BUYER_JID = "923460777483@s.whatsapp.net";

process.env.DB_PATH = TEST_DB;
process.env.ADMIN_ID = "7965730503";
process.env.ZURVAPAY_PUBLIC_KEY = "dummy-public-key-for-test";
process.env.PUBLIC_BASE_URL = "http://localhost:61495";
delete process.env.WA_BOT_NUMBER; // unset path bhi test ho (import crash na kare)

const wa = require("./wa-bot.js");
const Database = require("better-sqlite3");

let pass = 0;
let fail = 0;
const results = [];

async function t(name, fn) {
  try {
    await fn();
    pass++;
    results.push(`PASS  ${name}`);
  } catch (e) {
    fail++;
    results.push(`FAIL  ${name}: ${e.message}`);
  }
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg || "assertion failed");
}

function eq(a, b, msg) {
  if (a !== b) throw new Error(`${msg || "not equal"} — got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
}

// ---------- Test setup: temp DB me settings + stock ----------

function setupTestData() {
  const db = new Database(TEST_DB);
  db.pragma("journal_mode = WAL");
  db.pragma("busy_timeout = 10000");
  const set = db.prepare("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)");
  set.run("usdt_address", "TTestAddress12345678901234567890");
  set.run("jazzcash_number", "03001234567");
  set.run("jazzcash_name", "Test Jazz");
  set.run("easypaisa_number", "03007654321");
  set.run("easypaisa_name", "Test Easy");
  set.run("tg_channel", "@retail_shop_official");
  set.run("tg_group_url", "https://t.me/+testgroup");
  set.run("wa_channel_url", "https://whatsapp.com/channel/test");
  // Product 8 (Muse Ai 31B Token NW) ko mail-kind banao (live DB jaisa).
  db.prepare("UPDATE products SET kind='mail' WHERE id=8").run();
  // Stock: product 6 (Muse Ai 1B Credits) x5, product 8 (mail) x1, product 5 x0.
  const add = db.prepare("INSERT INTO accounts(product_id,login,password,extra,status) VALUES(?,?,?,?,'available')");
  for (let i = 1; i <= 5; i++) add.run(6, `testuser${i}@example.com`, `pw${i}`, "");
  add.run(8, "mailboxuser@test.com", "https://mailbox.test/xyz123", "CODE123");
  db.close();
}

function mockSend() {
  const sent = [];
  const fn = async (jid, text) => {
    sent.push({ jid, text });
  };
  fn.sent = sent;
  return fn;
}

async function main() {
  setupTestData();
  const db = wa.getDb();

  // 1 — import: socket start nahi hua, env unset path crash nahi karta.
  await t("import loads without connecting (WA_BOT_NUMBER unset)", () => {
    const cfg = wa.getConfig();
    eq(cfg.WA_BOT_NUMBER, "", "WA_BOT_NUMBER should be empty in test env");
    eq(cfg.dbPath, TEST_DB, "dbPath should be the temp DB");
    assert(cfg.ADMIN_ID === "7965730503", "ADMIN_ID from env");
  });

  // 2 — 26 seeded products.
  await t("listActiveProducts returns 26", () => {
    eq(wa.listActiveProducts().length, 26, "product count");
  });

  // 3 — browse formatting / pagination.
  await t("formatBrowse: page 1/3, 10 items, product names + stock", () => {
    const { text, page, totalPages } = wa.formatBrowse(wa.listActiveProducts(), 1);
    eq(page, 1, "page");
    eq(totalPages, 3, "totalPages");
    assert(text.includes("Muse Ai 1B Credits"), "product 6 name on page 1");
    assert(text.includes("Page 1/3"), "page label");
    assert(text.includes("1. Amazon Prime 6 Months"), "numbering starts at 1");
  });
  await t("formatBrowse: page 3 has items 21-26", () => {
    const { text, page, totalPages } = wa.formatBrowse(wa.listActiveProducts(), 3);
    eq(page, 3, "page");
    eq(totalPages, 3, "totalPages");
    assert(text.includes("21."), "item 21 present");
    assert(text.includes("26."), "item 26 present");
    assert(!text.includes("27."), "no item 27");
  });

  // 4 — product detail (description + terms + price + stock).
  await t("formatProductDetail: price + How to Use + stock", () => {
    const p = wa.getProduct(6);
    const text = wa.formatProductDetail(p);
    assert(text.includes("Rs 71"), "price PKR");
    assert(text.includes("$0.25 USDT"), "price USDT");
    assert(text.includes("How to Use:"), "terms section");
    assert(text.includes("In Stock"), "stock line");
    assert(text.includes("Kharidne ke liye 1 likho"), "buy hint");
  });

  // 5 — Zurvapay payload (Python fields ka mirror).
  await t("buildZurvapayPayload: exact fields", () => {
    const pl = wa.buildZurvapayPayload({
      identifier: "RS-123456",
      amountPkr: 71,
      details: "Muse Ai 1B Credits x1 — RetailShop WhatsApp",
      customerName: "923460777483",
      customerEmail: "",
    });
    eq(pl.public_key, "dummy-public-key-for-test", "public_key");
    eq(pl.bot_id, "Shop_at_Retail_bot", "bot_id");
    eq(pl.identifier, "RS-123456", "identifier");
    eq(pl.currency, "PKR", "currency");
    eq(pl.amount, 71, "amount int");
    eq(pl.ipn_url, "http://localhost:61495/zurvapay/ipn", "ipn_url");
    eq(pl.success_url, "http://localhost:61495/zurvapay/success", "success_url");
    eq(pl.cancel_url, "http://localhost:61495/zurvapay/cancel", "cancel_url");
  });

  // 6 — Zurvapay initiate, dummy key -> clean error, no crash.
  await t("zurvapayInitiate (dummy key): clean error, no crash", async () => {
    const { url, error } = await wa.zurvapayInitiate("RS-123456", 71, "test", "923460777483", "");
    eq(url, null, "no url with dummy key");
    assert(typeof error === "string" && error.length > 0, "error string present: " + error);
  });

  // 7 — Tronscan verify, garbage hash -> clean not-found failure.
  await t("verifyUsdt (garbage hash): ok=false, clean reason", async () => {
    const { ok, reason } = await wa.verifyUsdt(
      "0000000000000000000000000000000000000000000000000000000000000000",
      "TTestAddress12345678901234567890",
      1.0
    );
    eq(ok, false, "garbage hash must not verify");
    assert(typeof reason === "string" && reason.length > 0, "reason present: " + reason);
  });

  // 8 — order code format.
  await t("genOrderCode: RS- + 6 digits", () => {
    for (let i = 0; i < 20; i++) {
      assert(/^RS-\d{6}$/.test(wa.genOrderCode()), "code format");
    }
  });

  // 9 — bulk pricing helpers (pure, no DB).
  await t("parseBulk + unitPricePkr", () => {
    const p = { price_pkr: 100, bulk: "3:90,5:80" };
    eq(wa.unitPricePkr(p, 1), 100, "qty1 base");
    eq(wa.unitPricePkr(p, 2), 100, "qty2 base");
    eq(wa.unitPricePkr(p, 3), 90, "qty3 tier");
    eq(wa.unitPricePkr(p, 5), 80, "qty5 tier");
    eq(JSON.stringify(wa.parseBulk("3:90,5:80")), JSON.stringify([[3, 90], [5, 80]]), "parse");
  });
  await t("usdtAmount: 0.25 x 4 = 1.0", () => {
    eq(wa.usdtAmount({ price_usdt: 0.25 }, 4), 1, "usdt total");
  });

  // 10 — createOrder + deliverOrder (account kind, qty 2, mock send).
  let deliveredOrderId = null;
  await t("deliverOrder (account, qty 2): sold + delivered + text has account + How to Use", async () => {
    const order = wa.createOrder({
      userId: BUYER,
      productId: 6,
      paymentMethod: "usdt",
      amountText: "$0.50 USDT",
      proof: "testhash123",
      qty: 2,
      amountPkr: 142,
    });
    const send = mockSend();
    const res = await wa.deliverOrder(order.id, send);
    eq(res.ok, true, "deliver ok: " + res.reason);
    deliveredOrderId = order.id;
    const row = db.prepare("SELECT status FROM orders WHERE id=?").get(order.id);
    eq(row.status, "delivered", "order delivered");
    const sold = db.prepare("SELECT COUNT(*) n FROM accounts WHERE product_id=6 AND status='sold' AND order_id=?").get(order.id);
    eq(sold.n, 2, "2 accounts sold");
    eq(send.sent.length, 1, "1 delivery message");
    eq(send.sent[0].jid, BUYER_JID, "delivery to buyer JID");
    // Jo accounts sold hue, unke login delivery text me hone chahiye
    // (run-independent: dobara run pe oldest available accounts milte hain).
    const soldLogins = db.prepare("SELECT login FROM accounts WHERE order_id=?").all(order.id).map((r) => r.login);
    eq(soldLogins.length, 2, "2 sold logins");
    for (const login of soldLogins) {
      assert(send.sent[0].text.includes(login), "sold login in delivery: " + login);
    }
    assert(send.sent[0].text.includes("How to Use:"), "instructions in delivery");
    assert(send.sent[0].text.length <= 4096, "delivery under 4096 chars");
  });

  // 11 — deliverOrder out-of-stock (product 5, zero stock).
  await t("deliverOrder out-of-stock: status + admin outbox + buyer notice", async () => {
    const order = wa.createOrder({
      userId: BUYER,
      productId: 5,
      paymentMethod: "usdt",
      amountText: "$1.79 USDT",
      proof: "testhash456",
      qty: 1,
      amountPkr: 501,
    });
    const send = mockSend();
    const res = await wa.deliverOrder(order.id, send);
    eq(res.ok, false, "deliver fails");
    eq(res.reason, "out_of_stock", "reason");
    eq(db.prepare("SELECT status FROM orders WHERE id=?").get(order.id).status, "out_of_stock", "order marked");
    const adminRow = db.prepare("SELECT * FROM outbox WHERE channel='telegram' AND recipient='7965730503' ORDER BY id DESC LIMIT 1").get();
    assert(adminRow && adminRow.text.includes("stock khatam"), "admin notified via telegram outbox");
    assert(send.sent.length === 1 && send.sent[0].text.includes("stock khatam"), "buyer notified");
  });

  // 12 — mail-kind delivery (product 8).
  await t("deliverOrder (mail kind): Payment Verified + code | email | mailbox", async () => {
    const order = wa.createOrder({
      userId: BUYER,
      productId: 8,
      paymentMethod: "usdt",
      amountText: "$1.32 USDT",
      proof: "testhash789",
      qty: 1,
      amountPkr: 369,
    });
    const send = mockSend();
    const res = await wa.deliverOrder(order.id, send);
    eq(res.ok, true, "mail deliver ok: " + res.reason);
    assert(send.sent[0].text.includes("Payment Verified"), "mail header");
    assert(send.sent[0].text.includes("CODE123 | mailboxuser@test.com | https://mailbox.test/xyz123"), "mail line format");
    assert(send.sent[0].text.includes("How to Use:"), "instructions in mail delivery");
  });

  // 13 — manual order record + admin notify outbox row.
  await t("recordManualOrder: order + telegram outbox row to ADMIN_ID", () => {
    const order = wa.recordManualOrder({
      phoneInt: BUYER,
      productId: 6,
      qty: 1,
      method: "jazzcash",
      proof: "TID123456",
      amountPkr: 71,
    });
    const row = db.prepare("SELECT * FROM orders WHERE id=?").get(order.id);
    eq(row.channel, "whatsapp", "channel");
    eq(row.payment_method, "jazzcash", "method");
    eq(row.proof, "TID123456", "proof");
    eq(row.status, "pending", "status pending");
    const ob = db.prepare("SELECT * FROM outbox WHERE channel='telegram' AND recipient='7965730503' AND text LIKE ? ORDER BY id DESC LIMIT 1").get(`%${order.code}%`);
    assert(ob, "admin outbox row exists");
    assert(ob.text.includes("Muse Ai 1B Credits"), "product name in admin msg");
    assert(ob.text.includes("TID123456"), "proof in admin msg");
  });

  // 14 — my orders text.
  await t("myOrdersText: lists delivered + out_of_stock with labels", () => {
    const text = wa.myOrdersText(BUYER);
    assert(text.includes("DELIVERED"), "delivered label");
    assert(text.includes("CANCELLED (stock khatam)"), "oos label");
    assert(text.includes("Muse Ai 1B Credits"), "product name");
  });

  // 15 — full buyer flow via handleUserMessage (mock send, no Baileys).
  await t("buyer flow hi->browse->detail->qty->pay->jazzcash->TID creates order", async () => {
    const jid = "923000000001@s.whatsapp.net";
    const phone = 923000000001;
    const send = mockSend();
    const say = async (txt) => {
      await wa.handleUserMessage(jid, phone, txt, null, send, null);
      return send.sent[send.sent.length - 1].text;
    };
    let r = await say("hi");
    assert(r.includes("Browse Products") && r.includes("My Orders"), "main menu");
    r = await say("1");
    assert(r.includes("Page 1/3"), "browse page 1");
    r = await say("6");
    assert(r.includes("Muse Ai 1B Credits") && r.includes("Rs 71"), "product 6 detail");
    r = await say("1");
    assert(r.toLowerCase().includes("quantity"), "qty prompt");
    r = await say("2");
    assert(r.includes("Zurvapay") && r.includes("JazzCash") && r.includes("USDT"), "pay menu");
    assert(r.includes("Rs 142"), "total 71x2");
    r = await say("2");
    assert(r.includes("03001234567") && r.includes("Test Jazz"), "jazzcash details");
    r = await say("TID999888");
    assert(r.includes("admin ko bhej di gayi"), "confirm text");
    assert(r.includes("RS-"), "order code in confirm");
    const row = db.prepare("SELECT * FROM orders WHERE user_id=? AND proof='TID999888'").get(phone);
    assert(row, "manual order in DB");
    eq(row.payment_method, "jazzcash", "method jazzcash");
    eq(row.qty, 2, "qty 2");
    eq(row.channel, "whatsapp", "channel whatsapp");
  });

  // 16 — support flow.
  await t("support flow: message -> support_msgs + telegram outbox", async () => {
    const jid = "923000000002@s.whatsapp.net";
    const phone = 923000000002;
    const send = mockSend();
    await wa.handleUserMessage(jid, phone, "3", null, send, null);
    assert(send.sent[0].text.toLowerCase().includes("support"), "support prompt");
    await wa.handleUserMessage(jid, phone, "mera account kaam nahi kar raha", null, send, null);
    assert(send.sent[1].text.includes("admin ko bhej diya"), "support confirm");
    const sm = db.prepare("SELECT * FROM support_msgs WHERE user_id=?").get(phone);
    assert(sm && sm.text === "mera account kaam nahi kar raha", "support_msgs row");
    const ob = db.prepare("SELECT * FROM outbox WHERE channel='telegram' AND text LIKE '%mera account kaam nahi kar raha%'").get();
    assert(ob, "support telegram outbox row");
  });

  // 17 — outbox claim/send with mock socket.
  await t("pollOutboxOnce: pending whatsapp row -> sent, JID+text recorded", async () => {
    const id = wa.queueOutbox("whatsapp", BUYER_JID, "Test outbox delivery message");
    const send = mockSend();
    const res = await wa.pollOutboxOnce(send);
    assert(res.sent >= 1, "at least 1 sent");
    const row = db.prepare("SELECT * FROM outbox WHERE id=?").get(id);
    eq(row.status, "sent", "row sent");
    assert(row.sent_at, "sent_at set");
    assert(send.sent.some((m) => m.jid === BUYER_JID && m.text === "Test outbox delivery message"), "mock got JID+text");
    const res2 = await wa.pollOutboxOnce(send);
    eq(res2.sent, 0, "nothing pending on second poll");
  });

  // 18 — outbox send failure -> failed (not crash, not stuck in sending).
  await t("pollOutboxOnce: send throws -> row marked failed", async () => {
    const id = wa.queueOutbox("whatsapp", BUYER_JID, "This will fail to send");
    const failingSend = async () => {
      throw new Error("mock network down");
    };
    await wa.pollOutboxOnce(failingSend);
    const row = db.prepare("SELECT * FROM outbox WHERE id=?").get(id);
    eq(row.status, "failed", "row failed");
  });

  // 19 — channels text from settings.
  await t("channelsText: settings links", () => {
    const text = wa.channelsText();
    assert(text.includes("@retail_shop_official"), "tg channel");
    assert(text.includes("https://t.me/+testgroup"), "tg group");
    assert(text.includes("https://whatsapp.com/channel/test"), "wa channel");
  });

  // 20 — JID helpers.
  await t("jidToPhone / phoneToJid round-trip", () => {
    eq(wa.jidToPhone("923716248783@s.whatsapp.net"), "923716248783", "jid->phone");
    eq(wa.phoneToJid("923716248783"), "923716248783@s.whatsapp.net", "phone->jid");
  });

  console.log("\n" + results.join("\n"));
  console.log(`\n==== ${pass} passed, ${fail} failed (total ${pass + fail}) ====`);
  wa.closeDb();
  process.exit(fail ? 1 : 0);
}

main().catch((e) => {
  console.error("TEST RUNNER CRASH:", e);
  process.exit(2);
});
