# Hartford County Healthcare Price Transparency Pipeline & Dashboard

A clean SQLite data pipeline and Streamlit investor dashboard comparing real in-network negotiated prices across Hartford County hospitals and commercial insurers for 30 high-volume CPT procedures.

## Project Structure

- `schema.sql` — Normalized SQLite schema (`hospitals`, `cpt_codes`, `insurers`, `negotiated_rates`, `ingestion_runs`) and analytical views (`v_cpt_price_comparison`, `v_savings_opportunities`).
- `config.py` — Google Drive file IDs for the 5 Hartford County hospital MRFs, the 30 target CPT codes, payer normalization rules, and public insurer TiC MRF portals.
- `pipeline.py` — Streaming ETL pipeline supporting:
  1. Resumable Google Drive API downloads for large hospital MRF files (48 MB to 2.14 GB).
  2. Chunked, low-memory CMS Hospital Price Transparency v2/v3 CSV & ZIP ingestion.
  3. Streaming CMS Transparency in Coverage (TiC) monthly insurer JSON / JSON.GZ ingestion (`ijson`).
- `app.py` — Streamlit dashboard with CPT/insurer/hospital/setting filters, price variation charts, member steering savings simulator, and CSV export.
- `hartford_prices.db` — Pre-initialized SQLite database seeded with the 30 target CPT codes and 5 Hartford County hospital records.

## Quick Start

```bash
pip install -r requirements.txt

# 1. Download the 5 hospital files from Google Drive (requires OAuth credentials.json)
#    and ingest them into hartford_prices.db:
python pipeline.py --download-drive --credentials credentials.json --ingest-hospitals

# (Alternative) If you already downloaded the Drive folder to data/hospital_files/:
python pipeline.py --ingest-hospitals

# 2. (Monthly refresh) Ingest an insurer TiC MRF JSON/GZ file:
python pipeline.py --ingest-insurer-file data/insurer_files/aetna_in_network.json.gz --insurer-name Aetna

# 3. Launch the Streamlit investor dashboard:
streamlit run app.py
```

## Target CPT Codes (30)

`27447, 27130, 29881, 29827, 29888, 64721, 66984, 49505, 47562, 45378, 45380, 45385, 43239, 70551, 72148, 73721, 73221, 74177, 71250, 70450, 76700, 77080, 93306, 93015, 95810, 97110, 80053, 80061, 85025, 83036`
