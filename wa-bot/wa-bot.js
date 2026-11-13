/**
 * RetailShop — WhatsApp shop bot (Node.js + Baileys).
 *
 * Telegram bot (Python, bot.py) ke sath ONE shop: dono ek hi SQLite DB
 * (shop.db) share karte hain. Python bot payments/IPN ka source of truth
 * hai; ye bot sirf doosra storefront hai (buyer flow + USDT-auto delivery
 * + outbox polling). Contract: ../WA_CONTRACT.md
 *
 * MVP buyer flow: numbered TEXT menus (koi interactive buttons nahi).
 *   Main menu: 1 Browse, 2 My Orders, 3 Support, 4 Our Channels
 *   Browse 10/page -> product detail (description + How to Use) -> qty 1-5
 *   -> payment: 1 Zurvapay (auto), 2 JazzCash (manual), 3 Easypaisa
 *   (manual), 4 USDT TRC20 (auto verify via Tronscan).
 * Admin commands WhatsApp par NONE (admin Telegram /orders se manage karta hai).
 *
 * Run:  node wa-bot.js        (pehli baar pairing code / QR se pair hota hai)
 * Test: node test.js          (temp DB ke against; live shop.db kabhi nahi)
 *
 * Env vars (Botkeep env, Python bot wale hi naam):
 *   ADMIN_ID, ZURVAPAY_PUBLIC_KEY, ZURVAPAY_BASE_URL, ZURVAPAY_BOT_ID,
 *   PUBLIC_BASE_URL, ZURVAPAY_IPN_URL, DB_PATH, WA_BOT_NUMBER (digits only,
 *   spare number — pehli pairing ke liye). Payment details (JazzCash /
 *   Easypaisa numbers, USDT address, channel links) `settings` table se
 *   aate hain, env se NAHI — bilkul Python bot ki tarah.
 */
"use strict";

const path = require("path");
const fs = require("fs");
const crypto = require("crypto");

const ROOT = path.resolve(__dirname, "..");
const AUTH_DIR = path.join(__dirname, "auth");
const MEDIA_DIR = path.join(__dirname, "media");
const PAIRING_FILE = path.join(__dirname, "pairing-code.txt");

const ADMIN_ID = (process.env.ADMIN_ID || "").trim();
const ZURVAPAY_PUBLIC_KEY = (process.env.ZURVAPAY_PUBLIC_KEY || "").trim();
const ZURVAPAY_BASE_URL = (process.env.ZURVAPAY_BASE_URL || "https://zurvapay.com").replace(/\/+$/, "");
const ZURVAPAY_BOT_ID = (process.env.ZURVAPAY_BOT_ID || "Shop_at_Retail_bot").trim();
const PUBLIC_BASE_URL = (process.env.PUBLIC_BASE_URL || "").replace(/\/+$/, "");
const ZURVAPAY_IPN_URL = (process.env.ZURVAPAY_IPN_URL || "").trim();
// Spare number, digits only. Code me kabhi hardcode nahi — sirf env se.
// Unset ho to bot crash nahi karta, clear message log karke wait karta hai.
const WA_BOT_NUMBER = (process.env.WA_BOT_NUMBER || "").replace(/\D/g, "");
// WA_SKIP_PAIRING_CODE=1: pairing code request mat karo, QR mode me raho.
// (Pairing code har 5s rotate ho raha tha kyunki socket band ho jata tha;
// QR mode se pata chalega ke socket bina code request ke tikta hai ya nahi.)
const WA_SKIP_PAIRING_CODE = process.env.WA_SKIP_PAIRING_CODE === "1";

let dbPath = process.env.DB_PATH || path.join(ROOT, "shop.db");
let _db = null;

function log(...args) {
  console.log(new Date().toISOString(), "[wa-bot]", ...args);
}

// ---------- DB ----------

function setDbPath(p) {
  if (_db) {
    try { _db.close(); } catch (e) { /* ignore */ }
    _db = null;
  }
  dbPath = p;
}

function getDb() {
  if (!_db) {
    const Database = require("better-sqlite3");
    _db = new Database(dbPath);
    // Python db() ki tarah: WAL + busy_timeout, warna do processes
    // ek hi DB likhte waqt "database is locked" aayega.
    _db.pragma("journal_mode = WAL");
    _db.pragma("busy_timeout = 10000");
  }
  return _db;
}

function closeDb() {
  if (_db) {
    try { _db.close(); } catch (e) { /* ignore */ }
    _db = null;
  }
}

function getSetting(key) {
  const r = getDb().prepare("SELECT value FROM settings WHERE key=?").get(key);
  return r ? r.value : "";
}

function listActiveProducts() {
  return getDb().prepare("SELECT * FROM products WHERE active=1 ORDER BY id").all();
}

function getProduct(id) {
  return getDb().prepare("SELECT * FROM products WHERE id=?").get(id) || null;
}

function stockCount(productId) {
  const r = getDb()
    .prepare("SELECT COUNT(*) n FROM accounts WHERE product_id=? AND status='available'")
    .get(productId);
  return r ? r.n : 0;
}

function queueOutbox(channel, recipient, text) {
  const db = getDb();
  const now = fmtDateTime(new Date());
  const info = db
    .prepare("INSERT INTO outbox(channel,recipient,text,status,created_at) VALUES(?,?,?,'pending',?)")
    .run(channel, String(recipient), text, now);
  return info.lastInsertRowid;
}

// ---------- Small pure helpers ----------

function fmtDateTime(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function fmtDatePk(d) {
  // Python: "%d/%m/%Y %I:%M %p PKT"
  const p = (n) => String(n).padStart(2, "0");
  let h = d.getHours();
  const ap = h >= 12 ? "PM" : "AM";
  h = h % 12 || 12;
  return `${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()} ${p(h)}:${p(d.getMinutes())} ${ap} PKT`;
}

function genOrderCode() {
  // Python ki tarah: RS- + 6 random digits
  return "RS-" + String(100000 + crypto.randomInt(900000));
}

function jidToPhone(jid) {
  return String(jid || "").replace("@s.whatsapp.net", "").replace(/\D/g, "");
}

function phoneToJid(phone) {
  return `${String(phone).replace(/\D/g, "")}@s.whatsapp.net`;
}

/** '3:360,5:345' -> [[3,360],[5,345]] (Python parse_bulk jaisa). */
function parseBulk(s) {
  const tiers = [];
  for (const part of String(s || "").split(",")) {
    const p = part.trim();
    const idx = p.indexOf(":");
    if (idx < 0) continue;
    const qi = parseInt(p.slice(0, idx).trim(), 10);
    const upi = parseInt(p.slice(idx + 1).trim(), 10);
    if (!Number.isNaN(qi) && !Number.isNaN(upi)) tiers.push([qi, upi]);
  }
  return tiers.sort((a, b) => a[0] - b[0]);
}

/** Bulk tiers ke hisaab se unit price (Python unit_price_pkr jaisa). */
function unitPricePkr(product, qty) {
  let price = product.price_pkr;
  for (const [q, up] of parseBulk(product.bulk || "")) {
    if (qty >= q) price = up;
  }
  return price;
}

function usdtAmount(product, qty) {
  return Math.round(product.price_usdt * qty * 100) / 100;
}

// ---------- Menu / message text (plain text only — WhatsApp par Telegram Markdown nahi) ----------

const PAGE_SIZE = 10;

function mainMenuText() {
  return (
    "Assalam-o-Alaikum! RetailShop me khush aamdeed.\n\n" +
    "1. Browse Products\n" +
    "2. My Orders\n" +
    "3. Support\n" +
    "4. Our Channels\n\n" +
    "Apne option ka number bhejo (1-4).\n" +
    "'hi' ya 'menu' likhne se ye menu dobara aata hai."
  );
}

function formatBrowse(products, page) {
  const totalPages = Math.max(1, Math.ceil(products.length / PAGE_SIZE));
  const p = Math.min(Math.max(page, 1), totalPages);
  const slice = products.slice((p - 1) * PAGE_SIZE, p * PAGE_SIZE);
  const lines = slice.map((prod, i) => {
    const n = (p - 1) * PAGE_SIZE + i + 1;
    const stockTxt = prod.unlimited
      ? "In Stock"
      : stockCount(prod.id) > 0
        ? `In Stock (${stockCount(prod.id)})`
        : "Out of Stock";
    return `${n}. ${prod.name} — Rs ${prod.price_pkr} — ${stockTxt}`;
  });
  let text =
    `RetailShop — Products (Page ${p}/${totalPages})\n\n` +
    lines.join("\n") +
    "\n\nProduct dekhne ke liye uska number bhejo.";
  if (totalPages > 1) text += "\nAgla page: N, pichla page: P";
  text += "\nMenu: 0 ya 'menu' likho.";
  return { text, page: p, totalPages };
}

function formatProductDetail(product) {
  const stockTxt = product.unlimited
    ? "In Stock"
    : stockCount(product.id) > 0
      ? `In Stock (${stockCount(product.id)})`
      : "Out of Stock";
  const desc = (product.description || "").trim();
  const terms = (product.terms || "").trim();
  let text = `${product.name}\n\nPrice: Rs ${product.price_pkr} ($${product.price_usdt} USDT)\nStock: ${stockTxt}`;
  if (desc) text += `\n\n${desc}`;
  if (terms) text += `\n\nHow to Use:\n${terms}`;
  text += "\n\nKharidne ke liye 1 likho, wapas jaane ke liye 0 likho.";
  return text;
}

function formatPayMenu(product, qty, amountPkr, amountUsdt) {
  return (
    `Payment Method — ${product.name} x${qty}\n` +
    `Total: Rs ${amountPkr} ($${amountUsdt} USDT)\n\n` +
    `1. Zurvapay — JazzCash / Easypaisa / Raast (Auto)\n` +
    `2. JazzCash (Manual)\n` +
    `3. Easypaisa (Manual)\n` +
    `4. USDT TRC20 (Auto Verify)\n\n` +
    `Apna option ka number bhejo (1-4).\n` +
    `Wapas jaane ke liye 0 likho.`
  );
}

function formatManualPay(method, amountPkr) {
  if (method === "jazzcash") {
    return (
      `JazzCash pe Rs ${amountPkr} bhejo:\n` +
      `Number: ${getSetting("jazzcash_number")}\n` +
      `Name: ${getSetting("jazzcash_name")}\n\n` +
      `Payment ke baad TID (transaction ID) yahan bhejo, ya payment ka screenshot bhejo.\n` +
      `Admin verify karke account deliver kar dega.`
    );
  }
  return (
    `Easypaisa pe Rs ${amountPkr} bhejo:\n` +
    `Number: ${getSetting("easypaisa_number")}\n` +
    `Name: ${getSetting("easypaisa_name")}\n\n` +
    `Payment ke baad TID (transaction ID) yahan bhejo, ya payment ka screenshot bhejo.\n` +
    `Admin verify karke account deliver kar dega.`
  );
}

function formatUsdtPay(amountUsdt) {
  return (
    `USDT (TRC20) pe $${amountUsdt} bhejo:\n` +
    `Address: ${getSetting("usdt_address")}\n\n` +
    `Payment ke baad TX hash (transaction ID) yahan bhejo.\n` +
    `Bot khud verify karke account foran deliver kar dega.`
  );
}

function myOrdersText(phoneInt) {
  const rows = getDb()
    .prepare("SELECT * FROM orders WHERE user_id=? AND channel='whatsapp' ORDER BY id DESC LIMIT 10")
    .all(phoneInt);
  if (!rows.length) return "Aapke abhi tak koi orders nahi hain.\n\nMenu ke liye 'menu' likho.";
  const labels = {
    pending: "PAID — admin review",
    delivered: "DELIVERED",
    rejected: "CANCELLED",
    out_of_stock: "CANCELLED (stock khatam)",
  };
  const lines = rows.map((o) => {
    const prod = getProduct(o.product_id);
    const name = prod ? prod.name : "?";
    return `#${o.id} (${o.code || ""}) — ${name} — ${o.amount_text || ""} — ${labels[o.status] || o.status}`;
  });
  return `My Orders\n\n${lines.join("\n")}\n\nMenu ke liye 'menu' likho.`;
}

function channelsText() {
  return (
    `Our Channels\n\n` +
    `Telegram Channel: ${getSetting("tg_channel") || "-"}\n` +
    `Telegram Group: ${getSetting("tg_group_url") || "-"}\n` +
    `WhatsApp Channel: ${getSetting("wa_channel_url") || "-"}\n\n` +
    `Menu ke liye 'menu' likho.`
  );
}

// ---------- Delivery text (Python wa_delivery_text ka exact mirror, plain text) ----------

function buildDeliveryText(product, accs, order, dateTxt) {
  const terms = (product.terms || "").trim() || (product.description || "").trim();
  const howto = terms ? `\n\nHow to Use:\n${terms}` : "";
  const kind = product.kind || "account";
  if (kind === "mail") {
    const lines = accs.map((a) =>
      a.extra ? `${a.extra} | ${a.login} | ${a.password}` : `${a.login} | ${a.password}`
    );
    return (
      `✅ Payment Verified\n` +
      `Product: ${product.name}\nOrder ID: PAY-${order.id}\n` +
      `Paid: ${order.amount_text}\nDate: ${dateTxt}\n\n` +
      `Account\n${lines.join("\n")}\n\n` +
      `Order Delivery Note\n` +
      `You have received mail and mailbox url.\n` +
      `Copy the mail and paste it on muse.ai.\n\n` +
      `Then go to mailbox site, get otp and paste on muse.ai to complete login.\n\n` +
      `OTP na mile ya koi issue ho to Support se rabta karein — code admin bhej dega.` +
      howto
    );
  }
  if (accs.length === 1) {
    const acc = accs[0];
    return (
      `✅ Payment confirmed! Aapka account mil gaya:\n\n` +
      `Product: ${product.name}\n` +
      `Login / Email: ${acc.login}\n` +
      `Password: ${acc.password}\n\n` +
      `Order #${order.id} (PAY-${order.id})\n` +
      `Account ko save kar lein. Koi issue ho to admin se rabta karein.` +
      howto
    );
  }
  const lines = accs.map((a, i) => `${i + 1}. Login: ${a.login}\n   Password: ${a.password}`);
  return (
    `✅ Payment confirmed! Aapke ${accs.length} accounts mil gaye:\n\n` +
    `Product: ${product.name}\n\n${lines.join("\n")}\n\n` +
    `Order #${order.id} (PAY-${order.id})\n` +
    `Accounts ko save kar lein. Koi issue ho to admin se rabta karein.` +
    howto
  );
}

// ---------- Orders ----------

function createOrder({ userId, productId, paymentMethod, amountText, proof, qty, amountPkr }) {
  const db = getDb();
  const code = genOrderCode();
  const itemsJson = JSON.stringify([{ pid: productId, qty }]);
  const created = fmtDateTime(new Date());
  const info = db
    .prepare(
      `INSERT INTO orders(user_id,username,product_id,payment_method,amount_text,proof,status,created_at,code,items_json,qty,discount_pkr,coupon_code,amount_pkr,channel)
       VALUES(?,?,?,?,?,?,'pending',?,?,?,? ,0,'',?,'whatsapp')`
    )
    .run(userId, String(userId), productId, paymentMethod, amountText, proof || "", created, code, itemsJson, qty, amountPkr);
  return { id: info.lastInsertRowid, code };
}

/** Manual order (JazzCash/Easypaisa) record karo + admin ko Telegram pe notify karo (outbox). */
function recordManualOrder({ phoneInt, productId, qty, method, proof, amountPkr }) {
  const product = getProduct(productId);
  const order = createOrder({
    userId: phoneInt,
    productId,
    paymentMethod: method,
    amountText: `Rs ${amountPkr}`,
    proof,
    qty,
    amountPkr,
  });
  notifyAdminManualOrder(order.id);
  return order;
}

function notifyAdminManualOrder(orderId) {
  if (!ADMIN_ID) return null;
  const db = getDb();
  const o = db.prepare("SELECT * FROM orders WHERE id=?").get(orderId);
  if (!o) return null;
  const p = getProduct(o.product_id);
  const payLabel = o.payment_method === "jazzcash" ? "JazzCash" : o.payment_method === "easypaisa" ? "Easypaisa" : o.payment_method;
  const text =
    `New WhatsApp Order #${o.id} (${o.code || ""})\n` +
    `Product: ${p ? p.name : "?"}\n` +
    `Qty: ${o.qty}\n` +
    `Buyer: ${o.user_id} (WhatsApp)\n` +
    `Payment: ${payLabel}\n` +
    `Amount: ${o.amount_text}\n` +
    `Proof: ${o.proof}\n` +
    `Stock left: ${p ? stockCount(p.id) : 0}\n\n` +
    `Telegram /orders se Approve / Reject karein.`;
  return queueOutbox("telegram", String(ADMIN_ID), text);
}

function notifyAdminSupport(phoneInt, text) {
  if (!ADMIN_ID) return null;
  const db = getDb();
  db.prepare(
    "INSERT INTO support_msgs(user_id,username,text,direction,created_at) VALUES(?,?,?,?,?)"
  ).run(phoneInt, String(phoneInt), text, "user", fmtDateTime(new Date()));
  return queueOutbox(
    "telegram",
    String(ADMIN_ID),
    `WhatsApp Support — ${phoneInt}:\n${text}\n\nReply ke liye Telegram /reply use karein.`
  );
}

// ---------- deliverOrder (JS mirror of Python deliver_order — SIRF USDT-auto path ke liye) ----------

/**
 * Stock check -> accounts assign (sold) -> order delivered -> buyer ko
 * plain-text delivery message (instructions ke sath) via sendFn(jid, text).
 * Out-of-stock ho to order out_of_stock, admin ko Telegram outbox se
 * notify, buyer ko Baileys se batao. Returns {ok, reason}.
 * Zurvapay IPN wale orders Python deliver_order se deliver hote hain
 * (outbox ke zariye) — ye function sirf USDT-auto path ke liye hai.
 */
async function deliverOrder(orderId, sendFn) {
  const db = getDb();
  const order = db.prepare("SELECT * FROM orders WHERE id=?").get(orderId);
  if (!order) return { ok: false, reason: "order not found" };
  let items = null;
  try {
    items = order.items_json ? JSON.parse(order.items_json) : null;
  } catch (e) {
    items = null;
  }
  if (!items || !items.length) items = [{ pid: order.product_id, qty: order.qty || 1 }];

  const plan = [];
  for (const it of items) {
    const product = getProduct(it.pid);
    if (!product) continue;
    const accs = db
      .prepare("SELECT * FROM accounts WHERE product_id=? AND status='available' ORDER BY id LIMIT ?")
      .all(it.pid, it.qty);
    if (accs.length < it.qty) {
      db.prepare("UPDATE orders SET status='out_of_stock' WHERE id=?").run(orderId);
      const jid = phoneToJid(order.user_id);
      if (ADMIN_ID) {
        queueOutbox(
          "telegram",
          String(ADMIN_ID),
          `⚠️ WhatsApp Order #${orderId} (${order.code || ""}) ke liye stock khatam! Product: ${product.name}. Stock add karo, phir manually deliver karna hoga.`
        );
      }
      await sendFn(jid, `Sorry, ${product.name} ka stock khatam ho gaya. Admin se rabta karein, refund / replacement milega.`);
      return { ok: false, reason: "out_of_stock" };
    }
    plan.push({ product, accs });
  }
  if (!plan.length) {
    db.prepare("UPDATE orders SET status='out_of_stock' WHERE id=?").run(orderId);
    return { ok: false, reason: "out_of_stock" };
  }

  const now = fmtDateTime(new Date());
  const assign = db.transaction(() => {
    for (const { accs } of plan) {
      for (const acc of accs) {
        db.prepare("UPDATE accounts SET status='sold', sold_to=?, sold_at=?, order_id=? WHERE id=?")
          .run(order.user_id, now, orderId, acc.id);
      }
    }
    db.prepare("UPDATE orders SET status='delivered', account_id=? WHERE id=?")
      .run(plan[0].accs[0].id, orderId);
  });
  assign();

  const dateTxt = fmtDatePk(new Date());
  const jid = phoneToJid(order.user_id);
  for (const { product, accs } of plan) {
    await sendFn(jid, buildDeliveryText(product, accs, order, dateTxt));
  }
  return { ok: true };
}

// ---------- Zurvapay (Python zurvapay_initiate jaisa) ----------

function zurvapayIpnUrl() {
  if (ZURVAPAY_IPN_URL) return ZURVAPAY_IPN_URL;
  if (PUBLIC_BASE_URL) return `${PUBLIC_BASE_URL}/zurvapay/ipn`;
  return "";
}

function zurvapayPublicBase() {
  if (PUBLIC_BASE_URL) return PUBLIC_BASE_URL;
  const ipn = zurvapayIpnUrl();
  if (ipn) {
    try {
      const u = new URL(ipn);
      return `${u.protocol}//${u.host}`;
    } catch (e) {
      return "";
    }
  }
  return "";
}

/** Initiate payload — Python zurvapay_initiate ke fields, bilkul waisi hi. */
function buildZurvapayPayload({ identifier, amountPkr, details, customerName, customerEmail }) {
  const ipn = zurvapayIpnUrl();
  const base = zurvapayPublicBase();
  return {
    public_key: ZURVAPAY_PUBLIC_KEY,
    bot_id: ZURVAPAY_BOT_ID,
    identifier,
    currency: "PKR",
    amount: parseInt(amountPkr, 10),
    details,
    ipn_url: ipn,
    success_url: `${base}/zurvapay/success`,
    cancel_url: `${base}/zurvapay/cancel`,
    customer_name: customerName || "RetailShop Customer",
    customer_email: customerEmail || "customer@retailshop.local",
  };
}

async function zurvapayInitiate(identifier, amountPkr, details, customerName, customerEmail) {
  if (!ZURVAPAY_PUBLIC_KEY) return { url: null, error: "ZURVAPAY_PUBLIC_KEY set nahi hai" };
  const ipn = zurvapayIpnUrl();
  if (!ipn) return { url: null, error: "IPN URL nahi bani — PUBLIC_BASE_URL ya ZURVAPAY_IPN_URL set karein" };
  const payload = buildZurvapayPayload({ identifier, amountPkr, details, customerName, customerEmail });
  let res;
  try {
    res = await fetch(`${ZURVAPAY_BASE_URL}/payment/initiate`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(payload),
      signal: AbortSignal.timeout(20000),
    });
  } catch (e) {
    return { url: null, error: `Zurvapay se rabta nahi ho saka: ${e.message}` };
  }
  let d;
  try {
    d = await res.json();
  } catch (e) {
    return { url: null, error: `Zurvapay ka jawab samajh nahi aaya (HTTP ${res.status})` };
  }
  if (d && d.success === "ok" && d.url) return { url: d.url, error: null };
  const err = d ? d.message || d.error || JSON.stringify(d) : "unknown";
  return { url: null, error: `Zurvapay error: ${err}` };
}

// ---------- USDT verify (Python verify_usdt jaisa — Tronscan, no key) ----------

async function verifyUsdt(txHash, expectedAddress, expectedAmount) {
  try {
    const res = await fetch(
      `https://apilist.tronscanapi.com/api/transaction-info?hash=${encodeURIComponent(txHash)}`,
      { signal: AbortSignal.timeout(15000) }
    );
    const d = await res.json();
    if (!d || d.contractRet !== "SUCCESS") {
      return { ok: false, reason: "TX success nahi hai ya hash galat hai." };
    }
    const transfers = d.tokenTransferInfo || {};
    let items = [];
    if (Array.isArray(transfers)) items = transfers;
    else if (transfers && typeof transfers === "object") {
      if ("to_address" in transfers) items = [transfers];
      else items = Object.values(transfers);
    }
    for (const t of items) {
      if (!t || typeof t !== "object") continue;
      const toAddr = t.to_address || "";
      const raw = t.amount_str || t.amount || 0;
      let amt;
      try {
        amt = parseInt(raw, 10) / 1000000; // USDT 6 decimals
      } catch (e) {
        continue;
      }
      if (Number.isNaN(amt)) continue;
      if (toAddr === expectedAddress && Math.abs(amt - expectedAmount) < 0.01) {
        return { ok: true, reason: `Verified: ${amt} USDT to ${toAddr}` };
      }
      if (toAddr === expectedAddress) {
        return { ok: false, reason: `Amount mismatch: bheje gaye ${amt} USDT, chahiye ${expectedAmount} USDT.` };
      }
    }
    return { ok: false, reason: "Is TX me aapke USDT address par payment nahi mili. Hash dobara check karein." };
  } catch (e) {
    return { ok: false, reason: `Verify API error: ${e.message}. Admin manually check karega.` };
  }
}

// ---------- Outbox polling (channel='whatsapp' rows — Python IPN/admin ke messages) ----------

async function pollOutboxOnce(sendFn) {
  const db = getDb();
  const rows = db
    .prepare("SELECT * FROM outbox WHERE channel='whatsapp' AND status='pending' ORDER BY id LIMIT 20")
    .all();
  let sent = 0;
  let failed = 0;
  for (const row of rows) {
    // Claim: sirf tab jab abhi bhi pending ho (do pollers double-send na karein).
    const claimed = db
      .prepare("UPDATE outbox SET status='sending' WHERE id=? AND status='pending'")
      .run(row.id);
    if (claimed.changes === 0) continue;
    let ok = false;
    try {
      await sendFn(row.recipient, row.text);
      ok = true;
    } catch (e) {
      log(`Outbox row ${row.id} send fail:`, e.message);
    }
    if (ok) {
      db.prepare("UPDATE outbox SET status='sent', sent_at=? WHERE id=?")
        .run(fmtDateTime(new Date()), row.id);
      sent++;
    } else {
      db.prepare("UPDATE outbox SET status='failed' WHERE id=?").run(row.id);
      failed++;
    }
  }
  return { sent, failed };
}

let outboxTimer = null;
function startOutboxPoller(sendFn) {
  if (outboxTimer) return;
  outboxTimer = setInterval(async () => {
    try {
      await pollOutboxOnce(sendFn);
    } catch (e) {
      log("Outbox poll error:", e.message);
    }
  }, 5000);
}

// ---------- Buyer flow (per-user state, memory me — restart pe flow drop hota hai) ----------

// flow = { step, page, pid, qty, amountPkr, amountUsdt, method }
const flows = new Map();

function extractText(msg) {
  const m = msg.message || {};
  if (m.conversation) return m.conversation;
  if (m.extendedTextMessage && m.extendedTextMessage.text) return m.extendedTextMessage.text;
  if (m.imageMessage && m.imageMessage.caption) return m.imageMessage.caption;
  return "";
}

function isImageMessage(msg) {
  return !!(msg.message && msg.message.imageMessage);
}

async function saveScreenshot(sock, msg, phoneInt) {
  const { downloadMediaMessage } = require("@whiskeysockets/baileys");
  const buf = await downloadMediaMessage(msg, "buffer");
  fs.mkdirSync(MEDIA_DIR, { recursive: true });
  const mimetype = (msg.message.imageMessage && msg.message.imageMessage.mimetype) || "image/jpeg";
  const ext = mimetype.includes("png") ? "png" : "jpg";
  const name = `wa-${phoneInt}-${Date.now()}.${ext}`;
  fs.writeFileSync(path.join(MEDIA_DIR, name), buf);
  return name;
}

async function handleUserMessage(jid, phoneInt, text, imageMsg, sendFn, sock) {
  const low = (text || "").trim().toLowerCase();

  // Global resets — kahin bhi 'hi' / 'menu' / 'cancel' likho to main menu.
  if (["hi", "hello", "salam", "assalam-o-alaikum", "menu", "cancel"].includes(low)) {
    flows.delete(jid);
    await sendFn(jid, mainMenuText());
    return;
  }

  let flow = flows.get(jid);
  if (!flow) {
    flow = { step: "menu" };
    flows.set(jid, flow);
  }

  const products = () => listActiveProducts();

  if (flow.step === "menu") {
    if (low === "1") {
      flow.step = "browse";
      flow.page = 1;
      const { text: t } = formatBrowse(products(), 1);
      await sendFn(jid, t);
    } else if (low === "2") {
      await sendFn(jid, myOrdersText(phoneInt));
    } else if (low === "3") {
      flow.step = "support";
      await sendFn(jid, "Support ko apna message likho — admin jald jawab dega.\n\nWapas jaane ke liye 'menu' likho.");
    } else if (low === "4") {
      await sendFn(jid, channelsText());
    } else {
      await sendFn(jid, mainMenuText());
    }
    return;
  }

  if (flow.step === "browse") {
    const list = products();
    if (low === "0") {
      flow.step = "menu";
      await sendFn(jid, mainMenuText());
      return;
    }
    if (low === "n") {
      flow.page = (flow.page || 1) + 1;
      const { text: t } = formatBrowse(list, flow.page);
      await sendFn(jid, t);
      return;
    }
    if (low === "p") {
      flow.page = (flow.page || 1) - 1;
      const { text: t } = formatBrowse(list, flow.page);
      await sendFn(jid, t);
      return;
    }
    const n = parseInt(low, 10);
    if (!Number.isNaN(n) && n >= 1 && n <= list.length) {
      const product = list[n - 1];
      flow.step = "detail";
      flow.pid = product.id;
      await sendFn(jid, formatProductDetail(product));
      return;
    }
    const { text: t } = formatBrowse(list, flow.page || 1);
    await sendFn(jid, t);
    return;
  }

  if (flow.step === "detail") {
    const product = getProduct(flow.pid);
    if (!product) {
      flows.delete(jid);
      await sendFn(jid, mainMenuText());
      return;
    }
    if (low === "0") {
      flow.step = "browse";
      const { text: t } = formatBrowse(products(), flow.page || 1);
      await sendFn(jid, t);
      return;
    }
    if (low === "1") {
      if (!product.unlimited && stockCount(product.id) <= 0) {
        await sendFn(jid, "Sorry, ye product abhi Out of Stock hai.\n\nMenu ke liye 'menu' likho.");
        return;
      }
      flow.step = "qty";
      await sendFn(jid, `Quantity kitni chahiye? 1 se 5 tak number bhejo.\n\nWapas jaane ke liye 0 likho.`);
      return;
    }
    await sendFn(jid, formatProductDetail(product));
    return;
  }

  if (flow.step === "qty") {
    const product = getProduct(flow.pid);
    if (!product) {
      flows.delete(jid);
      await sendFn(jid, mainMenuText());
      return;
    }
    if (low === "0") {
      flow.step = "detail";
      await sendFn(jid, formatProductDetail(product));
      return;
    }
    const q = parseInt(low, 10);
    if (!Number.isNaN(q) && q >= 1 && q <= 5) {
      if (!product.unlimited && stockCount(product.id) < q) {
        await sendFn(jid, `Sirf ${stockCount(product.id)} stock bacha hai. Kam quantity likho (1-${stockCount(product.id)}).`);
        return;
      }
      flow.qty = q;
      flow.amountPkr = unitPricePkr(product, q) * q;
      flow.amountUsdt = usdtAmount(product, q);
      flow.step = "pay";
      await sendFn(jid, formatPayMenu(product, q, flow.amountPkr, flow.amountUsdt));
      return;
    }
    await sendFn(jid, "Quantity 1 se 5 tak number me bhejo.\n\nWapas jaane ke liye 0 likho.");
    return;
  }

  if (flow.step === "pay") {
    const product = getProduct(flow.pid);
    if (!product) {
      flows.delete(jid);
      await sendFn(jid, mainMenuText());
      return;
    }
    if (low === "0") {
      flow.step = "qty";
      await sendFn(jid, `Quantity kitni chahiye? 1 se 5 tak number bhejo.\n\nWapas jaane ke liye 0 likho.`);
      return;
    }
    if (low === "1") {
      // Zurvapay auto — order banao, initiate karo, Pay Now link bhejo.
      // Payment IPN se verify hoke Python deliver_order se deliver hoga
      // (outbox -> ye bot bhejega). Is bot ko IPN kabhi nahi aata.
      const order = createOrder({
        userId: phoneInt,
        productId: product.id,
        paymentMethod: "zurvapay",
        amountText: `Rs ${flow.amountPkr}`,
        proof: "",
        qty: flow.qty,
        amountPkr: flow.amountPkr,
      });
      const { url, error } = await zurvapayInitiate(
        order.code,
        flow.amountPkr,
        `${product.name} x${flow.qty} — RetailShop WhatsApp`,
        String(phoneInt),
        ""
      );
      flows.delete(jid);
      if (error) {
        await sendFn(jid, `Payment link nahi ban saka: ${error}\n\nOrder ${order.code} pending hai — Support se rabta karein.`);
        return;
      }
      await sendFn(jid,
        `Payment Link (Order ${order.code})\n` +
        `Amount: Rs ${flow.amountPkr}\n\n` +
        `Pay Now: ${url}\n\n` +
        `JazzCash / Easypaisa / Raast se pay karein — payment ke baad account automatic deliver ho jayega.`
      );
      return;
    }
    if (low === "2" || low === "3") {
      flow.method = low === "2" ? "jazzcash" : "easypaisa";
      flow.step = "await_tid";
      await sendFn(jid, formatManualPay(flow.method, flow.amountPkr));
      return;
    }
    if (low === "4") {
      flow.step = "await_tx";
      await sendFn(jid, formatUsdtPay(flow.amountUsdt));
      return;
    }
    await sendFn(jid, formatPayMenu(product, flow.qty, flow.amountPkr, flow.amountUsdt));
    return;
  }

  if (flow.step === "await_tid") {
    const product = getProduct(flow.pid);
    if (!product) {
      flows.delete(jid);
      await sendFn(jid, mainMenuText());
      return;
    }
    let proof = null;
    if (imageMsg) {
      try {
        const file = await saveScreenshot(sock, imageMsg, phoneInt);
        proof = `screenshot:${file}`;
      } catch (e) {
        await sendFn(jid, `Screenshot save nahi ho saka: ${e.message}\n\nTID text me bhejo ya dobara screenshot bhejo.`);
        return;
      }
    } else if (text) {
      proof = text.trim();
    } else {
      await sendFn(jid, "TID (transaction ID) bhejo ya payment ka screenshot bhejo.\n\nWapas jaane ke liye 'cancel' likho.");
      return;
    }
    const order = recordManualOrder({
      phoneInt,
      productId: product.id,
      qty: flow.qty,
      method: flow.method,
      proof,
      amountPkr: flow.amountPkr,
    });
    flows.delete(jid);
    await sendFn(jid,
      `Shukriya! Aapki payment admin ko bhej di gayi.\n` +
      `Order: ${order.code} (Order #${order.id})\n` +
      `Product: ${product.name} x${flow.qty}\n` +
      `Amount: Rs ${flow.amountPkr}\n\n` +
      `Verify hote hi account yahin deliver ho jayega.`
    );
    return;
  }

  if (flow.step === "await_tx") {
    const product = getProduct(flow.pid);
    if (!product) {
      flows.delete(jid);
      await sendFn(jid, mainMenuText());
      return;
    }
    const hash = (text || "").trim();
    if (!hash || hash.length < 10) {
      await sendFn(jid, "TX hash sahi nahi lag raha — poora transaction hash bhejo.\n\nWapas jaane ke liye 'cancel' likho.");
      return;
    }
    const usdtAddress = getSetting("usdt_address");
    const { ok, reason } = await verifyUsdt(hash, usdtAddress, flow.amountUsdt);
    if (!ok) {
      await sendFn(jid, `${reason}\n\nHash dobara bhejo ya 'cancel' likho.`);
      return;
    }
    // Verified -> order banao + foran deliver karo (JS deliverOrder).
    const order = createOrder({
      userId: phoneInt,
      productId: product.id,
      paymentMethod: "usdt",
      amountText: `$${flow.amountUsdt} USDT`,
      proof: hash,
      qty: flow.qty,
      amountPkr: flow.amountPkr,
    });
    flows.delete(jid);
    const result = await deliverOrder(order.id, sendFn);
    if (!result.ok) {
      // deliverOrder ne buyer ko out-of-stock message bhej diya hai.
      log(`USDT order #${order.id} deliver fail: ${result.reason}`);
    }
    return;
  }

  if (flow.step === "support") {
    if (text) {
      notifyAdminSupport(phoneInt, text.trim());
      flows.delete(jid);
      await sendFn(jid, "Aapka message admin ko bhej diya gaya hai. Jald jawab milega.\n\nMenu ke liye 'menu' likho.");
      return;
    }
    await sendFn(jid, "Support ko apna message likho — admin jald jawab dega.\n\nWapas jaane ke liye 'menu' likho.");
    return;
  }

  // Unknown step — reset.
  flows.delete(jid);
  await sendFn(jid, mainMenuText());
}

// ---------- Baileys socket ----------

async function sendText(sock, jid, text) {
  // WhatsApp message limit ~4096 chars — lambi delivery ko chunks me bhejo.
  let rest = String(text || "");
  const chunks = [];
  while (rest.length > 4000) {
    chunks.push(rest.slice(0, 4000));
    rest = rest.slice(4000);
  }
  chunks.push(rest);
  for (const c of chunks) {
    if (c) await sock.sendMessage(jid, { text: c });
  }
}

function disconnectStatusCode(lastDisconnect) {
  try {
    return lastDisconnect && lastDisconnect.error && lastDisconnect.error.output
      ? lastDisconnect.error.output.statusCode
      : null;
  } catch (e) {
    return null;
  }
}

async function startSock() {
  const baileys = require("@whiskeysockets/baileys");
  const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = baileys;
  const pino = require("pino");
  const QRCode = require("qrcode-terminal");

  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  let version;
  try {
    ({ version } = await fetchLatestBaileysVersion());
  } catch (e) {
    log("Baileys version check fail (default use hogi):", e.message);
  }

  const sock = makeWASocket({
    version,
    auth: state,
    logger: pino({ level: "silent" }),
    printQRInTerminal: false,
    browser: ["RetailShop WA Bot", "Chrome", "1.0"],
    // Connection jaldi toot jata hai — keep-alive zyada baar bhejo.
    keepAliveIntervalMs: 10000,
  });
  sock.ev.on("creds.update", saveCreds);

  // Pehli pairing: auth nahi hai to pairing code (ya QR fallback).
  if (!state.creds.registered) {
    if (WA_SKIP_PAIRING_CODE) {
      log("QR mode (WA_SKIP_PAIRING_CODE=1): pairing code request nahi kar raha — QR scan se pair karein. Socket kitni der tikta hai, yehi test hai.");
    } else if (WA_BOT_NUMBER) {
      try {
        const code = await sock.requestPairingCode(WA_BOT_NUMBER);
        fs.writeFileSync(PAIRING_FILE, String(code), "utf8");
        log(`Pairing code (${WA_BOT_NUMBER}): ${code}`);
        log(`Code pairing-code.txt me bhi likh diya. WhatsApp > Linked Devices > "Link with phone number" me ye code daalein.`);
      } catch (e) {
        log("Pairing code nahi mila:", e.message, "— QR se pair karein (neeche QR aayega).");
      }
    } else {
      log("WA_BOT_NUMBER set nahi hai — pairing ke liye env me WA_BOT_NUMBER (digits only, spare number) set karke restart karein. Bot crash nahi karega; QR se pair karne ke liye neeche QR bhi dikhaya jayega.");
    }
  }

  sock.ev.on("connection.update", (update) => {
    const { connection, lastDisconnect, qr } = update;
    if (qr) {
      log("QR code — WhatsApp > Linked Devices se scan karein:");
      QRCode.generate(qr, { small: true });
    }
    if (connection === "close") {
      const code = disconnectStatusCode(lastDisconnect);
      const isPaired = !!state.creds.registered;
      // Unpaired socket: jaldi restart karne se WhatsApp rate-limit karta
      // hai aur code har 5s rotate hota hai. Is liye unpaired ho to 30s
      // ruko; paired ho to 5s me dobara connect karo (fast recovery).
      const delay = isPaired ? 5000 : 30000;
      if (code === 401 || code === DisconnectReason.loggedOut) {
        if (isPaired) {
          log("Logged out (401) — auth delete karke dobara pairing hogi.");
          try {
            fs.rmSync(AUTH_DIR, { recursive: true, force: true });
          } catch (e) { /* ignore */ }
        } else {
          log(`Logged out (401) jabke abhi pair nahi hua — auth delete nahi kar raha, ${delay / 1000}s me dobara try hoga (rate-limit se bachne ke liye).`);
        }
        setTimeout(() => startSock().catch((e) => log("Restart fail:", e.message)), delay);
      } else {
        log(`Connection band (code ${code}) — ${delay / 1000}s me dobara connect ho raha hai...`);
        setTimeout(() => startSock().catch((e) => log("Restart fail:", e.message)), delay);
      }
    } else if (connection === "open") {
      log("WhatsApp connected.");
    }
  });

  const sendFn = async (jid, text) => sendText(sock, jid, text);

  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify") return;
    for (const msg of messages) {
      try {
        if (!msg || !msg.key) continue;
        if (msg.key.fromMe) continue; // apne messages ignore
        const jid = msg.key.remoteJid || "";
        if (!jid.endsWith("@s.whatsapp.net")) continue; // groups + status broadcast ignore
        const phoneInt = parseInt(jidToPhone(jid), 10);
        if (!phoneInt) continue;
        const text = extractText(msg).trim();
        const imageMsg = isImageMessage(msg) ? msg : null;
        if (!text && !imageMsg) continue;
        await handleUserMessage(jid, phoneInt, text, imageMsg, sendFn, sock);
      } catch (e) {
        log("Message handle error:", e.message);
      }
    }
  });

  startOutboxPoller(sendFn);
  return sock;
}

// ---------- Main ----------

async function main() {
  fs.mkdirSync(AUTH_DIR, { recursive: true });
  fs.mkdirSync(MEDIA_DIR, { recursive: true });
  const db = getDb();
  const has = db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='products'").get();
  if (!has) {
    log(`ERROR: ${dbPath} me products table nahi mili — pehle Telegram bot (python bot.py) chalao taake init_db tables banaye.`);
  } else {
    log(`DB ready: ${dbPath}`);
  }
  if (!WA_BOT_NUMBER) {
    log("WA_BOT_NUMBER set nahi hai — pairing ke liye env me WA_BOT_NUMBER (digits only, spare number) set karke restart karein. Bot crash nahi karega, wait karega.");
  }
  await startSock();
}

// Import karne pe socket start NAHI hota (tests ke liye) — sirf direct run pe.
if (require.main === module) {
  main().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}

module.exports = {
  // config (tests ke liye read-only snapshot)
  getConfig: () => ({ dbPath, ADMIN_ID, ZURVAPAY_PUBLIC_KEY, ZURVAPAY_BASE_URL, ZURVAPAY_BOT_ID, PUBLIC_BASE_URL, WA_BOT_NUMBER }),
  setDbPath,
  getDb,
  closeDb,
  getSetting,
  listActiveProducts,
  getProduct,
  stockCount,
  queueOutbox,
  fmtDateTime,
  fmtDatePk,
  genOrderCode,
  jidToPhone,
  phoneToJid,
  parseBulk,
  unitPricePkr,
  usdtAmount,
  mainMenuText,
  formatBrowse,
  formatProductDetail,
  formatPayMenu,
  formatManualPay,
  formatUsdtPay,
  myOrdersText,
  channelsText,
  buildDeliveryText,
  createOrder,
  recordManualOrder,
  notifyAdminManualOrder,
  notifyAdminSupport,
  deliverOrder,
  zurvapayIpnUrl,
  zurvapayPublicBase,
  buildZurvapayPayload,
  zurvapayInitiate,
  verifyUsdt,
  pollOutboxOnce,
  startOutboxPoller,
  handleUserMessage,
  flows,
};
