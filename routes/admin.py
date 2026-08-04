import calendar as calendar_module
import csv
import io
import json
from datetime import datetime, date, timedelta, time as dtime
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, jsonify, Response
from flask_login import login_required, current_user
from models import (
    db, User, Task, TaskComment, Vacation, DailyReport, StatusLog, Notification,
    Project, ProjectMember, ProjectNotice, ProjectRiskIssue, ProjectActivity,
    ChatMessage, ChatReaction, ChatReadMarker, TaskCollaborator, TaskWatcher, Note, NoteShare,
    ROLE_ADMIN, CompanySettings, STATUS_LABELS, TASK_DONE, VAC_APPROVED,
)
from status_tracking import (
    compute_daily_hours, seconds_to_hm, get_day_activity_window, work_window_utc, late_minutes, to_pacific,
)

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


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
def delete_user(user_id):
    admin_required()
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("You can't delete your own account.", "error")
        return redirect(url_for("admin.users"))

    if user.role == ROLE_ADMIN and User.query.filter_by(role=ROLE_ADMIN).count() <= 1:
        flash("Can't delete the last remaining admin account.", "error")
        return redirect(url_for("admin.users"))

    try:
        # --- Safe references elsewhere: null them out rather than deleting other people's data ---
        User.query.filter_by(manager_id=user.id).update({"manager_id": None})
        Vacation.query.filter_by(decided_by_id=user.id).update({"decided_by_id": None})
        DailyReport.query.filter_by(read_by_id=user.id).update(
            {"read_by_id": None, "read_at": None, "is_read": False})
        CompanySettings.query.filter_by(updated_by_id=user.id).update({"updated_by_id": None})
        Project.query.filter_by(manager_id=user.id).update({"manager_id": None})
        ProjectActivity.query.filter_by(actor_id=user.id).update({"actor_id": None})

        # --- Their own footprint elsewhere: remove it ---
        TaskComment.query.filter_by(user_id=user.id).delete()
        Notification.query.filter_by(user_id=user.id).delete()
        StatusLog.query.filter_by(user_id=user.id).delete()
        DailyReport.query.filter_by(user_id=user.id).delete()
        Vacation.query.filter_by(user_id=user.id).delete()
        ProjectMember.query.filter_by(user_id=user.id).delete()
        ChatReaction.query.filter_by(user_id=user.id).delete()
        ChatReadMarker.query.filter_by(user_id=user.id).delete()

        # Their role as a collaborator/watcher on OTHER people's tasks - just remove that
        # link, the task itself (and its other collaborators/watchers) is untouched.
        TaskCollaborator.query.filter_by(user_id=user.id).delete()
        TaskWatcher.query.filter_by(user_id=user.id).delete()

        # Notes shared WITH them (as a recipient) - revoke access, the note stays with
        # its owner. Notes they OWN - remove the notes themselves, clearing any shares
        # on those notes first since bulk delete bypasses the ORM cascade for that.
        NoteShare.query.filter_by(shared_with_id=user.id).delete()
        their_note_ids = [n.id for n in Note.query.filter_by(owner_id=user.id).all()]
        if their_note_ids:
            NoteShare.query.filter(NoteShare.note_id.in_(their_note_ids)).delete(synchronize_session=False)
            Note.query.filter_by(owner_id=user.id).delete(synchronize_session=False)

        # Notices/risks they authored have a required (not-nullable) author link, same
        # situation as task comments - remove those too rather than leave a dangling FK.
        ProjectNotice.query.filter_by(author_id=user.id).delete()
        ProjectRiskIssue.query.filter_by(created_by_id=user.id).delete()

        # Chat messages: author_id is a required field (can't be set to null like the
        # other references above), so the only way to actually clear this reference is
        # to remove their messages. Before doing that, detach any REPLIES BY OTHER
        # PEOPLE to those messages (set parent_id to null) so deleting this user's
        # message doesn't cascade-delete someone else's reply along with it - their
        # reply just becomes a standalone message instead.
        their_message_ids = [m.id for m in ChatMessage.query.filter_by(author_id=user.id).all()]
        if their_message_ids:
            ChatMessage.query.filter(ChatMessage.parent_id.in_(their_message_ids)).update(
                {"parent_id": None}, synchronize_session=False)
            ChatReaction.query.filter(ChatReaction.message_id.in_(their_message_ids)).delete(
                synchronize_session=False)
            ChatMessage.query.filter_by(author_id=user.id).delete(synchronize_session=False)

        # Any task assigned to or by them - deleted entirely (also cascades that task's
        # remaining comments via the existing relationship cascade). Clear any chat
        # messages linked to those tasks first so that link doesn't dangle either.
        affected_tasks = Task.query.filter(
            db.or_(Task.assigned_to_id == user.id, Task.assigned_by_id == user.id)
        ).all()
        affected_task_ids = [t.id for t in affected_tasks]
        if affected_task_ids:
            ChatMessage.query.filter(ChatMessage.linked_task_id.in_(affected_task_ids)).update(
                {"linked_task_id": None}, synchronize_session=False)
        for t in affected_tasks:
            db.session.delete(t)

        db.session.delete(user)
        db.session.commit()
        flash(f"{user.full_name} was permanently deleted, along with their tasks, reports, and requests.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Couldn't delete this user: {e}", "error")

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
    window_start, window_end = work_window_utc(day, settings)

    employee_id = request.args.get("employee_id", type=int)
    all_employees = User.query.filter_by(is_active_employee=True).order_by(User.full_name).all()
    employees = [u for u in all_employees if u.id == employee_id] if employee_id else all_employees

    rows = []
    for e in employees:
        totals = compute_daily_hours(e, day, window_start=window_start, window_end=window_end)
        first_seen, last_seen = get_day_activity_window(e, day)
        started_str = first_seen.strftime("%H:%M") if first_seen else "—"
        late_by = late_minutes(first_seen, settings)
        on_desk_seconds = totals.get("available", 0) + totals.get("idle", 0) + totals.get("meeting", 0)
        # If someone was active but only before the office opened (e.g. checked in at 5:47am
        # when the office opens at 6:00am), on-desk hours will correctly show 0 for that
        # activity - flag it so that doesn't read as a bug.
        window_start_pt = to_pacific(window_start)
        activity_before_office_hours = bool(last_seen and window_start_pt and last_seen <= window_start_pt)
        rows.append({
            "user": e,
            "totals": totals,
            "formatted": {status: seconds_to_hm(secs) for status, secs in totals.items()},
            "on_desk": seconds_to_hm(on_desk_seconds),
            "started": started_str,
            "ended": last_seen.strftime("%H:%M") if last_seen else "—",
            "late_minutes": late_by,
            "is_late": bool(late_by),
            "activity_before_office_hours": activity_before_office_hours,
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
        all_employees=all_employees,
        employee_id=employee_id,
    )


@admin_bp.route("/hours/reset", methods=["POST"])
@login_required
def reset_hours():
    admin_required()
    day_str = request.form.get("date")
    try:
        day = datetime.strptime(day_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        flash("Invalid date.", "error")
        return redirect(url_for("admin.hours"))

    employee_id = request.form.get("employee_id", type=int)

    day_start = datetime.combine(day, datetime.min.time())
    day_end = datetime.combine(day, datetime.max.time())

    query = StatusLog.query.filter(
        StatusLog.started_at < day_end,
        db.or_(StatusLog.ended_at == None, StatusLog.ended_at > day_start),  # noqa: E711
    )
    if employee_id:
        query = query.filter(StatusLog.user_id == employee_id)
    deleted = query.delete(synchronize_session=False)
    db.session.commit()

    who = User.query.get(employee_id).full_name if employee_id else "every employee"
    flash(f"Cleared tracked hours for {who} on {day.strftime('%b %d, %Y')} ({deleted} record(s) removed).", "success")
    return redirect(url_for("admin.hours", date=day.isoformat(), employee_id=employee_id))


@admin_bp.route("/hours/export.csv")
@login_required
def hours_export_csv():
    admin_required()
    day_str = request.args.get("date")
    try:
        day = datetime.strptime(day_str, "%Y-%m-%d").date() if day_str else date.today()
    except ValueError:
        day = date.today()

    settings = CompanySettings.get()
    window_start, window_end = work_window_utc(day, settings)

    employee_id = request.args.get("employee_id", type=int)
    all_employees = User.query.filter_by(is_active_employee=True).order_by(User.full_name).all()
    employees = [u for u in all_employees if u.id == employee_id] if employee_id else all_employees

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Employee", "Started (PT)", "Late by (min)", "Ended (PT)", "On-desk total", "Note",
                      "Available", "Idle", "Away from desk", "In a meeting", "Offline"])
    for e in employees:
        totals = compute_daily_hours(e, day, window_start=window_start, window_end=window_end)
        first_seen, last_seen = get_day_activity_window(e, day)
        started_str = first_seen.strftime("%H:%M") if first_seen else ""
        late_by = late_minutes(first_seen, settings)
        on_desk_seconds = totals.get("available", 0) + totals.get("idle", 0) + totals.get("meeting", 0)
        window_start_pt = to_pacific(window_start)
        activity_before_office_hours = bool(last_seen and window_start_pt and last_seen <= window_start_pt)
        writer.writerow([
            e.full_name,
            started_str,
            late_by if late_by else "",
            last_seen.strftime("%H:%M") if last_seen else "",
            seconds_to_hm(on_desk_seconds),
            "checked in before office opened" if activity_before_office_hours else "",
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


@admin_bp.route("/employee-log")
@login_required
def employee_log():
    """A per-employee monthly log: total late minutes, on-desk hours (weekly + monthly),
    vacation days taken, tasks completed, and days with no tracked activity at all."""
    admin_required()
    settings = CompanySettings.get()
    all_employees = User.query.filter_by(is_active_employee=True).order_by(User.full_name).all()

    employee_id = request.args.get("employee_id", type=int)

    month_str = request.args.get("month")
    try:
        month_start = (datetime.strptime(month_str, "%Y-%m").date().replace(day=1)
                        if month_str else date.today().replace(day=1))
    except ValueError:
        month_start = date.today().replace(day=1)

    last_day_num = calendar_module.monthrange(month_start.year, month_start.month)[1]
    month_end = month_start.replace(day=last_day_num)
    today = date.today()
    effective_end = min(month_end, today)

    log = None
    employee = User.query.get(employee_id) if employee_id else None

    if employee:
        total_late_minutes = 0
        late_days = 0
        active_days = 0
        no_activity_days = 0
        total_on_desk_seconds = 0.0

        d = month_start
        while d <= effective_end:
            window_start, window_end = work_window_utc(d, settings)
            first_seen, _ = get_day_activity_window(employee, d)
            if first_seen:
                active_days += 1
                lm = late_minutes(first_seen, settings)
                if lm:
                    total_late_minutes += lm
                    late_days += 1
            else:
                no_activity_days += 1
            totals = compute_daily_hours(employee, d, window_start=window_start, window_end=window_end)
            total_on_desk_seconds += totals.get("available", 0) + totals.get("idle", 0) + totals.get("meeting", 0)
            d += timedelta(days=1)

        # Rolling "this week" (Monday through today) on-desk total, independent of the month picker.
        week_start = today - timedelta(days=today.weekday())
        week_on_desk_seconds = 0.0
        d = week_start
        while d <= today:
            w_start, w_end = work_window_utc(d, settings)
            totals = compute_daily_hours(employee, d, window_start=w_start, window_end=w_end)
            week_on_desk_seconds += totals.get("available", 0) + totals.get("idle", 0) + totals.get("meeting", 0)
            d += timedelta(days=1)

        approved_vacations = Vacation.query.filter_by(
            user_id=employee.id, status=VAC_APPROVED, vac_type="vacation").all()
        vac_days_month = 0
        for v in approved_vacations:
            overlap_start = max(v.start_date, month_start)
            overlap_end = min(v.end_date, month_end)
            if overlap_end >= overlap_start:
                vac_days_month += (overlap_end - overlap_start).days + 1
        vac_days_year = sum((v.end_date - v.start_date).days + 1
                             for v in approved_vacations if v.start_date.year == month_start.year)

        tasks_completed = Task.query.filter(
            Task.assigned_to_id == employee.id,
            Task.status == TASK_DONE,
            Task.completed_at.isnot(None),
            Task.completed_at >= datetime.combine(month_start, dtime.min),
            Task.completed_at <= datetime.combine(month_end, dtime.max),
        ).count()

        log = {
            "employee": employee,
            "total_late_minutes": total_late_minutes,
            "late_days": late_days,
            "avg_late_per_late_day": round(total_late_minutes / late_days) if late_days else 0,
            "active_days": active_days,
            "no_activity_days": no_activity_days,
            "on_desk_month": seconds_to_hm(total_on_desk_seconds),
            "avg_on_desk_per_active_day": seconds_to_hm(total_on_desk_seconds / active_days) if active_days else "0h 00m",
            "on_desk_week": seconds_to_hm(week_on_desk_seconds),
            "week_start": week_start,
            "vac_days_month": vac_days_month,
            "vac_days_year": vac_days_year,
            "tasks_completed": tasks_completed,
        }

    return render_template(
        "admin_employee_log.html",
        all_employees=all_employees,
        employee_id=employee_id,
        month_start=month_start,
        month_end=month_end,
        month_value=month_start.strftime("%Y-%m"),
        today=today,
        log=log,
        settings=settings,
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
                    is_read=r.get("is_read", False),
                    read_by_id=id_map_users.get(r.get("read_by_id")) if r.get("read_by_id") else None,
                    read_at=datetime.fromisoformat(r["read_at"]) if r.get("read_at") else None,
                ))
        db.session.commit()

        flash("Import completed successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Import failed: {e}", "error")

    return redirect(url_for("admin.users"))
