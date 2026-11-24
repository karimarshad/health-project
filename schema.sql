-- Hartford County Healthcare Price Transparency Database Schema
-- Compatible with SQLite 3.35+

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS hospitals (
    hospital_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    hospital_name       TEXT NOT NULL UNIQUE,
    ein                 TEXT,
    location_name       TEXT,
    hospital_address    TEXT,
    type_2_npi          TEXT,
    last_updated_on     TEXT,
    schema_version      TEXT,
    source_filename     TEXT,
    drive_file_id       TEXT
);

CREATE TABLE IF NOT EXISTS cpt_codes (
    cpt_code            TEXT PRIMARY KEY,
    description         TEXT NOT NULL,
    category            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS insurers (
    insurer_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name      TEXT NOT NULL UNIQUE,
    tic_index_url       TEXT
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type         TEXT NOT NULL,  -- 'hospital_mrf' | 'insurer_tic_mrf'
    source_name         TEXT NOT NULL,
    source_month        TEXT,           -- YYYY-MM
    started_at          TEXT NOT NULL,
    completed_at        TEXT,
    rows_scanned        INTEGER DEFAULT 0,
    rows_inserted       INTEGER DEFAULT 0,
    status              TEXT NOT NULL   -- 'running' | 'completed' | 'failed'
);

CREATE TABLE IF NOT EXISTS negotiated_rates (
    rate_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    cpt_code            TEXT NOT NULL REFERENCES cpt_codes(cpt_code),
    hospital_id         INTEGER REFERENCES hospitals(hospital_id),
    provider_npi        TEXT,
    provider_name       TEXT,
    insurer_id          INTEGER NOT NULL REFERENCES insurers(insurer_id),
    raw_payer_name      TEXT NOT NULL,
    plan_name           TEXT NOT NULL DEFAULT '',
    setting             TEXT NOT NULL DEFAULT 'BOTH',
    billing_class       TEXT NOT NULL DEFAULT '',
    modifiers           TEXT NOT NULL DEFAULT '',
    gross_charge        REAL,
    discounted_cash     REAL,
    negotiated_dollar   REAL,
    negotiated_pct      REAL,
    rate_min            REAL,
    rate_max            REAL,
    methodology         TEXT,
    rate_source         TEXT NOT NULL,  -- 'hospital_mrf' | 'insurer_tic_mrf'
    effective_month     TEXT,           -- YYYY-MM
    ingested_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rates_cpt ON negotiated_rates(cpt_code);
CREATE INDEX IF NOT EXISTS idx_rates_insurer ON negotiated_rates(insurer_id);
CREATE INDEX IF NOT EXISTS idx_rates_hospital ON negotiated_rates(hospital_id);
CREATE INDEX IF NOT EXISTS idx_rates_cpt_insurer_hosp ON negotiated_rates(cpt_code, insurer_id, hospital_id);

-- Analytical view: In-network rate summary by CPT, Insurer, and Hospital
CREATE VIEW IF NOT EXISTS v_cpt_price_comparison AS
SELECT
    r.cpt_code,
    c.description AS cpt_description,
    c.category AS cpt_category,
    i.canonical_name AS insurer_name,
    COALESCE(h.hospital_name, r.provider_name, 'Unknown Provider') AS hospital_name,
    r.setting,
    r.rate_source,
    COUNT(*) AS plan_rate_count,
    ROUND(MIN(r.negotiated_dollar), 2) AS min_negotiated_rate,
    ROUND(AVG(r.negotiated_dollar), 2) AS avg_negotiated_rate,
    ROUND(MAX(r.negotiated_dollar), 2) AS max_negotiated_rate,
    ROUND(AVG(r.gross_charge), 2) AS avg_gross_charge,
    ROUND(AVG(r.discounted_cash), 2) AS avg_cash_price
FROM negotiated_rates r
JOIN cpt_codes c ON r.cpt_code = c.cpt_code
JOIN insurers i ON r.insurer_id = i.insurer_id
LEFT JOIN hospitals h ON r.hospital_id = h.hospital_id
WHERE r.negotiated_dollar IS NOT NULL AND r.negotiated_dollar > 0
GROUP BY
    r.cpt_code,
    c.description,
    c.category,
    i.canonical_name,
    COALESCE(h.hospital_name, r.provider_name, 'Unknown Provider'),
    r.setting,
    r.rate_source;

-- Analytical view: Price variation & member savings per CPT and Insurer across Hartford County hospitals
CREATE VIEW IF NOT EXISTS v_savings_opportunities AS
WITH hospital_medians AS (
    SELECT
        r.cpt_code,
        i.canonical_name AS insurer_name,
        h.hospital_name,
        AVG(r.negotiated_dollar) AS hospital_avg_rate
    FROM negotiated_rates r
    JOIN insurers i ON r.insurer_id = i.insurer_id
    JOIN hospitals h ON r.hospital_id = h.hospital_id
    WHERE r.negotiated_dollar IS NOT NULL AND r.negotiated_dollar > 0
    GROUP BY r.cpt_code, i.canonical_name, h.hospital_name
)
SELECT
    hm.cpt_code,
    c.description AS cpt_description,
    c.category AS cpt_category,
    hm.insurer_name,
    COUNT(DISTINCT hm.hospital_name) AS hospital_count,
    ROUND(MIN(hm.hospital_avg_rate), 2) AS lowest_hospital_rate,
    ROUND(AVG(hm.hospital_avg_rate), 2) AS market_avg_rate,
    ROUND(MAX(hm.hospital_avg_rate), 2) AS highest_hospital_rate,
    ROUND(MAX(hm.hospital_avg_rate) - MIN(hm.hospital_avg_rate), 2) AS max_dollar_spread,
    ROUND(MAX(hm.hospital_avg_rate) / NULLIF(MIN(hm.hospital_avg_rate), 0), 2) AS max_to_min_ratio,
    ROUND(AVG(hm.hospital_avg_rate) - MIN(hm.hospital_avg_rate), 2) AS avg_member_savings_vs_min,
    ROUND((AVG(hm.hospital_avg_rate) - MIN(hm.hospital_avg_rate)) / NULLIF(AVG(hm.hospital_avg_rate), 0) * 100.0, 1) AS avg_savings_pct
FROM hospital_medians hm
JOIN cpt_codes c ON hm.cpt_code = c.cpt_code
GROUP BY hm.cpt_code, c.description, c.category, hm.insurer_name;
