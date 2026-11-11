# Telegram Shop Bot — Accounts Seller

Manual (JazzCash / Easypaisa) + USDT (TRC20) dono. Payment verify ho, account auto deliver ho buyer ki chat me.

## Kya karta hai

**Buyer:**
- `/start` → products list (naam, Rs price / $ USDT, stock)
- Buy → payment choose:
  - **JazzCash / Easypaisa:** number dikhega, TID ya screenshot bhejo → admin approve → bot khud ek unused account assign karega aur `login + password` buyer ko bhej dega
  - **USDT:** TRC20 address + amount, TX hash bhejo → bot Tronscan API se verify karega → auto deliver. Verify fail ho to order admin ko pending me chala jata hai.

**Admin (sirf tum):**
- `/admin` — panel, pending orders count, stock
- `/addproduct` — naam → description → Rs price → USDT price
- `/addstock` — product ID → accounts lines me:
  ```
  test@gmail.com | Pass123
  user2 | Abc456
  ```
- `/orders` — pending orders, ✅ Approve / ❌ Reject buttons
- `/stock` — saara stock
- `/setpayments` — current payment details
- `/set jazzcash_number 03001234567` — koi bhi payment detail change karo

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
