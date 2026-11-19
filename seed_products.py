"""
Seed products — synced with AHM7 catalog (16 Nov 2026).
Run: python bot.py  (pehle ek dafa, taake shop.db + tables ban jayein)
     python seed_products.py
Phir dobara chalane se duplicate nahi bante — naam se check karta hai.
Existing products ki price/description UPDATE ho jati hai, naye INSERT hote hain.

IMPORTANT: Ye sirf PRODUCTS + PRICE add/update karta hai.
STOCK 0 hai — accounts tumhe /addstock se add karne honge.
USDT rate: 1 USDT = 280 PKR (approx, Nov 2026).
Pricing rule: har product par Rs. 100 izaafa; jo price AHM7 cost se kam thi,
wo AHM7 cost + Rs. 100 par set hai. Muse Ai 1B Credits = 173 (Junaid).
"""
import sqlite3

DB_PATH = "shop.db"

# (name, description, price_pkr, price_usdt)
PRODUCTS = [
    ("Amazon Prime 6 Months", "Limited Deal - was PKR 850", 768, 2.74),
    ("Udemy Personal Plan 1M", "Limited Deal - was PKR 669", 662, 2.36),
    ("Capcut Pro 1 Month", "Premium Service", 621, 2.22),
    ("HMA Vpn Monthly", "Premium Service", 283, 1.01),
    ("Microsoft 365 Plus 1 Year", "Premium Service", 581, 2.08),
    ("Muse Ai 1B Credits", "Premium Service", 123, 0.44),
    ("Windows 10/11 Pro", "Premium Service", 367, 1.31),
    ("Muse Ai 31B Token (NW)", "Premium Service", 634, 2.26),
    ("Capcut Pro 7D 500 credits", "CapCut Pro — 7 days, 500 credits", 214, 0.76),
    ("500$ API CLAUDE 30D (FW)", "Claude API — $500 credits, 30 days", 2770, 9.89),
    ("100$ API Claude 30D (FW)", "Claude API — $100 credits, 30 days", 861, 3.08),
    ("Adobe Express 12M", "Adobe Express Premium — 12 months", 261, 0.93),
    ("Gemini Plus 6m Links (No Card Required)", "Gemini Plus — 6 months, redeem link, no card needed", 366, 1.31),
    ("MUSE AI 1B TOKEN (W2D)", "Muse AI — 1 Billion tokens account", 261, 0.93),
    ("Outlook IMAP", "Outlook mail account (IMAP)", 58, 0.21),
    ("Hotmail Mail Accounts", "Hotmail mail account", 135, 0.48),
    ("Owl Proxy 200 Mb", "Owl Proxy — 200 MB data", 109, 0.39),
    ("iLovePdf Premium 1 Yr", "iLovePDF Premium — 1 year", 552, 1.97),
    ("Chatgpt Plus 1M (25DW)", "ChatGPT Plus — 1 month", 2649, 9.46),
    ("Coursera Plus 7 Days Trial", "Coursera Plus — 7 days trial", 311, 1.11),
    ("Bolt .new Pro 1 Month", "Bolt.new Pro — 1 month", 1003, 3.58),
    ("3 in 1 VIP Group", "3 in 1 VIP group access", 7700, 27.5),
    ("Canva Edu 3 Years Invite On Client Mail", "Invite on client mail", 385, 1.38),
    ("CrunchyRoll 1 Year Mega Fan Profile (FW)", "Crunchyroll Mega Fan — 1 year", 699, 2.5),
    ("Hostinger Unlimited Plan 12 Months (FW)", "Hostinger Unlimited — 12 months", 5650, 20.18),
    ("Muse Ai Token On Client Account", "Muse AI token — added to your own account", 131, 0.47),
    ("Capcut Pro 6 Months", "CapCut Pro — 6 months", 3184, 11.37),
    ("Figma Professional", "Figma Professional — design tool", 1102, 3.94),
    ("Gemini Plus 12m Links", "Gemini Plus — 12 months, redeem link", 461, 1.65),
    ("Linkedin Career 3M", "LinkedIn Career — 3 months", 410, 1.46),
    ("LinkedIn Sales Navigator", "LinkedIn Sales Navigator", 534, 1.91),
    ("Notion Plus 12M", "Notion Plus — 12 months", 899, 3.21),
    ("Super Duolingo", "Super Duolingo — language learning", 367, 1.31),
    ("Amazon Prime Video 1m", "Amazon Prime Video — 1 month", 367, 1.31),
    ("Bolt.new Pro Plan 12 Month", "Bolt.new Pro — 12 months", 2391, 8.54),
    ("Elevenlab Creator 3 Months", "ElevenLabs Creator — 3 months", 2570, 9.18),
    ("Google Drive Unlimited Storage", "Google Drive — unlimited storage", 2760, 9.86),
    ("Kling Standard 1M - 1k1 Credits", "Kling Standard — 1 month, 1k1 credits", 1644, 5.87),
    ("Muse Ai 25-38B", "Muse AI 25-38B tokens", 363, 1.3),
    ("MUSE AI 31B Stable", "Muse AI 31B tokens — stable", 630, 2.25),
    ("Nord VPN 3M", "NordVPN — 3 months", 822, 2.94),
    ("Surfshark 2 Month Account (FW)", "Surfshark VPN — 2 months", 1107, 3.95),
]

def main():
    c = sqlite3.connect(DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        description TEXT DEFAULT '', price_pkr INTEGER NOT NULL,
        price_usdt REAL NOT NULL, active INTEGER DEFAULT 1)""")
    added, updated, skipped = 0, 0, 0
    for name, desc, pkr, usdt in PRODUCTS:
        row = c.execute("SELECT id, price_pkr, price_usdt, description FROM products WHERE name=?", (name,)).fetchone()
        if row:
            if row[1] != pkr or row[2] != usdt or row[3] != desc:
                c.execute("UPDATE products SET description=?, price_pkr=?, price_usdt=? WHERE id=?",
                          (desc, pkr, usdt, row[0]))
                updated += 1
            else:
                skipped += 1
            continue
        c.execute("INSERT INTO products(name,description,price_pkr,price_usdt) VALUES(?,?,?,?)",
                  (name, desc, pkr, usdt))
        added += 1
    c.commit()
    total = c.execute("SELECT COUNT(*) n FROM products").fetchone()[0]
    c.close()
    print(f"Added: {added}, updated: {updated}, unchanged: {skipped}, total products: {total}")
    print("Stock abhi 0 hai — /addstock se accounts add karo.")

if __name__ == "__main__":
    main()
