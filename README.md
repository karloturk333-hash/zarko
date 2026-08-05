# zarko — osobni investicijski agent

Čita Trading212 račun **read-only**, normalizira sve valute u EUR, sprema dnevne
snapshotove u SQLite i izlaže ih Telegram agentu kroz deterministički sloj.

Načelo cijelog sustava:

> **Kod računa brojke · LLM ih tumači · čovjek odlučuje · nula pristupa novcu.**

Agent ne vidi API ključ, ne može pokrenuti dohvat podataka i ne može poslati
nalog. Nijedna od tih zabrana ne ovisi o tome hoće li ih model poslušati — sve
tri su provedene pravima na datotekama i odvojenim unix korisnicima.

Bez vanjskih ovisnosti: čista standardna biblioteka. Radi na Pythonu 3.9+
(testirano na 3.9 i 3.14).

---

## Brzi start

```bash
git clone <tvoj-repo> zarko && cd zarko
cp .env.example .env && chmod 600 .env    # upiši ključ i secret
python3 -m unittest discover -p "test_*.py"   # 109 testova, bez mreže
python3 portfolio.py --check              # 1 poziv, provjeri kredencijale
python3 portfolio.py --save               # snimi prvi snapshot
python3 report.py status
```

Ako ti je ključ vezan uz IP servera, `--check` radi samo tamo. Testovi rade
svugdje jer ne diraju mrežu.

---

## Trading212 ključ

Trading212 daje **dva stringa** i trebaju oba. Šalju se kao HTTP Basic — ključ
kao korisničko ime, secret kao lozinka (`authWithSecretKey` u
[OpenAPI specu](https://docs.trading212.com/_bundle/api.yaml)).

```bash
# .env
T212_API_KEY=...
T212_API_SECRET=...
T212_ENV=live          # ključ vrijedi samo za okolinu u kojoj je izdan
```

Stari jednodijelni ključevi idu golim headerom (`legacyApiKeyHeader`). Klijent
bira shemu sam: ima secret → Basic, nema → legacy. `--check` ispiše koju je
odabrao.

Pri izdavanju ključa **uključi samo read scopeove**. Ako Trading212 nudi
zaključavanje na IP, uključi i to.

Dijagnostika: **401** = krivi kredencijali ili kriva okolina (live/demo).
**403** = ključ radi, ali nema scope za taj endpoint ili IP nije dozvoljen.

---

## Valute — pročitati prije zbrajanja bilo čega

LSE dionice kotiraju u **penijima** (T212 vraća valutu `GBX`), ne u funtama.
Zbrajanje takve pozicije s EUR iznosima daje **100× napuhanu** vrijednost. Isto
vrijedi za `ZAC` (južnoafrički centi) i `ILA` (izraelski agoroti).

Konvencija imena stupaca u bazi:

| sufiks | značenje | smije se zbrajati |
|---|---|---|
| `*_native` | valuta **instrumenta** (može biti GBX) | nikad |
| `*_acct` | valuta **računa**, T212 već konvertirao | samo unutar istog računa |
| `*_eur` | normalizirano | da — jedino globalno |

Četiri obrane protiv ponavljanja greške:

1. **Valuta se nikad ne pogađa.** Dolazi iz `instrument.currency` unutar same
   pozicije. Pozicija bez valute → iznimka, ne zapis.
2. **Minor uniti se prepoznaju točnom oznakom**, bez `.upper()` — jer je
   `"GBp".upper() == "GBP"`, što tiho gubi faktor 100. Nepoznata oznaka baca
   `UnknownCurrency` i nikad ne pretpostavlja 1.0.
3. **Unakrsna provjera.** Vrijednost pozicije računa se dvaput: iz
   `walletImpact.currentValue` i neovisno iz `quantity × currentPrice`. Razlika
   ide u `check_delta_pct`; iznad 1 % je upozorenje na stderr.
4. **Tečaj se sprema uz snapshot** (`fx_rate_instrument`, `fx_rate_wallet`, puni
   ECB set u `fx_rates_json`), pa se svaki izračun može rekonstruirati kasnije.

**Kako čitati `check_delta_pct`:** 0,1–0,5 % je normalno i raste s dobi tečaja —
ECB objavljuje radnim danom, pa vikendom cijene idu žive uz tečaj od petka.
Odstupanje je sistematično po valuti. Ono što nije normalno je red veličine
**9900 %** — to je neprepoznat minor unit, dakle faktor 100.

```bash
python3 fx_conversion.py 275000 GBX     # brza provjera pretvorbe
```

```sql
SELECT * FROM v_fx_sanity;              -- gdje se dvije metode ne slažu
SELECT * FROM v_latest_allocation;      -- alokacija u EUR, zadnji snapshot
```

Tečajevi su ECB referentni, izravno s `ecb.europa.eu` — bez posrednika i bez
ključa. Objavljuju se radnim danom oko 16:00 CET. Ako je ECB nedostupan, `--save`
pada na tečajeve iz zadnjeg snapshota uz upozorenje. Alternativni izvor se
postavlja s `FX_API_URL`.

---

## Komponente

| Datoteka | Uloga | Dira novac? |
|---|---|---|
| `t212.py` | read-only klijent, obje auth sheme, retry na 429 | čita |
| `fx_conversion.py` | normalizacija u EUR, ECB tečajevi, **GBX ≠ GBP** | ne |
| `db.py` + `schema.sql` | SQLite shema, spremanje snapshota, unakrsna provjera | ne |
| `portfolio.py` | CLI za dohvat i spremanje — **jedini dodiruje ključ** | čita |
| `report.py` | izvještaji iz baze, read-only konekcija, bez ključa | ne |
| `rules.py` + `rules.yaml` | deterministička provjera portfelja protiv pragova | ne |
| `miniyaml.py` | YAML čitač bez ovisnosti | ne |
| `llm_output.py` | validacija odgovora modela | ne |
| `teza.py` + `teze.sql` | zapisnik tvrdnji agenta, s ishodom | ne |
| `hermes/skills/portfelj.md` | pravila po kojima agent radi | ne |

Testovi: `test_fx.py` (32), `test_rules.py` (22), `test_llm_output.py` (42),
`test_teza.py` (13). Ukupno **109**, nijedan ne dira mrežu.

---

## Deterministički sloj

Sve što agent smije koristiti kao izvor brojki.

```bash
python3 report.py status      # trenutno stanje i alokacija
python3 report.py digest      # + promjena od prošlog snapshota
python3 report.py --json      # strojno čitljivo
```

Pravila portfelja stoje u `rules.yaml` i pišu se **unaprijed**. Provjeru radi
`rules.py`, bez LLM-a:

```bash
python3 rules.py provjeri              # stanje protiv pravila
python3 rules.py kupnja TICKER IZNOS   # bi li ta kupnja prekršila pravilo
python3 rules.py pravila               # ispis pravila
```

Klasifikacija je po **izdavatelju**, ne po retku u portfelju: "max 25 % po
poziciji" bi inače proglasilo prekršajem all-world ETF s tisućama kompanija, a
propustilo 20 % u jednoj dionici. Nesvrstan ticker je **greška**, ne
pretpostavka — inače bi se tiho mjerio krivim pragom.

Izlazni kod je 1 ako postoji kršenje, pa se `rules.py` može staviti u cron.

**Prije prve upotrebe prepiši `rules.yaml` na svoje pozicije i svoje pragove.**
Zatečeni pragovi su primjer, ne preporuka.

---

## Zapisnik teza

Mišljenje agenta o poziciji inače nestane u povijesti razgovora. Ovako se
zapisuje s datumom i stanjem pozicije u tom trenutku, pa se poslije može
provjeriti.

```bash
echo '{"ticker": "...", "teza": "...", "protuteza": "...",
       "sto_bi_promijenilo_misljenje": "...", "sigurnost": "srednja"}' \
  | python3 teza.py zapisi

python3 teza.py popis --ticker MU_US_EQ
python3 teza.py otvorene --starije-od 90
python3 teza.py zatvori 3 --ishod promasila --biljeska "..."
```

Prije upisa `teza.py` provjeri troje: ticker postoji u `rules.yaml`, protuteza
nije prazna, i **nijedna brojka nije izmišljena** — svaka mora postojati u
izlazu `report.py` ili u `rules.yaml`. Odbijeni zapis izlazi s kodom 1 i
porukom `ODBIJENO:`, koju agent pročita i ispravi se sam.

Posljedica koju treba znati: brojevi u rečenici se pišu slovima ("dva kvartala",
ne "2 kvartala"), jer provjera ne razlikuje procjenu od brojanja.

Ishod upisuje **čovjek**, ne agent, i ne prepisuje se. Zapisnik koji se može
prepisati nije zapisnik.

---

## Deploy na vlastiti server

Primjeri koriste Debian i putanju `/opt/zarko`. Prilagodi po potrebi.

### 1. Kod na server

Napravi **read-only deploy key** na GitHubu (Settings → Deploy keys, bez
write pristupa) i na serveru zaseban SSH alias:

```
# ~/.ssh/config
Host github-zarko
  HostName github.com
  IdentityFile ~/.ssh/zarko_deploy
  IdentitiesOnly yes
```

```bash
git clone git@github-zarko:<korisnik>/<repo>.git /opt/zarko
```

**Zašto alias:** deploy key vrijedi za **jedan** repozitorij. Ako već imaš
`Host github.com` vezan na ključ drugog projekta uz `IdentitiesOnly yes`, git
će za ovaj repo koristiti krivi ključ i javiti "repository not found" — poruku
koja navodi na krivi trag jer zvuči kao da repo ne postoji.

Bez GitHub pristupa: `./deploy.sh korisnik@server` rsynca kod izravno; `.env` i
`*.db` su izuzeti pa se ne gaze.

### 2. Kredencijali

```bash
cd /opt/zarko && cp .env.example .env && chmod 600 .env && nano .env
python3 -m unittest discover -p "test_*.py" && python3 portfolio.py --check
```

`.env` živi **samo** na serveru i nikad ne ide u git.

### 3. Dnevni snapshot

```bash
crontab -e
```

```
15 22 * * 1-5 cd /opt/zarko && python3 portfolio.py --save --quiet >> snapshot.log 2>&1
```

Cron ima oskudan `PATH` i ne učitava shell profil — zato `cd` u mapu (`.env` se
čita iz radnog direktorija) i po potrebi puna putanja do `python3`. Vrijeme
odaberi nakon zatvaranja tržišta koja te zanimaju.

Provjera da cron ne ovisi o tvojoj okolini:

```bash
env -i /bin/sh -c 'cd /opt/zarko && python3 portfolio.py --check'
```

---

## Agent (neobavezno)

Ako želiš Telegram sučelje, sustav je pisan za
[Hermes Agent](https://github.com/NousResearch/hermes-agent), ali skill je običan
markdown i prenosiv je.

### Izolacija — bitniji dio od instalacije

Agent radi kao **zaseban unix korisnik**, tako da ni ne može doći do ključa:

```bash
sudo adduser --system --group --home /opt/hermes hermes
sudo chmod 600 /opt/zarko/.env          # samo vlasnik
sudo chmod 750 /opt/hermes              # agentove tajne skriva od tebe
```

Rezultat, provjerljiv sa `ls -l`:

| datoteka | vlasnik | agent smije | ti smiješ |
|---|---|---|---|
| `/opt/zarko/.env` | `600 ti` | ništa | čitati |
| `/opt/zarko/*.py` | `644 ti` | čitati | sve |
| `/opt/zarko/teze/` | `2770 hermes:ti` | pisati | pisati |
| `/opt/hermes/` | `750 hermes` | sve | ništa |

Zabrana u skillu i prava na datoteci su **dvije nezavisne brane**. Prva pada ako
model ne posluša; druga ne pada.

### Instalacija

```bash
curl -o install.sh <url> && bash install.sh
```

Ne kroz `curl | bash` — tada je stdin skripta, pa interaktivni wizard nema
odakle čitati odgovore.

Ako se prijavljuješ pretplatom umjesto API ključem: Hermes koristi postojeću
prijavu `claude` CLI-ja na tom stroju. Znači `claude /login` **kao korisnik pod
kojim agent radi**, a ne ručno lijepljenje tokena.

### Skill

```bash
sudo cp /opt/zarko/hermes/skills/portfelj.md /opt/hermes/.hermes/skills/
sudo chown hermes:hermes /opt/hermes/.hermes/skills/portfelj.md
```

Provjeri putanju svoje instalacije — kopija na krivo mjesto ne javlja grešku,
nego agent tiho nastavi po staroj verziji.

### Mapa za zapisnik teza

```bash
sudo mkdir -p /opt/zarko/teze
sudo chown hermes:<tvoj-korisnik> /opt/zarko/teze
sudo chmod 2770 /opt/zarko/teze
```

Setgid (dvojka) je nužan: bez njega nova datoteka koju agent stvori dobije
grupu `hermes` i ti je ne možeš čitati — a ishod teze upisuješ ti.

### Restart nakon izmjene skilla

```bash
sudo -u hermes -H env XDG_RUNTIME_DIR=/run/user/$(id -u hermes) \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u hermes)/bus \
  bash -lc 'systemctl --user restart hermes-gateway'
```

`sudo -u` ne stvara login sesiju, pa `systemctl --user` bez tih varijabli ne
nađe svoj DBUS.

---

## Endpointi i rate limiti

| Endpoint | Limit | Koristi se za |
|---|---|---|
| `/api/v0/equity/account/summary` | 1 / 5 s | valuta računa, cash, agregati |
| `/api/v0/equity/positions` | 1 / 1 s | pozicije (nose valutu instrumenta) |
| `/api/v0/equity/history/*` | 6 / 1 min | povijest |
| `/api/v0/equity/metadata/instruments` | 1 / 50 s | katalog instrumenata |

Paginacija je cursor-based: `nextPagePath` dok ne bude `null`, `limit` najviše 50.
Klijent na 429 čeka po `Retry-After` odnosno `x-ratelimit-reset`.

---

## Tvrde granice

- **API ključ je read-only, zauvijek.** Nula pristupa pisanju prema novcu.
- **LLM nikad ne računa brojke.** Sve brojke dolaze iz API-ja i SQLite-a; `teza.py`
  odbija svaku koje nema u determinističkom izvoru.
- **Agent ne pokreće `portfolio.py`** ni s jednom zastavicom — ta skripta drži
  kredencijale i pokreće je isključivo cron.
- **Pravila se mijenjaju svjesno**, s datumom u `LEARNING.md`, i nikad zato što
  se trenutno krše.
