# RetailShop — WhatsApp Bot (Baileys)

Telegram bot (`../bot.py`) ka doosra storefront. Dono ek hi SQLite DB
(`shop.db`) share karte hain — products, stock, orders, settings, outbox
sab ek hi jagah. Contract: `../WA_CONTRACT.md`

## Bot ka number

**+92 371 6248783** (spare SIM, sirf bot ke liye — kabhi main personal
number par bot mat chalao, ban risk hai).

Buyer isi number par WhatsApp karega. Number code me hardcode NAHI hai —
env var `WA_BOT_NUMBER=923716248783` se aata hai.

## Run

```bash
cd wa-bot
npm install          # pehli baar (node_modules missing ho to start.sh bhi yehi karta hai)
node wa-bot.js
```

Botkeep par `start.sh` dono processes chalata hai (`python bot.py` +
`node wa-bot/wa-bot.js`).

## Env vars

Telegram bot wale hi naam (Botkeep env me ek hi set):

| Var | Matlab |
|---|---|
| `WA_BOT_NUMBER` | Bot ka spare number, digits only: `923716248783` (pehli pairing ke liye) |
| `ADMIN_ID` | Telegram admin user id (manual orders / support notify isi ko jata hai) |
| `DB_PATH` | Default: repo root me `shop.db` |
| `ZURVAPAY_PUBLIC_KEY` | Zurvapay initiate ke liye |
| `ZURVAPAY_BASE_URL` | Default `https://zurvapay.com` |
| `ZURVAPAY_BOT_ID` | Default `Shop_at_Retail_bot` |
| `PUBLIC_BASE_URL` | IPN/success/cancel URLs ke liye, e.g. `http://node19.botkeep.cloud:61495` |
| `ZURVAPAY_IPN_URL` | Override (optional) |

Payment details (JazzCash/Easypaisa number+name, USDT TRC20 address,
channel links) env se NAHI — `settings` table se aate hain, bilkul
Telegram bot ki tarah. Admin Telegram `/set` se badalta hai.

`WA_BOT_NUMBER` unset ho to bot crash NAHI karta — clear message log
karke wait karta hai (QR bhi dikhata hai, us se bhi pair ho sakta hai).

## Pairing (pehli baar)

1. Botkeep env me `WA_BOT_NUMBER=923716248783` set karein, service restart karein.
2. Logs me 8-digit **pairing code** aayega — wo `wa-bot/pairing-code.txt`
   me bhi likha jata hai.
3. Spare phone (**+92 371 6248783** wali SIM) par WhatsApp kholein:
   **Settings → Linked Devices → Link a Device → "Link with phone number
   instead"** → code daalein.
4. Pairing code fail ho to terminal me **QR** aata hai — usay bhi scan kar
   sakte hain.
5. Pair hone ke baad auth `wa-bot/auth/` me save rehti hai — dobara
   pairing nahi chahiye. `auth/` kabhi delete mat karein, kabhi commit
   mat karein (`.gitignore` me hai).
6. 401 logged-out ho to bot khud auth delete karke dobara pairing maangta hai.

## Buyer flow (MVP)

Numbered TEXT menus (koi buttons nahi — Baileys par sab se reliable):

```
hi / menu
1. Browse Products (10/page, N/P se page badlo)
   -> product number -> detail (description + How to Use + price + stock)
   -> 1 (Buy) -> quantity 1-5 -> payment method
2. My Orders
3. Support (message admin ko Telegram pe jata hai)
4. Our Channels
```

Payment:

1. **Zurvapay (Auto)** — order banta hai, Pay Now link milta hai.
   Payment IPN se verify hoke **Python bot** deliver karta hai
   (outbox → ye bot WhatsApp pe bhejta hai). Is bot ko IPN kabhi nahi aata.
2. **JazzCash (Manual)** — number+name+amount dikhta hai; buyer TID
   (text) ya screenshot bhejta hai → order pending + admin ko Telegram
   notify (outbox) → admin Telegram `/orders` se Approve karta hai.
3. **Easypaisa (Manual)** — same, Easypaisa details ke sath.
4. **USDT TRC20 (Auto Verify)** — address + USDT amount; buyer TX hash
   bhejta hai → Tronscan se verify (Python `verify_usdt` jaisa logic) →
   ok ho to **ye bot khud** `deliverOrder` se account deliver karta hai.

Screenshots `wa-bot/media/` me save hote hain.

## Outbox (message bus)

- `channel='whatsapp'` pending rows: ye bot har 5s poll karta hai,
  claim karke (`status='sending'`) bhejta hai, `sent`/`failed` mark karta hai.
  Isi se Zurvapay-IPN deliveries aur admin-Approved deliveries buyer tak
  pohanchti hain.
- `channel='telegram'` rows: ye bot likhta hai (manual orders, support),
  Python bot ka poller Telegram pe bhejta hai.

## MVP me NAHI hai

- WhatsApp par admin commands (koi `/addstock` waghera nahi — admin
  Telegram bot se manage karta hai).
- Coupons, wallet, cart, referrals, reviews, giveaways.
- Payment ka koi doosra tareeqa (sirf upar wale 4).

## Tests

```bash
node test.js
```

Temp DB ke against chalta hai (Python `init_db` se banaya gaya) —
**kabhi live `shop.db` par test mat karo.**
