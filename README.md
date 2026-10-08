# Report Platform

A Flask-based internal report platform for running accounting and operations reports from one shared web dashboard.

## Features

- `Closing Report`: generate office closing reports as Excel files and download them as a zip package.
- `AR/AP breakdown`: search AR, AP, or combined AR/AP charge details with filters for ETD, job type, customer, and billing office. Results support query preview, sortable columns, and CSV download.
- `Offset Invoice`: generate AR/AP offset invoice upload workbooks from invoice numbers.
- `Archive Currency Invoice`: verify two currency invoices and archive them after confirmation.
- `Related Office Modification`: create related office data for a two-job HAWB after confirmation.
- `SQL Query`: approved users can run SQL, terminate active queries, export result sets to Excel, and save private `.sql` scripts.
- `Eason_DFW_Billing`: process a DFW billing workbook and generate full, DO, and NODO upload files.
- `Eason Client Report`: search AP/AR charges by JOB_NO or HBL_NO for a billing office, with CSV export.
- `ORD AE Closing Report`: review ORD air-export closing status and GP, then export results or a batch-closing file.
- `ORD_DO_ALLOCATION`: allocate carrier PRO costs across ORD DO/IWT HBL rows and generate AP/AR upload workbooks.
- `Truck Rate Manager`: manage shared areas, groups, locations, and rates, with a ZIP Map and administrator operation log.

## Requirements

- Python 3.10+
- MySQL access for the configured database profiles

Python dependencies are listed in `requirements.txt`.

## Setup

```powershell
python -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

Update `.env` with the database connection values:

```text
DB_HOST=your-mysql-host
DB_PORT=3306
DB_USER=your-user
DB_PASSWORD=your-password
SCDBUS_DATABASE=scdbus
SCDBCA_DATABASE=scdbca
```

You can also override each database profile separately with `SCDBUS_HOST`, `SCDBUS_USER`, `SCDBUS_PASSWORD`, `SCDBUS_PORT`, `SCDBCA_HOST`, `SCDBCA_USER`, `SCDBCA_PASSWORD`, and `SCDBCA_PORT`.

Set `FLASK_SECRET_KEY` to a private random value before startup; replace the example `change-me` value. Existing environment variables take precedence over `.env`. Restart the app after configuration changes. Database timeout settings are `DB_CONNECT_TIMEOUT` (10 seconds), `DB_READ_TIMEOUT` (600), and `DB_WRITE_TIMEOUT` (600). `CLOSING_REPORT_MAX_WORKERS` defaults to 4.

`ORD_DO_ALLOCATION` also requires a dedicated XC connection, in addition to `SCDBUS`:

```text
XC_DB_HOST=your-xc-mysql-host
XC_DB_PORT=3306
XC_DB_USER=your-xc-user
XC_DB_PASSWORD=your-xc-password
XC_DB_NAME=your-xc-database
```

### Truck Rate storage and ZIP Map

Shared business data defaults to `instance/truck_rate_data.json`. For deployment, set `TRUCK_RATE_DATA_PATH` to an absolute path outside the application folder, for example `C:\CodeX-data\truck_rate_data.json`, and include it in backups. The application must be able to write to that directory. Concurrent saves use revision checks to prevent stale pages from overwriting newer changes. Successful business edits record the signed-in user, UTC time, and old/new values. The Operation Log is available to the built-in admin and accounts assigned the `admin` role.

The ZIP Map uses saved location coordinates for ZIP center points. Optional ZIP boundaries require both `TRUCK_RATE_GOOGLE_MAPS_API_KEY` and `TRUCK_RATE_GOOGLE_MAP_ID`; the Map ID must be a vector Map ID with a published style enabling the **Postal Code** boundary layer. If configuration or boundary coverage is unavailable, ZIP points remain visible and the map explains the limitation. The postal-code place ID cache is system data and does not appear in the Operation Log. Keep real configuration values in `.env`.

`SQL Query` discovers each valid `*_DATABASE` or `*_DB` profile. Submitted SQL runs with the configured MySQL account's privileges; terminating a query requires MySQL `KILL QUERY` permission. Query results and private scripts are stored in the ignored `reports_v4/` runtime directory. An administrator enables SQL access per account from the dashboard.

## Run

```powershell
python app_v4.py
```

Then open:

```text
http://localhost:5001
```

On Windows, you can also double-click `start_v4_webapp.bat` after setup. It selects `py -3` or `python`, opens `/login`, and starts the app. **The batch file force-stops any process listening on port 5001 before starting.** Use `python app_v4.py` when you need to inspect a port conflict first. The app listens on `0.0.0.0:5001`; close the console or press Ctrl+C to stop it.

## Login

Users are configured in `auth_users_v4.json`; roles and feature grants are configured in `roles_v4.json`. On the first RBAC-enabled start, the platform creates `auth_users_v4.json.pre_rbac_backup.json`, converts legacy plaintext passwords to secure hashes, and leaves every non-admin account with no assigned role.

The built-in administrator ID is `admin`. Use the password configured for your deployment; startup does not create a default account or reset an existing password. If your deployment still uses the original starter password, change it after signing in.

Only `admin` can add, enable/disable, and assign roles to non-admin accounts. Create roles from **Manage roles and feature access**, then grant each role its permitted reports and tools (including SQL Query). An account with no role can sign in but cannot access any feature. Any logged-in user can change their own password.

Set `FLASK_SECRET_KEY` in `.env` before starting the platform. For HTTPS deployment, set `SESSION_COOKIE_SECURE=true`; leave it false only for local HTTP development.

## Project Structure

```text
app_v4.py                  Flask web app and API routes
features/                  Feature modules registered on the platform
templates/                 HTML templates
reports_v4/                Generated report output
platform_config.py         Environment and database configuration
run_service.py             Background run management for file-based reports
requirements.txt           Python dependencies
start_v4_webapp.bat         Windows launcher (restarts port 5001)
static/truck_rate/          Deployed Truck Rate frontend assets
instance/                  Default shared Truck Rate storage
auth_users_v4.json         Login accounts and password hashes
roles_v4.json              Roles and feature grants
feature_settings_v4.json   Dashboard feature settings
```

## API Overview

- `GET /api/features`
- `POST /api/runs`
- `GET /api/runs`
- `GET /api/runs/<run_id>`
- `POST /api/runs/<run_id>/cancel`
- `GET /download/<run_id>/<filename>`
- `POST /api/ar-ap-breakdown/preview`
- `POST /api/ar-ap-breakdown/search`
- `POST /api/offset-invoice/generate`
- `POST /api/archive-currency-invoice/lookup`
- `POST /api/archive-currency-invoice/execute`
- `POST /api/related-office/lookup`
- `POST /api/related-office/company`
- `POST /api/related-office/execute`

Legacy V4 closing report endpoints are still available:

- `POST /api/run`
- `GET /api/status/<run_id>`
- `POST /api/cancel/<run_id>`
- `GET /download/<run_id>`

## Notes

- Local `.env` values are intentionally ignored by Git.
- Generated output is stored under `reports_v4/`.
- Back up account, role, and feature settings files along with shared Truck Rate data before replacing a deployment. `AUTH_CONFIG_PATH` and `ROLE_CONFIG_PATH` can point to account and role files outside the application directory.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Python is not found or a dependency is missing | Install Python 3.10+ and run `python -m pip install -r requirements.txt` with the same interpreter used to launch the app. The Windows launcher prefers `py -3` when available. |
| `FLASK_SECRET_KEY must be configured` | Set a nonempty `FLASK_SECRET_KEY` in the project `.env` or process environment, then restart. |
| Missing database configuration or connection failure | Check the requested profile's database name, shared/per-profile credentials, network access, and MySQL permissions. ORD allocation additionally needs the XC settings above. |
| Sign-in works but no features are accessible | Ask the built-in admin to enable the account and assign a role with the required feature grants. |
| Login does not persist on local HTTP | Use `SESSION_COOKIE_SECURE=false` for local HTTP; use `true` for HTTPS deployment. |
| Port 5001 is already in use | Inspect the listener with `netstat -ano`; stop the intended process before manual startup. The batch launcher stops listeners automatically. |
| Truck Rate save conflicts or fails | Reload the latest data after a revision conflict; for write errors, check `TRUCK_RATE_DATA_PATH` and directory permissions. |
| ZIP points show but boundaries do not | Check the Maps key, vector Map ID, published Postal Code layer, and coverage for the selected ZIP. |
| SQL cannot be terminated | Check that the configured database account can issue `KILL QUERY` for the target query. |
