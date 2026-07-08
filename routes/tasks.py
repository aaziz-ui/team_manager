from datetime import datetime, date
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from models import (
    db, User, Task, TaskComment,
    TASK_PENDING, TASK_IN_PROGRESS, TASK_DONE,
    ROLE_ADMIN, ROLE_MANAGER, ROLE_EMPLOYEE,
)
from notifications import notify

tasks_bp = Blueprint("tasks", __name__, url_prefix="/tasks")


def assignable_employees_for(user):
    """Who this user is allowed to assign tasks to.
    - Nobody can ever assign a task to an admin - admins assign, they don't receive.
    - Admins can assign to any manager or employee.
    - Managers can assign to the employees they manage (not to other managers/admins).
    - A regular employee can only assign tasks if explicitly granted that permission
      by an admin (User.can_assign_tasks), and even then only to other employees -
      never to managers or admins.
    """
    pool = (User.query.filter_by(is_active_employee=True)
            .filter(User.id != user.id, User.role != ROLE_ADMIN).all())

    if user.role == ROLE_ADMIN:
        return pool
    if user.role == ROLE_MANAGER:
        return [u for u in pool if u.role == ROLE_EMPLOYEE and user.can_manage(u)]
    if user.role == ROLE_EMPLOYEE and user.can_assign_tasks:
        return [u for u in pool if u.role == ROLE_EMPLOYEE]
    return []


def can_manage_task(user, task):
    """Whether this user can update/delete a given task (assignee, its assigner, or admin)."""
    return (user.role == ROLE_ADMIN
            or task.assigned_to_id == user.id
            or task.assigned_by_id == user.id)


@tasks_bp.route("/")
@login_required
def index():
    employees = sorted(assignable_employees_for(current_user), key=lambda u: u.full_name)

    assigned_by_me = Task.query.filter_by(assigned_by_id=current_user.id).order_by(
        Task.due_date.asc().nullslast()).all()

    my_tasks = Task.query.filter_by(assigned_to_id=current_user.id).order_by(
        Task.due_date.asc().nullslast()).all()

    return render_template("tasks.html", my_tasks=my_tasks, assigned_by_me=assigned_by_me,
                            employees=employees, today=date.today())


@tasks_bp.route("/new", methods=["POST"])
@login_required
def new():
    allowed = {u.id: u for u in assignable_employees_for(current_user)}
    if not allowed:
        abort(403)

    assigned_to_id = request.form.get("assigned_to_id", type=int)
    if assigned_to_id not in allowed:
        abort(403)
    assignee = allowed[assigned_to_id]

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
    notify(assignee.id, "task", f'{current_user.full_name} assigned you a task: "{title}"',
           url=url_for("tasks.detail", task_id=task.id))
    db.session.commit()
    flash(f"Task assigned to {assignee.full_name}.", "success")
    return redirect(url_for("tasks.index"))


@tasks_bp.route("/<int:task_id>")
@login_required
def detail(task_id):
    task = Task.query.get_or_404(task_id)
    if not can_manage_task(current_user, task):
        abort(403)
    return render_template("task_detail.html", task=task)


@tasks_bp.route("/<int:task_id>/status", methods=["POST"])
@login_required
def update_status(task_id):
    task = Task.query.get_or_404(task_id)
    new_status = request.form.get("status")
    if not can_manage_task(current_user, task):
        abort(403)
    if new_status not in (TASK_PENDING, TASK_IN_PROGRESS, TASK_DONE):
        abort(400)
    task.status = new_status
    task.completed_at = datetime.utcnow() if new_status == TASK_DONE else None
    if new_status == TASK_DONE:
        task.percent_complete = 100
        if current_user.id != task.assigned_by_id:
            notify(task.assigned_by_id, "task", f'{current_user.full_name} marked "{task.title}" as done',
                   url=url_for("tasks.detail", task_id=task.id))
    db.session.commit()
    flash("Task updated.", "success")
    return redirect(request.referrer or url_for("tasks.index"))


@tasks_bp.route("/<int:task_id>/progress", methods=["POST"])
@login_required
def update_progress(task_id):
    task = Task.query.get_or_404(task_id)
    if not can_manage_task(current_user, task):
        abort(403)

    percent = request.form.get("percent_complete", type=int)
    if percent is None or not (0 <= percent <= 100):
        abort(400)
    task.percent_complete = percent
    if percent == 100 and task.status != TASK_DONE:
        task.status = TASK_DONE
        task.completed_at = datetime.utcnow()
    elif percent < 100 and task.status == TASK_DONE:
        task.status = TASK_IN_PROGRESS
        task.completed_at = None
    elif percent > 0 and task.status == TASK_PENDING:
        task.status = TASK_IN_PROGRESS
    db.session.commit()
    flash("Progress updated.", "success")
    return redirect(request.referrer or url_for("tasks.detail", task_id=task.id))


@tasks_bp.route("/<int:task_id>/delete", methods=["POST"])
@login_required
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    # Admins can delete any task. Anyone else can only delete a task they personally assigned.
    if current_user.role != ROLE_ADMIN and task.assigned_by_id != current_user.id:
        abort(403)
    db.session.delete(task)
    db.session.commit()
    flash("Task deleted.", "success")
    return redirect(request.referrer or url_for("tasks.index"))


@tasks_bp.route("/<int:task_id>/comment", methods=["POST"])
@login_required
def add_comment(task_id):
    task = Task.query.get_or_404(task_id)
    if not can_manage_task(current_user, task):
        abort(403)

    body = request.form.get("body", "").strip()
    if body:
        comment = TaskComment(task_id=task.id, user_id=current_user.id, body=body)
        db.session.add(comment)
        db.session.commit()

        recipients = {task.assigned_to_id, task.assigned_by_id} - {current_user.id}
        for recipient_id in recipients:
            notify(recipient_id, "task", f'{current_user.full_name} commented on "{task.title}"',
                   url=url_for("tasks.detail", task_id=task.id))
        db.session.commit()
    return redirect(url_for("tasks.detail", task_id=task.id))
