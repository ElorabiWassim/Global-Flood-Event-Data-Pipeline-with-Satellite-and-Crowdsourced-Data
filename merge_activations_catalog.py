#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import glob
import re
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd


def sniff_delimiter(csv_path: Path) -> str:
    sample = csv_path.read_text(encoding="utf-8-sig", errors="ignore")[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,|\t")
        return dialect.delimiter
    except csv.Error:
        return ";" if sample.count(";") >= sample.count(",") else ","


def normalize_column_name(name: str) -> str:
    value = str(name).strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def normalize_df_columns(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={col: normalize_column_name(col) for col in df.columns})


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
    parsed = pd.to_datetime(text, dayfirst=True, errors="coerce")
    try:
        parsed = parsed.dt.tz_convert(None)
    except (TypeError, AttributeError):
        pass
    try:
        parsed = parsed.dt.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    return parsed


def load_one_catalog(path: Path) -> pd.DataFrame:
    delimiter = sniff_delimiter(path)
    df = pd.read_csv(path, sep=delimiter, dtype=str, encoding="utf-8-sig")
    if df.empty:
        return pd.DataFrame(columns=["code", "title", "date", "event_type", "country", "_source_file"])

    df = normalize_df_columns(df)

    code_col = first_existing_column(
        df.columns,
        ["code", "acode", "activation_code", "activationcode", "emsr_id", "ems_id", "id"],
    )
    title_col = first_existing_column(df.columns, ["title", "event_name", "name"])
    date_col = first_existing_column(df.columns, ["date", "activation_date", "event_date", "datetime"])
    event_type_col = first_existing_column(df.columns, ["event_type", "eventtype", "type", "event"])
    country_col = first_existing_column(df.columns, ["country", "countries"])

    if code_col is None:
        raise ValueError(
            f"{path.as_posix()}: missing activation code column. "
            "Expected one of code/acode/activation_code/activationcode/emsr_id/ems_id/id"
        )

    out = pd.DataFrame(index=df.index)
    out["code"] = df[code_col].fillna("").astype(str).str.upper().str.strip()
    out["title"] = df[title_col].fillna("").astype(str).str.strip() if title_col else ""
    out["event_type"] = df[event_type_col].fillna("").astype(str).str.strip() if event_type_col else ""
    out["country"] = df[country_col].fillna("").astype(str).str.strip() if country_col else ""

    if date_col:
        parsed = parse_activation_datetime(df[date_col])
    else:
        parsed = pd.to_datetime(pd.Series([None] * len(out)), errors="coerce")

    out["date"] = parsed.dt.strftime("%Y-%m-%d %H:%M:%S").fillna("")
    out["_sort_dt"] = parsed
    out["_source_file"] = path.name
    return out


def resolve_input_files(input_glob: str) -> list[Path]:
    wildcard_chars = set("*?[]")
    has_wildcards = any(ch in input_glob for ch in wildcard_chars)

    if not has_wildcards:
        literal = Path(input_glob)
        return [literal] if literal.exists() and literal.is_file() else []

    matches = [Path(p) for p in glob.glob(input_glob, recursive=True)]
    return sorted(p for p in matches if p.is_file())


def merge_catalog_parts(input_glob: str, output_path: Path) -> None:
    input_files = resolve_input_files(input_glob)
    if not input_files:
        raise FileNotFoundError(
            f"No CSV files found for glob: {input_glob}. "
            "Put all downloaded page exports in data/copernicus/catalog_pages/."
        )

    frames = []
    total_rows_loaded = 0

    for file_path in input_files:
        part = load_one_catalog(file_path)
        total_rows_loaded += int(len(part))
        frames.append(part)

    merged = pd.concat(frames, ignore_index=True)

    merged = merged[merged["code"] != ""]
    merged = merged[merged["code"].str.match(r"^EMS[RN]\d+$", na=False)]

    merged = merged.sort_values(["_sort_dt", "code"], ascending=[False, True], na_position="last")
    before_dedup = len(merged)
    merged = merged.drop_duplicates(subset=["code"], keep="first").copy()
    duplicates_removed = before_dedup - len(merged)

    merged = merged[["code", "title", "date", "event_type", "country"]]
    merged = merged.sort_values(["date", "code"], ascending=[False, True], na_position="last")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output_path, sep=";", index=False, encoding="utf-8")

    flood_count = int(merged["event_type"].str.contains("flood", case=False, na=False).sum())

    print("Catalog merge complete.")
    print(f"- input_files_loaded: {len(input_files)}")
    print(f"- rows_loaded_total: {total_rows_loaded}")
    print(f"- rows_after_dedup: {len(merged)}")
    print(f"- duplicates_removed: {duplicates_removed}")
    print(f"- flood_rows: {flood_count}")
    print(f"- output_csv: {output_path.as_posix()}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge paginated Copernicus activation CSV exports into one deduplicated catalog."
    )
    parser.add_argument(
        "--input-glob",
        default="data/copernicus/catalog_pages/*.csv",
        help="Glob for downloaded page-level CSV files.",
    )
    parser.add_argument(
        "--output",
        default="data/activations.csv",
        help="Path for merged output CSV.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    merge_catalog_parts(input_glob=args.input_glob, output_path=Path(args.output))


if __name__ == "__main__":
    main()
