# Telegram Shop Bot — Accounts Seller

Manual (JazzCash / Easypaisa) + USDT (TRC20) dono. Payment verify ho, account auto deliver ho buyer ki chat me.

## Kya karta hai

**Buyer:**
- `/start` → channel gate (Telegram channel join check + WhatsApp trust button) → main menu: Products, Deposit, My Orders, Giveaways, Wallet, Join Us, Our Channels, Change Payment Method, Help, Support
- **Products:** promo banner, categories (Muse Ai, Capcut Pro, Claude Api, Gemini) + standalone products, pagination 10/page, stock counts (♾️ sirf admin-set unlimited products pe), Search + Request Products
- **Product detail:** description, bulk discounts, average rating, Buy Now / Add to Cart / Reviews
- **Buy flow:** quantity (1/2/3/5/10 + custom, bulk discount ke saath) → Terms & Conditions → Order Summary (RS-XXXXXX code, coupon, wallet balance) → payment choose:
  - **JazzCash / Easypaisa:** number dikhega, TID ya screenshot bhejo → admin approve → bot khud ek unused account assign karega aur `login + password` buyer ko bhej dega
  - **USDT:** TRC20 address + amount, TX hash bhejo → bot Tronscan API se verify karega → auto deliver. Verify fail ho to order admin ko pending me chala jata hai.
  - **⚡ Zurvapay (JazzCash / Easypaisa / Raast QR):** hosted checkout link milta hai, wahan wallet se approve karo → Zurvapay signed IPN bhejta hai → bot khud verify karke auto deliver karta hai. Deposit bhi isi se auto-credit hota hai. (Env me keys + public URL chahiye — neeche dekho.)
  - **Pay from Wallet:** wallet balance kaafi ho to seedha deduct + deliver
- **Cart:** Add to Cart, View Cart, Checkout (ek order, multiple lines), Remove/Clear
- **First-order coupon:** pehli `/start` pe `FIRST-ORDER` — 7% off, sirf pehli order se pehle, checkout pe Apply Coupon Code
- **Currency:** PKR (default) ya USDT — Change Payment Method se toggle; prices usi currency me dikhte hain (rate 280 PKR/USDT)
- **Wallet:** PKR + USDT balances, Deposit (min 1, manual proof → admin approve → credit; USDT auto-verify → instant credit), Redeem Top-up Code, Wallet Statement + Download (.txt)
- **My Orders:** status (PAID / DELIVERED / CANCELLED), detail, Buy Again, Write Review (sirf delivered orders)
- **Giveaways:** apna invite link (`?start=ref_<id>`), referrals count, 3 giveaways (Muse 1B Credits, Make.com 3 Months, Canva Pro) — Telegram channel membership verify button
- **Mail-type delivery:** Muse token products pe buyer ko `email + mailbox URL` milta hai (Payment Verified message) — khud mailbox site se OTP le ke muse.ai pe login kare

**Admin (sirf tum):**
- `/admin` — panel, pending orders + deposits count, stock
- `/addproduct` — naam → description → Rs price → USDT price
- `/addstock` — product ID → accounts lines me:
  ```
  test@gmail.com | Pass123
  user2 | Abc456
  ```
  Mail-type products ke liye: `code | email | mailbox_url`
- `/orders` — pending orders, ✅ Approve / ❌ Reject buttons
- `/stock` — saara stock
- `/setpayments` — current payment details
- `/set jazzcash_number 03001234567` — koi bhi payment detail change karo
- `/setdesc <id> <text>` — product description
- `/setterms <id> <text>` — terms & conditions
- `/setbulk <id> <tiers>` — bulk discount, jaise `3:360,5:345` (PKR unit price); clear = `clear`
- `/setunlimited <id> <on|off>` — ♾️ stock display (sirf display — delivery ke liye phir bhi stock chahiye)
- `/setkind <id> <mail|account>` — delivery type
- `/addreview <id> <name> <rating 1-5> <text>` — shop ki taraf se review (label "Added by shop")
- `/topupcode <CODE> <amount> <currency>` — wallet top-up code banao
- `/reply <user_id> <text>` — support message ka jawab

Har account sirf ek dafa sell hota hai. Sold hote hi `sold` mark ho jata hai, dobara kisi ko nahi milta.

## Setup — 5 minute

1. Telegram me `@BotFather` → `/newbot` → naam + username → token milega
2. `@userinfobot` → `/start` → tumhari numeric ID milegi (ADMIN_ID)
3. Is folder me:
   ```bash
   cp .env.example .env
   # .env kholo, BOT_TOKEN aur ADMIN_ID bharo, payment numbers bharo
   pip install -r requirements.txt
   python bot.py
   ```
4. Bot ko Telegram me `/start` karo. Admin se `/addproduct` phir `/addstock` karo. Test order khud dusri ID se karo.

## Zurvapay auto-verify kaise kaam karta hai

Zurvapay (zurvapay.com) JazzCash / Easypaisa / Raast QR ke liye hosted checkout + signed IPN deta hai. Flow:

1. Buyer Order Summary pe **⚡ Zurvapay** dabata hai → bot order banata hai (identifier = RS-XXXXXX code) aur `POST https://zurvapay.com/payment/initiate` call karta hai (public_key, identifier, currency PKR, amount, details, ipn_url, success_url, cancel_url, customer_name, customer_email) → checkout URL milta hai, buyer wahan wallet se approve karta hai.
2. Payment confirm hote hi Zurvapay bot ke health server pe `POST /zurvapay/ipn` bhejta hai (8 dafa retry agar 200 na mile).
3. Bot IPN signature verify karta hai (secret key se HMAC), order code + amount match karta hai, phir **wahi `deliver_order` chalata hai** jo manual-approve aur USDT flows use karte hain — mail-type products pe mail delivery bhi waisi hi hoti hai. Duplicate IPN dobara deliver nahi karta (atomic claim). PKR deposit bhi isi IPN se auto-credit hota hai (identifier `DEP-<id>`).

**Env vars (Botkeep/Render Environment tab me dalo):**

```bash
ZURVAPAY_PUBLIC_KEY=zp_live_...        # Zurvapay dashboard se
ZURVAPAY_SECRET_KEY=...                # Zurvapay dashboard se (kabhi code/chat me nahi)
PUBLIC_BASE_URL=https://<botkeep-host>:<port>   # bot ki public URL, jaise https://node19.botkeep.cloud:61495
# Optional:
ZURVAPAY_IPN_URL=https://<host>:<port>/zurvapay/ipn   # PUBLIC_BASE_URL se banta hai, override ke liye
ZURVAPAY_BASE_URL=https://zurvapay.com                 # default
ZURVAPAY_IPN_MODE=auto                                # signature scheme, neeche dekho
```

Khaali keys = Zurvapay option dikhega hi nahi, baaki flows (manual, USDT, wallet) waisi hi chalengi.

**⚠️ IPN signature — zaroori note:** Zurvapay ki public site pe IPN payload/signature ka exact format nahi hai — full API docs merchant dashboard ke peeche hain. `ZURVAPAY_IPN_MODE=auto` (default) tamam common HMAC schemes try karta hai (HMAC-SHA256/512 over `identifier|amount`, raw body, ya sorted params — sab secret se keyed, is liye baghair secret ke forge nahi ho sakta). Live keys milte hi dashboard docs se sahi scheme check karo aur `ZURVAPAY_IPN_MODE` se pin kar do. Agar koi scheme match na kare to bot IPN reject karke admin ko Telegram pe fields bhej deta hai (taake scheme update ki ja sake) — baghair signature ke koi delivery nahi hoti. Status-check API ka endpoint bhi public docs me nahi hai; `ZURVAPAY_STATUS_URL` set ho to hi use hoti hai.

## Website se direct purchase + AHM7 reseller flow

RetailShop AHM7 ka reseller hai — website (Cloudflare Pages) se direct khareedari. **SAME-CHANNEL delivery:** website se khareeda → delivery website pe hoti hai (order page); Telegram se khareeda → delivery Telegram pe (unchanged).

1. Customer site pe product chunta hai → site Cloudflare Worker `retailshop-api` ko `POST /api/checkout {product_id, contact?}` bhejti hai → Worker bot server ko `POST /api/web-checkout` forward karta hai (header `X-Web-Secret`). **Telegram @username ki zaroorat NAHI.** `contact` (email ya Telegram) optional hai — sirf manual follow-up ke liye.
2. Bot server order banata hai (channel=`web`, uid=0, amount = server-side site price — client ki bheji price pe bharosa nahi) + ek random **delivery token** (32 hex chars) issue karta hai, aur Zurvapay initiate karke `{checkout_url, order_code, delivery_token, order_url}` wapas bhejta hai. Zurvapay ki success/cancel URLs site ke order page pe point karte hain (`order.html?code=RS-...&token=...`). Customer wahan JazzCash / Easypaisa / Raast se pay karta hai. USDT site pe nahi — sirf bot se.
3. Payment confirm hote hi Zurvapay IPN bhejta hai → bot **apne stock se NAHI**, AHM7 se auto-buy karta hai (`POST https://api.ahm7.tech/v1/orders`, currency `pkr`, Idempotency-Key = RetailShop order code) aur AHM7 ki credentials order row me save karke status `delivered` kar deta hai. Commission = site price − AHM7 cost. **Credentials customer ko order page pe dikhte hain** — order page `GET /api/order/:code?token=...` (Worker proxy) ko poll karta hai.
4. **Order status endpoint:** `GET /api/order/:code?token=...` (Worker → bot server, X-Web-Secret ke saath). Order code + delivery token dono chahiye — ghalat/missing token, non-web order, ya missing secret → 404 `not found` (koi information leak nahi). Credentials sirf `delivered` status pe milte hain. Rate limit: 30 requests / 60s per IP (429 uske baad). Status values: `processing` (payment confirm ho rahi hai), `delivered` (+ credentials array), `manual` (paid-pending — payment mil gayi, manual process ho raha hai, order code save rakhein).
5. **Margin guard:** AHM7 cost >= site price ho to auto-buy NAHI hota — order `ahm7_pending` me chala jata hai (manual review). Mapping `ahm7-mapping.json` me hai (26 products, 21 mapped, 8 site prices pe profitable).
6. **Failure handling:** AHM7 wallet 0 ho (402 `insufficient_balance`), key na ho, ya koi aur error ho → order `ahm7_pending` me safe rehta hai, admin ko Telegram pe notify hota hai, order page pe "payment mil gayi, manual process ho raha hai" dikhta hai aur polling jaari rehti hai. Paid order kabhi lose nahi hota. Wallet top-up ke baad `POST /api/ahm7-retry {"order_code": "RS-XXXXXX"}` (X-Web-Secret ke saath) se dobara fulfill karo — dobara buy nahi hota agar pehle buy ho chuka ho (saved credentials order page pe available ho jate hain).

**Env vars (Botkeep .env me dalo):**

```bash
WEB_API_SECRET=<openssl rand -hex 32>   # Worker secret se SAME hona chahiye
AHM7_API_KEY=<ahm7 reseller key>        # api.ahm7.tech key
# Optional:
AHM7_BASE_URL=https://api.ahm7.tech/v1  # default
```

**Note:** Telegram bot ke andar ki orders (channel=`telegram`) abhi bhi purane `deliver_order` path se apne stock se deliver hoti hain. AHM7 fulfillment sirf web orders pe hai — Telegram orders ko AHM7 pe shift karna ho to `_zurva_handle_order_ipn` me channel check hatana hoga (wallet top-up ke baad).

## 24/7 chalane ke liye (free options)

Bot tab tak chalega jab tak `python bot.py` chal raha hai. Band kar diya to bot offline.
- **Render.com (free):** repo GitHub pe dalo → New Web Service → `pip install -r requirements.txt` + `python bot.py`, Environment Variables me BOT_TOKEN, ADMIN_ID dalo.
- **Railway / PythonAnywhere:** same tareeqa.
- **Apna laptop / VPS:** terminal khula rakhna hoga ya `nohup python bot.py &` / systemd.

## USDT verify kaise kaam karta hai

Bot Tronscan public API (`apilist.tronscanapi.com`) se TX hash check karta hai: TX SUCCESS hai, USDT TRC20 contract hai, `to_address` tumhara address hai, amount product price ke barabar hai (±$0.01). Koi API key nahi chahiye.

Note: JazzCash/Easypaisa ka Pakistan me koi official public auto-verify API nahi hai, is liye wo manual approve rakha hai — delivery phir bhi automatic hai.

## Files

- `bot.py` — poora bot
- `requirements.txt` — python-telegram-bot, requests, dotenv
- `.env.example` — config template
- `shop.db` — auto ban jayegi (products, accounts, orders)
