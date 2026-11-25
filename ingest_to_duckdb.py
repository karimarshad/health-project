#!/usr/bin/env python3
import os
import glob
import argparse
import duckdb
import pandas as pd
from tqdm import tqdm

TARGET_CPT_CODES = {
    '27447', '27130', '29881', '29827', '29888', '64721', '66984', '49505',
    '47562', '45378', '45380', '45385', '43239', '70551', '72148', '73721',
    '73221', '74177', '71250', '70450', '76700', '77080', '93306', '93015',
    '95810', '97110', '80053', '80061', '85025', '83036'
}

CPT_CATALOG = {
    '27447': 'Total Knee Arthroplasty',
    '27130': 'Total Hip Arthroplasty',
    '29881': 'Knee Meniscectomy',
    '29827': 'Rotator Cuff Repair',
    '29888': 'ACL Reconstruction',
    '64721': 'Carpal Tunnel Release',
    '66984': 'Cataract Surgery w/ IOL',
    '49505': 'Inguinal Hernia Repair',
    '47562': 'Laparoscopic Cholecystectomy',
    '45378': 'Diagnostic Colonoscopy',
    '45380': 'Colonoscopy w/ Biopsy',
    '45385': 'Colonoscopy w/ Lesion Removal',
    '43239': 'Upper GI Endoscopy w/ Biopsy',
    '70551': 'MRI Brain w/o Contrast',
    '72148': 'MRI Lumbar Spine w/o Contrast',
    '73721': 'MRI Lower Extremity Joint',
    '73221': 'MRI Upper Extremity Joint',
    '74177': 'CT Abdomen/Pelvis w/ Contrast',
    '71250': 'CT Thorax w/o Contrast',
    '70450': 'CT Head/Brain w/o Contrast',
    '76700': 'Complete Abdominal Ultrasound',
    '77080': 'DEXA Bone Density Scan',
    '93306': 'Complete Echocardiography',
    '93015': 'Cardiovascular Stress Test',
    '95810': 'Polysomnography (Sleep Study)',
    '97110': 'Physical Therapy (15 min)',
    '80053': 'Comprehensive Metabolic Panel',
    '80061': 'Lipid Panel',
    '85025': 'Complete Blood Count (CBC)',
    '83036': 'Hemoglobin A1c'
}

def init_database(db_path: str):
    """Initializes DuckDB schema and audit metadata table."""
    con = duckdb.connect(db_path)
    con.execute("""
    CREATE TABLE IF NOT EXISTS negotiated_rates (
        cpt_code VARCHAR,
        procedure_name VARCHAR,
        hospital_name VARCHAR,
        payer_name VARCHAR,
        plan_name VARCHAR,
        billing_class VARCHAR,
        negotiated_rate DOUBLE,
        gross_charge DOUBLE,
        discounted_cash_rate DOUBLE,
        source_file VARCHAR
    );

    CREATE TABLE IF NOT EXISTS ingestion_manifest (
        source_file VARCHAR PRIMARY KEY,
        hospital_name VARCHAR,
        records_loaded INTEGER,
        last_ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_cpt ON negotiated_rates(cpt_code);
    CREATE INDEX IF NOT EXISTS idx_payer ON negotiated_rates(payer_name);
    CREATE INDEX IF NOT EXISTS idx_source ON negotiated_rates(source_file);
    """)
    con.close()

def infer_hospital_name(filename: str) -> str:
    fn = os.path.basename(filename).lower()
    if 'hartford' in fn:
        return 'Hartford Hospital'
    elif 'central_connecticut' in fn or 'hocc' in fn:
        return 'Hospital of Central Connecticut'
    elif 'manchester' in fn:
        return 'Manchester Memorial Hospital'
    elif 'saint-francis' in fn or 'saint_francis' in fn:
        return 'Saint Francis Hospital'
    elif 'johnson' in fn:
        return 'Johnson Memorial Hospital'
    return os.path.splitext(os.path.basename(filename))[0]

def detect_skip_rows(filepath: str) -> int:
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for idx in range(10):
            line = f.readline()
            if any(k in line.lower() for k in ['code|1', 'code', 'standard_charge|negotiated_dollar', 'standard_charge']):
                return idx
    return 2

def process_csv_file(filepath: str, db_path: str, chunk_size: int = 50000, force_reload: bool = False):
    source_filename = os.path.basename(filepath)
    hospital_name = infer_hospital_name(filepath)

    con = duckdb.connect(db_path)

    # 1. Deduplication Gate: Check if file was already ingested
    already_processed = con.execute(
        "SELECT COUNT(*) FROM ingestion_manifest WHERE source_file = ?", [source_filename]
    ).fetchone()[0]

    if already_processed and not force_reload:
        print(f"Skipping {source_filename} (already ingested). Use --force to reload.")
        con.close()
        return

    # 2. Idempotent Purge: Clean out previous rows for this specific file
    con.execute("DELETE FROM negotiated_rates WHERE source_file = ?", [source_filename])
    con.execute("DELETE FROM ingestion_manifest WHERE source_file = ?", [source_filename])

    skip_rows = detect_skip_rows(filepath)
    total_loaded = 0

    try:
        reader = pd.read_csv(
            filepath,
            skiprows=skip_rows,
            chunksize=chunk_size,
            low_memory=False,
            dtype=str,
            on_bad_lines='skip'
        )
    except Exception as e:
        print(f"Skipping {filepath} due to read error: {e}")
        con.close()
        return

    for chunk in reader:
        code_col = next((c for c in ['code|1', 'code', 'billing_code', 'Code'] if c in chunk.columns), None)
        rate_col = next((c for c in ['standard_charge|negotiated_dollar', 'standard_charge', 'negotiated_rate', 'price'] if c in chunk.columns), None)

        if not code_col or not rate_col:
            continue

        chunk[code_col] = chunk[code_col].astype(str).str.strip()
        filtered = chunk[chunk[code_col].isin(TARGET_CPT_CODES)].copy()

        if filtered.empty:
            continue

        payer_col = next((c for c in ['payer_name', 'payer', 'Payer Name'] if c in filtered.columns), None)
        plan_col = next((c for c in ['plan_name', 'plan', 'Plan Name'] if c in filtered.columns), None)
        class_col = next((c for c in ['billing_class', 'setting', 'Billing Class'] if c in filtered.columns), None)
        gross_col = next((c for c in ['standard_charge|gross', 'gross_charge'] if c in filtered.columns), None)
        cash_col = next((c for c in ['standard_charge|discounted_cash', 'discounted_cash'] if c in filtered.columns), None)

        rows = []
        for _, row in filtered.iterrows():
            cpt = row[code_col]
            rate = pd.to_numeric(row[rate_col], errors='coerce')
            if pd.isna(rate) or rate <= 0:
                continue

            rows.append({
                'cpt_code': cpt,
                'procedure_name': CPT_CATALOG.get(cpt, 'Unknown Procedure'),
                'hospital_name': hospital_name,
                'payer_name': str(row[payer_col]).strip() if payer_col and pd.notna(row[payer_col]) else 'Commercial / Default',
                'plan_name': str(row[plan_col]).strip() if plan_col and pd.notna(row[plan_col]) else '',
                'billing_class': str(row[class_col]).upper().strip() if class_col and pd.notna(row[class_col]) else 'FACILITY',
                'negotiated_rate': float(rate),
                'gross_charge': pd.to_numeric(row[gross_col], errors='coerce') if gross_col else None,
                'discounted_cash_rate': pd.to_numeric(row[cash_col], errors='coerce') if cash_col else None,
                'source_file': source_filename
            })

        if rows:
            df_insert = pd.DataFrame(rows)
            con.register("temp_chunk", df_insert)
            con.execute("INSERT INTO negotiated_rates SELECT * FROM temp_chunk")
            con.unregister("temp_chunk")
            total_loaded += len(rows)

    # 3. Log into manifest
    con.execute(
        "INSERT INTO ingestion_manifest (source_file, hospital_name, records_loaded, last_ingested_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
        [source_filename, hospital_name, total_loaded]
    )

    con.close()
    print(f"Loaded {total_loaded} records for {hospital_name} from {source_filename}")

def main():
    parser = argparse.ArgumentParser(description="Ingest Hospital Price Transparency CSVs into DuckDB")
    parser.add_argument("--data-dir", default="./hospital_files", help="Path to folder containing CSV files")
    parser.add_argument("--db-path", default="./hartford_rates.duckdb", help="Target DuckDB database file path")
    parser.add_argument("--force", action="store_true", help="Force re-ingestion of already processed files")
    args = parser.parse_args()

    init_database(args.db_path)

    csv_files = glob.glob(os.path.join(args.data_dir, "**/*.csv"), recursive=True)
    if not csv_files:
        print(f"No CSV files found in {args.data_dir}")
        return

    print(f"Found {len(csv_files)} files to evaluate.")
    for file in tqdm(csv_files, desc="Processing files"):
        process_csv_file(file, args.db_path, force_reload=args.force)

if __name__ == "__main__":
    main()