# zarko — osobni investicijski agent

**M1 — Prvi podatak.** Povlačenje pozicija s Trading212 (read-only), normalizacija svih iznosa u EUR i spremanje snapshota u SQLite.

## Setup

```bash
cp .env.example .env         # upiši T212_API_KEY i T212_API_SECRET
python3 portfolio.py --check # provjeri kredencijale (1 poziv, ne dira bazu)
python3 portfolio.py         # stanje kao JSON, s EUR vrijednostima
python3 portfolio.py --save  # + spremi snapshot u portfolio.db
python3 -m unittest test_fx  # 32 testa, bez mreže
```

Bez dependencyja — čisti Python 3.10+ stdlib.

## Autentikacija

Trading212 daje **dva stringa** i trebaju oba. Šalju se kao HTTP Basic — API key kao username, secret kao password (`authWithSecretKey` u [OpenAPI specu](https://docs.trading212.com/_bundle/api.yaml)):

```bash
T212_API_KEY=...
T212_API_SECRET=...
T212_ENV=live        # ključ vrijedi samo za okolinu u kojoj je generiran
```

Stari jednodijelni ključevi idu golim headerom (`legacyApiKeyHeader`). Klijent bira shemu sam: ima secret → Basic, nema → legacy. `--check` ispiše koju je odabrao.

Dijagnostika: **401** = krivi kredencijali ili kriva okolina (live/demo). **403** = ključ radi, ali nema scope za taj endpoint ili IP nije na whitelisti.

## Fajlovi

| Fajl | Uloga |
|---|---|
| `t212.py` | Read-only klijent, obje auth sheme, retry na 429 |
| `fx_conversion.py` | Normalizacija u EUR; ECB tečajevi; **GBX ≠ GBP** |
| `db.py` | SQLite: shema, spremanje snapshota, unakrsna provjera |
| `portfolio.py` | CLI ulazna točka |
| `schema.sql` | snapshots / positions / instruments / transactions + 2 pogleda |
| `test_fx.py` | 32 testa, težište na faktoru 100 |

## Valute — pročitati prije zbrajanja bilo čega

LSE dionice su kotirane u **penijima** (T212 vraća valutu `GBX`), ne u funtama. Zbrajanje takve pozicije s EUR iznosima daje **100× napuhanu** vrijednost. Isto vrijedi za `ZAC` (JAR centi) i `ILA` (izraelski agoroti).

Konvencija imena kolona u bazi:

- `*_native` — valuta **instrumenta** (GBX!), nikad ne zbrajati
- `*_acct` — valuta **računa**, T212 je već konvertirao; zbrajati samo unutar istog računa
- `*_eur` — normalizirano, **jedino globalno agregabilno**

Četiri obrane protiv ponavljanja greške:

1. **Valuta se nikad ne pogađa.** Dolazi iz `instrument.currency` unutar same pozicije. Pozicija bez valute → iznimka, ne zapis.
2. **Minor uniti se prepoznaju točnom oznakom**, bez `.upper()` — jer je `"GBp".upper() == "GBP"`, što tiho gubi faktor 100. Nepoznata oznaka baca `UnknownCurrency`, nikad ne pretpostavlja 1.0.
3. **Unakrsna provjera.** Vrijednost pozicije računa se dvaput: iz `walletImpact.currentValue` (T212 već sveo na valutu računa) i neovisno iz `quantity × currentPrice` u valuti instrumenta. Razlika se sprema u `check_delta_pct`; iznad 1 % ide upozorenje na stderr. Neprepoznat peni ovdje iskoči kao odstupanje od ~9900 %.
4. **Tečaj se sprema uz snapshot** (`fx_rate_instrument` i `fx_rate_wallet` po poziciji, puni ECB set u `fx_rates_json`), pa se svaki izračun može rekonstruirati mjesecima kasnije.

Brze provjere:

```bash
python3 fx_conversion.py 275000 GBX
```

```sql
SELECT * FROM v_fx_sanity;        -- pozicije gdje se dvije metode ne slažu
SELECT * FROM v_latest_allocation; -- alokacija u EUR, zadnji snapshot
```

Tečajevi su ECB referentni, direktno s `ecb.europa.eu` (bez posrednika, bez ključa). Objavljuju se radnim danom ~16:00 CET; vikendom vrijedi zadnji objavljeni, a `fx_date` kaže koji. Ako je ECB nedostupan, `--save` pada na tečajeve iz zadnjeg snapshota uz upozorenje.

## Endpointi i rate limiti

| Endpoint | Limit | Koristi se za |
|---|---|---|
| `/api/v0/equity/account/summary` | 1 / 5 s | valuta računa, cash, agregati |
| `/api/v0/equity/positions` | 1 / 1 s | pozicije (nose i valutu instrumenta) |
| `/api/v0/equity/history/*` | 6 / 1 min | povijest — sljedeći korak |
| `/api/v0/equity/metadata/instruments` | 1 / 50 s | katalog; za M1 nije potreban |

Paginacija je cursor-based: `nextPagePath` dok ne bude `null`, `limit` max 50.

## Deploy

API ključ je IP-lockan na server, pa se testira **na serveru**, ne lokalno:

```bash
./deploy.sh user@46.62.233.229
```

Skripta rsynca kod i pokrene testove gore. `.env` i `portfolio.db` su izuzeti — secret se kreira na serveru i nikad ne putuje s razvojnog stroja, a baza na serveru je mjerodavna i ne gazi se lokalnom.

Prvi put, na serveru:

```bash
cp .env.example .env && nano .env && chmod 600 .env && python3 portfolio.py --check
```

## Cron (dnevni snapshot, radnim danom nakon zatvaranja US tržišta)

```bash
15 22 * * 1-5 cd /opt/zarko && python3 portfolio.py --save --quiet >> snapshot.log 2>&1
```

Cron ima oskudan `PATH` i ne učitava shell profil — zato `cd` u folder (`.env` se čita iz radnog direktorija) i po potrebi puna putanja do `python3`.

## Tvrde granice (iz roadmapa)

- API ključ: **read-only + IP-lockan**, zauvijek. Nula write pristupa novcu na live računu.
- LLM nikad ne računa brojke — brojke dolaze iz API-ja i SQLite-a.
