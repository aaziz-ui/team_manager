import csv
import io
import json
from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, jsonify, Response
from flask_login import login_required, current_user
from models import db, User, Task, TaskComment, Vacation, DailyReport, ROLE_ADMIN, CompanySettings, STATUS_LABELS
from status_tracking import compute_daily_hours, seconds_to_hm

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def admin_required():
    if current_user.role != ROLE_ADMIN:
        abort(403)


@admin_bp.route("/users")
@login_required
def users():
    admin_required()
    all_users = User.query.order_by(User.full_name).all()
    settings = CompanySettings.get()
    return render_template("admin_users.html", users=all_users, settings=settings)


@admin_bp.route("/users/new", methods=["POST"])
@login_required
def new_user():
    admin_required()
    username = request.form.get("username", "").strip()
    if not username or User.query.filter_by(username=username).first():
        flash("Username is required and must be unique.", "error")
        return redirect(url_for("admin.users"))

    manager_id = request.form.get("manager_id", type=int)
    vac_override_raw = request.form.get("vacation_days_override", "").strip()
    user = User(
        username=username,
        full_name=request.form.get("full_name", "").strip() or username,
        role=request.form.get("role", "employee"),
        department=request.form.get("department", "").strip(),
        manager_id=manager_id or None,
        vacation_days_override=int(vac_override_raw) if vac_override_raw else None,
        can_assign_tasks=request.form.get("can_assign_tasks") == "on",
    )
    user.set_password(request.form.get("password") or "ChangeMe123!")
    db.session.add(user)
    db.session.commit()
    flash(f"User {username} created.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/edit", methods=["POST"])
@login_required
def edit_user(user_id):
    admin_required()
    user = User.query.get_or_404(user_id)
    user.full_name = request.form.get("full_name", user.full_name).strip()
    user.role = request.form.get("role", user.role)
    user.department = request.form.get("department", "").strip()
    manager_id = request.form.get("manager_id", type=int)
    user.manager_id = manager_id or None
    user.is_active_employee = request.form.get("is_active") == "on"
    vac_override_raw = request.form.get("vacation_days_override", "").strip()
    user.vacation_days_override = int(vac_override_raw) if vac_override_raw else None
    user.can_assign_tasks = request.form.get("can_assign_tasks") == "on"
    new_password = request.form.get("password", "").strip()
    if new_password:
        user.set_password(new_password)
    db.session.commit()
    flash(f"User {user.username} updated.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/hours")
@login_required
def hours():
    admin_required()

    day_str = request.args.get("date")
    try:
        day = datetime.strptime(day_str, "%Y-%m-%d").date() if day_str else date.today()
    except ValueError:
        day = date.today()

    settings = CompanySettings.get()
    employees = User.query.filter_by(is_active_employee=True).order_by(User.full_name).all()

    rows = []
    for e in employees:
        totals = compute_daily_hours(e, day)
        rows.append({
            "user": e,
            "totals": totals,
            "formatted": {status: seconds_to_hm(secs) for status, secs in totals.items()},
        })

    return render_template(
        "admin_hours.html",
        rows=rows,
        day=day,
        prev_day=day - timedelta(days=1),
        next_day=day + timedelta(days=1),
        today=date.today(),
        settings=settings,
        status_labels=STATUS_LABELS,
    )


@admin_bp.route("/hours/export.csv")
@login_required
def hours_export_csv():
    admin_required()
    day_str = request.args.get("date")
    try:
        day = datetime.strptime(day_str, "%Y-%m-%d").date() if day_str else date.today()
    except ValueError:
        day = date.today()

    employees = User.query.filter_by(is_active_employee=True).order_by(User.full_name).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Employee", "Available", "Idle", "Away from desk", "In a meeting", "Offline"])
    for e in employees:
        totals = compute_daily_hours(e, day)
        writer.writerow([
            e.full_name,
            seconds_to_hm(totals.get("available", 0)),
            seconds_to_hm(totals.get("idle", 0)),
            seconds_to_hm(totals.get("away_desk", 0)),
            seconds_to_hm(totals.get("meeting", 0)),
            seconds_to_hm(totals.get("offline", 0)),
        ])

    filename = f"hours_{day.isoformat()}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename={filename}"},
    )


@admin_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    admin_required()
    settings = CompanySettings.get()

    if request.method == "POST":
        try:
            settings.work_start = datetime.strptime(request.form["work_start"], "%H:%M").time()
            settings.work_end = datetime.strptime(request.form["work_end"], "%H:%M").time()
            settings.annual_vacation_days = int(request.form.get("annual_vacation_days", settings.annual_vacation_days))
            settings.updated_at = datetime.utcnow()
            settings.updated_by_id = current_user.id
            db.session.commit()
            flash("Working hours updated.", "success")
        except (KeyError, ValueError):
            flash("Please provide valid times.", "error")
        return redirect(url_for("admin.settings"))

    return render_template("admin_settings.html", settings=settings)


@admin_bp.route("/export")
@login_required
def export_data():
    admin_required()
    data = {
        "exported_at": datetime.utcnow().isoformat(),
        "users": [u.to_export_dict() for u in User.query.all()],
        "tasks": [t.to_export_dict() for t in Task.query.all()],
        "task_comments": [c.to_export_dict() for c in TaskComment.query.all()],
        "vacations": [v.to_export_dict() for v in Vacation.query.all()],
        "daily_reports": [r.to_export_dict() for r in DailyReport.query.all()],
    }
    payload = json.dumps(data, indent=2)
    filename = f"team_manager_export_{date.today().isoformat()}.json"
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment;filename={filename}"},
    )


@admin_bp.route("/import", methods=["POST"])
@login_required
def import_data():
    admin_required()
    file = request.files.get("file")
    if not file:
        flash("No file uploaded.", "error")
        return redirect(url_for("admin.users"))

    try:
        data = json.load(file.stream)
    except Exception:
        flash("Invalid JSON file.", "error")
        return redirect(url_for("admin.users"))

    mode = request.form.get("mode", "merge")  # merge | replace

    try:
        if mode == "replace":
            TaskComment.query.delete()
            Task.query.delete()
            Vacation.query.delete()
            DailyReport.query.delete()
            User.query.delete()
            db.session.commit()

        id_map_users = {}
        for u in data.get("users", []):
            existing = User.query.filter_by(username=u["username"]).first()
            if existing:
                target = existing
            else:
                target = User(username=u["username"])
                db.session.add(target)
            target.password_hash = u["password_hash"]
            target.full_name = u["full_name"]
            target.role = u["role"]
            target.department = u.get("department")
            target.is_active_employee = u.get("is_active_employee", True)
            target.vacation_days_override = u.get("vacation_days_override")
            target.can_assign_tasks = u.get("can_assign_tasks", False)
            db.session.flush()
            id_map_users[u["id"]] = target.id

        # second pass for manager_id relationships
        for u in data.get("users", []):
            if u.get("manager_id"):
                target = User.query.get(id_map_users[u["id"]])
                target.manager_id = id_map_users.get(u["manager_id"])
        db.session.commit()

        id_map_tasks = {}
        for t in data.get("tasks", []):
            task = Task(
                title=t["title"],
                description=t.get("description"),
                assigned_to_id=id_map_users.get(t["assigned_to_id"]),
                assigned_by_id=id_map_users.get(t["assigned_by_id"]),
                due_date=datetime.strptime(t["due_date"], "%Y-%m-%d").date() if t.get("due_date") else None,
                priority=t.get("priority", "medium"),
                status=t.get("status", "pending"),
                percent_complete=t.get("percent_complete", 0),
            )
            db.session.add(task)
            db.session.flush()
            id_map_tasks[t["id"]] = task.id
        db.session.commit()

        for c in data.get("task_comments", []):
            if c["task_id"] in id_map_tasks:
                db.session.add(TaskComment(
                    task_id=id_map_tasks[c["task_id"]],
                    user_id=id_map_users.get(c["user_id"]),
                    body=c["body"],
                ))
        db.session.commit()

        for v in data.get("vacations", []):
            db.session.add(Vacation(
                user_id=id_map_users.get(v["user_id"]),
                start_date=datetime.strptime(v["start_date"], "%Y-%m-%d").date(),
                end_date=datetime.strptime(v["end_date"], "%Y-%m-%d").date(),
                vac_type=v.get("vac_type", "vacation"),
                reason=v.get("reason"),
                status=v.get("status", "pending"),
                decided_by_id=id_map_users.get(v.get("decided_by_id")) if v.get("decided_by_id") else None,
                manager_comment=v.get("manager_comment"),
            ))
        db.session.commit()

        for r in data.get("daily_reports", []):
            uid = id_map_users.get(r["user_id"])
            rdate = datetime.strptime(r["report_date"], "%Y-%m-%d").date()
            if uid and not DailyReport.query.filter_by(user_id=uid, report_date=rdate).first():
                db.session.add(DailyReport(
                    user_id=uid,
                    report_date=rdate,
                    content=r["content"],
                ))
        db.session.commit()

        flash("Import completed successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Import failed: {e}", "error")

    return redirect(url_for("admin.users"))
