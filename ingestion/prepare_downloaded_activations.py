#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from zipfile import ZipFile

NESTED_POLICY_CHOICES = ("del_product_only", "del_product_plus_latest_monit", "all")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare downloaded Copernicus activations and keep only selected product families."
    )
    parser.add_argument(
        "--manifest",
        default="data/copernicus/manifest/activations_manifest_downloaded_only.csv",
        help="Manifest CSV containing downloaded activation rows.",
    )
    parser.add_argument(
        "--output",
        default="data/copernicus/manifest/downloaded_activations_inventory.csv",
        help="Output CSV inventory path.",
    )
    parser.add_argument(
        "--nested-policy",
        choices=NESTED_POLICY_CHOICES,
        default="del_product_only",
        help="Nested zip retention policy.",
    )
    parser.add_argument(
        "--force-extract",
        action="store_true",
        help="Force re-extraction even when extraction marker already exists.",
    )
    parser.add_argument(
        "--no-prune-unwanted",
        action="store_true",
        help="Do not remove unselected nested zips and files.",
    )
    parser.add_argument(
        "--drop-outer-zip",
        action="store_true",
        help="Delete outer <activation>_products.zip after processing.",
    )
    return parser.parse_args()


@dataclass
class ActivationRow:
    activation_code: str
    status: str
    raw_zip_path: Path
    raw_activation_dir: Path


def load_downloaded_rows(manifest_path: Path) -> list[ActivationRow]:
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    rows: list[ActivationRow] = []
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            status = str(row.get("status", "")).strip().lower()
            if status != "downloaded":
                continue

            activation_code = str(row.get("activation_code", "")).strip().upper()
            raw_zip_path = Path(str(row.get("raw_zip_path", "")).strip())
            raw_activation_dir = Path(str(row.get("raw_activation_dir", "")).strip())

            if not activation_code:
                continue
            if not str(raw_activation_dir):
                raw_activation_dir = raw_zip_path.parent

            rows.append(
                ActivationRow(
                    activation_code=activation_code,
                    status=status,
                    raw_zip_path=raw_zip_path,
                    raw_activation_dir=raw_activation_dir,
                )
            )

    return sorted(rows, key=lambda r: r.activation_code)


def iter_files(root: Path) -> Iterable[Path]:
    if not root.exists() or not root.is_dir():
        return []
    return (p for p in root.rglob("*") if p.is_file())


def count_by_suffix(files: Iterable[Path], suffix: str) -> int:
    suffix = suffix.lower()
    return sum(1 for p in files if p.suffix.lower() == suffix)


def parse_nested_zip_info(zip_path: Path) -> tuple[int, str, str, int] | None:
    match = re.match(r"^[A-Z0-9]+_AOI(\d+)_([A-Z]+)_(PRODUCT|MONIT\d+)_v\d+\.zip$", zip_path.name)
    if not match:
        return None

    aoi = int(match.group(1))
    family = match.group(2)
    stage = match.group(3)
    stage_order = 0 if stage == "PRODUCT" else int(stage.replace("MONIT", ""))
    return aoi, family, stage, stage_order


def nested_prefix_from_zip_name(zip_name: str) -> str:
    match = re.match(r"^(.*_)(v\d+)\.zip$", zip_name)
    if match:
        return match.group(1)
    return Path(zip_name).stem + "_"


def select_nested_keep_set(zip_paths: list[Path], nested_policy: str) -> set[Path]:
    if nested_policy == "all":
        return set(zip_paths)

    del_product = set()
    latest_del_monit: dict[int, tuple[int, Path]] = {}

    for zip_path in zip_paths:
        info = parse_nested_zip_info(zip_path)
        if info is None:
            continue
        aoi, family, stage, stage_order = info

        if family == "DEL" and stage == "PRODUCT":
            del_product.add(zip_path)
        if family == "DEL" and stage.startswith("MONIT"):
            prev = latest_del_monit.get(aoi)
            if prev is None or stage_order > prev[0]:
                latest_del_monit[aoi] = (stage_order, zip_path)

    if nested_policy == "del_product_only":
        selected = set(del_product)
    else:
        selected = set(del_product)
        selected.update(path for _, path in latest_del_monit.values())

    # If expected DEL products do not exist for this activation, keep all nested zips.
    if not selected and zip_paths:
        return set(zip_paths)

    return selected


def build_marker_payload(zip_path: Path, zip_members: int) -> dict[str, object]:
    stat = zip_path.stat()
    return {
        "zip_file": zip_path.name,
        "zip_size_bytes": stat.st_size,
        "zip_modified_ns": stat.st_mtime_ns,
        "zip_members": zip_members,
    }


def write_marker(marker_path: Path, payload: dict[str, object]) -> None:
    marker_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def marker_matches(marker_path: Path, expected: dict[str, object]) -> bool:
    if not marker_path.exists():
        return False
    try:
        marker_data = json.loads(marker_path.read_text(encoding="utf-8"))
    except Exception:
        return False

    for key, value in expected.items():
        if marker_data.get(key) != value:
            return False
    return True


def extract_zip_if_needed(zip_path: Path, target_dir: Path, force_extract: bool) -> tuple[str, int, str]:
    marker_path = target_dir / ".products_extracted.ok"
    target_dir.mkdir(parents=True, exist_ok=True)

    if not zip_path.exists() or not zip_path.is_file():
        return "missing_zip", 0, "zip file not found"

    with ZipFile(zip_path, "r") as zf:
        zip_members = len(zf.infolist())
        marker_payload = build_marker_payload(zip_path, zip_members)

        if not force_extract and marker_matches(marker_path, marker_payload):
            return "skipped_marker", zip_members, "already extracted (marker match)"

        zf.extractall(target_dir)
        write_marker(marker_path, marker_payload)
        return "extracted", zip_members, "ok"


def extract_selected_nested_zips(
    selected_zips: set[Path],
    force_extract: bool,
) -> tuple[int, int, int]:
    extracted_now = 0
    skipped_marker = 0
    missing_zip = 0

    for zip_path in sorted(selected_zips, key=lambda p: p.as_posix()):
        if not zip_path.exists() or not zip_path.is_file():
            missing_zip += 1
            continue

        with ZipFile(zip_path, "r") as zf:
            members = len(zf.infolist())

        payload = build_marker_payload(zip_path, members)
        marker_path = Path(f"{zip_path.as_posix()}.extracted.ok")

        if not force_extract and marker_matches(marker_path, payload):
            skipped_marker += 1
            continue

        with ZipFile(zip_path, "r") as zf:
            zf.extractall(zip_path.parent)
        write_marker(marker_path, payload)
        extracted_now += 1

    return extracted_now, skipped_marker, missing_zip


def remove_file_quiet(path: Path) -> int:
    try:
        path.unlink()
        return 1
    except Exception:
        return 0


def remove_empty_dirs(root: Path) -> None:
    dirs = sorted([p for p in root.rglob("*") if p.is_dir()], key=lambda p: len(p.parts), reverse=True)
    for directory in dirs:
        try:
            next(directory.iterdir())
        except StopIteration:
            try:
                directory.rmdir()
            except Exception:
                pass
        except Exception:
            pass


def prune_unwanted_files(
    activation_dir: Path,
    outer_zip_path: Path,
    keep_nested_zips: set[Path],
    keep_outer_zip: bool,
) -> tuple[int, int]:
    removed_zip_files = 0
    removed_other_files = 0

    keep_prefixes = {nested_prefix_from_zip_name(path.name) for path in keep_nested_zips}
    keep_prefixes = {prefix for prefix in keep_prefixes if prefix}

    all_files = sorted([p for p in activation_dir.rglob("*") if p.is_file()], key=lambda p: p.as_posix())
    for file_path in all_files:
        if file_path.suffix.lower() == ".zip":
            if file_path.resolve() == outer_zip_path.resolve():
                if not keep_outer_zip:
                    removed_zip_files += remove_file_quiet(file_path)
                continue

            if file_path in keep_nested_zips:
                continue

            removed_zip_files += remove_file_quiet(file_path)
            continue

        name = file_path.name
        if name == ".products_extracted.ok":
            continue

        if name.endswith(".zip.extracted.ok"):
            if any(prefix in name for prefix in keep_prefixes):
                continue
            removed_other_files += remove_file_quiet(file_path)
            continue

        # Keep only files extracted from selected nested zip prefixes.
        if keep_prefixes and any(name.startswith(prefix) for prefix in keep_prefixes):
            continue

        removed_other_files += remove_file_quiet(file_path)

    remove_empty_dirs(activation_dir)
    return removed_zip_files, removed_other_files


def process_row(
    row: ActivationRow,
    force_extract: bool,
    nested_policy: str,
    prune_unwanted: bool,
    keep_outer_zip: bool,
) -> dict[str, object]:
    zip_path = row.raw_zip_path
    activation_dir = row.raw_activation_dir

    action, zip_members, message = extract_zip_if_needed(zip_path, activation_dir, force_extract)
    nested_candidates = sorted(
        p for p in activation_dir.rglob("*.zip") if p.is_file() and p.resolve() != zip_path.resolve()
    )
    keep_nested_zips = select_nested_keep_set(nested_candidates, nested_policy=nested_policy)
    nested_extracted, nested_skipped, nested_missing = extract_selected_nested_zips(
        selected_zips=keep_nested_zips,
        force_extract=force_extract,
    )

    removed_nested_zip = 0
    removed_other_files = 0
    if prune_unwanted:
        removed_nested_zip, removed_other_files = prune_unwanted_files(
            activation_dir=activation_dir,
            outer_zip_path=zip_path,
            keep_nested_zips=keep_nested_zips,
            keep_outer_zip=keep_outer_zip,
        )

    files = list(iter_files(activation_dir))
    non_zip_files = [
        p
        for p in files
        if p.suffix.lower() != ".zip" and p.name != ".products_extracted.ok" and not p.name.endswith(".zip.extracted.ok")
    ]

    return {
        "activation_code": row.activation_code,
        "status": row.status,
        "zip_path": zip_path.as_posix(),
        "zip_exists": str(zip_path.exists()).lower(),
        "zip_size_bytes": zip_path.stat().st_size if zip_path.exists() else 0,
        "raw_activation_dir": activation_dir.as_posix(),
        "nested_policy": nested_policy,
        "prune_unwanted": str(prune_unwanted).lower(),
        "keep_outer_zip": str(keep_outer_zip).lower(),
        "extraction_action": action,
        "extraction_message": message,
        "zip_members": zip_members,
        "nested_zip_total": len(nested_candidates),
        "nested_zip_kept": len(keep_nested_zips),
        "nested_zip_extracted_now": nested_extracted,
        "nested_zip_skipped_marker": nested_skipped,
        "nested_zip_missing": nested_missing,
        "nested_zip_removed": removed_nested_zip,
        "files_removed": removed_other_files,
        "files_total": len(files),
        "files_non_zip": len(non_zip_files),
        "files_zip": count_by_suffix(files, ".zip"),
        "files_shp": count_by_suffix(non_zip_files, ".shp"),
        "files_geojson": count_by_suffix(non_zip_files, ".geojson"),
        "files_json": count_by_suffix(non_zip_files, ".json"),
        "files_tif": count_by_suffix(non_zip_files, ".tif") + count_by_suffix(non_zip_files, ".tiff"),
        "files_dbf": count_by_suffix(non_zip_files, ".dbf"),
        "files_shx": count_by_suffix(non_zip_files, ".shx"),
        "files_prj": count_by_suffix(non_zip_files, ".prj"),
        "files_xml": count_by_suffix(non_zip_files, ".xml"),
        "files_lyr": count_by_suffix(non_zip_files, ".lyr"),
        "files_sld": count_by_suffix(non_zip_files, ".sld"),
    }


def write_inventory(rows: list[dict[str, object]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        with output_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["activation_code"])
        return

    fieldnames = list(rows[0].keys())
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()

    manifest_path = Path(args.manifest)
    output_path = Path(args.output)

    downloaded_rows = load_downloaded_rows(manifest_path)
    if not downloaded_rows:
        raise RuntimeError("No downloaded rows found in manifest.")

    prune_unwanted = not args.no_prune_unwanted
    keep_outer_zip = not args.drop_outer_zip

    inventory_rows = [
        process_row(
            row,
            force_extract=args.force_extract,
            nested_policy=args.nested_policy,
            prune_unwanted=prune_unwanted,
            keep_outer_zip=keep_outer_zip,
        )
        for row in downloaded_rows
    ]
    write_inventory(inventory_rows, output_path)

    extracted = sum(1 for r in inventory_rows if r["extraction_action"] == "extracted")
    skipped = sum(1 for r in inventory_rows if r["extraction_action"] == "skipped_marker")
    missing = sum(1 for r in inventory_rows if r["extraction_action"] == "missing_zip")
    nested_total = sum(int(r["nested_zip_total"]) for r in inventory_rows)
    nested_kept = sum(int(r["nested_zip_kept"]) for r in inventory_rows)
    nested_removed = sum(int(r["nested_zip_removed"]) for r in inventory_rows)
    files_removed = sum(int(r["files_removed"]) for r in inventory_rows)

    print("Preparation complete.")
    print(f"- downloaded_rows: {len(downloaded_rows)}")
    print(f"- nested_policy: {args.nested_policy}")
    print(f"- prune_unwanted: {str(prune_unwanted).lower()}")
    print(f"- keep_outer_zip: {str(keep_outer_zip).lower()}")
    print(f"- extracted_now: {extracted}")
    print(f"- skipped_marker: {skipped}")
    print(f"- missing_zip: {missing}")
    print(f"- nested_zip_total: {nested_total}")
    print(f"- nested_zip_kept: {nested_kept}")
    print(f"- nested_zip_removed: {nested_removed}")
    print(f"- files_removed: {files_removed}")
    print(f"- inventory_csv: {output_path.as_posix()}")


if __name__ == "__main__":
    main()
