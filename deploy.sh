#!/usr/bin/env bash
# Deploy na server. Kod ide preko rsynca; .env i baza OSTAJU na serveru.
#
#   ./deploy.sh user@46.62.233.229
#   ./deploy.sh user@46.62.233.229 /opt/zarko
#
# Nakon prvog deploya .env se kreira NA SERVERU (secret nikad ne putuje odavde).

set -euo pipefail

TARGET="${1:?Upotreba: ./deploy.sh user@host [remote_path]}"
REMOTE_PATH="${2:-/opt/zarko}"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

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
    --exclude 'snapshot.log' \
    "$LOCAL_DIR/" "$TARGET:$REMOTE_PATH/"

echo
echo "Kod je gore. Provjera okoline:"
ssh "$TARGET" "cd '$REMOTE_PATH' && python3 --version && python3 -m unittest test_fx 2>&1 | tail -3"

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
