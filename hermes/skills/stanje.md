---
name: stanje
description: Izvoz kripto i ZSE holdinga u JSON kontrakt koji čita zarko dashboard. Koristi nakon što izračunaš cijene, ili kad Karlo pita da osvježiš state/crypto.json ili state/zse.json.
---

# Izvoz stanja (kripto / ZSE)

Dashboard čita **samo** ove datoteke, nikad `~/.hermes/state`:

```
/opt/zarko/state/crypto.json
/opt/zarko/state/zse.json
```

Mapa je `2770 hermes:<karlo>` — smiješ pisati samo te dvije datoteke, ništa drugo u `/opt/zarko`.

## Oblik

```json
{
  "source": "crypto",
  "as_of": "2026-08-21T20:00:00Z",
  "freshness": "live",
  "cash_eur": 0,
  "positions": [
    {
      "ticker": "BTC",
      "name": "Bitcoin",
      "quantity": 0.01,
      "currency": "EUR",
      "value_eur": 1234.56,
      "cost_eur": null
    }
  ]
}
```

- `crypto.json` → `"source": "crypto"`
- `zse.json` → `"source": "zse"`
- `freshness` je `"live"` ako si cijene upravo povukao, inače `"snapshot"`
- `as_of` je sada, ISO-8601 s `Z`
- `value_eur` je obavezan; bez njega dashboard staje
- ticker mora već postojati u `/opt/zarko/rules.yaml` pod `klasifikacija:` — ako ne, reci Karlu da ga doda, **nemoj** nagađati kategoriju

Uzorak oblika: `/opt/zarko/state/crypto.example.json` i `zse.example.json`.

## Kako zapisati

Cijene izračunaš ti (CoinGecko, ZSE REST — kako već radiš). JSON sastaviš i zapišeš atomarno:

```bash
tmp=$(mktemp /opt/zarko/state/.tmp.XXXXXX)
cat > "$tmp" <<'EOF'
{ ... }
EOF
mv "$tmp" /opt/zarko/state/crypto.json
```

`mv` na istoj datotečnoj particiji je atomaran, pa dashboard nikad ne pročita pola datoteke.

## Tvrde granice

- **Ne čitaš** `/opt/zarko/.env` i **ne pokrećeš** `portfolio.py`.
- **Ne zoveš** `view.py` da bi "provjerio" — to je Karlov UI, ne tvoj kanal.
- **Ne pišeš** u `portfolio.db` ni `rules.yaml`.
- Ako nemaš količine (holding), nemoj izmišljati pozicije. Bolje izostaviti datoteku nego upisati nulu koju nisi izmjerio.
