# Report Platform

A Flask-based internal report platform for running accounting and operations reports from one shared web dashboard.

## Features

- `Closing Report`: generate office closing reports as Excel files and download them as a zip package.
- `AR/AP breakdown`: search AR, AP, or combined AR/AP charge details with filters for ETD, job type, customer, and billing office. Results support query preview, sortable columns, and CSV download.
- `Archive Currency Invoice`: verify two currency invoices and archive them after confirmation.
- `Related Office Modification`: create related office data for a two-job HAWB after confirmation.

## Requirements

- Python 3.10+
- MySQL access for the configured database profiles

Python dependencies are listed in `requirements.txt`.

## Setup

```powershell
pip install -r requirements.txt
copy .env.example .env
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

## Run

```powershell
python app_v4.py
```

Then open:

```text
http://localhost:5001
```

## Login

Users are configured in `auth_users_v4.json`.

Default admin account:

```text
ID: admin
Password: admin88
```

Admin users can add users and enable or disable non-admin accounts from the dashboard. Any logged-in user can change their own password.

## Project Structure

```text
app_v4.py                  Flask web app and API routes
features/                  Feature modules registered on the platform
templates/                 HTML templates
reports_v4/                Generated report output
platform_config.py         Environment and database configuration
run_service.py             Background run management for file-based reports
requirements.txt           Python dependencies
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
