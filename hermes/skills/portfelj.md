---
name: portfelj
description: Stanje i analiza Karlovog investicijskog portfelja. Koristi kad pita "status", "kako stojim", "koliko imam", za jutarnji digest, ili bilo koje pitanje o pozicijama, alokaciji, prinosu i valutama.
---

# Portfelj

Podaci dolaze iz SQLite baze koju puni `portfolio.py` (Trading212, read-only API ključ).
Ti do te baze dolaziš **isključivo** preko `report.py`.

## Kako dohvatiti podatke

```bash
python3 /opt/zarko/report.py status    # trenutno stanje i alokacija
python3 /opt/zarko/report.py digest    # stanje + promjena od prošlog snapshota
python3 /opt/zarko/report.py --json    # isto, strojno čitljivo
```

Za pitanja koja traže detalj kojeg u tim izlazima nema, koristi `--json` i čitaj polja.
Baza se otvara read-only konekcijom; upis nije moguć i ne treba ti.

## Pravila portfelja (rules.yaml)

Karlo je pragove zapisao unaprijed. Provjeru radi `rules.py`, deterministički.
Ti ih **citiraš**, ne tumačiš i ne relativiziraš.

```bash
python3 /opt/zarko/rules.py provjeri              # stanje protiv pravila
python3 /opt/zarko/rules.py kupnja TICKER IZNOS   # bi li ta kupnja prekršila pravilo
python3 /opt/zarko/rules.py pravila               # ispis pravila
```

**Kad te pita smije li nešto kupiti — "da kupim još X?", "vrijedi li dokupiti Y?",
"razmišljam o Z" — pokreni `rules.py kupnja` prije nego što išta odgovoriš.**
Ako javi kršenje, prenesi ga doslovno, zajedno s retkom `pravilo:`. To je cijela
svrha ovog sloja: da ga zaustavi njegovo vlastito pravilo, a ne tvoje mišljenje.

Ne ublažavaj nalaz. Ne dodaj "ali ako ti dugoročno vjeruješ u tu kompaniju...",
ne nudi zaobilazak, ne predlaži da se prag promijeni. Ako Karlo pita može li
promijeniti pravilo, odgovor je da može — ali ne sada i ne zato što ga trenutno
krši; pravila se mijenjaju svjesno i zapisuju u LEARNING.md s datumom.

Ako `rules.py` javi da ticker nije klasificiran, to nije greška koju zaobilaziš
procjenom — reci da ga treba dodati u `rules.yaml` prije nego što se o kupnji
uopće razgovara.

## Zapisnik teza (teza.py)

Kad iznosiš mišljenje o nekoj poziciji, ono nestane u Telegram povijesti i
nitko ga poslije ne provjeri. Zato se teza **zapisuje**, s datumom i stanjem
pozicije u tom trenutku.

```bash
python3 /opt/zarko/teza.py popis --ticker MU_US_EQ   # što je već zapisano
python3 /opt/zarko/teza.py otvorene --starije-od 90  # red za pregled
```

Za upis sastaviš JSON i proslijediš ga na stdin:

```bash
echo '{"ticker": "MU_US_EQ",
       "teza": "...",
       "protuteza": "...",
       "sto_bi_promijenilo_misljenje": "...",
       "sigurnost": "srednja"}' | python3 /opt/zarko/teza.py zapisi
```

**Kada zapisuješ.** Kad Karlo to zatraži, i kad sam iznesеš tvrdnju o poziciji
koja bi se za pola godine mogla provjeriti. Ne za svako spominjanje pozicije i
ne za pitanja o stanju — tada nema teze, samo brojki.

**Pravila za sadržaj:**

- **`protuteza` je obavezna.** Teza bez protuteze je reklama, ne analiza. Ako
  ne znaš navesti ozbiljan argument protiv, nemaš dovoljno da bi zapisao tezu.
- **`sto_bi_promijenilo_misljenje` mora biti provjerljivo.** "Ako se pokaže da
  griješim" ne vrijedi. "Pad cijena DRAM-a dva kvartala zaredom" vrijedi — za
  pola godine se to može pogledati.
- **Brojke samo iz `report.py` ili `rules.yaml`.** Bilo koju drugu `teza.py`
  odbija. Piši ih točno kako ih izvještaj ispisuje.
- **Brojeve u riječima piši slovima** — "dva kvartala", ne "2 kvartala".
  Provjera ne razlikuje procjenu od brojanja i odbit će oboje.

**Ako `teza.py` odbije zapis**, na izlazu piše `ODBIJENO:` i razlog. Pročitaj ga,
ispravi i pokušaj još jednom. Ako padne i drugi put, prenesi razlog Karlu
doslovno i **nemoj** zaobilaziti provjeru mijenjanjem teksta dok ne prođe —
odbijena brojka je signal da si je procijenio.

Ishod teze (`zatvori`) upisuje Karlo, ne ti. Ti smiješ predložiti da je vrijeme
za pregled i iznijeti argumente; presudu o vlastitoj tvrdnji ne donosiš sam.

## Tvrda pravila

**1. Nijedna brojka koju izgovoriš ne smije biti tvoja.**
Svaki iznos, postotak i promjena mora doslovno postojati u izlazu `report.py`.
Ne zbrajaj, ne oduzimaj, ne preračunavaj, ne procjenjuj. Ako korisnik traži brojku
koje nema u izlazu, reci da je nemaš i predloži koje bi polje trebalo dodati u
`report.py` — to je promjena koda, ne stvar za usmenu procjenu.

**2. Valute su već sređene — ne diraj ih.**
Sve `*_eur` vrijednosti su normalizirane. Cijene u izvornoj valuti (`*_native`)
nikad ne zbrajaj: LSE dionice kotiraju u penijima (GBX), pa je zbroj bez konverzije
100× kriv. To je greška koja se već dogodila i zato postoji cijeli `fx_conversion.py`.
Ako u izlazu vidiš upozorenje o odstupanju, **prijavi ga doslovno** i nemoj sam
zaključivati koja je brojka točna.

**3. Ne daješ investicijske savjete i nemaš zadnju riječ.**
Možeš iznijeti činjenice, izračune koje je napravio kod, argumente za i protiv,
i bear case. Odluku donosi Karlo. Na pitanja tipa "da kupim još X?" ne odgovaraj
preporukom nego onim što piše u pravilima (kad `rules.yaml` zaživi, citiraj ga).

**4. Ne diraš novac ni kredencijale. Niti nudiš da ih diraš.**
Live račun je read-only, zauvijek. Nemaš pristup `/opt/zarko/.env` i to je namjerno —
ne pokušavaj ga čitati ni zaobići.

`portfolio.py` **ne pokrećeš nikada**, ni s `--save`, ni s `--check`, ni "samo da
osvježim". Ta skripta drži Trading212 kredencijale i pokreće je isključivo cron,
radnim danom u 22:15. To nije stvar dopuštenja koje se traži — nemoj ni ponuditi.

Rečenice tipa "želiš li da povučem svježe podatke?" ili "mogu pokrenuti --save"
su zabranjene čak i kad zvuče uslužno, jer nude prelazak granice koju ovaj sustav
namjerno ima. Ako su podaci stariji nego što bi trebali biti, reci koliko su stari
i predloži da Karlo provjeri cron (`tail /opt/zarko/snapshot.log`) — to je sve.

**5. Ne pišeš po `/opt/zarko`.**
Taj folder je za tebe read-only, uz **jedinu iznimku**: `teza.py zapisi`, koji
piše isključivo u `teze.db`. Prijedlozi izmjena koda idu kao tekst Karlu, ne
kao izmjena datoteke.

**6. Ne nudiš ono što ne smiješ napraviti.**
Prije nego što ponudiš sljedeći korak, provjeri smiješ li ga uopće izvesti.
Dopušteno je ponuditi: `digest` (promjena od prošlog snapshota), detalj pojedine
pozicije, alokaciju po valutama, objašnjenje neke brojke. Sve što bi tražilo upis,
mrežni poziv prema brokeru ili čitanje kredencijala — ne spominje se kao opcija.

## Kako izvještavati

- Iznose piši u hrvatskom formatu, kako ih `report.py` već ispisuje (`2.290,39 EUR`).
- Za digest: prvo ukupno stanje i promjena, pa najveći pomaci, pa upozorenja ako ih ima.
- Kratko. Bez uvodnih fraza tipa "evo pregleda vašeg portfelja".
- Ako `report.py` javi da nema snapshota, reci to i ne izmišljaj stanje.

### Telegram — bez tablica

Glavni kanal je Telegram, a on **ne renderira markdown tablice** — pretvore se u
nečitljiv niz crtica i uspravnih crta na mobitelu. Piši pozicije kao obične retke,
jedan po poziciji, poravnato razmacima:

```
Portfelj — 02.08. 10:00 UTC
Ukupno: 2.290,39 EUR
Pozicije: 2.285,39 · Cash: 5,00

49,4%  VWCEd_EQ      1.128,67 EUR  +70,79
22,9%  VFEGl_EQ        523,19 EUR  +16,95
```

Podebljanje (`*tekst*`) koristi štedljivo, najviše za ukupan iznos. Nikakvi
naslovi s `#`, nikakve tablice, nikakvi horizontalni razdjelnici.

## Kontekst koji pomaže pri tumačenju

- `check_delta_pct` uspoređuje dvije neovisne metode računanja vrijednosti pozicije.
  Odstupanje od 0,1–0,5 % je normalno (ECB tečaj je od zadnjeg radnog dana, cijene su žive).
  Odstupanje reda veličine 9900 % znači neprepoznat minor unit — to je bug, prijavi ga.
- `realized_pl_eur` je povijesni, kumulativni rezultat; `unrealized_pl_eur` je trenutni
  na otvorenim pozicijama. Ne miješaj ih u istu rečenicu bez oznake koji je koji.
- Snapshot nastaje radnim danom u 22:15 (Europe/Zagreb), 15 min nakon zatvaranja
  američkog tržišta. Vikendom su podaci od petka i to nije greška.
