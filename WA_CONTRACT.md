# RetailShop — WhatsApp Bot Contract (Telegram bot <-> WhatsApp bot)

Two bots, ONE shop. The Python Telegram bot (`bot.py`) is the source of truth
for payments/IPN; the Node.js WhatsApp bot (`wa-bot/wa-bot.js`, Baileys) is a
second storefront. Both run in the SAME Botkeep service (same filesystem) and
share ONE SQLite database.

## Shared database — `shop.db` (cwd, `DB_PATH` env or `shop.db`)

- WAL mode + `busy_timeout=10000` on BOTH sides (Python `db()` sets it;
  Node better-sqlite3 must set `PRAGMA journal_mode=WAL` + `busy_timeout`).
- NEVER delete the DB, NEVER run two writers without WAL. All writes use
  short transactions.

## Tables the WA bot uses (read/write)

- `products`: id, name, description, price_pkr, price_usdt, active,
  kind ('account'|'mail'), category, bulk, terms, unlimited.
  Only read `active=1` products. `terms` = How-to-Use instructions.
- `accounts`: id, product_id, login, password, extra, status
  ('available'|'sold'), sold_to, sold_at, order_id.
  Stock = `status='available'`. Assign with
  `UPDATE accounts SET status='sold', sold_to=?, sold_at=?, order_id=?`.
- `orders`: id, user_id INTEGER, username, product_id, payment_method,
  amount_text, proof, status, account_id, created_at, code, items_json,
  qty, discount_pkr, coupon_code, amount_pkr, **channel**.
  - `channel`: `'telegram'` (default) or `'whatsapp'`.
  - `user_id`: Telegram user id, OR WhatsApp phone number as INTEGER
    (digits only, e.g. `923460777483`). The WA JID is
    `"<user_id>@s.whatsapp.net"`.
  - `items_json`: JSON `[{"pid": N, "qty": M}]`. `code`: `RS-XXXXXX`
    (gen: `RS-` + 6 random digits). `status`: pending/delivered/
    out_of_stock/cancelled.
- `settings`: key/value. Payment details live here (NOT env):
  `jazzcash_number`, `jazzcash_name`, `easypaisa_number`, `easypaisa_name`,
  `usdt_address`, `tg_channel`, `tg_group_url`, `wa_channel_url`,
  `support_username`. Read them with `SELECT value FROM settings WHERE key=?`.
- `outbox` (the message bus between the two processes):
  `id INTEGER PK, channel TEXT, recipient TEXT, text TEXT,
   status TEXT DEFAULT 'pending', created_at TEXT, sent_at TEXT`.
  - `channel='whatsapp'`: recipient is a WA JID (`<phone>@s.whatsapp.net`).
    The Node bot polls these, sends via Baileys, marks `sent` (+ sent_at)
    or `failed`.
  - `channel='telegram'`: recipient is a Telegram user id as text.
    The Python bot polls these (background thread) and sends via Telegram.
  - NEVER delete rows; only update `status`/`sent_at`. Poll with
    `SELECT ... WHERE status='pending' ORDER BY id LIMIT 20` and claim
    with `UPDATE outbox SET status='sending' WHERE id=? AND status='pending'`
    (so the two pollers never double-send).

## Delivery routing (who sends what)

- `deliver_order` (Python) is the ONLY place that marks accounts sold and
  orders delivered for Zurvapay IPN + Telegram admin Approve.
  - Telegram order: sends the Telegram-Markdown delivery message as now.
  - WhatsApp order: builds a PLAIN-TEXT delivery message
    (`wa_delivery_text`, no Markdown) and inserts ONE outbox row
    (channel='whatsapp', recipient=`<phone>@s.whatsapp.net`). The Node bot
    delivers it. `deliver_order` returns True once queued.
- The Node bot implements its own `deliverOrder(orderId)` in JS ONLY for
  the USDT-auto path (verify ok -> deliver immediately). It mirrors the
  Python logic: stock check -> assign accounts -> mark sold/delivered ->
  plain-text delivery message (with instructions) via Baileys.
- Out-of-stock: mark order `out_of_stock`, notify admin (Telegram, via
  outbox channel='telegram' to ADMIN_ID) and notify the buyer (Telegram
  send, or outbox channel='whatsapp' for WA buyers).

## Zurvapay (auto-verify) — same for both bots

- Initiate: `POST https://zurvapay.com/payment/initiate`, form-encoded:
  `public_key` (env ZURVAPAY_PUBLIC_KEY), `bot_id` (`Shop_at_Retail_bot`,
  env ZURVAPAY_BOT_ID), `identifier` (RS code), `currency=PKR`,
  `amount` (int PKR), `details`, `ipn_url`, `success_url`, `cancel_url`,
  `customer_name`, `customer_email`.
  - `ipn_url` = `{PUBLIC_BASE_URL}/zurvapay/ipn` (the PYTHON health
    server — the WA bot has no public URL of its own).
  - `success_url`/`cancel_url` = `{PUBLIC_BASE_URL}/zurvapay/success|/cancel`.
  - Success JSON: `{success: "ok", url: <checkout_url>}`.
- IPN is received + verified ONLY by the Python bot (signature scheme
  CONFIRMED 2026-11-13: HMAC-SHA256(secret, `status|identifier|amount`)
  with the raw IPN amount string, e.g. `success|RS-431970|20.00000000`).
  The Python IPN handler calls `deliver_order`, which routes WA orders
  to the outbox. The WA bot NEVER verifies IPN itself.
- Env (same Botkeep env for both processes): ADMIN_ID, ZURVAPAY_PUBLIC_KEY,
  PUBLIC_BASE_URL, DB_PATH. WA-only: WA_BOT_NUMBER (digits only, the spare
  number, e.g. `923001234567`) — used ONLY for first-time pairing.

## WhatsApp bot (Node + Baileys) — buyer flow (MVP)

- Numbered TEXT menus (no interactive buttons — most reliable on Baileys).
- Main menu: 1 Browse Products, 2 My Orders, 3 Support, 4 Our Channels.
- Browse: 10 products/page, `name — Rs price — In Stock (n)` / Out of Stock.
  Product detail shows description + How to Use (terms) + price + stock.
- Buy: quantity (1-5) -> payment method: 1 Zurvapay (Auto),
  2 JazzCash (manual), 3 Easypaisa (manual), 4 USDT TRC20 (auto verify).
- Zurvapay: create order (channel='whatsapp'), initiate, send Pay Now link.
- JazzCash/Easypaisa: show number+name+amount; buyer sends TID (text) or
  screenshot (image) -> create order (status pending, proof=TID or
  `screenshot:<file>`) -> admin notify via outbox (channel='telegram',
  recipient=ADMIN_ID). Admin approves in Telegram `/orders`.
- USDT: show TRC20 address + USDT amount; buyer sends TX hash -> verify via
  Tronscan `GET https://apilist.tronscanapi.com/api/transaction-info?hash=<hash>`
  (same logic as Python `verify_usdt`: contractRet SUCCESS, tokenTransferInfo
  to_address == usdt_address, amount within 0.01) -> ok: create order +
  JS `deliverOrder` immediately; fail: tell buyer the reason.
- My Orders: buyer's orders by `user_id` (phone int) with status.
- Support: buyer writes a message -> outbox (telegram, ADMIN_ID) + confirm.
- Our Channels: tg_channel / tg_group_url / wa_channel_url from settings.
- `hi`/`menu`/`cancel` reset to the main menu. Ignore group messages,
  status broadcasts, and own messages. Per-user flow state in memory
  (Map keyed by JID); a restart just drops the flow, user sends `hi` again.
- Admin commands on WhatsApp: NONE in MVP (admin manages via Telegram).

## Baileys notes

- `@whiskeysockets/baileys`, `useMultiFileAuthState("wa-bot/auth")`.
  Auth folder persists on disk (same service) — do NOT delete, do NOT commit.
- First run: `sock.requestPairingCode(WA_BOT_NUMBER)` (8-digit code).
  Print it to console AND write to `wa-bot/pairing-code.txt`.
  Fallback: QR via `qrcode-terminal` if pairing code fails.
- Reconnect on `connection.update` close (unless 401 logged-out ->
  delete auth + request a new pairing code).
- `sock.sendMessage(jid, {text})`. Receive via `messages.upsert`.
  Screenshots: `downloadMediaMessage` -> save to `wa-bot/media/`.

## Hosting — one Botkeep service, two processes

- `start.sh` (repo root) launches both: `python bot.py` and
  `node wa-bot/wa-bot.js`. Node auto-downloads (Node 22 LTS tarball) if
  `node` is missing; `npm install` in `wa-bot/` runs only if
  `node_modules` is missing. Both processes log to stdout.
- Botkeep start command must be `bash start.sh` (set in dashboard).
- NEVER run the WA bot on the user's main personal number. Ban risk is
  real and accepted by the user (2026-11-13) — spare number only.
