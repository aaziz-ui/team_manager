from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from models import (
    db, User, Task, TaskComment, Project, ProjectMember, DailyReport,
    TaskCollaborator, TaskWatcher,
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


def can_edit_task(user, task):
    """Whether this user can change status/progress: admin, the assignee, whoever assigned
    it, or a collaborator working on it together with the assignee."""
    if (user.role == ROLE_ADMIN
            or task.assigned_to_id == user.id
            or task.assigned_by_id == user.id):
        return True
    return TaskCollaborator.query.filter_by(task_id=task.id, user_id=user.id).first() is not None


def can_view_task(user, task):
    """Whether this user can see the task and comment on it - everyone who can edit it,
    plus watchers who were given visibility but can't change anything."""
    if can_edit_task(user, task):
        return True
    return TaskWatcher.query.filter_by(task_id=task.id, user_id=user.id).first() is not None


def task_notification_url(task):
    """Notifications about a task go straight to its own detail page - that already shows
    the full details (title, description, comments, due date) rather than dropping the
    person into a generic day-list view they'd have to search through."""
    return url_for("tasks.detail", task_id=task.id)


def draft_completed_task_into_report(task):
    """When a task is marked done, add a line for it into the assignee's daily report for
    today (and every collaborator's, if it's a shared task - everyone who worked on it
    gets it noted in their own report), creating that report if it doesn't exist yet. It's
    still just a normal editable draft afterward - they can add to it, remove the line, or
    change anything else about it freely, same as if they'd typed it themselves (unless a
    manager has already marked that report read and locked it, in which case we leave it
    alone rather than silently editing something that's already been reviewed)."""
    today = date.today()
    line = f"- Completed: {task.title}"
    recipient_ids = {task.assigned_to_id} | {c.user_id for c in task.collaborators}

    for user_id in recipient_ids:
        report = DailyReport.query.filter_by(user_id=user_id, report_date=today).first()
        if report:
            if report.is_read or line in report.content:
                continue
            report.content = (report.content.rstrip("\n") + "\n" + line).strip()
            report.updated_at = datetime.utcnow()
        else:
            db.session.add(DailyReport(user_id=user_id, report_date=today, content=line))


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

    # Newest/nearest date first, oldest at the bottom; undated tasks trail at the very end
    # either way since nullslast() is independent of sort direction.
    assigned_by_me = Task.query.filter_by(assigned_by_id=current_user.id).order_by(
        Task.due_date.desc().nullslast()).all()

    # "My tasks" = tasks I'm the primary assignee on, PLUS shared tasks I'm a collaborator
    # on - both count as "mine to work on", just presented together.
    collab_task_ids = {c.task_id for c in TaskCollaborator.query.filter_by(user_id=current_user.id).all()}
    my_tasks = (Task.query.filter(
        db.or_(Task.assigned_to_id == current_user.id, Task.id.in_(collab_task_ids)))
        .order_by(Task.due_date.desc().nullslast()).all())

    # "Watching" = tasks I've been given view+comment-only visibility into, without being
    # able to touch them - kept separate so it's clear these aren't mine to act on.
    watch_task_ids = {w.task_id for w in TaskWatcher.query.filter_by(user_id=current_user.id).all()}
    watching_tasks = (Task.query.filter(Task.id.in_(watch_task_ids))
                       .order_by(Task.due_date.desc().nullslast()).all()) if watch_task_ids else []

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

    # Full company-wide task visibility - admin only - with filters.
    all_company_tasks = []
    all_assignable_people = []
    task_filters = {
        "employee_id": request.args.get("f_employee_id", type=int),
        "status": request.args.get("f_status", ""),
        "priority": request.args.get("f_priority", ""),
        "due_from": request.args.get("f_due_from", ""),
        "due_to": request.args.get("f_due_to", ""),
    }
    if current_user.role == ROLE_ADMIN:
        all_assignable_people = (User.query.filter_by(is_active_employee=True)
                                  .filter(User.role != ROLE_ADMIN).order_by(User.full_name).all())

        q = Task.query
        if task_filters["employee_id"]:
            q = q.filter(Task.assigned_to_id == task_filters["employee_id"])
        if task_filters["status"] in (TASK_PENDING, TASK_IN_PROGRESS, TASK_DONE):
            q = q.filter(Task.status == task_filters["status"])
        if task_filters["priority"] in ("low", "medium", "high"):
            q = q.filter(Task.priority == task_filters["priority"])
        if task_filters["due_from"]:
            due_from = _parse_date(task_filters["due_from"], None)
            if due_from:
                q = q.filter(Task.due_date >= due_from)
        if task_filters["due_to"]:
            due_to = _parse_date(task_filters["due_to"], None)
            if due_to:
                q = q.filter(Task.due_date <= due_to)

        all_company_tasks = q.order_by(Task.due_date.desc().nullslast()).limit(500).all()

    return render_template(
        "tasks.html",
        my_tasks_by_day=group_tasks_by_due_date(my_tasks),
        assigned_by_me_by_day=group_tasks_by_due_date(assigned_by_me),
        watching_tasks_by_day=group_tasks_by_due_date(watching_tasks),
        all_company_tasks_by_day=group_tasks_by_due_date(all_company_tasks),
        employees=employees, today=date.today(),
        date_scope_tasks=date_scope_tasks, filter_date=filter_date,
        prev_date=filter_date - timedelta(days=1), next_date=filter_date + timedelta(days=1),
        all_assignable_people=all_assignable_people, task_filters=task_filters,
        linkable_projects=_visible_projects_for_linking(current_user),
    )


def _visible_projects_for_linking(user):
    if user.role == ROLE_ADMIN:
        return Project.query.filter_by(is_archived=False).order_by(Project.name).all()
    member_ids = {m.project_id for m in ProjectMember.query.filter_by(user_id=user.id).all()}
    managed_ids = {p.id for p in Project.query.filter_by(manager_id=user.id).all()}
    ids = member_ids | managed_ids
    if not ids:
        return []
    return Project.query.filter(Project.id.in_(ids), Project.is_archived == False).order_by(Project.name).all()  # noqa: E712


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
    project_id = request.form.get("project_id", type=int) or None

    task = Task(
        title=title,
        description=request.form.get("description", "").strip(),
        assigned_to_id=assignee.id,
        assigned_by_id=current_user.id,
        due_date=due_date,
        priority=request.form.get("priority", "medium"),
        status=TASK_PENDING,
        project_id=project_id,
    )
    db.session.add(task)
    db.session.flush()

    # Optional collaborators - other people working on this same task together with the
    # assignee. Only from the same pool this person is allowed to assign to in the first
    # place, and never the primary assignee themselves (already on the task).
    collaborator_ids = [int(i) for i in request.form.getlist("collaborator_ids") if i.isdigit()]
    collaborators = [allowed[i] for i in collaborator_ids if i in allowed and i != assignee.id]
    for collaborator in collaborators:
        db.session.add(TaskCollaborator(task_id=task.id, user_id=collaborator.id))
    db.session.commit()

    if collaborators:
        names = ", ".join(u.full_name for u in collaborators)
        notify(assignee.id, "task",
               f'{current_user.full_name} assigned you a shared task: "{title}" (with {names})',
               url=task_notification_url(task))
        for collaborator in collaborators:
            other_names = ", ".join(u.full_name for u in [assignee] + collaborators if u.id != collaborator.id)
            notify(collaborator.id, "task",
                   f'{current_user.full_name} added you to a shared task: "{title}" (with {other_names})',
                   url=task_notification_url(task))
    else:
        notify(assignee.id, "task", f'{current_user.full_name} assigned you a task: "{title}"',
               url=task_notification_url(task))
    db.session.commit()

    flash(f"Task assigned to {assignee.full_name}" + (f" and {len(collaborators)} collaborator(s)" if collaborators else "") + ".", "success")
    return redirect(url_for("tasks.index"))


@tasks_bp.route("/<int:task_id>")
@login_required
def detail(task_id):
    task = Task.query.get_or_404(task_id)
    if not can_view_task(current_user, task):
        abort(403)

    can_edit = can_edit_task(current_user, task)
    can_add_collaborator = _can_add_collaborator(current_user, task)
    can_add_watcher = _can_add_watcher(current_user, task)

    already_involved_ids = ({task.assigned_to_id} | {c.user_id for c in task.collaborators}
                             | {w.user_id for w in task.watchers})
    addable_users = (User.query.filter_by(is_active_employee=True)
                      .filter(User.id.notin_(already_involved_ids), User.role != ROLE_ADMIN)
                      .order_by(User.full_name).all())

    return render_template(
        "task_detail.html", task=task, can_edit=can_edit,
        can_add_collaborator=can_add_collaborator, can_add_watcher=can_add_watcher,
        addable_users=addable_users,
    )


@tasks_bp.route("/<int:task_id>/status", methods=["POST"])
@login_required
def update_status(task_id):
    task = Task.query.get_or_404(task_id)
    new_status = request.form.get("status")
    if not can_edit_task(current_user, task):
        abort(403)
    if new_status not in (TASK_PENDING, TASK_IN_PROGRESS, TASK_DONE):
        abort(400)
    task.status = new_status
    task.completed_at = datetime.utcnow() if new_status == TASK_DONE else None
    if new_status == TASK_DONE:
        task.percent_complete = 100
        draft_completed_task_into_report(task)
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
    if not can_edit_task(current_user, task):
        abort(403)

    percent = request.form.get("percent_complete", type=int)
    if percent is None or not (0 <= percent <= 100):
        abort(400)
    task.percent_complete = percent
    if percent == 100 and task.status != TASK_DONE:
        task.status = TASK_DONE
        task.completed_at = datetime.utcnow()
        draft_completed_task_into_report(task)
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
    if not can_view_task(current_user, task):
        abort(403)

    body = request.form.get("body", "").strip()
    if body:
        comment = TaskComment(task_id=task.id, user_id=current_user.id, body=body)
        db.session.add(comment)
        db.session.commit()

        recipients = ({task.assigned_to_id, task.assigned_by_id}
                       | {c.user_id for c in task.collaborators}
                       | {w.user_id for w in task.watchers}) - {current_user.id}
        for recipient_id in recipients:
            notify(recipient_id, "task", f'{current_user.full_name} commented on "{task.title}"',
                   url=task_notification_url(task))
        db.session.commit()
    return redirect(url_for("tasks.detail", task_id=task.id))


@tasks_bp.route("/<int:task_id>/comment/<int:comment_id>/edit", methods=["POST"])
@login_required
def edit_comment(task_id, comment_id):
    comment = TaskComment.query.filter_by(id=comment_id, task_id=task_id).first_or_404()
    if comment.user_id != current_user.id:
        abort(403)
    body = request.form.get("body", "").strip()
    if body:
        comment.body = body
        comment.edited_at = datetime.utcnow()
        db.session.commit()
        flash("Comment updated.", "success")
    return redirect(url_for("tasks.detail", task_id=task_id))


@tasks_bp.route("/<int:task_id>/comment/<int:comment_id>/delete", methods=["POST"])
@login_required
def delete_comment(task_id, comment_id):
    comment = TaskComment.query.filter_by(id=comment_id, task_id=task_id).first_or_404()
    if comment.user_id != current_user.id and current_user.role != ROLE_ADMIN:
        abort(403)
    db.session.delete(comment)
    db.session.commit()
    flash("Comment deleted.", "success")
    return redirect(url_for("tasks.detail", task_id=task_id))


def _can_add_collaborator(user, task):
    """Turning a task into a shared one is up to whoever assigned it, or admin."""
    return user.role == ROLE_ADMIN or task.assigned_by_id == user.id


def _can_add_watcher(user, task):
    """Giving someone view-only visibility into a task: admin, whoever assigned this
    particular task (if they're a manager), or any manager who oversees the assignee."""
    if user.role == ROLE_ADMIN:
        return True
    if user.role == ROLE_MANAGER:
        return task.assigned_by_id == user.id or user.can_manage(task.assigned_to)
    return False


@tasks_bp.route("/<int:task_id>/collaborators/add", methods=["POST"])
@login_required
def add_collaborator(task_id):
    task = Task.query.get_or_404(task_id)
    if not _can_add_collaborator(current_user, task):
        abort(403)
    user_id = request.form.get("user_id", type=int)
    user = User.query.get(user_id)
    if not user or user.id == task.assigned_to_id:
        abort(400)
    if not TaskCollaborator.query.filter_by(task_id=task.id, user_id=user.id).first():
        db.session.add(TaskCollaborator(task_id=task.id, user_id=user.id))
        db.session.commit()
        existing = ", ".join(u.full_name for u in [task.assigned_to] + task.collaborator_users if u.id != user.id)
        notify(user.id, "task", f'{current_user.full_name} added you to a shared task: "{task.title}" (with {existing})',
               url=task_notification_url(task))
        notify(task.assigned_to_id, "task", f'{user.full_name} joined the shared task "{task.title}"',
               url=task_notification_url(task))
        db.session.commit()
        flash(f"{user.full_name} added as a collaborator.", "success")
    return redirect(url_for("tasks.detail", task_id=task.id))


@tasks_bp.route("/<int:task_id>/collaborators/<int:user_id>/remove", methods=["POST"])
@login_required
def remove_collaborator(task_id, user_id):
    task = Task.query.get_or_404(task_id)
    if not _can_add_collaborator(current_user, task):
        abort(403)
    row = TaskCollaborator.query.filter_by(task_id=task.id, user_id=user_id).first_or_404()
    db.session.delete(row)
    db.session.commit()
    flash("Removed from the shared task.", "success")
    return redirect(url_for("tasks.detail", task_id=task.id))


@tasks_bp.route("/<int:task_id>/watchers/add", methods=["POST"])
@login_required
def add_watcher(task_id):
    task = Task.query.get_or_404(task_id)
    if not _can_add_watcher(current_user, task):
        abort(403)
    user_id = request.form.get("user_id", type=int)
    user = User.query.get(user_id)
    if not user:
        abort(400)
    if not TaskWatcher.query.filter_by(task_id=task.id, user_id=user.id).first():
        db.session.add(TaskWatcher(task_id=task.id, user_id=user.id))
        db.session.commit()
        notify(user.id, "task",
               f'{current_user.full_name} shared a task with you to follow: "{task.title}" (view only)',
               url=task_notification_url(task))
        db.session.commit()
        flash(f"{user.full_name} can now see this task (view only).", "success")
    return redirect(url_for("tasks.detail", task_id=task.id))


@tasks_bp.route("/<int:task_id>/watchers/<int:user_id>/remove", methods=["POST"])
@login_required
def remove_watcher(task_id, user_id):
    task = Task.query.get_or_404(task_id)
    if not _can_add_watcher(current_user, task):
        abort(403)
    row = TaskWatcher.query.filter_by(task_id=task.id, user_id=user_id).first_or_404()
    db.session.delete(row)
    db.session.commit()
    flash("Removed their visibility into this task.", "success")
    return redirect(url_for("tasks.detail", task_id=task.id))
