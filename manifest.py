#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

import pandas as pd

MANIFEST_COLUMNS = [
    "activation_code",
    "title",
    "event_type",
    "country",
    "activation_datetime",
    "status",
    "attempt_count",
    "last_error",
    "last_attempt_at",
    "last_success_at",
    "download_url",
    "raw_zip_path",
    "raw_activation_dir",
    "source_catalog_row_hash",
    "source_catalog_path",
    "catalog_loaded_at",
    "in_latest_catalog",
    "created_at",
    "updated_at",
]

PRESERVE_ON_REFRESH = [
    "status", "attempt_count", "last_error", "last_attempt_at",
    "last_success_at", "download_url", "raw_zip_path", "raw_activation_dir",
]

TRUE_VALUES = {"1", "true", "yes", "t", "y"}

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def normalize_column_name(name: str) -> str:
    value = str(name).strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")

def normalize_df_columns(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={col: normalize_column_name(col) for col in df.columns})

def bool_to_text(value: object) -> str:
    return "true" if str(value).strip().lower() in TRUE_VALUES else "false"

def sniff_delimiter(csv_path: Path) -> str:
    sample = csv_path.read_text(encoding="utf-8-sig", errors="ignore")[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,|\t")
        return dialect.delimiter
    except csv.Error:
        return ";" if sample.count(";") >= sample.count(",") else ","
    
def first_existing_column(columns: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    col_set = set(columns)
    for candidate in candidates:
        if candidate in col_set:
            return candidate
    return None

def parse_activation_datetime(raw_series: pd.Series) -> pd.Series:
    text = raw_series.fillna("").astype(str)
    text = text.str.replace(",", " ", regex=False)
    text = text.str.replace(r"\s+", " ", regex=True).str.strip()
    parsed = pd.to_datetime(text, dayfirst=True, errors='coerce')
    try:
        parsed = parsed.dt.tz_convert(None)
    except (TypeError, AttributeError):
        pass
    try:
        parsed = parsed.dt.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    return parsed

def hash_row(row: pd.Series, columns: Iterable[str]) -> str:
    payload = {col: ("" if pd.isna(row.get(col)) else str(row.get(col)).strip()) for col in columns}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()

def ensure_manifest_schema(df: pd.DataFrame) -> pd.DataFrame:
    defaults = {
        'title': "",
        "event_type": "",
        "country": "",
        "activation_datetime": "",
        "status": "pending",
        "attempt_count": 0,
        "last_error": "",
        "last_attempt_at": "",
        "last_success_at": "",
        "download_url": "",
        "raw_zip_path": "",
        "raw_activation_dir": "",
        "source_catalog_row_hash": "",
        "source_catalog_path": "",
        "catalog_loaded_at": "",
        "in_latest_catalog": "true",
        "created_at": "",
        "updated_at": "",
    }

    for col in MANIFEST_COLUMNS:
        if col not in df.columns:
            df[col] = defaults[col]
    
    for col in MANIFEST_COLUMNS:
        if col == 'attempt_count':
            continue
        df[col] = df[col].fillna("").astype(str).str.strip()

    df["attempt_count"] = pd.to_numeric(df["attempt_count"], errors='coerce').fillna(0).astype(int)
    df['in_latest_catalog'] = df['in_latest_catalog'].map(bool_to_text)

    now = utc_now_iso()
    df.loc[df['created_at'] == "", "created_at"] = now
    df.loc[df["updated_at"] == "", "updated_at"] = now

    return df[MANIFEST_COLUMNS]

def load_catalog(catalog_path: Path) -> Tuple[pd.DataFrame, str]:
    delimiter = sniff_delimiter(catalog_path)
    df = pd.read_csv(catalog_path, sep=delimiter, dtype=str, encoding='utf-8-sig')
    df = normalize_df_columns(df)
    return df, delimiter

def build_manifest_from_catalog(
        catalog_df: pd.DataFrame,
        catalog_path: Path,
        include_non_flood: bool,
) -> pd.DataFrame:
    code_col = first_existing_column(
        catalog_df.columns,
        ["code", "acode", "activation_code", "activationcode", "emsr_id", "ems_id", "id"],
    )
    title_col = first_existing_column(catalog_df.columns, ["title", "event_name", "name"])
    date_col = first_existing_column(catalog_df.columns, ["date", "activation_date", "event_date", "datetime"])
    event_type_col = first_existing_column(catalog_df.columns, ["event_type", "type", "event"])
    country_col = first_existing_column(catalog_df.columns, ["country", "countries"])

    if code_col is None:
        raise ValueError(
            "Could not find activation code column. Expected one of: code, acode, activation_code, activationcode, emsr_id, ems_id, id"
        )

    work = pd.DataFrame(index=catalog_df.index)
    work["activation_code"] = catalog_df[code_col].fillna("").astype(str).str.upper().str.strip()
    work["title"] = catalog_df[title_col].fillna("").astype(str).str.strip() if title_col else ""
    work["event_type"] = catalog_df[event_type_col].fillna("").astype(str).str.strip() if event_type_col else ""
    work["country"] = catalog_df[country_col].fillna("").astype(str).str.strip() if country_col else ""

    if date_col:
        parsed_dt = parse_activation_datetime(catalog_df[date_col])
    else:
        parsed_dt = pd.to_datetime(pd.Series([None] * len(work)), errors="coerce")

    work["activation_datetime"] = parsed_dt.dt.strftime("%Y-%m-%d %H:%M:%S").fillna("")
    work["_sort_dt"] = parsed_dt

    work = work[work["activation_code"] != ""]
    work = work[work["activation_code"].str.match(r"^EMS[RN]\d+$", na=False)]

    if (not include_non_flood) and event_type_col:
        work = work[work["event_type"].str.contains("flood", case=False, na=False)]

    work = work.sort_values(["_sort_dt", "activation_code"], ascending=[False, True], na_position="last")
    work = work.drop_duplicates(subset=["activation_code"], keep="first").copy()

    hash_cols = ["activation_code", "title", "event_type", "country", "activation_datetime"]
    work["source_catalog_row_hash"] = work.apply(lambda row: hash_row(row, hash_cols), axis=1)

    now = utc_now_iso()
    work["status"] = "pending"
    work["attempt_count"] = 0
    work["last_error"] = ""
    work["last_attempt_at"] = ""
    work["last_success_at"] = ""
    work["download_url"] = ""
    work["raw_zip_path"] = ""
    work["raw_activation_dir"] = work["activation_code"].apply(lambda x: f"data/raw/copernicus/{x}")
    work["source_catalog_path"] = catalog_path.as_posix()
    work["catalog_loaded_at"] = now
    work["in_latest_catalog"] = "true"
    work["created_at"] = now
    work["updated_at"] = now

    work = work.drop(columns=["_sort_dt"])
    work = ensure_manifest_schema(work)
    return work

def merge_with_existing_manifest(
    new_df: pd.DataFrame,
    manifest_path: Path,
    reset: bool,
    keep_stale_rows: bool,
) -> Tuple[pd.DataFrame, Dict[str, int]]:
    now = utc_now_iso()
    stats = {
        "new_rows_from_catalog": int(len(new_df)),
        "matched_existing_rows": 0,
        "stale_rows_kept": 0,
    }

    if reset or not manifest_path.exists():
        fresh = new_df.copy()
        fresh["updated_at"] = now
        return fresh, stats

    existing = pd.read_csv(manifest_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    existing = normalize_df_columns(existing)
    existing = ensure_manifest_schema(existing)
    existing = existing.set_index("activation_code")

    merged = new_df.copy().set_index("activation_code")
    common_codes = merged.index.intersection(existing.index)
    stats["matched_existing_rows"] = int(len(common_codes))

    for code in common_codes:
        for col in PRESERVE_ON_REFRESH:
            prev = existing.at[code, col]
            if col == "attempt_count":
                merged.at[code, col] = int(prev) if str(prev).strip() != "" else merged.at[code, col]
            else:
                if str(prev).strip() != "":
                    merged.at[code, col] = prev

        prev_created = existing.at[code, "created_at"]
        if str(prev_created).strip() != "":
            merged.at[code, "created_at"] = prev_created

    merged["in_latest_catalog"] = "true"
    merged["updated_at"] = now

    if keep_stale_rows:
        stale_codes = existing.index.difference(merged.index)
        stats["stale_rows_kept"] = int(len(stale_codes))
        if len(stale_codes) > 0:
            stale = existing.loc[stale_codes].copy()
            stale["in_latest_catalog"] = "false"
            stale["updated_at"] = now
            merged = pd.concat([merged, stale], axis=0)

    merged = merged.reset_index()
    merged = ensure_manifest_schema(merged)
    merged = merged.sort_values(["status", "activation_code"], ascending=[True, True]).reset_index(drop=True)
    return merged, stats

def write_outputs(
    manifest_df: pd.DataFrame,
    manifest_path: Path,
    summary_path: Path,
    pending_path: Path,
    catalog_path: Path,
    delimiter: str,
    include_non_flood: bool,
    merge_stats: Dict[str, int],
) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    pending_path.parent.mkdir(parents=True, exist_ok=True)

    manifest_df.to_csv(manifest_path, index=False, encoding="utf-8")

    pending_mask = (
        manifest_df["in_latest_catalog"].str.lower().eq("true")
        & manifest_df["status"].str.lower().isin({"pending", "failed", "retry"})
    )
    pending_codes = (
        manifest_df.loc[pending_mask, "activation_code"]
        .dropna()
        .astype(str)
        .sort_values()
        .tolist()
    )
    pending_path.write_text("\n".join(pending_codes), encoding="utf-8")

    status_counts = manifest_df["status"].str.lower().value_counts().to_dict()
    summary = {
        "generated_at": utc_now_iso(),
        "catalog_path": catalog_path.as_posix(),
        "catalog_delimiter": delimiter,
        "only_flood_rows": (not include_non_flood),
        "manifest_path": manifest_path.as_posix(),
        "manifest_rows_total": int(len(manifest_df)),
        "manifest_rows_in_latest_catalog": int(manifest_df["in_latest_catalog"].str.lower().eq("true").sum()),
        "manifest_rows_missing_datetime": int((manifest_df["activation_datetime"] == "").sum()),
        "pending_or_retry_count": int(len(pending_codes)),
        "status_counts": {k: int(v) for k, v in status_counts.items()},
        "merge_stats": merge_stats,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def report_manifest(manifest_path: Path) -> None:
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    df = pd.read_csv(manifest_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df = normalize_df_columns(df)
    df = ensure_manifest_schema(df)

    duplicates = int(df["activation_code"].duplicated().sum())
    missing_code = int((df["activation_code"] == "").sum())
    in_latest_count = int(df["in_latest_catalog"].str.lower().eq("true").sum())
    pending_count = int(
        (
            df["in_latest_catalog"].str.lower().eq("true")
            & df["status"].str.lower().isin({"pending", "failed", "retry"})
        ).sum()
    )
    missing_dt = int((df["activation_datetime"] == "").sum())
    status_counts = df["status"].str.lower().value_counts().to_dict()

    print("Manifest Report")
    print(f"- rows_total: {len(df)}")
    print(f"- rows_in_latest_catalog: {in_latest_count}")
    print(f"- rows_pending_or_retry: {pending_count}")
    print(f"- rows_missing_activation_code: {missing_code}")
    print(f"- rows_missing_activation_datetime: {missing_dt}")
    print(f"- duplicate_activation_codes: {duplicates}")
    print("- status_counts:")
    for status, count in status_counts.items():
        print(f"  - {status}: {int(count)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 1 Copernicus EMS: build and maintain activation manifest."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build or refresh manifest from activations CSV.")
    build.add_argument("--catalog", default="data/activations.csv", help="Path to latest activations CSV.")
    build.add_argument("--manifest", default="data/copernicus/manifest/activations_manifest.csv", help="Output manifest CSV.")
    build.add_argument("--summary", default="data/copernicus/manifest/manifest_summary.json", help="Output summary JSON.")
    build.add_argument("--pending", default="data/copernicus/manifest/pending_activations.txt", help="Output pending activation list.")
    build.add_argument("--include-non-flood", action="store_true", help="Do not filter to flood-only rows.")
    build.add_argument("--reset", action="store_true", help="Ignore existing manifest and rebuild from scratch.")
    build.add_argument(
        "--drop-stale",
        action="store_true",
        help="Drop rows not present in latest catalog (default keeps stale rows with in_latest_catalog=false).",
    )

    report = sub.add_parser("report", help="Print quality report for a manifest CSV.")
    report.add_argument("--manifest", default="data/copernicus/manifest/activations_manifest.csv")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.command == "build":
        catalog_path = Path(args.catalog)
        manifest_path = Path(args.manifest)
        summary_path = Path(args.summary)
        pending_path = Path(args.pending)

        if not catalog_path.exists():
            raise FileNotFoundError(f"Catalog CSV not found: {catalog_path}")

        catalog_df, delimiter = load_catalog(catalog_path)
        new_manifest = build_manifest_from_catalog(
            catalog_df=catalog_df,
            catalog_path=catalog_path,
            include_non_flood=args.include_non_flood,
        )

        final_manifest, merge_stats = merge_with_existing_manifest(
            new_df=new_manifest,
            manifest_path=manifest_path,
            reset=args.reset,
            keep_stale_rows=(not args.drop_stale),
        )

        write_outputs(
            manifest_df=final_manifest,
            manifest_path=manifest_path,
            summary_path=summary_path,
            pending_path=pending_path,
            catalog_path=catalog_path,
            delimiter=delimiter,
            include_non_flood=args.include_non_flood,
            merge_stats=merge_stats,
        )

        pending_mask = (
            final_manifest["status"].str.lower().isin({"pending", "failed", "retry"})
            & final_manifest["in_latest_catalog"].str.lower().eq("true")
        )
        pending_count = int(pending_mask.sum())

        print("Phase 1 build complete.")
        print(f"- catalog_rows_loaded: {len(catalog_df)}")
        print(f"- manifest_rows_written: {len(final_manifest)}")
        print(f"- pending_or_retry_rows: {pending_count}")
        print(f"- manifest_csv: {manifest_path.as_posix()}")
        print(f"- summary_json: {summary_path.as_posix()}")
        print(f"- pending_txt: {pending_path.as_posix()}")

    elif args.command == "report":
        report_manifest(Path(args.manifest))


if __name__ == "__main__":
    main()