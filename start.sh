#!/bin/bash
# RetailShop — dono bots ek hi service pe chalao (Telegram + WhatsApp).
# Botkeep start command: bash start.sh
set -u
cd "$(dirname "$0")"

# Botkeep env vars sirf .env file se aate hain (panel ka .env) — Node process
# ko wo nazar nahi aate (wa-bot.js process.env parhta hai, dotenv nahi).
# Is liye .env ko environment me export karo, taake DONO bots (Python +
# Node) ko ADMIN_ID, ZURVAPAY keys, PUBLIC_BASE_URL, WA_BOT_NUMBER milein.
# (Python bot .env khud bhi load karta hai — ye duplicate harmless hai.)
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
    echo ".env loaded (WA_BOT_NUMBER set: ${WA_BOT_NUMBER:+yes})"
else
    echo "WARNING: .env file nahi mili — env vars missing ho sakte hain"
fi

# --- Node.js (WhatsApp bot ke liye) ---
# Botkeep Python image me Node nahi hota; pehli dafa download karke
# $HOME/.local/node me rakho (service disk persist karta hai).
if ! command -v node >/dev/null 2>&1; then
    if [ -x "$HOME/.local/node/bin/node" ]; then
        export PATH="$HOME/.local/node/bin:$PATH"
    else
        echo "Node nahi mila — Node 22 LTS download kar raha hun..."
        mkdir -p "$HOME/.local"
        # .tar.xz + 'tar -xJf' needs the xz binary, jo is container me NAHI
        # hai — is liye .tar.gz lo (gzip har jagah hota hai). Agar tar -xzf
        # bhi fail ho to Python ke built-in tarfile se extract karo.
        curl -fsSL -o /tmp/node.tar.gz https://nodejs.org/dist/v22.14.0/node-v22.14.0-linux-x64.tar.gz
        if ! tar -xzf /tmp/node.tar.gz -C "$HOME/.local" 2>/dev/null; then
            echo "tar -xzf fail — Python tarfile se extract kar raha hun..."
            python3 -c "import tarfile; tarfile.open('/tmp/node.tar.gz', 'r:gz').extractall('$HOME/.local')"
        fi
        mv "$HOME/.local/node-v22.14.0-linux-x64" "$HOME/.local/node"
        rm -f /tmp/node.tar.gz
        export PATH="$HOME/.local/node/bin:$PATH"
    fi
fi
node --version

# WhatsApp bot ki dependencies (sirf pehli dafa, ya node_modules na ho to).
if [ ! -d "wa-bot/node_modules" ]; then
    echo "WA bot dependencies install kar raha hun (npm install)..."
    (cd wa-bot && npm install --omit=dev --no-audit --no-fund)
fi

# --- Dono bots chalao, har ek apni restart loop me ---
# (Ek bot crash ho to dusra chalta rahe — WA bot ka crash Telegram bot ko
# nahi girayega. Dono alag loop me restart hote rahenge.)
run_loop() {
    local name="$1"; shift
    while true; do
        echo "[$name] starting: $*"
        "$@"
        echo "[$name] band ho gaya (exit $?), 5s me restart..."
        sleep 5
    done
}

run_loop telegram python bot.py &
run_loop whatsapp node wa-bot/wa-bot.js &
wait
