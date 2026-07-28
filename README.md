# Team Manager

A small internal web app to track employee presence, assign & follow up on tasks,
and manage vacation requests — with login, a real database, and JSON export/import.

## Features

- **Presence board** — every employee's status (Available / Idle / Away from desk /
  In a meeting / Offline), each with its own color. Status auto-updates from browser
  activity (idle after 5 min of no activity). If the browser tab is closed, the person
  automatically shows as Offline within ~3 minutes.
- **Manual status + "hold"** — anyone can manually set their status to Available, Away
  from desk, In a meeting, or Offline. Checking "hold this status" keeps it fixed even
  while the tab is minimized or the person is idle — it only changes when they manually
  update it again (or actually close the browser, which always shows as Offline).
- **Tasks** — managers/admins assign tasks (title, description, due date, priority) to
  their team. Employees update a completion percentage (which auto-syncs with
  Pending/In progress/Done status) and either side can leave comments on a task (e.g. to
  ask a question or explain something). **Admins can edit or delete any task.** Tasks are
  grouped by due date everywhere they're listed, so it reads like an agenda rather than
  one long flat list. A calendar date-picker lets anyone browse "what's due on this date" —
  employees see their own, managers see their team's, admins see everything company-wide,
  plus a full always-visible company-wide task table for admins.
- **Daily hours report (admin only)** — Admin → Hours shows, per employee per day, when they
  started and ended (in Pacific Time, like a time clock — flagged in red if they started after
  official office hours began), an "On-desk total" (Available + Idle + In a meeting combined),
  plus how many hours were spent in each individual status **within office hours only** (the
  counter starts when the office opens and stops when it closes, based on Admin → Settings). A
  search dropdown lets you jump straight to any one employee or manager's hours for any day. A
  **"Reset hours for this day"** button (with a confirmation prompt) lets an admin wipe tracked
  hours for a specific day — for everyone, or just the currently-selected employee.
- **Daily reports** — every employee can submit a quick end-of-day summary ("what I worked on today"), editable only on the same day it was submitted — once the day passes, it's locked for them. A calendar/date picker lets anyone browse past reports by date: employees see only their own, managers see their team's, admins see everyone's company-wide, along with who hasn't submitted yet for that day. Only the first submission of the day triggers a notification to the manager, not every edit.
- **Admin full override** — admins can edit or delete *any* task, daily report, or vacation request belonging to *any* manager or employee, regardless of who created it or its current status — useful for correcting mistakes. Managers keep their narrower permissions (their own assigned tasks, their team's pending vacation approvals).
- **Notifications** — a bell icon in the navbar (with a live unread count) tells anyone when they're assigned a task, get a comment on a task, or have a vacation request approved/rejected. Managers/admins are notified when a new vacation request needs their review. The notifications list is grouped by day (Today / Yesterday / specific dates). Clicking a task notification goes straight to that task's full detail page; clicking a report or vacation notification jumps directly to and highlights that specific entry (not just the page in general).
- **Vacation history** — managers and admins get a full list of every vacation request (pending, approved, and rejected) for their team, not just the ones awaiting review. Admins see it company-wide.
- **Vacations** — employees request time off (vacation / sick / personal), their manager
  approves or rejects with an optional comment. Approved vacations show a badge on the
  presence board. The requester can delete their own request any time while it's still
  pending — once a manager approves or rejects it, it's locked and can no longer be
  deleted or edited.
- **Admin panel** — create/edit employees, set roles (employee / manager / admin) and
  reporting lines, activate/deactivate accounts, or **permanently delete** an employee or
  manager entirely (with a confirmation prompt) — this also cleans up everything tied to
  them (their tasks, comments, reports, and vacation requests) so nothing is left orphaned.
  Deactivating (uncheck "Active") is the safer, reversible option for someone who's just
  left the company; deleting is permanent.
- **Backup** — export the entire database to a JSON file, and re-import it (merge or
  full replace) — useful for migrating between deployments or keeping offline backups.
- Works in any modern browser, no install required. Data is stored in a real
  database (PostgreSQL in production, SQLite for local dev) so it persists between
  visits and across devices — not just in the browser.
- **Upgrading from an earlier version?** The app auto-adds any new database columns/
  tables on startup — just redeploy, no manual migration needed.

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
