"""Back up Truck Rate business data in the Export All Data CSV format."""

import csv
from datetime import datetime
import io
import os
import sys
import tempfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import app_v4
from platform_config import env_value


CSV_HEADER = ("pickup", "zone_set", "area_name", "group", "ftl_rate", "per_kilo_rate",
              "effective_from", "effective_to", "city", "state", "zip")


def backup_truck_rate_csv(backup_date=None):
    if backup_date is None:
        backup_date = datetime.now(ZoneInfo(env_value("TRUCK_RATE_BACKUP_TZ", "America/Chicago"))).date()
    backup_dir = os.path.abspath(env_value(
        "TRUCK_RATE_BACKUP_DIR",
        os.path.join(os.path.dirname(app_v4.TRUCK_RATE_DATA_PATH), "backups"),
    ))
    backup_path = os.path.join(backup_dir, backup_date.isoformat() + ".csv")
    with app_v4._truck_rate_file_lock():
        current = app_v4.load_truck_rate_store()
        if current is None:
            return None
        store = app_v4._migrate_truck_rate_store(current["store"])
        app_v4._validate_truck_rate_store(store)
        if os.path.exists(backup_path):
            return backup_path
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\r\n")
        writer.writerow(CSV_HEADER)
        zone_sets = {zone_set["id"]: zone_set for zone_set in store["zoneSets"]}
        periods = {}
        for rate in store["rates"]:
            periods.setdefault((rate["originId"], rate["areaId"]), []).append(rate)
        for origin in store["origins"]:
            zone_set = zone_sets[origin["zoneSetId"]]
            for area in store["areas"]:
                if area["zoneSetId"] != origin["zoneSetId"]:
                    continue
                # Every validity period is kept, oldest first; an area without any rate still gets one row.
                pair_periods = sorted(periods.get((origin["id"], area["id"]), []),
                                      key=lambda item: (item.get("effectiveFrom") or "", item.get("effectiveTo") or "9999-12-31")) or [{}]
                for rate in pair_periods:
                    for location in area["locations"] or [None]:
                        place = location or {}
                        writer.writerow((origin.get("name"), zone_set.get("name"), area.get("name"), area.get("group"),
                                         rate.get("ftlRate", ""), rate.get("perKiloRate", ""),
                                         rate.get("effectiveFrom", ""), rate.get("effectiveTo", ""),
                                         place.get("city"), place.get("state"), place.get("zip")))
        os.makedirs(backup_dir, mode=0o700, exist_ok=True)
        fd, temporary_path = tempfile.mkstemp(prefix=".truck-rate-", suffix=".csv", dir=backup_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(output.getvalue())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, backup_path)
        except Exception:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
            raise
    return backup_path


if __name__ == "__main__":
    try:
        backup_path = backup_truck_rate_csv()
    except (OSError, ValueError) as exc:
        print("Truck Rate backup failed: {0}".format(exc), file=sys.stderr)
        raise SystemExit(1)
    if backup_path is None:
        print("Truck Rate backup failed: data file does not exist", file=sys.stderr)
        raise SystemExit(1)
    print(backup_path)
