import json
from datetime import datetime, date
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, jsonify, Response
from flask_login import login_required, current_user
from models import db, User, Task, TaskComment, Vacation, ROLE_ADMIN

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def admin_required():
    if current_user.role != ROLE_ADMIN:
        abort(403)


@admin_bp.route("/users")
@login_required
def users():
    admin_required()
    all_users = User.query.order_by(User.full_name).all()
    return render_template("admin_users.html", users=all_users)


@admin_bp.route("/users/new", methods=["POST"])
@login_required
def new_user():
    admin_required()
    username = request.form.get("username", "").strip()
    if not username or User.query.filter_by(username=username).first():
        flash("Username is required and must be unique.", "error")
        return redirect(url_for("admin.users"))

    manager_id = request.form.get("manager_id", type=int)
    user = User(
        username=username,
        full_name=request.form.get("full_name", "").strip() or username,
        role=request.form.get("role", "employee"),
        department=request.form.get("department", "").strip(),
        manager_id=manager_id or None,
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
    new_password = request.form.get("password", "").strip()
    if new_password:
        user.set_password(new_password)
    db.session.commit()
    flash(f"User {user.username} updated.", "success")
    return redirect(url_for("admin.users"))


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

        flash("Import completed successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Import failed: {e}", "error")

    return redirect(url_for("admin.users"))
