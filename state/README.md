# Kontrakt za kripto i ZSE

zarko **ne čita** `~/.hermes/state`. Ta mapa je `750 hermes` — namjerno.
Hermes piše ovamo, zarko samo čita. Ista granica kao `teze/`.

## Datoteke

| datoteka | `source` u JSON-u |
|---|---|
| `crypto.json` | `"crypto"` |
| `zse.json` | `"zse"` |

`*.example.json` su uzorak oblika, ne holdingi. Žive datoteke su u `.gitignore`.

## Shema

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

- `source` mora točno odgovarati imenu datoteke (`crypto` / `zse`)
- `freshness` je `live` ili `snapshot`
- `as_of` je ISO-8601
- `value_eur` je obavezan; `cost_eur` / `pnl_eur` / `quantity` smiju biti `null`
- svaki `ticker` mora biti u `klasifikacija:` u `rules.yaml` (nesvrstan = greška, ne pogodak)

Nedostajuća datoteka: taj izvor je prazan, T212 i dalje radi.
Neispravan JSON: cijeli pogled staje — tiho preskakanje bi sakrilo krive brojke.

## Prava na serveru

Isto kao mapa za teze:

```bash
sudo mkdir -p /opt/zarko/state
sudo chown hermes:<tvoj-korisnik> /opt/zarko/state
sudo chmod 2770 /opt/zarko/state
```

Hermes (skill `stanje.md`) piše `crypto.json` / `zse.json` nakon što izračuna cijene.
zarko ih samo čita. CoinGecko i ZSE REST zarko **ne zove**.
