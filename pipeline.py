"""Hartford County Healthcare Price Transparency ETL Pipeline.

Ingests:
1. Hospital Price Transparency MRFs (CMS v2/v3 CSV or ZIP) from Google Drive
   ("Project Price Transparency/Hospital Files") or a local directory.
2. Monthly Insurer Transparency in Coverage (TiC) MRFs (JSON / JSON.GZ)
   filtered to the 30 target CPT codes and Hartford County providers.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import re
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

from config import (
    DB_PATH,
    DRIVE_HOSPITAL_FILES,
    INSURER_TIC_PORTALS,
    PAYER_NORMALIZATION_RULES,
    RAW_HOSPITAL_DIR,
    RAW_INSURER_DIR,
    TARGET_CPTS,
)

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
TARGET_CPT_SET = set(TARGET_CPTS.keys())


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_payer(raw_payer: str) -> str:
    """Map raw payer strings to canonical insurer names."""
    if not raw_payer:
        return "Unknown"
    lowered = raw_payer.strip().lower()
    for needle, canonical in PAYER_NORMALIZATION_RULES:
        if needle in lowered:
            return canonical
    return raw_payer.strip().title()


def clean_float(val: object) -> Optional[float]:
    if val is None:
        return None
    s = str(val).strip().replace("$", "").replace(",", "")
    if not s or s.lower() in {"nan", "none", "n/a", "null", ""}:
        return None
    try:
        num = float(s)
        return num if num >= 0 else None
    except ValueError:
        return None


def init_database(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Initialize SQLite schema and seed reference tables."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())

    # Seed 30 target CPT codes
    conn.executemany(
        """
        INSERT INTO cpt_codes (cpt_code, description, category)
        VALUES (?, ?, ?)
        ON CONFLICT(cpt_code) DO UPDATE SET
            description = excluded.description,
            category = excluded.category
        """,
        [(code, desc, cat) for code, (desc, cat) in TARGET_CPTS.items()],
    )

    # Seed known Hartford County hospitals from Drive folder metadata
    for h in DRIVE_HOSPITAL_FILES:
        conn.execute(
            """
            INSERT INTO hospitals (hospital_name, ein, source_filename, drive_file_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(hospital_name) DO UPDATE SET
                ein = COALESCE(excluded.ein, hospitals.ein),
                source_filename = COALESCE(excluded.source_filename, hospitals.source_filename),
                drive_file_id = COALESCE(excluded.drive_file_id, hospitals.drive_file_id)
            """,
            (h["hospital_name"], h["ein"], h["filename"], h["file_id"]),
        )

    # Seed canonical insurers
    for name, url in INSURER_TIC_PORTALS.items():
        conn.execute(
            """
            INSERT INTO insurers (canonical_name, tic_index_url)
            VALUES (?, ?)
            ON CONFLICT(canonical_name) DO UPDATE SET
                tic_index_url = COALESCE(excluded.tic_index_url, insurers.tic_index_url)
            """,
            (name, url),
        )

    conn.commit()
    return conn


def get_or_create_insurer(conn: sqlite3.Connection, canonical_name: str) -> int:
    cur = conn.execute(
        "SELECT insurer_id FROM insurers WHERE canonical_name = ?", (canonical_name,)
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO insurers (canonical_name) VALUES (?)", (canonical_name,)
    )
    return cur.lastrowid


def get_or_create_hospital(
    conn: sqlite3.Connection,
    hospital_name: str,
    location_name: str = "",
    hospital_address: str = "",
    type_2_npi: str = "",
    last_updated_on: str = "",
    schema_version: str = "",
    source_filename: str = "",
) -> int:
    # Match by exact name or substring against seeded hospitals
    cur = conn.execute("SELECT hospital_id, hospital_name FROM hospitals")
    for hid, existing_name in cur.fetchall():
        if (
            existing_name.lower() in hospital_name.lower()
            or hospital_name.lower() in existing_name.lower()
        ):
            conn.execute(
                """
                UPDATE hospitals
                SET location_name = COALESCE(NULLIF(?, ''), location_name),
                    hospital_address = COALESCE(NULLIF(?, ''), hospital_address),
                    type_2_npi = COALESCE(NULLIF(?, ''), type_2_npi),
                    last_updated_on = COALESCE(NULLIF(?, ''), last_updated_on),
                    schema_version = COALESCE(NULLIF(?, ''), schema_version),
                    source_filename = COALESCE(NULLIF(?, ''), source_filename)
                WHERE hospital_id = ?
                """,
                (
                    location_name,
                    hospital_address,
                    type_2_npi,
                    last_updated_on,
                    schema_version,
                    source_filename,
                    hid,
                ),
            )
            return hid

    cur = conn.execute(
        """
        INSERT INTO hospitals (
            hospital_name, location_name, hospital_address,
            type_2_npi, last_updated_on, schema_version, source_filename
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            hospital_name,
            location_name,
            hospital_address,
            type_2_npi,
            last_updated_on,
            schema_version,
            source_filename,
        ),
    )
    return cur.lastrowid


def download_drive_hospital_files(
    credentials_path: Path,
    token_path: Path = Path("token.json"),
    output_dir: Path = RAW_HOSPITAL_DIR,
) -> None:
    """Download >10MB hospital MRF files from Google Drive using resumable chunks."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload

    scopes = ["https://www.googleapis.com/auth/drive.readonly"]
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), scopes)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    service = build("drive", "v3", credentials=creds)
    output_dir.mkdir(parents=True, exist_ok=True)

    for item in DRIVE_HOSPITAL_FILES:
        dest = output_dir / item["filename"]
        if dest.exists() and dest.stat().st_size > 0:
            print(f"[SKIP] Already downloaded: {dest.name}")
            continue
        print(f"[DOWNLOADING] {item['filename']} (ID: {item['file_id']})...")
        request = service.files().get_media(fileId=item["file_id"])
        with open(dest, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request, chunksize=32 * 1024 * 1024)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    print(f"  Progress: {int(status.progress() * 100)}%")
        print(f"[DONE] Saved {dest}")


def _extract_matching_cpt(row: dict) -> Optional[str]:
    """Check code|1, code|2, code|3 (or billing_code) for any of the 30 target CPTs."""
    for idx in ("1", "2", "3"):
        code_col = f"code|{idx}"
        type_col = f"code|{idx}|type"
        code_val = str(row.get(code_col, "") or "").strip()
        if not code_val:
            continue
        code_type = str(row.get(type_col, "") or "").strip().upper()
        if code_val in TARGET_CPT_SET and (not code_type or code_type in {"CPT", "HCPCS"}):
            return code_val

    # Fallback for non-standard headers
    for key in ("code", "cpt", "cpt_code", "billing_code"):
        val = str(row.get(key, "") or "").strip()
        if val in TARGET_CPT_SET:
            return val
    return None


def _stream_csv_file(
    text_stream: io.TextIOBase,
    filename: str,
    conn: sqlite3.Connection,
    default_hospital_name: str,
) -> Tuple[int, int]:
    """Stream-parse a CMS v2/v3 hospital CSV (tall or wide) in constant memory."""
    reader = csv.reader(text_stream)
    try:
        row1 = next(reader)
        row2 = next(reader)
    except StopIteration:
        return 0, 0

    # Detect CMS v3 two-row metadata preamble
    if row1 and row1[0].strip().lower() == "hospital_name":
        meta = dict(zip([c.strip().lower() for c in row1], row2))
        hospital_name = meta.get("hospital_name") or default_hospital_name
        hospital_id = get_or_create_hospital(
            conn,
            hospital_name=hospital_name,
            location_name=meta.get("location_name", ""),
            hospital_address=meta.get("hospital_address", ""),
            type_2_npi=meta.get("type_2_npi", ""),
            last_updated_on=meta.get("last_updated_on", ""),
            schema_version=meta.get("version", ""),
            source_filename=filename,
        )
        header = [c.strip() for c in next(reader)]
    else:
        # Standard single-header CSV fallback
        hospital_id = get_or_create_hospital(
            conn, hospital_name=default_hospital_name, source_filename=filename
        )
        header = [c.strip() for c in row1]
        # Re-chain row2 back into processing
        text_stream_rows = [row2]

    is_tall = "payer_name" in header and "standard_charge|negotiated_dollar" in header
    wide_rate_cols = [
        c for c in header if c.startswith("standard_charge|") and c.endswith("|negotiated_dollar")
    ]

    insurer_cache: Dict[str, int] = {}
    batch: List[tuple] = []
    scanned = 0
    inserted = 0
    now_iso = utc_now_iso()
    eff_month = datetime.now(timezone.utc).strftime("%Y-%m")

    def get_ins_id(raw_payer: str) -> int:
        canon = normalize_payer(raw_payer)
        if canon not in insurer_cache:
            insurer_cache[canon] = get_or_create_insurer(conn, canon)
        return insurer_cache[canon]

    def flush_batch() -> int:
        if not batch:
            return 0
        conn.executemany(
            """
            INSERT INTO negotiated_rates (
                cpt_code, hospital_id, insurer_id, raw_payer_name, plan_name,
                setting, billing_class, modifiers, gross_charge, discounted_cash,
                negotiated_dollar, negotiated_pct, rate_min, rate_max,
                methodology, rate_source, effective_month, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        count = len(batch)
        batch.clear()
        return count

    rows_iter: Iterable[List[str]] = (
        reader if "text_stream_rows" not in locals() else iter(text_stream_rows + list(reader))
    )

    for raw_row in rows_iter:
        scanned += 1
        if len(raw_row) < len(header):
            raw_row = raw_row + [""] * (len(header) - len(raw_row))
        row = dict(zip(header, raw_row))

        cpt = _extract_matching_cpt(row)
        if not cpt:
            continue

        gross = clean_float(row.get("standard_charge|gross"))
        cash = clean_float(row.get("standard_charge|discounted_cash"))
        setting = (row.get("setting") or "BOTH").strip().upper()
        billing_class = (row.get("billing_class") or "").strip().upper()
        modifiers = (row.get("modifiers") or "").strip()
        rate_min = clean_float(row.get("standard_charge|min"))
        rate_max = clean_float(row.get("standard_charge|max"))
        methodology = (row.get("standard_charge|methodology") or "").strip()

        if is_tall:
            raw_payer = (row.get("payer_name") or "").strip()
            plan_name = (row.get("plan_name") or "").strip()
            neg_dollar = clean_float(row.get("standard_charge|negotiated_dollar"))
            neg_pct = clean_float(row.get("standard_charge|negotiated_percentage"))
            if raw_payer and (neg_dollar is not None or neg_pct is not None):
                batch.append(
                    (
                        cpt,
                        hospital_id,
                        get_ins_id(raw_payer),
                        raw_payer,
                        plan_name,
                        setting,
                        billing_class,
                        modifiers,
                        gross,
                        cash,
                        neg_dollar,
                        neg_pct,
                        rate_min,
                        rate_max,
                        methodology,
                        "hospital_mrf",
                        eff_month,
                        now_iso,
                    )
                )
        else:
            # Wide format: standard_charge|<payer>|<plan>|negotiated_dollar
            for col in wide_rate_cols:
                neg_dollar = clean_float(row.get(col))
                if neg_dollar is None:
                    continue
                parts = col.split("|")
                raw_payer = parts[1] if len(parts) >= 2 else "Unknown"
                plan_name = parts[2] if len(parts) >= 4 else ""
                batch.append(
                    (
                        cpt,
                        hospital_id,
                        get_ins_id(raw_payer),
                        raw_payer,
                        plan_name,
                        setting,
                        billing_class,
                        modifiers,
                        gross,
                        cash,
                        neg_dollar,
                        None,
                        rate_min,
                        rate_max,
                        methodology,
                        "hospital_mrf",
                        eff_month,
                        now_iso,
                    )
                )

        if len(batch) >= 5000:
            inserted += flush_batch()

    inserted += flush_batch()
    conn.commit()
    return scanned, inserted


def ingest_hospital_directory(
    conn: sqlite3.Connection, hospital_dir: Path = RAW_HOSPITAL_DIR
) -> None:
    """Ingest all hospital CSV and ZIP files in hospital_dir."""
    if not hospital_dir.exists():
        print(f"[WARN] Directory not found: {hospital_dir}")
        return

    files = sorted(list(hospital_dir.glob("*.csv")) + list(hospital_dir.glob("*.zip")))
    if not files:
        print(f"[WARN] No CSV or ZIP files found in {hospital_dir}")
        return

    for file_path in files:
        run_cur = conn.execute(
            """
            INSERT INTO ingestion_runs (source_type, source_name, source_month, started_at, status)
            VALUES ('hospital_mrf', ?, ?, ?, 'running')
            """,
            (file_path.name, datetime.now(timezone.utc).strftime("%Y-%m"), utc_now_iso()),
        )
        run_id = run_cur.lastrowid
        conn.commit()

        default_name = re.sub(r"^\d+_", "", file_path.stem).replace("_standardcharges", "").replace("-", " ").replace("_", " ").title()
        total_scanned, total_inserted = 0, 0
        try:
            if file_path.suffix.lower() == ".zip":
                with zipfile.ZipFile(file_path, "r") as zf:
                    for member in zf.namelist():
                        if member.lower().endswith(".csv"):
                            print(f"[INGEST] {file_path.name} -> {member}")
                            with zf.open(member, "r") as raw_bin:
                                text_io = io.TextIOWrapper(raw_bin, encoding="utf-8-sig", errors="replace", newline="")
                                s, i = _stream_csv_file(text_io, member, conn, default_name)
                                total_scanned += s
                                total_inserted += i
            else:
                print(f"[INGEST] {file_path.name}")
                with open(file_path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
                    total_scanned, total_inserted = _stream_csv_file(f, file_path.name, conn, default_name)

            conn.execute(
                """
                UPDATE ingestion_runs
                SET completed_at = ?, rows_scanned = ?, rows_inserted = ?, status = 'completed'
                WHERE run_id = ?
                """,
                (utc_now_iso(), total_scanned, total_inserted, run_id),
            )
            conn.commit()
            print(f"  -> Scanned {total_scanned:,} rows, inserted {total_inserted:,} target CPT rates.")
        except Exception as exc:
            conn.execute(
                "UPDATE ingestion_runs SET completed_at = ?, status = 'failed' WHERE run_id = ?",
                (utc_now_iso(), run_id),
            )
            conn.commit()
            raise exc


def ingest_insurer_tic_mrf(
    conn: sqlite3.Connection,
    file_path: Path,
    insurer_name: str,
    target_npis: Optional[set] = None,
    effective_month: Optional[str] = None,
) -> Tuple[int, int]:
    """Stream-parse a CMS Transparency in Coverage (TiC) in-network JSON or JSON.GZ file.

    Filters to TARGET_CPT_SET and optional Hartford County provider NPIs.
    """
    import ijson

    canon_insurer = normalize_payer(insurer_name)
    insurer_id = get_or_create_insurer(conn, canon_insurer)
    eff_month = effective_month or datetime.now(timezone.utc).strftime("%Y-%m")
    now_iso = utc_now_iso()

    # Map known hospital NPIs from DB if target_npis not explicitly passed
    hospital_npi_map: Dict[str, int] = {}
    for hid, npi_str in conn.execute("SELECT hospital_id, type_2_npi FROM hospitals WHERE type_2_npi IS NOT NULL"):
        for npi in re.findall(r"\b\d{10}\b", npi_str or ""):
            hospital_npi_map[npi] = hid

    if target_npis is None and hospital_npi_map:
        target_npis = set(hospital_npi_map.keys())

    opener = gzip.open if file_path.name.endswith(".gz") else open
    # Pass 1: Build provider_group_id -> NPIs map if external/top-level provider_references exist
    provider_groups: Dict[int, List[str]] = {}
    with opener(file_path, "rb") as f:
        for ref in ijson.items(f, "provider_references.item"):
            gid = ref.get("provider_group_id")
            npis = []
            for pg in ref.get("provider_groups", []):
                npis.extend([str(n) for n in pg.get("npi", [])])
            if gid is not None and npis:
                provider_groups[gid] = npis

    # Pass 2: Stream in_network items
    batch: List[tuple] = []
    scanned = 0
    inserted = 0
    with opener(file_path, "rb") as f:
        for item in ijson.items(f, "in_network.item"):
            scanned += 1
            bcode = str(item.get("billing_code", "")).strip()
            btype = str(item.get("billing_code_type", "")).strip().upper()
            if bcode not in TARGET_CPT_SET or btype not in {"CPT", "HCPCS"}:
                continue

            for neg_rate in item.get("negotiated_rates", []):
                npis_for_rate: List[str] = []
                for gid in neg_rate.get("provider_references", []):
                    npis_for_rate.extend(provider_groups.get(gid, []))
                for pg in neg_rate.get("provider_groups", []):
                    npis_for_rate.extend([str(n) for n in pg.get("npi", [])])

                if target_npis:
                    matched_npis = [n for n in npis_for_rate if n in target_npis]
                    if not matched_npis:
                        continue
                else:
                    matched_npis = npis_for_rate[:1] or [""]

                for price_obj in neg_rate.get("negotiated_prices", []):
                    rate_val = clean_float(price_obj.get("negotiated_rate"))
                    if rate_val is None or rate_val <= 0:
                        continue
                    billing_class = str(price_obj.get("billing_class", "")).upper()
                    neg_type = str(price_obj.get("negotiated_type", "")).upper()
                    service_codes = ",".join(str(s) for s in price_obj.get("service_code", []))
                    for npi in matched_npis:
                        hid = hospital_npi_map.get(npi)
                        batch.append(
                            (
                                bcode,
                                hid,
                                npi,
                                None,
                                insurer_id,
                                insurer_name,
                                "",
                                service_codes or "BOTH",
                                billing_class,
                                "",
                                None,
                                None,
                                rate_val,
                                None,
                                None,
                                None,
                                neg_type,
                                "insurer_tic_mrf",
                                eff_month,
                                now_iso,
                            )
                        )
            if len(batch) >= 5000:
                conn.executemany(
                    """
                    INSERT INTO negotiated_rates (
                        cpt_code, hospital_id, provider_npi, provider_name,
                        insurer_id, raw_payer_name, plan_name, setting, billing_class,
                        modifiers, gross_charge, discounted_cash, negotiated_dollar,
                        negotiated_pct, rate_min, rate_max, methodology, rate_source,
                        effective_month, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    batch,
                )
                inserted += len(batch)
                batch.clear()

    if batch:
        conn.executemany(
            """
            INSERT INTO negotiated_rates (
                cpt_code, hospital_id, provider_npi, provider_name,
                insurer_id, raw_payer_name, plan_name, setting, billing_class,
                modifiers, gross_charge, discounted_cash, negotiated_dollar,
                negotiated_pct, rate_min, rate_max, methodology, rate_source,
                effective_month, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        inserted += len(batch)
    conn.commit()
    return scanned, inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Hartford County Price Transparency Pipeline")
    parser.add_argument("--init-only", action="store_true", help="Initialize DB schema and seed metadata only")
    parser.add_argument("--download-drive", action="store_true", help="Download hospital files from Google Drive")
    parser.add_argument("--credentials", type=Path, default=Path("credentials.json"), help="Google OAuth client secrets JSON")
    parser.add_argument("--ingest-hospitals", action="store_true", help="Parse hospital CSV/ZIP files into SQLite")
    parser.add_argument("--hospital-dir", type=Path, default=RAW_HOSPITAL_DIR)
    parser.add_argument("--ingest-insurer-file", type=Path, help="Path to monthly insurer TiC JSON/JSON.GZ file")
    parser.add_argument("--insurer-name", type=str, help="Canonical insurer name (e.g. Aetna, Anthem BCBS)")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args()

    conn = init_database(args.db)
    print(f"[OK] Initialized database at {args.db}")

    if args.init_only:
        conn.close()
        return

    if args.download_drive:
        download_drive_hospital_files(args.credentials, output_dir=args.hospital_dir)

    if args.ingest_hospitals:
        ingest_hospital_directory(conn, args.hospital_dir)

    if args.ingest_insurer_file:
        if not args.insurer_name:
            raise ValueError("--insurer-name is required with --ingest-insurer-file")
        s, i = ingest_insurer_tic_mrf(conn, args.ingest_insurer_file, args.insurer_name)
        print(f"[INGEST INSURER] Scanned {s:,} items, inserted {i:,} target CPT rates.")

    conn.close()


if __name__ == "__main__":
    main()
