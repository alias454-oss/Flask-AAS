# app/core/locations.py
from sqlalchemy import select

from app.core.extensions import db
from app.models.country import Country
from app.models.zone import Zone


def normalize_country_code(value):
    if value is None:
        return None
    normalized = str(value).strip().upper()
    return normalized or None


def normalize_zone_code(value):
    if value is None:
        return None
    normalized = str(value).strip().upper()
    return normalized or None


def country_choices():
    rows = db.session.execute(
        select(Country.iso_code_2, Country.name)
        .where(Country.active.is_(True))
        .order_by(Country.name.asc(), Country.iso_code_2.asc())
    ).all()
    return [("", "Select country")] + [
        (country.iso_code_2, country.name) for country in rows
    ]


def _zone_rows(country_code):
    normalized = normalize_country_code(country_code)
    if not normalized:
        return []

    return db.session.execute(
        select(
            Zone.zone_id,
            Zone.country_id,
            Zone.code,
            Zone.name,
            Zone.type,
            Zone.parent_zone_id,
        )
        .join(Country, Zone.country_id == Country.country_id)
        .where(
            Country.iso_code_2 == normalized,
            Country.active.is_(True),
            Zone.active.is_(True),
        )
    ).all()


def _zone_label_map(rows):
    by_id = {row.zone_id: row for row in rows}
    cache = {}

    def label_for(row, trail=None):
        if row.zone_id in cache:
            return cache[row.zone_id]

        trail = set() if trail is None else set(trail)
        if row.zone_id in trail:
            label = row.name
        else:
            trail.add(row.zone_id)
            parent = by_id.get(row.parent_zone_id)
            if parent is None:
                label = row.name
            else:
                label = f"{label_for(parent, trail)} — {row.name}"

        cache[row.zone_id] = label
        return label

    return {row.code: label_for(row) for row in rows}


def zone_records(country_code):
    rows = _zone_rows(country_code)
    labels = _zone_label_map(rows)
    by_id = {zone.zone_id: zone for zone in rows}
    records = [
        {
            "code": zone.code,
            "name": zone.name,
            "type": zone.type,
            "parent": (
                by_id[zone.parent_zone_id].code
                if zone.parent_zone_id in by_id
                else None
            ),
            "label": labels[zone.code],
        }
        for zone in rows
    ]
    return sorted(records, key=lambda item: (item["label"].casefold(), item["code"]))


def zone_choices(country_code):
    records = zone_records(country_code)
    placeholder = "Select subdivision" if records else "No ISO subdivision available"
    return [("", placeholder)] + [(record["code"], record["label"]) for record in records]


def configure_location_choices(form):
    form.country_code.choices = country_choices()
    form.zone_code.choices = zone_choices(form.country_code.data)


def country_name(country_code):
    normalized = normalize_country_code(country_code)
    if not normalized:
        return None
    name = db.session.scalar(
        select(Country.name).where(Country.iso_code_2 == normalized)
    )
    return name if name is not None else normalized


def zone_name(zone_code):
    normalized = normalize_zone_code(zone_code)
    if not normalized:
        return None
    name = db.session.scalar(select(Zone.name).where(Zone.code == normalized))
    return name if name is not None else normalized
