"""
Seed products from Junaid's screenshots (11 Nov 2026).
Run: python bot.py  (pehle ek dafa, taake shop.db + tables ban jayein)
     python seed_products.py
Phir dobara chalane se duplicate nahi bante — naam se check karta hai.

IMPORTANT: Ye sirf PRODUCTS + PRICE add karta hai.
STOCK 0 hai — accounts tumhe /addstock se add karne honge.
Screenshot wale stock numbers (13, 429, 1025...) us dusre seller ke hain,
tumhare nahi. Unhe copy nahi kiya.
USDT rate: 1 USDT = 280 PKR (approx, Nov 2026). Apne hisab se badal sakte ho.
"""
import sqlite3

DB_PATH = "shop.db"

# (name, description, price_pkr, price_usdt)
PRODUCTS = [
    ("Amazon Prime 6 Months", "Limited Deal - was PKR 850", 699, 2.50),
    ("Udemy Personal Plan 1M", "Limited Deal - was PKR 669", 559, 2.00),
    ("Capcut Pro 1 Month", "Premium Service", 526, 1.88),
    ("HMA Vpn Monthly", "Premium Service", 186, 0.66),
    ("Microsoft 365 Plus 1 Year", "Premium Service", 501, 1.79),
    ("Muse Ai 1B Credits", "Premium Service", 71, 0.25),
    ("Windows 10/11 Pro", "Premium Service", 276, 0.99),
    ("Muse Ai 31B Token (NW)", "Premium Service", 369, 1.32),
    ("Capcut Pro 7D 500 credits", "", 114, 0.41),
    ("500$ API CLAUDE 30D (FW)", "", 2499, 8.93),
    ("100$ API Claude 30D (FW)", "", 749, 2.68),
    ("Adobe Express 12M", "", 169, 0.60),
    ("Gemini Plus 6m Links (No Card Required)", "", 379, 1.35),
    ("MUSE AI 1B TOKEN (W2D)", "", 139, 0.50),
    ("Outlook IMAP", "", 35, 0.13),
    ("Hotmail Mail Accounts", "", 35, 0.13),
    ("Owl Proxy 200 Mb", "", 49, 0.18),
    ("iLovePdf Premium 1 Yr", "", 419, 1.50),
    ("Chatgpt Plus 1M (25DW)", "", 2549, 9.10),
    ("Coursera Plus 7 Days Trial", "", 211, 0.75),
    ("Bolt .new Pro 1 Month", "", 399, 1.43),
    ("3 in 1 VIP Group", "", 12999, 46.43),
    ("Canva Edu 3 Years Invite On Client Mail", "Invite on client mail", 250, 0.89),
    ("CrunchyRoll 1 Year Mega Fan Profile (FW)", "", 599, 2.14),
    ("Hostinger Unlimited Plan 12 Months (FW)", "", 5550, 19.82),
    ("Muse Ai Token On Client Account", "", 50, 0.18),
    # NOTE: "Canva Admin Panel 3Year (2MW)" screenshot me cut off tha, price nazar nahi aayi
    # is liye is list me nahi hai. Price batao to add kar dunga.
]

def main():
    c = sqlite3.connect(DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        description TEXT DEFAULT '', price_pkr INTEGER NOT NULL,
        price_usdt REAL NOT NULL, active INTEGER DEFAULT 1)""")
    added, skipped = 0, 0
    for name, desc, pkr, usdt in PRODUCTS:
        exists = c.execute("SELECT id FROM products WHERE name=?", (name,)).fetchone()
        if exists:
            skipped += 1
            continue
        c.execute("INSERT INTO products(name,description,price_pkr,price_usdt) VALUES(?,?,?,?)",
                  (name, desc, pkr, usdt))
        added += 1
    c.commit()
    total = c.execute("SELECT COUNT(*) n FROM products").fetchone()[0]
    c.close()
    print(f"Added: {added}, already thi: {skipped}, total products: {total}")
    print("Stock abhi 0 hai — /addstock se accounts add karo.")

if __name__ == "__main__":
    main()
