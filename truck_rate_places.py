import os
import re
import threading

import truck_rate_zips

PLACES_DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "us_zip_places.tsv")
MAX_RESULTS = 300
WORD_FORMS = {"ST": "SAINT", "STE": "SAINTE", "FT": "FORT", "MT": "MOUNT", "PT": "POINT"}

_lock = threading.Lock()
_index = None


def normalize_city(value):
    """Upper case, no punctuation, and ST/FT/MT spelled out so "St. Louis" finds "SAINT LOUIS"."""
    text = re.sub(r"[.'’`]", "", str(value or "").upper().replace("&", " AND "))
    text = re.sub(r"[^A-Z0-9]+", " ", text).strip()
    return " ".join(WORD_FORMS.get(word, word) for word in text.split())


def _build():
    centroids = {row["zip"]: row for row in truck_rate_zips.all_zips()}
    entries = {}
    for row in centroids.values():
        entries[(row["zip"], row["state"], row["city"])] = {**row, "primary": True, "share": None}
    try:
        with open(PLACES_DATA_PATH, "r", encoding="utf-8-sig") as handle:
            for line in handle:
                parts = line.rstrip("\r\n").split("\t")
                if len(parts) != 4 or parts[0] not in centroids:
                    continue
                zip_code, state, city, share = parts[0], parts[1], parts[2], float(parts[3])
                existing = entries.get((zip_code, state, city))
                if existing:
                    existing["share"] = share
                    continue
                centroid = centroids[zip_code]
                entries[(zip_code, state, city)] = {"zip": zip_code, "city": city, "state": state,
                                                    "lat": centroid["lat"], "lng": centroid["lng"],
                                                    "primary": False, "share": share}
    except OSError:
        pass  # without the place file, only each ZIP's main city is known
    by_zip, by_city = {}, {}
    for entry in entries.values():
        entry["key"] = normalize_city(entry["city"])
        by_zip.setdefault(entry["zip"], []).append(entry)
        by_city.setdefault(entry["key"], []).append(entry)
    cities = sorted(by_city)
    return {"by_zip": by_zip, "by_city": by_city, "cities": cities}


def index():
    global _index
    with _lock:
        if _index is None:
            _index = _build()
        return _index


def _order(entry):
    return (not entry["primary"], -(entry["share"] or 0), entry["state"], entry["zip"])


def find_places(city="", state="", zip_code=""):
    """Every City/State/ZIP combination that matches whatever was filled in."""
    data = index()
    city_key = normalize_city(city)
    state = str(state or "").strip().upper()
    zip_code = str(zip_code or "").strip()
    if zip_code:
        found = data["by_zip"].get(zip_code, [])
    elif city_key:
        found = data["by_city"].get(city_key, [])
    else:
        return []
    if city_key and zip_code:
        found = [item for item in found if item["key"] == city_key]
    if state:
        found = [item for item in found if item["state"] == state]
    found = sorted(found, key=_order)
    return [{k: v for k, v in item.items() if k != "key"} for item in found[:MAX_RESULTS]]


def suggest_cities(prefix, state="", limit=12):
    """City names starting with `prefix`, each with the states that have one."""
    data = index()
    key = normalize_city(prefix)
    if len(key) < 2:
        return []
    state = str(state or "").strip().upper()
    from bisect import bisect_left
    names = data["cities"]
    out = []
    position = bisect_left(names, key)
    while position < len(names) and names[position].startswith(key) and len(out) < limit:
        entries = data["by_city"][names[position]]
        states = sorted({item["state"] for item in entries if not state or item["state"] == state})
        if states:
            out.append({"city": entries[0]["city"], "states": states})
        position += 1
    return out
