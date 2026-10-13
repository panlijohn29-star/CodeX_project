"""Builds data/us_zip_places.tsv (zip, state, city, share) from the Census 2020 ZCTA-to-place relationship file.

Download tab20_zcta520_place20_natl.txt from
https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/ and run:
    python data/build_zip_places.py path/to/tab20_zcta520_place20_natl.txt
share is the part of the ZIP area (0-1) that lies inside the place. Places covering under 5% are dropped.
"""
import csv
import os
import re
import sys

FIPS = {"01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE", "11": "DC",
        "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
        "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT",
        "31": "NE", "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
        "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
        "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI", "56": "WY", "72": "PR"}
SUFFIX = re.compile(r"\s+(city|town|village|borough|municipality|CDP|city and borough|urban county|comunidad|zona urbana|"
                    r"consolidated government|metropolitan government|unified government|metro government)(\s*\(.*\))?$", re.I)
MIN_SHARE = 0.05


def main(source):
    rows = {}
    with open(source, "r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="|"):
            zip_code, place = row["GEOID_ZCTA5_20"], row["GEOID_PLACE_20"]
            if not zip_code or not place or place[:2] not in FIPS:
                continue
            total = float(row["AREALAND_ZCTA5_20"] or 0) + float(row["AREAWATER_ZCTA5_20"] or 0)
            part = float(row["AREALAND_PART"] or 0) + float(row["AREAWATER_PART"] or 0)
            share = part / total if total else 0
            if share < MIN_SHARE:
                continue
            name = SUFFIX.sub("", row["NAMELSAD_PLACE_20"]).strip().upper()
            key = (zip_code, FIPS[place[:2]], name)
            rows[key] = max(rows.get(key, 0), share)
    target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "us_zip_places.tsv")
    with open(target, "w", encoding="utf-8", newline="\n") as out:
        for (zip_code, state, name), share in sorted(rows.items()):
            out.write("{0}\t{1}\t{2}\t{3:.2f}\n".format(zip_code, state, name, min(share, 1)))
    print(len(rows), "rows")


if __name__ == "__main__":
    main(sys.argv[1])
