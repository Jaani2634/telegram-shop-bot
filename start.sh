#!/bin/bash
# RetailShop — dono bots ek hi service pe chalao (Telegram + WhatsApp).
# Botkeep start command: bash start.sh
set -u
cd "$(dirname "$0")"

# --- Node.js (WhatsApp bot ke liye) ---
# Botkeep Python image me Node nahi hota; pehli dafa download karke
# $HOME/.local/node me rakho (service disk persist karta hai).
if ! command -v node >/dev/null 2>&1; then
    if [ -x "$HOME/.local/node/bin/node" ]; then
        export PATH="$HOME/.local/node/bin:$PATH"
    else
        echo "Node nahi mila — Node 22 LTS download kar raha hun..."
        mkdir -p "$HOME/.local"
        curl -fsSL -o /tmp/node.tar.xz https://nodejs.org/dist/v22.14.0/node-v22.14.0-linux-x64.tar.xz
        tar -xJf /tmp/node.tar.xz -C "$HOME/.local"
        mv "$HOME/.local/node-v22.14.0-linux-x64" "$HOME/.local/node"
        rm -f /tmp/node.tar.xz
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
