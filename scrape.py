#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36 CopernicusPipeline/1.0"
)

API_BASE_URL = "https://rapidmapping.emergency.copernicus.eu/backend"
ACTIVATION_PAGE_BASE_URL = "https://mapping.emergency.copernicus.eu/activations"

DISCOVERY_STATUSES = {"", "pending", "retry", "failed", "link_missing", "ready_to_download"}
DOWNLOAD_READY_STATUSES = {"ready_to_download"}
DOWNLOAD_ALL_STATUSES = {"", "pending", "retry", "failed", "failed_download", "ready_to_download"}

REQUIRED_COLUMNS_DEFAULTS = {
    "activation_code": "",
    "status": "pending",
    "attempt_count": "0",
    "last_error": "",
    "last_attempt_at": "",
    "last_success_at": "",
    "download_url": "",
    "raw_zip_path": "",
    "in_latest_catalog": "true",
    "updated_at": "",
    "source_page_url": "",
    "discovered_links_json": "[]",
    "download_file_size": "",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_column_name(name: str) -> str:
    value = str(name).strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def normalize_df_columns(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={col: normalize_column_name(col) for col in df.columns})


def bool_text(value: object) -> str:
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "t"}:
        return "true"
    return "false"


def to_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def ensure_manifest_schema(df: pd.DataFrame) -> pd.DataFrame:
    for col, default in REQUIRED_COLUMNS_DEFAULTS.items():
        if col not in df.columns:
            df[col] = default

    for col in REQUIRED_COLUMNS_DEFAULTS:
        if col == "attempt_count":
            continue
        df[col] = df[col].fillna("").astype(str).str.strip()

    df["attempt_count"] = pd.to_numeric(df["attempt_count"], errors="coerce").fillna(0).astype(int)
    df["in_latest_catalog"] = df["in_latest_catalog"].map(bool_text)
    df.loc[df["updated_at"] == "", "updated_at"] = utc_now_iso()

    return df


def load_manifest(manifest_path: Path) -> pd.DataFrame:
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    df = pd.read_csv(manifest_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df = normalize_df_columns(df)
    df = ensure_manifest_schema(df)

    if "activation_code" not in df.columns:
        raise ValueError("activation_code column is required in manifest.")

    return df


def save_manifest_atomic(df: pd.DataFrame, manifest_path: Path) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    df.to_csv(tmp_path, index=False, encoding="utf-8")
    tmp_path.replace(manifest_path)


def parse_codes_arg(value: str | None) -> set[str] | None:
    if not value:
        return None
    codes = {part.strip().upper() for part in value.split(",") if part.strip()}
    return codes if codes else None


def build_activation_api_url(activation_code: str) -> str:
    return f"{API_BASE_URL}/dashboard-api/public-activations/?code={activation_code.upper()}"


def build_activation_page_url(activation_code: str) -> str:
    return f"{ACTIVATION_PAGE_BASE_URL}/{activation_code.upper()}/"


def fetch_json(url: str, timeout: int, retries: int, pause: float, jitter: float) -> tuple[dict | None, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }

    last_error = ""
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
            return response.json(), ""
        except Exception as exc:
            last_error = str(exc)
            sleep_s = (pause * (2 ** attempt)) + random.uniform(0, jitter)
            time.sleep(sleep_s)

    return None, last_error


def unique_preserve_order(values: list[str]) -> list[str]:
    seen = set()
    unique_values = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique_values.append(value)

    return unique_values


def extract_links_from_activation_json(activation: dict) -> list[str]:
    links: list[str] = []

    products_path = str(activation.get("productsPath") or "").strip()
    if products_path:
        links.append(products_path)

    for aoi in activation.get("aois", []) or []:
        blp_path = str(aoi.get("blpPath") or "").strip()
        if blp_path:
            links.append(blp_path)

        for product in aoi.get("products", []) or []:
            download_path = str(product.get("downloadPath") or "").strip()
            if download_path:
                links.append(download_path)

    links = unique_preserve_order(links)
    return [link for link in links if ".zip" in link.lower()]


def discover_one_activation(
    activation_code: str,
    timeout: int,
    retries: int,
    pause: float,
    jitter: float,
) -> dict:
    code = activation_code.upper()
    api_url = build_activation_api_url(code)
    source_page_url = build_activation_page_url(code)

    payload, err = fetch_json(api_url, timeout=timeout, retries=retries, pause=pause, jitter=jitter)
    if err:
        return {
            "activation_code": code,
            "ok": False,
            "status": "link_missing",
            "download_url": "",
            "source_page_url": source_page_url,
            "links": [],
            "error": f"API request failed: {api_url} -> {err}"[:2000],
        }

    if not isinstance(payload, dict):
        return {
            "activation_code": code,
            "ok": False,
            "status": "link_missing",
            "download_url": "",
            "source_page_url": source_page_url,
            "links": [],
            "error": f"Unexpected API payload type for {api_url}"[:2000],
        }

    results = payload.get("results", []) or []
    if not results:
        return {
            "activation_code": code,
            "ok": False,
            "status": "link_missing",
            "download_url": "",
            "source_page_url": source_page_url,
            "links": [],
            "error": f"No activation results from API: {api_url}"[:2000],
        }

    activation = results[0]
    all_links = extract_links_from_activation_json(activation)

    if all_links:
        return {
            "activation_code": code,
            "ok": True,
            "status": "ready_to_download",
            "download_url": all_links[0],
            "source_page_url": source_page_url,
            "links": all_links,
            "error": "",
        }

    return {
        "activation_code": code,
        "ok": False,
        "status": "link_missing",
        "download_url": "",
        "source_page_url": source_page_url,
        "links": [],
        "error": f"Activation found but no downloadable zip paths in API payload: {api_url}"[:2000],
    }


def run_discovery(args: argparse.Namespace) -> None:
    manifest_path = Path(args.manifest)
    df = load_manifest(manifest_path)

    if "in_latest_catalog" not in df.columns:
        df["in_latest_catalog"] = "true"

    codes_filter = parse_codes_arg(args.codes)

    mask = df["in_latest_catalog"].str.lower().eq("true")
    mask &= ~df["status"].str.lower().eq("downloaded")
    mask &= df["status"].str.lower().isin(DISCOVERY_STATUSES)

    if not args.overwrite_links:
        mask &= df["download_url"].str.strip().eq("")

    if codes_filter:
        mask &= df["activation_code"].str.upper().isin(codes_filter)

    target_idx = df.index[mask].tolist()
    if args.max_items is not None:
        target_idx = target_idx[: args.max_items]

    if not target_idx:
        print("Discovery: no target rows matched.")
        return

    print(f"Discovery: processing {len(target_idx)} activation(s).")

    tasks = [(idx, df.at[idx, "activation_code"].upper()) for idx in target_idx]
    results: list[tuple[int, dict]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_idx = {
            executor.submit(
                discover_one_activation,
                activation_code=code,
                timeout=args.timeout,
                retries=args.retries,
                pause=args.pause,
                jitter=args.jitter,
            ): idx
            for idx, code in tasks
        }

        for future in concurrent.futures.as_completed(future_to_idx):
            idx = future_to_idx[future]
            code = df.at[idx, "activation_code"]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "activation_code": code,
                    "ok": False,
                    "status": "link_missing",
                    "download_url": "",
                    "source_page_url": "",
                    "links": [],
                    "error": f"Unhandled discovery exception: {exc}",
                }
            results.append((idx, result))

    now = utc_now_iso()
    found_count = 0
    missing_count = 0

    for idx, result in results:
        df.at[idx, "attempt_count"] = to_int(df.at[idx, "attempt_count"]) + 1
        df.at[idx, "last_attempt_at"] = now
        df.at[idx, "updated_at"] = now
        df.at[idx, "source_page_url"] = result.get("source_page_url", "")
        df.at[idx, "discovered_links_json"] = json.dumps(result.get("links", []), ensure_ascii=True)

        if result["ok"]:
            found_count += 1
            df.at[idx, "download_url"] = result["download_url"]
            df.at[idx, "status"] = "ready_to_download"
            df.at[idx, "last_error"] = ""
            df.at[idx, "last_success_at"] = now
        else:
            missing_count += 1
            df.at[idx, "status"] = "link_missing"
            df.at[idx, "last_error"] = result["error"]

    save_manifest_atomic(df, manifest_path)

    print("Discovery complete.")
    print(f"- found_links: {found_count}")
    print(f"- link_missing: {missing_count}")
    print(f"- manifest_updated: {manifest_path.as_posix()}")


def safe_filename_from_url(url: str, activation_code: str) -> str:
    parsed = urlparse(url)
    name = Path(parsed.path).name.strip()

    if not name:
        name = f"{activation_code}.zip"
    if not name.lower().endswith(".zip"):
        name = f"{name}.zip"

    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return name


def download_file(
    url: str,
    destination_path: Path,
    timeout: int,
    retries: int,
    pause: float,
    jitter: float,
) -> tuple[bool, int, str]:
    headers = {"User-Agent": USER_AGENT}
    last_error = ""

    for attempt in range(retries + 1):
        temp_path = destination_path.with_suffix(destination_path.suffix + ".part")
        try:
            with requests.get(url, headers=headers, stream=True, timeout=timeout, allow_redirects=True) as response:
                response.raise_for_status()
                with temp_path.open("wb") as file_obj:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            file_obj.write(chunk)

            temp_path.replace(destination_path)
            file_size = destination_path.stat().st_size
            if file_size <= 0:
                raise RuntimeError("Downloaded file is empty.")

            return True, file_size, ""
        except Exception as exc:
            last_error = str(exc)
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass

            sleep_s = (pause * (2 ** attempt)) + random.uniform(0, jitter)
            time.sleep(sleep_s)

    return False, 0, last_error


def download_one_activation(
    activation_code: str,
    download_url: str,
    raw_root: Path,
    timeout: int,
    retries: int,
    pause: float,
    jitter: float,
    skip_existing: bool,
) -> dict:
    activation_dir = raw_root / activation_code
    activation_dir.mkdir(parents=True, exist_ok=True)

    filename = safe_filename_from_url(download_url, activation_code)
    destination = activation_dir / filename

    if skip_existing and destination.exists() and destination.stat().st_size > 0:
        return {
            "activation_code": activation_code,
            "ok": True,
            "status": "downloaded",
            "raw_zip_path": destination.as_posix(),
            "file_size": destination.stat().st_size,
            "error": "",
        }

    ok, file_size, error = download_file(
        url=download_url,
        destination_path=destination,
        timeout=timeout,
        retries=retries,
        pause=pause,
        jitter=jitter,
    )

    if ok:
        return {
            "activation_code": activation_code,
            "ok": True,
            "status": "downloaded",
            "raw_zip_path": destination.as_posix(),
            "file_size": file_size,
            "error": "",
        }

    return {
        "activation_code": activation_code,
        "ok": False,
        "status": "failed_download",
        "raw_zip_path": "",
        "file_size": 0,
        "error": error[:2000],
    }


def run_download(args: argparse.Namespace) -> None:
    manifest_path = Path(args.manifest)
    raw_root = Path(args.raw_root)

    df = load_manifest(manifest_path)
    codes_filter = parse_codes_arg(args.codes)

    mask = df["in_latest_catalog"].str.lower().eq("true")
    mask &= df["download_url"].str.strip().ne("")
    mask &= ~df["status"].str.lower().eq("downloaded")

    if args.all_statuses:
        mask &= df["status"].str.lower().isin(DOWNLOAD_ALL_STATUSES)
    else:
        mask &= df["status"].str.lower().isin(DOWNLOAD_READY_STATUSES)

    if codes_filter:
        mask &= df["activation_code"].str.upper().isin(codes_filter)

    target_idx = df.index[mask].tolist()
    if args.max_items is not None:
        target_idx = target_idx[: args.max_items]

    if not target_idx:
        print("Download: no target rows matched.")
        return

    print(f"Download: processing {len(target_idx)} activation(s).")

    tasks = [
        (idx, df.at[idx, "activation_code"].upper(), df.at[idx, "download_url"].strip())
        for idx in target_idx
    ]

    results: list[tuple[int, dict]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_idx = {
            executor.submit(
                download_one_activation,
                activation_code=code,
                download_url=url,
                raw_root=raw_root,
                timeout=args.timeout,
                retries=args.retries,
                pause=args.pause,
                jitter=args.jitter,
                skip_existing=args.skip_existing,
            ): idx
            for idx, code, url in tasks
        }

        for future in concurrent.futures.as_completed(future_to_idx):
            idx = future_to_idx[future]
            code = df.at[idx, "activation_code"]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "activation_code": code,
                    "ok": False,
                    "status": "failed_download",
                    "raw_zip_path": "",
                    "file_size": 0,
                    "error": f"Unhandled download exception: {exc}",
                }
            results.append((idx, result))

    now = utc_now_iso()
    success_count = 0
    failed_count = 0

    for idx, result in results:
        df.at[idx, "attempt_count"] = to_int(df.at[idx, "attempt_count"]) + 1
        df.at[idx, "last_attempt_at"] = now
        df.at[idx, "updated_at"] = now

        if result["ok"]:
            success_count += 1
            df.at[idx, "status"] = "downloaded"
            df.at[idx, "raw_zip_path"] = result["raw_zip_path"]
            df.at[idx, "download_file_size"] = str(result["file_size"])
            df.at[idx, "last_error"] = ""
            df.at[idx, "last_success_at"] = now
        else:
            failed_count += 1
            df.at[idx, "status"] = "failed_download"
            df.at[idx, "last_error"] = result["error"]

    save_manifest_atomic(df, manifest_path)

    print("Download complete.")
    print(f"- downloaded_or_already_present: {success_count}")
    print(f"- failed_download: {failed_count}")
    print(f"- manifest_updated: {manifest_path.as_posix()}")


def run_report(args: argparse.Namespace) -> None:
    manifest_path = Path(args.manifest)
    df = load_manifest(manifest_path)

    total = len(df)
    in_latest = int(df["in_latest_catalog"].str.lower().eq("true").sum())
    with_url = int(df["download_url"].str.strip().ne("").sum())
    with_file = int(df["raw_zip_path"].str.strip().ne("").sum())

    status_counts = df["status"].str.lower().value_counts().to_dict()

    print("Phase 2 Report")
    print(f"- manifest: {manifest_path.as_posix()}")
    print(f"- rows_total: {total}")
    print(f"- rows_in_latest_catalog: {in_latest}")
    print(f"- rows_with_download_url: {with_url}")
    print(f"- rows_with_raw_zip_path: {with_file}")
    print("- status_counts:")
    for status, count in status_counts.items():
        print(f"  - {status}: {int(count)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 2 Copernicus EMS: discover links and download activation zips."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    discover = sub.add_parser("discover", help="Scrape Copernicus pages and fill manifest download_url.")
    discover.add_argument(
        "--manifest",
        default="data/copernicus/manifest/activations_manifest.csv",
        help="Manifest CSV path.",
    )
    discover.add_argument("--workers", type=int, default=6, help="Parallel workers.")
    discover.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds.")
    discover.add_argument("--retries", type=int, default=3, help="Retries per request.")
    discover.add_argument("--pause", type=float, default=0.4, help="Base pause for backoff.")
    discover.add_argument("--jitter", type=float, default=0.2, help="Random jitter added to pause.")
    discover.add_argument(
        "--overwrite-links",
        action="store_true",
        help="Re-run discovery even when download_url already exists.",
    )
    discover.add_argument("--max-items", type=int, default=None, help="Limit number of activations.")
    discover.add_argument(
        "--codes",
        default=None,
        help="Comma-separated activation codes to process only these rows.",
    )

    download = sub.add_parser("download", help="Download zips for rows with discovered links.")
    download.add_argument(
        "--manifest",
        default="data/copernicus/manifest/activations_manifest.csv",
        help="Manifest CSV path.",
    )
    download.add_argument("--raw-root", default="data/raw/copernicus", help="Destination root directory.")
    download.add_argument("--workers", type=int, default=4, help="Parallel workers.")
    download.add_argument("--timeout", type=int, default=120, help="HTTP timeout in seconds.")
    download.add_argument("--retries", type=int, default=4, help="Retries per download.")
    download.add_argument("--pause", type=float, default=0.8, help="Base pause for backoff.")
    download.add_argument("--jitter", type=float, default=0.3, help="Random jitter added to pause.")
    download.add_argument("--max-items", type=int, default=None, help="Limit number of activations.")
    download.add_argument(
        "--codes",
        default=None,
        help="Comma-separated activation codes to process only these rows.",
    )
    download.add_argument(
        "--all-statuses",
        action="store_true",
        help="Download rows with any non-downloaded status if they have a URL.",
    )
    download.set_defaults(skip_existing=True)
    download.add_argument(
        "--no-skip-existing",
        dest="skip_existing",
        action="store_false",
        help="Force re-download even if file already exists.",
    )

    report = sub.add_parser("report", help="Show phase 2 status summary.")
    report.add_argument(
        "--manifest",
        default="data/copernicus/manifest/activations_manifest.csv",
        help="Manifest CSV path.",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "discover":
        run_discovery(args)
    elif args.command == "download":
        run_download(args)
    elif args.command == "report":
        run_report(args)
    else:
        parser.error("Unknown command.")


if __name__ == "__main__":
    main()