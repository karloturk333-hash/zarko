#!/usr/bin/env bash
# Deploy na server. Kod ide preko rsynca; .env i baza OSTAJU na serveru.
#
#   ./deploy.sh user@46.62.233.229
#   ./deploy.sh user@46.62.233.229 /opt/zarko
#   SERVICE=dashboard ./deploy.sh user@host      # drugo ime systemd unita
#   SERVICE= ./deploy.sh user@host               # preskoci restart
#
# Nakon prvog deploya .env se kreira NA SERVERU (secret nikad ne putuje odavde).
#
# Restart NIJE kozmetika: HTML se renderira iz koda ucitanog u memoriju procesa,
# pa rsync sam po sebi ne mijenja stranicu. CSS se cita s diska po zahtjevu, sto
# znaci da bez restarta dobijes novi CSS na starom HTML-u.

set -euo pipefail

TARGET="${1:?Upotreba: ./deploy.sh user@host [remote_path]}"
REMOTE_PATH="${2:-/opt/zarko}"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE="${SERVICE-dashboard}"

echo "→ $TARGET:$REMOTE_PATH"

ssh "$TARGET" "mkdir -p '$REMOTE_PATH'"

# --exclude .env: secret se ne prenosi, živi samo na serveru
# --exclude *.db: baza na serveru je mjerodavna, ne gazi je lokalnom
rsync -avz --delete \
    --exclude '.env' \
    --exclude '*.db' \
    --exclude '*.db-journal' \
    --exclude '__pycache__' \
    --exclude '.git' \
    --exclude '*.log' \
    --exclude 'state/crypto.json' \
    --exclude 'state/zse.json' \
    "$LOCAL_DIR/" "$TARGET:$REMOTE_PATH/"

echo
echo "Kod je gore. Provjera okoline:"
ssh "$TARGET" "cd '$REMOTE_PATH' && python3 --version && python3 -m unittest discover -s tests -t . -p 'test_*.py' 2>&1 | tail -5"

echo
if [ -z "$SERVICE" ]; then
    echo "SERVICE je prazan — restart preskocen. Stara stranica ostaje dok proces ne restartas."
elif ! ssh "$TARGET" "systemctl list-unit-files 2>/dev/null | grep -q '^${SERVICE}\.service'"; then
    cat <<EOF
✗ Unit '$SERVICE.service' ne postoji na serveru. Dashboard vjerojatno vrti kao
  obican proces, pa novi kod nece ozivjeti sam od sebe. Instaliraj unit jednom:

    ssh $TARGET
    pkill -f 'view.py serve'
    sudo cp $REMOTE_PATH/deploy/dashboard.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now dashboard
EOF
elif ssh "$TARGET" "sudo -n systemctl restart '$SERVICE'" 2>/dev/null; then
    echo "✓ $SERVICE restartan."
    if ssh "$TARGET" "curl -sf -o /dev/null http://127.0.0.1:8787/health"; then
        echo "✓ /health odgovara."
    else
        echo "✗ /health ne odgovara. Pogledaj: ssh $TARGET 'systemctl status $SERVICE --no-pager'"
    fi
else
    echo "✗ Restart nije prosao (sudo trazi lozinku?). Pokreni rucno:"
    echo "    ssh -t $TARGET 'sudo systemctl restart $SERVICE'"
fi

echo
if ssh "$TARGET" "test -f '$REMOTE_PATH/.env'"; then
    echo "✓ .env postoji na serveru. Provjeri kredencijale:"
    echo "    ssh $TARGET 'cd $REMOTE_PATH && python3 portfolio.py --check'"
else
    cat <<EOF
✗ .env još ne postoji na serveru. Kreiraj ga TAMO (secret ne ide kroz ovaj stroj):

    ssh $TARGET
    cd $REMOTE_PATH
    cp .env.example .env
    nano .env          # upiši T212_API_KEY i T212_API_SECRET
    chmod 600 .env
    python3 portfolio.py --check
EOF
fi
