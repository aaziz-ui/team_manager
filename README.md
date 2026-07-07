# Team Manager

A small internal web app to track employee presence, assign & follow up on tasks,
and manage vacation requests — with login, a real database, and JSON export/import.

## Features

- **Presence board** — every employee's status (Available / Idle / Away from desk /
  In a meeting / Offline) visible to the whole company. Status auto-updates from
  browser activity (idle after 5 min of no activity), plus manual override buttons.
  If a browser tab is closed, the person automatically shows as Offline within ~3 minutes.
- **Tasks** — managers/admins assign tasks (title, description, due date, priority) to
  their team. Employees mark tasks Pending / In progress / Done, and either side can
  leave comments on a task (e.g. to ask a question or explain something).
- **Vacations** — employees request time off (vacation / sick / personal), their manager
  approves or rejects with an optional comment. Approved vacations show a badge on the
  presence board.
- **Admin panel** — create/edit employees, set roles (employee / manager / admin) and
  reporting lines, activate/deactivate accounts.
- **Backup** — export the entire database to a JSON file, and re-import it (merge or
  full replace) — useful for migrating between deployments or keeping offline backups.
- Works in any modern browser, no install required. Data is stored in a real
  database (PostgreSQL in production, SQLite for local dev) so it persists between
  visits and across devices — not just in the browser.

## Roles

- **Employee** — sees own tasks/status/vacation requests, sees the company presence board.
- **Manager** — everything an employee can do, plus can assign tasks to their direct
  reports (or any employee with no manager set) and approve/reject their vacation requests.
- **Admin** — everything a manager can do, plus user management and backup/restore.

## Run locally

Requires Python 3.10+.

```bash
cd team_manager
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env          # then edit SECRET_KEY / DEFAULT_ADMIN_PASSWORD if you like
export $(cat .env | xargs)    # Windows: set each var manually, or use python-dotenv

python app.py
```

Open **http://localhost:5000**. On first run the app creates a default admin account:

- username: `admin`
- password: whatever you set as `DEFAULT_ADMIN_PASSWORD` (defaults to `ChangeMe123!`)

**Log in and change that password immediately** (top-right → *account*), then go to
**Admin → Add employee** to create accounts for your team.

Locally, data is stored in `team_manager.db` (SQLite) in the project folder.

## Deploy to Render

Render deploys from a Git repository, so first push this folder to GitHub:

```bash
cd team_manager
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo>.git
git push -u origin main
```

Then, in Render:

1. Go to **New → Blueprint**, connect your GitHub repo. Render will read `render.yaml`
   in this project and automatically create:
   - a free PostgreSQL database
   - a web service running `gunicorn app:app`, wired to that database via `DATABASE_URL`
2. When prompted, set the `DEFAULT_ADMIN_PASSWORD` environment variable to something
   you'll remember (this is only used the very first time the app starts, to create
   the initial admin account).
3. Click **Apply**. First deploy takes a couple of minutes.
4. Once live, open the URL Render gives you, log in as `admin`, change the password,
   and start adding employees.

If you'd rather not use the Blueprint file, you can create the Postgres database and
web service manually in the Render dashboard — just set the web service's
**Build Command** to `pip install -r requirements.txt`, **Start Command** to
`gunicorn app:app`, and add a `DATABASE_URL` env var pointing at your database plus a
`SECRET_KEY`.

### Notes on Render's free tier

- Free web services spin down after inactivity and take ~30–60s to wake up on the
  next visit — expected, not a bug.
- Free Postgres databases on Render expire after 90 days unless upgraded. Use the
  **Export data** button in the Admin panel periodically to keep an off-platform backup,
  and use **Import** to restore it into a fresh database if needed.

## Backup & restore

Admin → **Backup & restore**:

- **Export data** downloads a single JSON file with all users, tasks, comments, and
  vacation records.
- **Import** uploads that file back in. Choose *merge* to add/update on top of existing
  data (matched by username/id), or *replace* to wipe the database and load the file
  fresh.

## Adding more employees quickly

You can also create users from the command line:

```bash
flask create-user
```

## Project structure

```
team_manager/
├── app.py                 # app factory, config, CLI
├── models.py               # User, Task, TaskComment, Vacation
├── routes/
│   ├── auth.py              # login/logout/account
│   ├── dashboard.py          # presence board + heartbeat API
│   ├── tasks.py               # task assignment/updates/comments
│   ├── vacations.py            # vacation requests/approvals
│   └── admin.py                 # user management + export/import
├── templates/               # Jinja2 HTML
├── static/css/style.css
├── static/js/app.js         # activity/idle detection + heartbeat
├── requirements.txt
├── Procfile
└── render.yaml
```

## Limitations / honest notes

- "On the computer or not" is inferred from browser activity in this web app's
  own tab (idle after 5 minutes) plus manual buttons — it can't detect activity in
  other applications on someone's PC. True desktop-level tracking would require
  installing monitoring software on each machine, which is invasive and out of scope
  here; this activity + manual-override approach is the common, privacy-respecting
  middle ground.
- The free Render Postgres tier has the 90-day expiry noted above — for permanent
  production use, a paid Postgres plan is worth it.
