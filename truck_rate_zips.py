import math
import os
import threading

ZIP_DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "us_zip_centroids.tsv")
EARTH_MILES = 3958.7613
MILES_PER_LAT_DEGREE = 69.0
MAX_RADIUS_MILES = 250

_lock = threading.Lock()
_zips = None


def _load(path=ZIP_DATA_PATH):
    rows = []
    with open(path, "r", encoding="utf-8-sig") as handle:
        for line in handle:
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) != 5:
                continue
            try:
                rows.append({"zip": parts[0], "city": parts[1], "state": parts[2],
                             "lat": float(parts[3]), "lng": float(parts[4])})
            except ValueError:
                continue
    return rows


def all_zips():
    global _zips
    with _lock:
        if _zips is None:
            _zips = _load()
        return _zips


def great_circle_miles(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_lat = p2 - p1
    d_lng = math.radians(lng2 - lng1)
    h = math.sin(d_lat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lng / 2) ** 2
    return 2 * EARTH_MILES * math.asin(min(1.0, math.sqrt(h)))


def zips_within(lat, lng, miles, rows=None):
    """ZIP centroids within `miles` of a point, nearest first, each with its distance."""
    rows = all_zips() if rows is None else rows
    lat_span = miles / MILES_PER_LAT_DEGREE
    cos_lat = max(math.cos(math.radians(lat)), 0.01)
    lng_span = miles / (MILES_PER_LAT_DEGREE * cos_lat)
    found = []
    for row in rows:
        if abs(row["lat"] - lat) > lat_span or abs(row["lng"] - lng) > lng_span:
            continue
        distance = great_circle_miles(lat, lng, row["lat"], row["lng"])
        if distance <= miles:
            found.append({**row, "distance": round(distance, 2)})
    found.sort(key=lambda item: (item["distance"], item["zip"]))
    return found
