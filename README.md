# TruBluV2 - Local Operations Portal

A local-first Flask web app for Tru Blu's container unloading operations.

## What this app supports

### Worker
- Log in.
- Submit a new container job.
- Submit toolbox meeting records.
- Optionally upload issue photos.
- View only their own submissions.
- No access to owner/admin settings.

### Admin
- Review all submitted container entries.
- Approve/reject/pending statuses.
- Review toolbox meetings.
- View worker summaries.
- Cannot manage system settings, rate tables, or pay period locks.

### Owner
- Full admin visibility plus:
- Edit/delete any container job.
- Manage users/staff.
- Manage sites.
- Manage rate tables.
- Lock pay periods.
- Override calculations by editing jobs.
- Print weekly summary.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open: `http://127.0.0.1:5000`

## Default local users
- Worker: `worker1 / worker123`
- Admin: `admin1 / admin123`
- Owner: `owner1 / owner123`

## Notes
- Data is stored in local SQLite (`trublu.db`).
- Uploaded photos are stored in local `uploads/`.
- This is intended for internal local use now; can be extended to production later.
