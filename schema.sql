-- Portfelj: snapshotovi stanja i pozicije po snapshotu.
-- Oblik prati službeni Trading212 OpenAPI spec (AccountSummary, Position, PositionWalletImpact).
--
-- PRAVILO O VALUTAMA:
--   *_native  = valuta INSTRUMENTA (GBX = peniji!) — nikad ne zbrajati
--   *_acct    = valuta RAČUNA (T212 je već konvertirao) — zbrajati samo unutar istog računa
--   *_eur     = normalizirano preko fx_conversion.py — jedino globalno agregabilno

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS snapshots (
    id                  INTEGER PRIMARY KEY,
    taken_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    account_id          INTEGER,
    account_currency    TEXT,

    -- Cash (valuta računa)
    cash_available_acct REAL,   -- availableToTrade
    cash_in_pies_acct   REAL,   -- inPies
    cash_reserved_acct  REAL,   -- reservedForOrders

    -- Investments (valuta računa)
    inv_current_value_acct REAL,
    inv_total_cost_acct    REAL,
    inv_unrealized_pl_acct REAL,
    inv_realized_pl_acct   REAL,
    total_value_acct       REAL,   -- AccountSummary.totalValue

    -- Isto, normalizirano u EUR
    cash_available_eur     REAL,
    cash_in_pies_eur       REAL,
    cash_reserved_eur      REAL,
    inv_current_value_eur  REAL,
    inv_total_cost_eur     REAL,
    inv_unrealized_pl_eur  REAL,
    inv_realized_pl_eur    REAL,
    total_value_eur        REAL,
    positions_value_eur    REAL,   -- suma po pozicijama, neovisan izračun

    -- Revizijski trag za FX
    account_fx_rate     REAL,   -- valuta računa -> EUR
    fx_source           TEXT,
    fx_date             TEXT,   -- datum ECB objave
    fx_rates_json       TEXT,   -- puni set tečajeva, da se izračun može ponoviti
    raw_json            TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    id                INTEGER PRIMARY KEY,
    snapshot_id       INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,

    ticker            TEXT NOT NULL,
    name              TEXT,
    isin              TEXT,
    currency          TEXT,   -- IZVORNA valuta instrumenta (GBX = peniji!)

    quantity          REAL NOT NULL,
    quantity_available REAL,
    quantity_in_pies  REAL,

    -- cijene u valuti instrumenta — ne zbrajati
    average_price_native REAL,
    current_price_native REAL,
    market_value_native  REAL,   -- quantity * current_price_native

    -- walletImpact: T212 je već sveo na valutu računa
    wallet_currency      TEXT,
    current_value_acct   REAL,
    total_cost_acct      REAL,
    unrealized_pl_acct   REAL,
    fx_impact_acct       REAL,

    -- normalizirano u EUR
    fx_rate_instrument   REAL,   -- valuta instrumenta -> EUR (uključuje /100 za penije)
    fx_rate_wallet       REAL,   -- valuta walleta -> EUR
    market_value_eur     REAL,   -- mjerodavno: current_value_acct * fx_rate_wallet
    market_value_eur_check REAL, -- neovisno: market_value_native * fx_rate_instrument
    check_delta_pct      REAL,   -- postotno odstupanje dviju metoda; > 1 % = sumnjivo
    total_cost_eur       REAL,
    unrealized_pl_eur    REAL,

    created_at        TEXT,   -- Position.createdAt
    raw_json          TEXT
);

CREATE INDEX IF NOT EXISTS idx_positions_snapshot ON positions(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_positions_ticker   ON positions(ticker);

-- Registar instrumenata, puni se usput iz pozicija (ne treba spori metadata endpoint).
CREATE TABLE IF NOT EXISTS instruments (
    ticker        TEXT PRIMARY KEY,
    currency_code TEXT,
    name          TEXT,
    isin          TEXT,
    type          TEXT,
    fetched_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Povijest (punjenje iz /equity/history/* je sljedeći korak).
CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY,
    t212_id     TEXT UNIQUE,   -- HistoryTransactionItem.reference
    type        TEXT,          -- WITHDRAW / DEPOSIT / FEE / TRANSFER / INTEREST_ON_FREE_CASH / LENDING_INTEREST
    ticker      TEXT,
    quantity    REAL,
    price       REAL,
    amount      REAL,
    currency    TEXT,
    amount_eur  REAL,
    fx_rate     REAL,
    executed_at TEXT,
    raw_json    TEXT
);

CREATE INDEX IF NOT EXISTS idx_transactions_ticker   ON transactions(ticker);
CREATE INDEX IF NOT EXISTS idx_transactions_executed ON transactions(executed_at);

-- Alokacija po poziciji u EUR, zadnji snapshot.
CREATE VIEW IF NOT EXISTS v_latest_allocation AS
SELECT p.ticker,
       p.name,
       p.currency,
       p.quantity,
       p.market_value_native,
       p.market_value_eur,
       ROUND(100.0 * p.market_value_eur /
             NULLIF((SELECT SUM(market_value_eur) FROM positions
                     WHERE snapshot_id = p.snapshot_id), 0), 2) AS pct_of_positions,
       p.check_delta_pct
FROM positions p
WHERE p.snapshot_id = (SELECT MAX(id) FROM snapshots)
ORDER BY p.market_value_eur DESC;

-- Pozicije kod kojih se dvije metode izračuna ne slažu — prvo mjesto za pogledati
-- kad brojka izgleda čudno (tipično: neprepoznat minor unit).
CREATE VIEW IF NOT EXISTS v_fx_sanity AS
SELECT snapshot_id, ticker, currency, market_value_native,
       market_value_eur, market_value_eur_check, check_delta_pct
FROM positions
WHERE check_delta_pct IS NOT NULL AND ABS(check_delta_pct) > 1.0
ORDER BY ABS(check_delta_pct) DESC;
