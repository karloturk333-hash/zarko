-- Zapisnik teza: što je agent tvrdio o nekoj poziciji i kada.
--
-- ZASEBNA baza, ne portfolio.db. Razlog je granica prava, ne uredno slaganje:
-- portfolio.db drži novac i agent ga smije samo čitati. Ovo su agentove
-- vlastite bilješke, pa smije pisati — ali samo ovdje i nigdje drugdje.
--
-- Svrha je jedno načelo iz rules.yaml:
--     "Nisam pobijedio tržište dok to ne pokaže log, ne osjećaj."
-- Bez zapisa s datumom, teza je razgovor koji nestane u Telegram povijesti.

CREATE TABLE IF NOT EXISTS teze (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    zapisano_at                   TEXT NOT NULL,

    ticker                        TEXT NOT NULL,
    teza                          TEXT NOT NULL,
    protuteza                     TEXT NOT NULL,
    sto_bi_promijenilo_misljenje  TEXT NOT NULL,
    sigurnost                     TEXT NOT NULL
        CHECK (sigurnost IN ('niska', 'srednja', 'visoka')),

    -- Stanje u trenutku tvrdnje. Bez ovoga se za pola godine ne zna je li
    -- teza bila o poziciji od 8 % ili od 33 % — a to mijenja sve.
    snapshot_id                   INTEGER,
    snapshot_at                   TEXT,
    vrijednost_eur                REAL,
    udio_pct                      REAL,

    -- Popunjava se kasnije, ručno, pri pregledu. NULL = teza je još otvorena.
    ishod                         TEXT
        CHECK (ishod IS NULL OR ishod IN ('obistinila', 'promasila', 'nejasno')),
    ishod_at                      TEXT,
    biljeska                      TEXT
);

CREATE INDEX IF NOT EXISTS idx_teze_ticker ON teze (ticker, zapisano_at DESC);
CREATE INDEX IF NOT EXISTS idx_teze_otvorene ON teze (ishod, zapisano_at);

-- Otvorene teze, najstarije prve — to je red za pregled.
CREATE VIEW IF NOT EXISTS v_otvorene_teze AS
SELECT id, zapisano_at, ticker, sigurnost, udio_pct, teza
FROM teze
WHERE ishod IS NULL
ORDER BY zapisano_at;
