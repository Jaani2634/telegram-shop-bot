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
