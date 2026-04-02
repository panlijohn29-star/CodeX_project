# Closing Report Web App V4

This version keeps the original web app intact and adds:

- office checkboxes with all offices selected by default
- a stop button to cancel a running report
- password-protected login
- password change for the current user
- enable or disable accounts from the admin screen

## Login

Login users are now configured in [auth_users_v4.json](C:\Users\JohnPan\Documents\CodeX_project\auth_users_v4.json).

Default user:

- ID: `admin`
- Password: `admin88`

Add more users by appending entries under `users`, for example:

```json
{
  "users": [
    { "user_id": "admin", "password": "admin88", "enabled": true },
    { "user_id": "jackie", "password": "jackie123", "enabled": true },
    { "user_id": "tom", "password": "tom456", "enabled": false }
  ]
}
```

## Account features

- Any logged-in user can change their own password from the V4 homepage
- Admin can add users and enable or disable non-admin accounts
- Disabled accounts cannot log in

## Run

```powershell
pip install -r requirements.txt
python app_v4.py
```

Then open [http://localhost:5001](http://localhost:5001).

## Notes

- The original app still remains in `app.py`
- V4 run output is stored in `reports_v4/`
- Cancellation is best-effort: the app requests stop immediately and closes active DB connections when possible
