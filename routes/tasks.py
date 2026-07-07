from datetime import datetime, date
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from models import db, User, Task, TaskComment, TASK_PENDING, TASK_IN_PROGRESS, TASK_DONE

tasks_bp = Blueprint("tasks", __name__, url_prefix="/tasks")


@tasks_bp.route("/")
@login_required
def index():
    if current_user.role in ("admin", "manager"):
        assigned_by_me = Task.query.filter_by(assigned_by_id=current_user.id).order_by(
            Task.due_date.asc().nullslast()).all()
    else:
        assigned_by_me = []

    my_tasks = Task.query.filter_by(assigned_to_id=current_user.id).order_by(
        Task.due_date.asc().nullslast()).all()

    employees = []
    if current_user.role in ("admin", "manager"):
        employees = [u for u in User.query.filter_by(is_active_employee=True).order_by(User.full_name).all()
                     if current_user.can_manage(u)]

    return render_template("tasks.html", my_tasks=my_tasks, assigned_by_me=assigned_by_me,
                            employees=employees, today=date.today())


@tasks_bp.route("/new", methods=["POST"])
@login_required
def new():
    if current_user.role not in ("admin", "manager"):
        abort(403)

    assigned_to_id = request.form.get("assigned_to_id", type=int)
    assignee = User.query.get_or_404(assigned_to_id)
    if not current_user.can_manage(assignee):
        abort(403)

    title = request.form.get("title", "").strip()
    if not title:
        flash("Task title is required.", "error")
        return redirect(url_for("tasks.index"))

    due_date_str = request.form.get("due_date")
    due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date() if due_date_str else None

    task = Task(
        title=title,
        description=request.form.get("description", "").strip(),
        assigned_to_id=assignee.id,
        assigned_by_id=current_user.id,
        due_date=due_date,
        priority=request.form.get("priority", "medium"),
        status=TASK_PENDING,
    )
    db.session.add(task)
    db.session.commit()
    flash(f"Task assigned to {assignee.full_name}.", "success")
    return redirect(url_for("tasks.index"))


@tasks_bp.route("/<int:task_id>")
@login_required
def detail(task_id):
    task = Task.query.get_or_404(task_id)
    if task.assigned_to_id != current_user.id and not current_user.can_manage(task.assigned_to) \
            and task.assigned_by_id != current_user.id:
        abort(403)
    return render_template("task_detail.html", task=task)


@tasks_bp.route("/<int:task_id>/status", methods=["POST"])
@login_required
def update_status(task_id):
    task = Task.query.get_or_404(task_id)
    new_status = request.form.get("status")
    is_owner = task.assigned_to_id == current_user.id
    is_manager = current_user.can_manage(task.assigned_to)
    if not (is_owner or is_manager):
        abort(403)
    if new_status not in (TASK_PENDING, TASK_IN_PROGRESS, TASK_DONE):
        abort(400)
    task.status = new_status
    task.completed_at = datetime.utcnow() if new_status == TASK_DONE else None
    db.session.commit()
    flash("Task updated.", "success")
    return redirect(request.referrer or url_for("tasks.index"))


@tasks_bp.route("/<int:task_id>/comment", methods=["POST"])
@login_required
def add_comment(task_id):
    task = Task.query.get_or_404(task_id)
    is_owner = task.assigned_to_id == current_user.id
    is_manager = current_user.can_manage(task.assigned_to)
    if not (is_owner or is_manager or task.assigned_by_id == current_user.id):
        abort(403)

    body = request.form.get("body", "").strip()
    if body:
        comment = TaskComment(task_id=task.id, user_id=current_user.id, body=body)
        db.session.add(comment)
        db.session.commit()
    return redirect(url_for("tasks.detail", task_id=task.id))
