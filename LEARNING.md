# Learning journal

Jedan zapis po milestoneu ili featureu, isti dan. "Što je puklo" je najvrjedniji red.

---

## 2026-08-02 — M3: Hermes agent na Telegramu

**Što sam gradio:**
Hermes Agent na Hetzneru pod vlastitim unix korisnikom, spojen na Telegram s
allowlistom, sa skillom koji mu daje pristup portfelju preko `report.py`.
Uz to `report.py` — deterministički izvještaji iz baze, bez pristupa T212 ključu.

**Što je puklo i zašto:**

1. **Tri sata na autentikaciji, a rješenje je bila jedna prijava.** Hermes je vraćao
   401 na svaki OAuth token. Rješenje: `claude` CLI je cijelo vrijeme bio instaliran
   na serveru i trebalo ga je samo **prijaviti** (`claude /login`) — Hermes onda koristi
   tu prijavu. Zalijepljeni setup-token nikad nije bio put.

2. **Krivo dijagnosticirano tri puta zaredom, uvijek uvjerljivo.** Prvo "token je u
   krivoj varijabli" (`ANTHROPIC_API_KEY` umjesto `ANTHROPIC_TOKEN`) — bio je prazan.
   Pa "kvari se pri lijepljenju kroz ssh" — duljina se stvarno razlikovala za 2 znaka,
   ali to je bio izvedeni zapis, ne token. Pa "Anthropic odbija token" — račun je bio
   posve ispravan. Svaka hipoteza je imala dokaz koji ju je naizgled podupirao.

3. **`curl | bash` guta interaktivni wizard.** Prva instalacija je išla pipeom, pa je
   stdin bila skripta a ne terminal — wizard nije imao odakle čitati odgovore. Zato smo
   ga preskočili s `--skip-setup` i onda ručno petljali oko tokena, što je i stvorilo
   cijeli problem. Ispravno: `curl -o install.sh && bash install.sh`.

4. **SSH alias je tiho pokazivao na krivi deploy key.** Server je već imao
   `Host github.com` vezan na ključ drugog projekta uz `IdentitiesOnly yes`. Poruka je
   bila "repository not found" — što navodi na krivi trag, jer zvuči kao da repo ne
   postoji. Rješenje: zaseban alias `github-zarko`.

5. **Agent je ponudio da prekrši vlastito pravilo.** Na prvom testu je uz točan
   izvještaj dodao: "želiš li svježi `--save` da povučem nove podatke s Trading212?".
   Skill je to zabranjivao, ali kao pravilo — model ga je pročitao kao preporuku.
   Popravljeno tako da je zabrana eksplicitna i bez ponuđene alternative.

**Što sada znam, a jučer nisam:**

- **Kad alat javlja grešku autentikacije, prvo provjeri kako se on očekuje autentici-
  rati, a ne kako mu ti pokušavaš dostaviti kredencijal.** Sve moje hipoteze bile su o
  načinu dostave tokena; nijedna o tome treba li token uopće.
- **Poruka o grešci je i podatak o tome što se promijenilo.** Prijelaz s
  `invalid x-api-key` na `OAuth access token is invalid` značio je da je klasifikacija
  konačno ispravna — napredak koji se lako previdi jer je i dalje 401.
- **Izolacija unix korisnikom radi u oba smjera i vrijedi je imati.** `hermes` ne može
  pročitati `/opt/zarko/.env` (T212 ključ), a `karlo` ne može pročitati Hermesov token.
  Ništa od toga nije trebalo posebnu konfiguraciju osim ispravnih prava.
- **Zabrana u promptu mora biti bez alternative.** "Ne radi X, nego predloži Y" model
  čita kao poziv da ponudi X kao opciju. Trebalo je: X se ne spominje.
- **Telegram ne renderira markdown tablice.** Lijepa tablica iz CLI-ja na mobitelu je
  niz crtica.

**Brojke:**

- Trošak jednog upita "status portfelja": **0,079 USD** — 58.877 tokena ukupno,
  5 API poziva, model Sonnet 5. Većina je cache write (25.080) na prvom pozivu.
  Dnevni digest ≈ 2,4 USD mjesečno.
- Agent je vratio **sve brojke točno**, doslovno iz `report.py`: ukupno 2.290,39 EUR,
  8 pozicija, postoci do decimale. Nijedna izmišljena, nijedna preračunata.
- Gateway: long polling, **nula novih otvorenih portova** prema van. `Linger=yes`,
  dakle preživi odjavu i reboot.
- Vrijeme: M1 (od nule do prvog snapshota) ≈ 1 h. M3 autentikacija ≈ 3 h.

**Za intervju (2 rečenice):**
Postavio sam LLM agenta koji odgovara na pitanja o portfelju preko Telegrama, uz
podjelu gdje deterministički kod računa svaku brojku a model ih samo formulira —
agent čita bazu isključivo kroz read-only sučelje i nema pristup brokerskim
kredencijalima ni na razini operacijskog sustava, što je provjereno testom a ne
pretpostavkom. Najkorisnija lekcija nije bila tehnička nego dijagnostička: tri sata
sam rješavao krivi problem jer je svaka od tri uzastopne hipoteze imala dokaz koji ju
je naizgled potvrđivao, a nijedna nije provjeravala osnovnu pretpostavku — kako alat
uopće očekuje da se autentikacija obavi.

---

## 2026-08-02 — M1: Prvi podatak (T212 → EUR → SQLite)

**Što sam gradio:**
Read-only pipeline od Trading212 API-ja do SQLite snapshota: klijent (`t212.py`), FX normalizacija u EUR (`fx_conversion.py`), sloj za bazu s unakrsnom provjerom (`db.py`), CLI (`portfolio.py`), 32 testa. Deploy na Hetzner preko git pulla s read-only deploy keyem, jer je API ključ IP-lockan na server pa se lokalno ionako ne može testirati.

**Što je puklo i zašto:**

1. **Kriva shema autentikacije.** Klijent je slao goli ključ u `Authorization` headeru. T212 sada koristi HTTP Basic — API key kao username, secret kao password. Rezultat: 401 koji je izgledao kao problem s ključem, a bio je problem s formatom. Uzrok: napisao sam klijent po sjećanju na stariju verziju API-ja umjesto da otvorim spec.

2. **Zastarjeli endpointi, ista greška.** `/equity/account/info` + `/account/cash` su spojeni u `/equity/account/summary`; `/equity/portfolio` je postao `/equity/positions`; history endpointi su dobili `equity/` prefiks. Sve tri promjene su bile u specu cijelo vrijeme.

3. **FX izvor odbio zahtjev.** `api.frankfurter.app` sada 301-redirecta na `.dev`, a odredište vraća 403 na default urllib User-Agent. Prešao sam na ECB `eurofxref-daily.xml` direktno — to je ionako izvor tih tečajeva, bez posrednika koji može pasti usred crona.

4. **SSH alias je tiho pokazivao na krivi ključ.** Server je već imao `Host github.com` vezan na deploy key drugog projekta, uz `IdentitiesOnly yes`. Deploy key vrijedi za jedan repo, pa bi clone pukao s "repository not found" — poruka koja navodi na krivi trag (kao da repo ne postoji ili nemaš pristup). Rješenje: zaseban alias `github-zarko`, bez diranja postojećeg unosa.

5. **Vlastiti test je bio kriv.** U testu za nepoznate valute sam u samom testu pozvao `.strip()` na ulazu koji je trebao pasti, pa je slučaj postao besmislen i test je pao. Greška u testu, ne u kodu — ali da je "prošao", ne bih ništa provjerio.

**Što sada znam, a jučer nisam:**

- **Uzrok sva četiri fejla je isti: pretpostavka umjesto provjere izvora.** Auth, endpointi, FX URL, SSH ključ — svaki put sam krenuo od "znam kako ovo radi". Dohvaćanje OpenAPI speca (`/_bundle/api.yaml`) riješilo je prva dva u jednom potezu. Kod integracija je čitanje speca jeftinije od jednog ciklusa debuggiranja.
- **`"GBp".upper() == "GBP"`** — zato se minor uniti moraju prepoznavati točnim podudaranjem oznake, a ne normalizacijom. Uppercase "čišćenje" ulaza ovdje tiho briše faktor 100.
- **Dvostruki izračun je jeftin, a hvata upravo ovu klasu greške.** T212 uz svaku poziciju vraća `walletImpact.currentValue` (već svedeno na valutu računa) *i* `currentPrice` u valuti instrumenta. Računanje oba puta i usporedba košta nekoliko redaka, a neprepoznat peni iskoči kao odstupanje od ~9900 %.
- **Kod deploy keyeva "repository not found" najčešće znači krivi ključ, ne krivi repo.** GitHub namjerno ne razlikuje "ne postoji" od "nemaš pristup".
- **Nepoznata valuta mora biti iznimka, ne default.** Svaki fallback na 1.0 je tiha greška koja se otkrije tek kad brojka izgleda čudno — a do tada je već u nekom izvještaju.

**Brojke:**

- 32 testa, sva prolaze; prvi snapshot: 8 pozicija, 2285.39 EUR.
- **GBX validacija na stvarnim podacima:** Invesco Physical Gold, 6517.42 GBX → **76.08 EUR**. Bez dijeljenja sa 100 bilo bi ~7616 EUR, dakle portfelj bi izgledao 4× veći nego što jest.
- Isti račun ima i prave GBP pozicije (`VFEGl_EQ`, `NUCGl_EQ`) koje su prošle bez dijeljenja — razlikovanje radi, ne primjenjuje se paušalno.
- Suma mojih EUR vrijednosti po pozicijama poklapa se u cent s T212-ovim `investments.currentValue` (2285.39) — dva neovisna puta do istog broja.
- Odstupanje dviju metoda: EUR ~0 %, GBP ~0.10 %, USD ~0.47 %. Sistematično po valuti, uzrok je starost ECB tečaja (petak) uz žive cijene (nedjelja), ne FX marža — da je marža, GBP i USD bi odstupali sličnim iznosom, a ne u omjeru 5:1.
- Prag upozorenja od 1 % ne pali se na ovom driftu, a grešku s penijima hvata s tri reda veličine rezerve.

**Za intervju (2 rečenice):**
Gradio sam read-only integraciju s brokerskim API-jem gdje su cijene dolazile u miješanim valutama, uključujući britanske penije koje izgledaju kao funte i tiho napuhuju svaki zbroj 100×; riješio sam to tako da se valuta nikad ne pogađa nego dolazi uz svaku poziciju, minor uniti se prepoznaju točnim podudaranjem oznake umjesto normalizacije, a nepoznata valuta baca iznimku umjesto da padne na 1.0. Ključna odluka bila je računati vrijednost svake pozicije dvaput neovisnim putevima i usporediti rezultate — to košta nekoliko redaka, a klasu grešaka s krivim FX faktorom pretvara iz tihog krivog broja u glasno upozorenje.
