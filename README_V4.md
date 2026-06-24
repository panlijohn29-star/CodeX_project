# Report Platform

This branch upgrades the V4 closing report web app into a small Flask platform for multiple reports and internal tools.

The first platform feature is `closing_report`. It keeps the existing office selection, cancellable background run, Excel generation, zip download, login, password change, and admin account management.

## Run

```powershell
pip install -r requirements.txt
copy .env.example .env
python app_v4.py
```

Then open [http://localhost:5001](http://localhost:5001).

## Configuration

Runtime settings are loaded from environment variables. A local `.env` file is also supported and is intentionally ignored by Git.

Required database settings:

```text
DB_HOST=your-mysql-host
DB_PORT=3306
DB_USER=your-user
DB_PASSWORD=your-password
SCDBUS_DATABASE=scdbus
SCDBCA_DATABASE=scdbca
```

You can also override each profile independently with `SCDBUS_HOST`, `SCDBUS_USER`, `SCDBUS_PASSWORD`, `SCDBUS_PORT`, `SCDBCA_HOST`, `SCDBCA_USER`, `SCDBCA_PASSWORD`, and `SCDBCA_PORT`.

## Features

Features are registered through Python plugin modules. A feature declares:

- `id`, `title`, `category`, and `description`
- `input_schema`
- `validate_inputs`
- `total_tasks`
- `execute`
- optional `cancel`

The current plugin is implemented as `features/closing_report.py`.

## API

- `GET /api/features`
- `POST /api/runs`
- `GET /api/runs`
- `GET /api/runs/<run_id>`
- `POST /api/runs/<run_id>/cancel`
- `GET /download/<run_id>/<filename>`

Legacy V4 endpoints for the closing report are still available:

- `POST /api/run`
- `GET /api/status/<run_id>`
- `POST /api/cancel/<run_id>`
- `GET /download/<run_id>`

## Accounts

Login users are still configured in [auth_users_v4.json](C:\Users\JohnPan\Documents\CodeX_project\auth_users_v4.json).

Default user:

- ID: `admin`
- Password: `admin88`

Admin users can add users and enable or disable non-admin accounts from the dashboard. Any logged-in user can change their own password.

## Output

Run output is stored in `reports_v4/`. Each run writes a `manifest.json` containing platform fields such as `feature_id`, `feature_title`, `inputs_summary`, and `outputs`.
