from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from models import (
    db, User, Task, TaskComment,
    TASK_PENDING, TASK_IN_PROGRESS, TASK_DONE,
    ROLE_ADMIN, ROLE_MANAGER, ROLE_EMPLOYEE,
)
from notifications import notify

tasks_bp = Blueprint("tasks", __name__, url_prefix="/tasks")


def _parse_date(date_str, default):
    if not date_str:
        return default
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return default


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


def task_notification_url(task):
    """Where a notification about this task should land: the Tasks page pre-filtered to
    its due date (so it opens directly on that specific day), or the task detail page
    if it has no due date to jump to."""
    if task.due_date:
        return url_for("tasks.index", date=task.due_date.isoformat())
    return url_for("tasks.detail", task_id=task.id)


def group_tasks_by_due_date(tasks):
    """Groups an already due-date-sorted task list into (label, tasks) pairs, one per day,
    with undated tasks collected into a trailing 'No due date' group. Avoids Python's
    None-vs-date comparison crash that Jinja's built-in groupby filter would hit."""
    dated, undated = [], []
    for t in tasks:
        (dated if t.due_date else undated).append(t)

    groups = []
    current_date, current_bucket = None, None
    for t in dated:
        if t.due_date != current_date:
            current_date = t.due_date
            current_bucket = []
            groups.append((current_date.strftime("%A, %b %d, %Y"), current_bucket))
        current_bucket.append(t)

    if undated:
        groups.append(("No due date", undated))

    return groups


@tasks_bp.route("/")
@login_required
def index():
    employees = sorted(assignable_employees_for(current_user), key=lambda u: u.full_name)

    assigned_by_me = Task.query.filter_by(assigned_by_id=current_user.id).order_by(
        Task.due_date.asc().nullslast()).all()

    my_tasks = Task.query.filter_by(assigned_to_id=current_user.id).order_by(
        Task.due_date.asc().nullslast()).all()

    # Calendar-driven "what's due on this date" browsing, scoped to what each role can see.
    filter_date = _parse_date(request.args.get("date"), date.today())
    if current_user.role == ROLE_ADMIN:
        date_scope_tasks = (Task.query.filter(Task.due_date == filter_date)
                             .join(User, Task.assigned_to_id == User.id).order_by(User.full_name).all())
    elif current_user.role == ROLE_MANAGER:
        managed_ids = {u.id for u in User.query.filter_by(is_active_employee=True).all()
                       if u.id != current_user.id and current_user.can_manage(u)}
        date_scope_tasks = (Task.query.filter(
            Task.due_date == filter_date,
            db.or_(Task.assigned_to_id.in_(managed_ids), Task.assigned_by_id == current_user.id))
            .order_by(Task.assigned_to_id).all())
    else:
        date_scope_tasks = (Task.query.filter(Task.due_date == filter_date,
                             Task.assigned_to_id == current_user.id).all())

    # Full company-wide task visibility - admin only.
    all_company_tasks = []
    if current_user.role == ROLE_ADMIN:
        all_company_tasks = Task.query.order_by(Task.due_date.asc().nullslast()).limit(300).all()

    return render_template(
        "tasks.html",
        my_tasks_by_day=group_tasks_by_due_date(my_tasks),
        assigned_by_me_by_day=group_tasks_by_due_date(assigned_by_me),
        all_company_tasks_by_day=group_tasks_by_due_date(all_company_tasks),
        employees=employees, today=date.today(),
        date_scope_tasks=date_scope_tasks, filter_date=filter_date,
        prev_date=filter_date - timedelta(days=1), next_date=filter_date + timedelta(days=1),
    )


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
           url=task_notification_url(task))
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
                   url=task_notification_url(task))
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


@tasks_bp.route("/<int:task_id>/edit", methods=["GET", "POST"])
@login_required
def edit_task(task_id):
    # Admin override: full edit of any task's core fields, regardless of who assigned it.
    if current_user.role != ROLE_ADMIN:
        abort(403)
    task = Task.query.get_or_404(task_id)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        if not title:
            flash("Title is required.", "error")
            return redirect(url_for("tasks.edit_task", task_id=task.id))

        assigned_to_id = request.form.get("assigned_to_id", type=int)
        assignee = User.query.filter(User.id == assigned_to_id, User.role != ROLE_ADMIN).first()
        if not assignee:
            flash("Invalid assignee.", "error")
            return redirect(url_for("tasks.edit_task", task_id=task.id))

        due_date_str = request.form.get("due_date")
        task.title = title
        task.description = request.form.get("description", "").strip()
        task.assigned_to_id = assignee.id
        task.due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date() if due_date_str else None
        task.priority = request.form.get("priority", task.priority)
        db.session.commit()
        flash("Task updated.", "success")
        return redirect(url_for("tasks.detail", task_id=task.id))

    all_possible_assignees = (User.query.filter_by(is_active_employee=True)
                               .filter(User.role != ROLE_ADMIN).order_by(User.full_name).all())
    return render_template("task_edit.html", task=task, assignees=all_possible_assignees)


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
                   url=task_notification_url(task))
        db.session.commit()
    return redirect(url_for("tasks.detail", task_id=task.id))
