#!/usr/bin/env python3
"""Refresh vendored ISO 3166 reference data from an installed iso-codes package."""

import argparse
import json
import shutil
from pathlib import Path

COUNTRY_FILENAME = "iso_3166-1.json"
ZONE_FILENAME = "iso_3166-2.json"


def load_records(path, key):
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    records = payload.get(key)
    if not isinstance(records, list):
        raise ValueError(f"{path} does not contain a {key!r} list")
    return records


def validate(country_path, zone_path):
    countries = load_records(country_path, "3166-1")
    zones = load_records(zone_path, "3166-2")

    country_codes = set()
    for record in countries:
        alpha_2 = str(record.get("alpha_2", "")).strip().upper()
        alpha_3 = str(record.get("alpha_3", "")).strip().upper()
        name = str(record.get("name", "")).strip()
        if len(alpha_2) != 2 or len(alpha_3) != 3 or not name:
            raise ValueError(f"Invalid ISO 3166-1 record: {record!r}")
        if alpha_2 in country_codes:
            raise ValueError(f"Duplicate ISO 3166-1 code: {alpha_2}")
        country_codes.add(alpha_2)

    zone_by_code = {}
    for record in zones:
        code = str(record.get("code", "")).strip().upper()
        name = str(record.get("name", "")).strip()
        if "-" not in code or not name:
            raise ValueError(f"Invalid ISO 3166-2 record: {record!r}")
        if code in zone_by_code:
            raise ValueError(f"Duplicate ISO 3166-2 code: {code}")
        if code.split("-", 1)[0] not in country_codes:
            raise ValueError(f"ISO 3166-2 code {code!r} has no matching country")
        zone_by_code[code] = record

    for code, record in zone_by_code.items():
        parent = str(record.get("parent", "")).strip().upper() or None
        if parent is None:
            continue
        if parent not in zone_by_code:
            raise ValueError(f"ISO 3166-2 code {code!r} has unknown parent {parent!r}")
        if parent.split("-", 1)[0] != code.split("-", 1)[0]:
            raise ValueError(f"ISO 3166-2 code {code!r} has cross-country parent {parent!r}")

    return len(countries), len(zones)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path("/usr/share/iso-codes/json"),
        help="directory containing iso_3166-1.json and iso_3166-2.json",
    )
    args = parser.parse_args()

    source_country = args.source_dir / COUNTRY_FILENAME
    source_zone = args.source_dir / ZONE_FILENAME
    country_count, zone_count = validate(source_country, source_zone)

    repo_root = Path(__file__).resolve().parents[1]
    target_dir = repo_root / "app" / "data"
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_country, target_dir / COUNTRY_FILENAME)
    shutil.copy2(source_zone, target_dir / ZONE_FILENAME)

    print(
        f"ISO reference data refreshed: countries={country_count} zones={zone_count} "
        f"source={args.source_dir}"
    )


if __name__ == "__main__":
    main()
