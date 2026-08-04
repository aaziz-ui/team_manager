from datetime import datetime, date
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from models import (
    db, User, Task, Project, ProjectMember, ProjectDiscipline, ProjectMilestone,
    ProjectNotice, ProjectRiskIssue, ChatMessage,
    TASK_DONE, ROLE_ADMIN, PROJECT_STATUSES, PROJECT_STATUS_LABELS, log_project_activity,
)
from notifications import notify

projects_bp = Blueprint("projects", __name__, url_prefix="/projects")


def _can_manage_project(user, project):
    """Admins, or the project's own manager, can edit project settings / sub-resources."""
    return user.role == ROLE_ADMIN or (project.manager_id == user.id)


def _is_project_participant(user, project):
    """Admin, the project's manager, or any team member - used for the content sections
    (notices, milestones, risks/issues, discipline progress) that any team member should
    be able to add/update, even though only admins can delete entries there."""
    if user.role == ROLE_ADMIN or project.manager_id == user.id:
        return True
    return bool(ProjectMember.query.filter_by(project_id=project.id, user_id=user.id).first())


def _visible_projects_for(user):
    if user.role == ROLE_ADMIN:
        return Project.query.filter_by(is_archived=False).order_by(Project.name).all()
    member_project_ids = {m.project_id for m in ProjectMember.query.filter_by(user_id=user.id).all()}
    managed_project_ids = {p.id for p in Project.query.filter_by(manager_id=user.id).all()}
    ids = member_project_ids | managed_project_ids
    if not ids:
        return []
    return (Project.query.filter(Project.id.in_(ids), Project.is_archived == False)  # noqa: E712
            .order_by(Project.name).all())


def _require_project_access(project):
    if current_user.role == ROLE_ADMIN:
        return
    is_member = ProjectMember.query.filter_by(project_id=project.id, user_id=current_user.id).first()
    if not is_member and project.manager_id != current_user.id:
        abort(403)


@projects_bp.route("/")
@login_required
def index():
    projects = _visible_projects_for(current_user)
    all_possible_managers = User.query.filter(User.role.in_(["admin", "manager"]),
                                               User.is_active_employee == True).order_by(User.full_name).all()  # noqa: E712
    return render_template("projects_list.html", projects=projects,
                            can_create=current_user.role == ROLE_ADMIN,
                            all_possible_managers=all_possible_managers)


@projects_bp.route("/new", methods=["POST"])
@login_required
def new():
    if current_user.role != ROLE_ADMIN:
        abort(403)
    name = request.form.get("name", "").strip()
    if not name:
        flash("Project name is required.", "error")
        return redirect(url_for("projects.index"))

    start_date_str = request.form.get("start_date")
    target_date_str = request.form.get("target_date")
    project = Project(
        name=name,
        client=request.form.get("client", "").strip(),
        manager_id=request.form.get("manager_id", type=int) or None,
        phase=request.form.get("phase", "").strip(),
        status="on_track",
        start_date=datetime.strptime(start_date_str, "%Y-%m-%d").date() if start_date_str else None,
        target_date=datetime.strptime(target_date_str, "%Y-%m-%d").date() if target_date_str else None,
        description=request.form.get("description", "").strip(),
    )
    db.session.add(project)
    db.session.flush()
    log_project_activity(project.id, current_user.id, f"{current_user.full_name} created the project")
    db.session.commit()
    flash(f'Project "{name}" created.', "success")
    return redirect(url_for("projects.detail", project_id=project.id))


@projects_bp.route("/<int:project_id>")
@login_required
def detail(project_id):
    project = Project.query.get_or_404(project_id)
    _require_project_access(project)

    today = date.today()
    overdue_tasks = (Task.query.filter(Task.project_id == project.id, Task.due_date < today,
                                        Task.status != TASK_DONE)
                      .order_by(Task.due_date.asc()).all())
    upcoming_milestones = [m for m in project.milestones if not m.is_done]
    recent_activity = (project.activities and
                        sorted(project.activities, key=lambda a: a.created_at, reverse=True)[:20]) or []
    open_risks = [r for r in project.risks if r.status != "closed"]
    pinned_notices = sorted(project.notices, key=lambda n: n.created_at, reverse=True)
    saved_messages = (ChatMessage.query.filter_by(saved_to_project_id=project.id, is_deleted=False)
                       .order_by(ChatMessage.created_at.desc()).limit(20).all())

    all_active_users = User.query.filter_by(is_active_employee=True).order_by(User.full_name).all()
    member_ids = {m.user_id for m in project.members}
    addable_users = [u for u in all_active_users if u.id not in member_ids]

    return render_template(
        "project_dashboard.html",
        project=project,
        overdue_tasks=overdue_tasks,
        upcoming_milestones=upcoming_milestones,
        recent_activity=recent_activity,
        open_risks=open_risks,
        all_risks=sorted(project.risks, key=lambda r: r.created_at, reverse=True),
        pinned_notices=pinned_notices,
        saved_messages=saved_messages,
        addable_users=addable_users,
        can_manage=_can_manage_project(current_user, project),
        can_edit_content=_is_project_participant(current_user, project),
        can_delete_content=current_user.role == ROLE_ADMIN,
        status_labels=PROJECT_STATUS_LABELS,
        statuses=PROJECT_STATUSES,
        today=today,
    )


@projects_bp.route("/<int:project_id>/edit", methods=["GET", "POST"])
@login_required
def edit(project_id):
    project = Project.query.get_or_404(project_id)
    if not _can_manage_project(current_user, project):
        abort(403)

    if request.method == "POST":
        project.name = request.form.get("name", project.name).strip()
        project.client = request.form.get("client", "").strip()
        project.manager_id = request.form.get("manager_id", type=int) or None
        project.phase = request.form.get("phase", "").strip()
        project.status = request.form.get("status", project.status)
        project.description = request.form.get("description", "").strip()
        start_date_str = request.form.get("start_date")
        target_date_str = request.form.get("target_date")
        project.start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date() if start_date_str else None
        project.target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date() if target_date_str else None
        overall = request.form.get("overall_progress", type=int)
        if overall is not None:
            project.overall_progress = max(0, min(100, overall))
        log_project_activity(project.id, current_user.id, f"{current_user.full_name} updated project details")
        db.session.commit()
        flash("Project updated.", "success")
        return redirect(url_for("projects.detail", project_id=project.id))

    all_possible_managers = User.query.filter(User.role.in_(["admin", "manager"]),
                                               User.is_active_employee == True).order_by(User.full_name).all()  # noqa: E712
    return render_template("project_edit.html", project=project, all_possible_managers=all_possible_managers,
                            statuses=PROJECT_STATUSES, status_labels=PROJECT_STATUS_LABELS)


@projects_bp.route("/<int:project_id>/archive", methods=["POST"])
@login_required
def archive(project_id):
    project = Project.query.get_or_404(project_id)
    if current_user.role != ROLE_ADMIN:
        abort(403)
    project.is_archived = not project.is_archived
    db.session.commit()
    flash("Project archived." if project.is_archived else "Project restored.", "success")
    return redirect(url_for("projects.index"))


# --- Members ---

@projects_bp.route("/<int:project_id>/members/add", methods=["POST"])
@login_required
def add_member(project_id):
    project = Project.query.get_or_404(project_id)
    if not _can_manage_project(current_user, project):
        abort(403)
    user_id = request.form.get("user_id", type=int)
    user = User.query.get(user_id)
    if not user:
        abort(400)
    if not ProjectMember.query.filter_by(project_id=project.id, user_id=user.id).first():
        db.session.add(ProjectMember(project_id=project.id, user_id=user.id,
                                      role_on_project=request.form.get("role_on_project", "").strip()))
        log_project_activity(project.id, current_user.id, f"{user.full_name} was added to the team")
        notify(user.id, "task", f'{current_user.full_name} added you to project "{project.name}"',
               url=url_for("projects.detail", project_id=project.id))
        db.session.commit()
        flash(f"{user.full_name} added to the project.", "success")
    return redirect(url_for("projects.detail", project_id=project.id))


@projects_bp.route("/<int:project_id>/members/<int:user_id>/remove", methods=["POST"])
@login_required
def remove_member(project_id, user_id):
    project = Project.query.get_or_404(project_id)
    if not _can_manage_project(current_user, project):
        abort(403)
    member = ProjectMember.query.filter_by(project_id=project.id, user_id=user_id).first_or_404()
    db.session.delete(member)
    db.session.commit()
    flash("Removed from project.", "success")
    return redirect(url_for("projects.detail", project_id=project.id))


# --- Disciplines (engineering progress) ---

@projects_bp.route("/<int:project_id>/disciplines/add", methods=["POST"])
@login_required
def add_discipline(project_id):
    project = Project.query.get_or_404(project_id)
    if not _is_project_participant(current_user, project):
        abort(403)
    name = request.form.get("name", "").strip()
    if name:
        db.session.add(ProjectDiscipline(project_id=project.id, name=name,
                                          percent_complete=request.form.get("percent_complete", type=int) or 0))
        db.session.commit()
        flash(f"Added {name}.", "success")
    return redirect(url_for("projects.detail", project_id=project.id))


@projects_bp.route("/<int:project_id>/disciplines/<int:discipline_id>/update", methods=["POST"])
@login_required
def update_discipline(project_id, discipline_id):
    project = Project.query.get_or_404(project_id)
    if not _is_project_participant(current_user, project):
        abort(403)
    disc = ProjectDiscipline.query.filter_by(id=discipline_id, project_id=project.id).first_or_404()
    pct = request.form.get("percent_complete", type=int)
    if pct is not None:
        disc.percent_complete = max(0, min(100, pct))
        disc.updated_at = datetime.utcnow()
        log_project_activity(project.id, current_user.id,
                              f"{current_user.full_name} updated {disc.name} progress to {disc.percent_complete}%")
        db.session.commit()
    return redirect(url_for("projects.detail", project_id=project.id))


@projects_bp.route("/<int:project_id>/disciplines/<int:discipline_id>/delete", methods=["POST"])
@login_required
def delete_discipline(project_id, discipline_id):
    project = Project.query.get_or_404(project_id)
    if current_user.role != ROLE_ADMIN:  # only admins can delete project content
        abort(403)
    disc = ProjectDiscipline.query.filter_by(id=discipline_id, project_id=project.id).first_or_404()
    db.session.delete(disc)
    db.session.commit()
    return redirect(url_for("projects.detail", project_id=project.id))


# --- Milestones ---

@projects_bp.route("/<int:project_id>/milestones/add", methods=["POST"])
@login_required
def add_milestone(project_id):
    project = Project.query.get_or_404(project_id)
    if not _is_project_participant(current_user, project):
        abort(403)
    title = request.form.get("title", "").strip()
    if not title:
        flash("Milestone title is required.", "error")
        return redirect(url_for("projects.detail", project_id=project.id))
    due_date_str = request.form.get("due_date")
    m = ProjectMilestone(project_id=project.id, title=title,
                          due_date=datetime.strptime(due_date_str, "%Y-%m-%d").date() if due_date_str else None)
    db.session.add(m)
    log_project_activity(project.id, current_user.id, f'{current_user.full_name} added milestone "{title}"')
    db.session.commit()
    flash("Milestone added.", "success")
    return redirect(url_for("projects.detail", project_id=project.id))


@projects_bp.route("/<int:project_id>/milestones/<int:milestone_id>/toggle", methods=["POST"])
@login_required
def toggle_milestone(project_id, milestone_id):
    project = Project.query.get_or_404(project_id)
    if not _is_project_participant(current_user, project):
        abort(403)
    m = ProjectMilestone.query.filter_by(id=milestone_id, project_id=project.id).first_or_404()
    m.is_done = not m.is_done
    m.completed_at = datetime.utcnow() if m.is_done else None
    log_project_activity(project.id, current_user.id,
                          f'{current_user.full_name} marked milestone "{m.title}" as '
                          f'{"done" if m.is_done else "not done"}')
    db.session.commit()
    return redirect(url_for("projects.detail", project_id=project.id))


@projects_bp.route("/<int:project_id>/milestones/<int:milestone_id>/delete", methods=["POST"])
@login_required
def delete_milestone(project_id, milestone_id):
    project = Project.query.get_or_404(project_id)
    if current_user.role != ROLE_ADMIN:  # only admins can delete project content
        abort(403)
    m = ProjectMilestone.query.filter_by(id=milestone_id, project_id=project.id).first_or_404()
    db.session.delete(m)
    db.session.commit()
    return redirect(url_for("projects.detail", project_id=project.id))


# --- Notices ---

@projects_bp.route("/<int:project_id>/notices/add", methods=["POST"])
@login_required
def add_notice(project_id):
    project = Project.query.get_or_404(project_id)
    if not _is_project_participant(current_user, project):
        abort(403)
    content = request.form.get("content", "").strip()
    if content:
        db.session.add(ProjectNotice(project_id=project.id, author_id=current_user.id, content=content))
        log_project_activity(project.id, current_user.id, f"{current_user.full_name} pinned a notice")
        db.session.commit()
        flash("Notice pinned.", "success")
    return redirect(url_for("projects.detail", project_id=project.id))


@projects_bp.route("/<int:project_id>/notices/<int:notice_id>/delete", methods=["POST"])
@login_required
def delete_notice(project_id, notice_id):
    project = Project.query.get_or_404(project_id)
    if current_user.role != ROLE_ADMIN:  # only admins can delete project content
        abort(403)
    n = ProjectNotice.query.filter_by(id=notice_id, project_id=project.id).first_or_404()
    db.session.delete(n)
    db.session.commit()
    return redirect(url_for("projects.detail", project_id=project.id))


# --- Risks / Issues ---

@projects_bp.route("/<int:project_id>/risks/add", methods=["POST"])
@login_required
def add_risk(project_id):
    project = Project.query.get_or_404(project_id)
    _require_project_access(project)  # any project member can flag a risk/issue, not just the PM
    title = request.form.get("title", "").strip()
    if not title:
        flash("Title is required.", "error")
        return redirect(url_for("projects.detail", project_id=project.id))
    r = ProjectRiskIssue(
        project_id=project.id,
        kind=request.form.get("kind", "risk") if request.form.get("kind") in ("risk", "issue") else "risk",
        title=title,
        description=request.form.get("description", "").strip(),
        severity=request.form.get("severity", "medium"),
        created_by_id=current_user.id,
    )
    db.session.add(r)
    log_project_activity(project.id, current_user.id, f'{current_user.full_name} flagged a {r.kind}: "{title}"')
    db.session.commit()
    flash(f"{r.kind.capitalize()} added.", "success")
    return redirect(url_for("projects.detail", project_id=project.id))


@projects_bp.route("/<int:project_id>/risks/<int:risk_id>/status", methods=["POST"])
@login_required
def update_risk_status(project_id, risk_id):
    project = Project.query.get_or_404(project_id)
    if not _is_project_participant(current_user, project):
        abort(403)
    r = ProjectRiskIssue.query.filter_by(id=risk_id, project_id=project.id).first_or_404()
    new_status = request.form.get("status")
    if new_status in ("open", "mitigated", "closed"):
        r.status = new_status
        log_project_activity(project.id, current_user.id,
                              f'{current_user.full_name} marked "{r.title}" as {new_status}')
        db.session.commit()
    return redirect(url_for("projects.detail", project_id=project.id))


@projects_bp.route("/<int:project_id>/risks/<int:risk_id>/delete", methods=["POST"])
@login_required
def delete_risk(project_id, risk_id):
    project = Project.query.get_or_404(project_id)
    if current_user.role != ROLE_ADMIN:  # only admins can delete project content
        abort(403)
    r = ProjectRiskIssue.query.filter_by(id=risk_id, project_id=project.id).first_or_404()
    db.session.delete(r)
    db.session.commit()
    return redirect(url_for("projects.detail", project_id=project.id))
