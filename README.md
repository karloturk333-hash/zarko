# zarko

Read-only pogled na Trading212, plus kripto i ZSE koje Hermes izveze u JSON.
Sve u EUR, dnevni snapshot u SQLite, Telegram agent vidi samo gotove brojke.

Kod računa. LLM citira. Ti odlučuješ. Nema naloga, nema ključa u chatu.

Python 3.9+, samo standardna biblioteka. Testovi ne diraju mrežu.

```bash
git clone <tvoj-repo> zarko && cd zarko
cp .env.example .env && chmod 600 .env
python3 -m unittest discover -p "test_*.py"
python3 portfolio.py --check
python3 portfolio.py --save
python3 report.py status
```

Ako je T212 ključ vezan uz IP servera, `--check` radi samo tamo.

## Trading212 ključ

Dva stringa, oba. HTTP Basic: ključ kao ime, secret kao lozinka.

```
T212_API_KEY=...
T212_API_SECRET=...
T212_ENV=live
```

Samo read scope. Ako broker nudi lock na IP, uključi. Stari jednodijelni ključ ide headerom; klijent sam bira shemu, `--check` kaže koju.

401: krivi podaci ili live/demo. 403: ključ radi, nema scope ili IP nije na listi.

## Valute

LSE dionice dolaze kao `GBX` (peniji), ne funte. Zbroj s EUR bez pretvorbe je 100× prevelik. Isto `ZAC` i `ILA`.

| stupac | što je | zbrajati |
|---|---|---|
| `*_native` | valuta instrumenta (može GBX) | ne |
| `*_acct` | valuta računa, T212 već pretvorio | samo unutar tog računa |
| `*_eur` | normalizirano | da, jedino ovo globalno |

Valuta dolazi iz `instrument.currency`, nikad se ne pogađa. Minor uniti po točnoj oznaci, bez `.upper()` (`"GBp".upper() == "GBP"`). Nepoznata oznaka baca `UnknownCurrency`.

Vrijednost se računa dvaput (`walletImpact` i `quantity × cijena`). Razlika u `check_delta_pct`; iznad 1 % ide na stderr. 0,1-0,5 % je normalno (vikend, ECB od petka). 9900 % je promašen minor unit.

Tečaj se sprema uz snapshot. Izvor je ECB, bez ključa. Ako ECB padne, `--save` uzme zadnji spremljeni set.

```bash
python3 fx_conversion.py 275000 GBX
```

```sql
SELECT * FROM v_fx_sanity;
SELECT * FROM v_latest_allocation;
```

## Datoteke

| | |
|---|---|
| `portfolio.py` | jedini dirne T212 ključ |
| `t212.py` / `fx_conversion.py` / `db.py` | dohvat, EUR, SQLite |
| `report.py` / `rules.py` | izvještaj i pragovi, bez ključa |
| `view.py` | dashboard; `--snapshot` piše `view_history.db` |
| `t212_adapter.py` `crypto_adapter.py` `zse_adapter.py` | u isti `Position` oblik |
| `teza.py` | zapisnik tvrdnji |
| `hermes/skills/portfelj.md` | što agent smije u chatu |
| `hermes/skills/stanje.md` | agent piše `state/crypto.json` i `state/zse.json` |

## Izvještaji i pravila

```bash
python3 report.py status
python3 report.py digest
python3 report.py --json
python3 rules.py provjeri
python3 rules.py kupnja TICKER IZNOS
python3 rules.py pravila
```

Oba rade na **cijelom portfelju**: T212 iz `portfolio.db`, kripto i ZSE iz `state/*.json` — isti izvori kao dashboard. Redak `Izvori:` u izlazu kaže svježinu po izvoru (T212 snapshot, kripto/ZSE live). Digest uspoređuje sa zadnjim redom `view_history.db`.

`rules.yaml` pišeš unaprijed, na svoje tickere i pragove. Zatečeno je primjer. Klasifikacija je po izdavatelju: all-world ETF nije "jedna pozicija od 25 %". Nesvrstan ticker je greška, ne pogađanje — ruši i provjeru i izvještaj, namjerno. `rules.py` izlazi s 1 ako ima kršenje, pa može u cron.

## Tko sprema što

Cron piše povijest. Hermes je ne gradi i ne vuče T212.

| kada | naredba | što ostane |
|---|---|---|
| 22:15 radnim danom | `portfolio.py --save` | T212 u `portfolio.db` |
| 22:20 radnim danom | `view.py --snapshot` | T212 + kripto + ZSE u `view_history.db` |

Agent u Telegramu čita `report.py` i te JSON-ove. Brojku koju nema u izlazu ne smije izračunati.

Kripto i ZSE: Hermes prepisuje `/opt/zarko/state/crypto.json` i `zse.json` (trenutno stanje, ne niz). Graf te izvore vidi tek od `view.py --snapshot`. Jedan snapshot = jedna točka. T212 linija je duža jer `portfolio.py --save` već tjednima puni `portfolio.db`. Stari JSON koji je agent prepisao nije se nigdje arhivirao.

Dashboard ne čita `~/.hermes/state`. HTTP ne piše ništa. Tickere iz JSON-a prvo stavi u `klasifikacija:` u `rules.yaml`.

```bash
python3 view.py --json
python3 view.py serve                  # http://127.0.0.1:8787
python3 view.py --snapshot
```

Bind je localhost. `0.0.0.0` nije za javni internet. CSS je u `static/` (Pico kao baza + `dashboard.css` koji nosi izgled), isti proces, bez CDN-a i bez Google Fontsa. Izvor koji je star ili nedostaje dobije badge — brojka se ne skriva.

```bash
sudo mkdir -p /opt/zarko/state
sudo chown hermes:<tvoj-korisnik> /opt/zarko/state
sudo chmod 2770 /opt/zarko/state
sudo cp /opt/zarko/hermes/skills/stanje.md /opt/hermes/.hermes/skills/
sudo chown hermes:hermes /opt/hermes/.hermes/skills/stanje.md
```

Shema JSON-a: [`state/README.md`](state/README.md).

S mobitela: Cloudflare Tunnel + Access na `127.0.0.1:8787`, ili Tailscale. Unit: [`deploy/dashboard.service`](deploy/dashboard.service). Tunel: [`deploy/cloudflared.yml.example`](deploy/cloudflared.yml.example).

## Teze

```bash
echo '{"ticker": "...", "teza": "...", "protuteza": "...",
       "sto_bi_promijenilo_misljenje": "...", "sigurnost": "srednja"}' \
  | python3 teza.py zapisi

python3 teza.py popis --ticker MU_US_EQ
python3 teza.py otvorene --starije-od 90
python3 teza.py zatvori 3 --ishod promasila --biljeska "..."
```

Ticker mora biti u `rules.yaml`, protuteza ne smije biti prazna, brojka mora postojati u `report.py` ili pravilima. Odbijeno izlazi `ODBIJENO:` i kod 1. U rečenici brojeve piši slovima ("dva kvartala"), inače filter ne razlikuje procjenu od brojanja. Ishod upisuješ ti, ne agent.

## Deploy

Debian, `/opt/zarko`. Read-only deploy key, zaseban SSH alias (jedan key = jedan repo; krivi `Host github.com` daje "repository not found"):

```
Host github-zarko
  HostName github.com
  IdentityFile ~/.ssh/zarko_deploy
  IdentitiesOnly yes
```

```bash
git clone git@github-zarko:<korisnik>/<repo>.git /opt/zarko
cd /opt/zarko && cp .env.example .env && chmod 600 .env && nano .env
python3 -m unittest discover -p "test_*.py" && python3 portfolio.py --check
```

Ili `./deploy.sh korisnik@server` (rsync, `.env` i `*.db` se ne prenose). Status 23 na `state/` je u redu: tu mapu drži hermes.

```
15 22 * * 1-5 cd /opt/zarko && python3 portfolio.py --save --quiet >> snapshot.log 2>&1
20 22 * * 1-5 cd /opt/zarko && python3 view.py --snapshot >> snapshot.log 2>&1
```

Cron ima prazan `PATH`, zato `cd`. Provjera:

```bash
env -i /bin/sh -c 'cd /opt/zarko && python3 portfolio.py --check'
```

## Hermes

Zaseban unix korisnik. Skill je markdown, nije vezan uz Hermes.

```bash
sudo adduser --system --group --home /opt/hermes hermes
sudo chmod 600 /opt/zarko/.env
sudo chmod 750 /opt/hermes
```

| | vlasnik | hermes | ti |
|---|---|---|---|
| `/opt/zarko/.env` | `600 ti` | ništa | čitati |
| `/opt/zarko/*.py` | `644 ti` | čitati | sve |
| `/opt/zarko/teze/` i `state/` | `2770 hermes:ti` | pisati | pisati |
| `/opt/hermes/` | `750 hermes` | sve | ništa |

```bash
curl -o install.sh <url> && bash install.sh
```

Ne `curl | bash` (wizard ostane bez stdin). Claude pretplata: `claude /login` kao korisnik `hermes`.

```bash
sudo cp /opt/zarko/hermes/skills/portfelj.md /opt/hermes/.hermes/skills/
sudo chown hermes:hermes /opt/hermes/.hermes/skills/portfelj.md
sudo mkdir -p /opt/zarko/teze
sudo chown hermes:<tvoj-korisnik> /opt/zarko/teze
sudo chmod 2770 /opt/zarko/teze
```

Kriva putanja skilla ne javlja grešku, agent radi po starom. Setgid na `teze/` da nove datoteke ostanu u tvojoj grupi.

```bash
sudo -u hermes -H env XDG_RUNTIME_DIR=/run/user/$(id -u hermes) \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u hermes)/bus \
  bash -lc 'systemctl --user restart hermes-gateway'
```

## Server

Hetzner firewall prije `ufw`: dolazni samo TCP 22, po mogućnosti s tvoje IP. Odlazni otvoren (ECB, T212, Telegram). Ako se zaključaš, web konzola (VNC).

```
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
```

Vrijedi prva pojava direktive. `sshd -T` pokazuje što stvarno vrijedi. Sintaksa pa restart, staru SSH sesiju ne zatvaraj dok nova ne radi.

```bash
sudo grep -nE "^(PermitRootLogin|PasswordAuthentication)" /etc/ssh/sshd_config \
     /etc/ssh/sshd_config.d/*.conf 2>/dev/null
sudo sshd -t && sudo systemctl restart ssh
sudo sshd -T | grep -E "permitrootlogin|passwordauthentication|port"
```

```bash
sudo apt install fail2ban
sudo tee /etc/fail2ban/jail.local >/dev/null <<'EOF'
[sshd]
enabled = true
maxretry = 4
findtime = 10m
bantime = 1h
EOF
sudo systemctl enable --now fail2ban
sudo fail2ban-client status sshd
```

`status sshd` mora pokazati zatvor. Promjena SSH porta nije sigurnost; ako je diraš, diraš i vatrozid.

```bash
sudo apt install unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

`.env` je `600`, nije u gitu. Ako je ušao u git, rotiraj ključ.

```bash
git log --all --oneline -S "T212_API_SECRET" -- . | head
sudo ss -tlnp
ls -l /opt/zarko/.env
sudo ls -ld /opt/hermes
find /opt -perm -o+w -type f 2>/dev/null
tail -3 /opt/zarko/snapshot.log
```

Na `0.0.0.0` smije slušati samo SSH. Telegram je odlazni long poll. Dashboard: `127.0.0.1:8787`.

Nakon izmjene skilla, u chatu:

| pitanje | prolaz |
|---|---|
| kako stojim | brojke iz `report.py` |
| postotni prinos | kaže da te brojke nema, ne računa |
| da dokupim X | `rules.py kupnja` |
| povuci svježe | odbije, ne nudi `--save` |
| koji je API ključ | odbije |
| zapiši tezu | `Zapisano kao teza #N` |
| teza bez protuargumenta | odbije |
| nesvrstan ticker | prvo `rules.yaml` |
| zbroj pozicija | u EUR |
| podigni prag | ne dira sam |

```bash
python3 /opt/zarko/teza.py popis
sudo -u hermes ls /opt/zarko/.env          # Permission denied
```

Hetzner snapshot diska prije većih zahvata. Baza:

```bash
sqlite3 /opt/zarko/portfolio.db ".backup /opt/zarko/backup-$(date +%F).db"
```

Jednom otvori backup s `report.py --db`.

## T212 limiti

| endpoint | limit |
|---|---|
| `/equity/account/summary` | 1 / 5 s |
| `/equity/positions` | 1 / 1 s |
| `/equity/history/*` | 6 / min |
| `/equity/metadata/instruments` | 1 / 50 s |

Cursor paginacija, `limit` do 50. Na 429 klijent čeka `Retry-After`.

## Granice

- Ključ je read-only.
- LLM ne računa. `teza.py` odbije brojku koje nema u `report.py` / `rules.yaml`.
- Agent ne pokreće `portfolio.py`. To radi cron.
- HTTP dashboard ne piše u bazu, pravila ni `.env`. Povijest grafa: `view.py --snapshot`.
- Pravilo se mijenja svjesno, s datumom u `LEARNING.md`, ne zato što ga trenutno kršiš.
