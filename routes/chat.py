import os
import re
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, jsonify, current_app, send_from_directory
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from models import (
    db, User, Task, Project, ProjectMember, ProjectMilestone, ProjectRiskIssue,
    ChatMessage, ChatReaction, ChatReadMarker, REACTION_EMOJIS, ROLE_ADMIN, log_project_activity,
)
from notifications import notify

chat_bp = Blueprint("chat", __name__)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "pdf", "doc", "docx",
                       "xls", "xlsx", "csv", "txt", "dwg", "zip"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# NOTE: files are saved to the app's local filesystem. On Render's standard plan this
# storage is EPHEMERAL - it's wiped on every redeploy/restart, same as the "started
# on SQLite" issue discussed earlier for the database. This is fine for casual use but
# don't rely on it for anything you can't afford to lose; a persistent disk or an
# external bucket (S3-compatible) would be needed for durable attachments.


def _upload_dir():
    d = os.path.join(current_app.root_path, "static", "uploads", "chat")
    os.makedirs(d, exist_ok=True)
    return d


def _can_post_in(project):
    if project is None:
        return True  # anyone can post in the general company-wide chat
    if current_user.role == ROLE_ADMIN or project.manager_id == current_user.id:
        return True
    return bool(ProjectMember.query.filter_by(project_id=project.id, user_id=current_user.id).first())


def _can_manage_message(msg):
    return current_user.role == ROLE_ADMIN or msg.author_id == current_user.id


def _can_pin_in(project):
    if current_user.role == ROLE_ADMIN:
        return True
    return bool(project and project.manager_id == current_user.id)


def _scope_members(project):
    """Who's in this chat's audience, for @mention matching and reply targets."""
    if project is None:
        return User.query.filter_by(is_active_employee=True).all()
    return [m.user for m in project.members] + ([project.manager] if project.manager else [])


def _find_mentions(message, project):
    """Returns the set of user ids @mentioned in this message's body (by full name or
    username), without sending anything - notification dispatch happens in
    _notify_chat_scope so each person gets exactly one, tailored notification."""
    body_lower = message.body.lower()
    mentioned = set()
    for user in _scope_members(project):
        if user.id == message.author_id:
            continue
        handles = [f"@{user.full_name}".lower(), f"@{user.username}".lower()]
        if any(h in body_lower for h in handles):
            mentioned.add(user.id)
    return mentioned


def _notify_chat_scope(message, project, parent):
    """A real notification for the whole team, not just @mentions: everyone in this
    chat's scope (the whole company for general chat, or the whole project team for a
    project chat) gets notified of a new message - except the author. Each person gets
    exactly one notification, upgraded to something more specific if it's relevant to
    them (they were mentioned, or it's a reply to their own message)."""
    scope_label = f'the "{project.name}" project chat' if project else "the team chat"
    mentioned_ids = _find_mentions(message, project)

    for user in _scope_members(project):
        if user.id == message.author_id:
            continue
        if user.id in mentioned_ids:
            notify(user.id, "chat", f'{message.author.full_name} mentioned you in chat',
                   url=_message_url(message))
        elif parent and parent.author_id == user.id:
            notify(user.id, "chat", f'{message.author.full_name} replied to your message in {scope_label}',
                   url=_message_url(message))
        else:
            notify(user.id, "chat", f'{message.author.full_name} posted in {scope_label}',
                   url=_message_url(message))


def _message_url(message):
    if message.project_id:
        return url_for("chat.project_chat", project_id=message.project_id, _anchor=f"msg-{message.id}")
    return url_for("chat.general", _anchor=f"msg-{message.id}")


def _visible_projects_for_save():
    if current_user.role == ROLE_ADMIN:
        return Project.query.filter_by(is_archived=False).order_by(Project.name).all()
    member_ids = {m.project_id for m in ProjectMember.query.filter_by(user_id=current_user.id).all()}
    managed_ids = {p.id for p in Project.query.filter_by(manager_id=current_user.id).all()}
    ids = member_ids | managed_ids
    if not ids:
        return []
    return Project.query.filter(Project.id.in_(ids), Project.is_archived == False).order_by(Project.name).all()  # noqa: E712


def _render_chat_page(project):
    project_id = project.id if project else None

    root_messages = (ChatMessage.query
                      .filter(ChatMessage.project_id == project_id, ChatMessage.parent_id.is_(None))
                      .order_by(ChatMessage.created_at.asc()).all())
    for m in root_messages:
        m.replies.sort(key=lambda r: r.created_at)

    pinned = [m for m in root_messages if m.is_pinned and not m.is_deleted]

    marker = ChatReadMarker.query.filter_by(user_id=current_user.id, project_id=project_id).first()
    all_ids = [m.id for m in root_messages] + [r.id for m in root_messages for r in m.replies]
    if marker:
        marker.last_read_message_id = max(all_ids + [marker.last_read_message_id]) if all_ids else marker.last_read_message_id
    else:
        db.session.add(ChatReadMarker(user_id=current_user.id, project_id=project_id,
                                       last_read_message_id=max(all_ids) if all_ids else 0))
    db.session.commit()

    linkable_tasks = []
    if project:
        linkable_tasks = Task.query.filter_by(project_id=project.id).order_by(Task.title).all()

    return render_template(
        "chat.html",
        project=project,
        root_messages=root_messages,
        pinned=pinned,
        reaction_emojis=REACTION_EMOJIS,
        can_post=_can_post_in(project),
        can_pin=_can_pin_in(project),
        projects_for_save=_visible_projects_for_save(),
        linkable_tasks=linkable_tasks,
        query=request.args.get("q", ""),
    )


@chat_bp.route("/chat/")
@login_required
def general():
    return _render_chat_page(None)


@chat_bp.route("/projects/<int:project_id>/chat")
@login_required
def project_chat(project_id):
    project = Project.query.get_or_404(project_id)
    if current_user.role != ROLE_ADMIN:
        is_member = ProjectMember.query.filter_by(project_id=project.id, user_id=current_user.id).first()
        if not is_member and project.manager_id != current_user.id:
            abort(403)
    return _render_chat_page(project)


def _back_url(project, anchor=None):
    if project:
        return url_for("chat.project_chat", project_id=project.id, _anchor=anchor)
    return url_for("chat.general", _anchor=anchor)


@chat_bp.route("/chat/send", methods=["POST"])
@login_required
def send():
    project_id = request.form.get("project_id", type=int)
    project = Project.query.get_or_404(project_id) if project_id else None
    if not _can_post_in(project):
        abort(403)

    body = request.form.get("body", "").strip()
    parent_id = request.form.get("parent_id", type=int) or None
    linked_task_id = request.form.get("linked_task_id", type=int) or None
    linked_milestone_id = request.form.get("linked_milestone_id", type=int) or None
    linked_risk_id = request.form.get("linked_risk_id", type=int) or None

    attachment_filename = None
    attachment_original_name = None
    file = request.files.get("attachment")
    if file and file.filename:
        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if ext not in ALLOWED_EXTENSIONS:
            flash(f"File type .{ext} isn't allowed.", "error")
            return redirect(_back_url(project))
        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(0)
        if size > MAX_FILE_SIZE:
            flash("File is too large (10MB max).", "error")
            return redirect(_back_url(project))
        safe_name = secure_filename(file.filename)
        stored_name = f"{datetime.utcnow().timestamp():.0f}_{safe_name}"
        file.save(os.path.join(_upload_dir(), stored_name))
        attachment_filename = stored_name
        attachment_original_name = safe_name

    if not body and not attachment_filename:
        flash("Write something or attach a file.", "error")
        return redirect(_back_url(project))

    msg = ChatMessage(
        project_id=project.id if project else None,
        author_id=current_user.id,
        parent_id=parent_id,
        body=body or "(attachment)",
        linked_task_id=linked_task_id,
        linked_milestone_id=linked_milestone_id,
        linked_risk_id=linked_risk_id,
        attachment_filename=attachment_filename,
        attachment_original_name=attachment_original_name,
    )
    db.session.add(msg)
    db.session.flush()

    parent = ChatMessage.query.get(parent_id) if parent_id else None
    _notify_chat_scope(msg, project, parent)

    if project:
        log_project_activity(project.id, current_user.id, f"{current_user.full_name} posted in project chat")

    db.session.commit()
    return redirect(_back_url(project, anchor=f"msg-{msg.id}"))


@chat_bp.route("/chat/<int:msg_id>/edit", methods=["POST"])
@login_required
def edit_message(msg_id):
    msg = ChatMessage.query.get_or_404(msg_id)
    if not _can_manage_message(msg):
        abort(403)
    body = request.form.get("body", "").strip()
    if body:
        msg.body = body
        msg.edited_at = datetime.utcnow()
        db.session.commit()
    return redirect(_back_url(msg.project, anchor=f"msg-{msg.id}"))


@chat_bp.route("/chat/<int:msg_id>/delete", methods=["POST"])
@login_required
def delete_message(msg_id):
    msg = ChatMessage.query.get_or_404(msg_id)
    if not _can_manage_message(msg):
        abort(403)
    msg.is_deleted = True
    msg.body = ""
    db.session.commit()
    return redirect(_back_url(msg.project))


@chat_bp.route("/chat/<int:msg_id>/react", methods=["POST"])
@login_required
def react(msg_id):
    msg = ChatMessage.query.get_or_404(msg_id)
    emoji = request.form.get("emoji", "")
    if emoji not in REACTION_EMOJIS:
        abort(400)
    existing = ChatReaction.query.filter_by(message_id=msg.id, user_id=current_user.id, emoji=emoji).first()
    if existing:
        db.session.delete(existing)
    else:
        db.session.add(ChatReaction(message_id=msg.id, user_id=current_user.id, emoji=emoji))
    db.session.commit()
    return redirect(_back_url(msg.project, anchor=f"msg-{msg.id}"))


@chat_bp.route("/chat/<int:msg_id>/pin", methods=["POST"])
@login_required
def toggle_pin(msg_id):
    msg = ChatMessage.query.get_or_404(msg_id)
    if not _can_pin_in(msg.project):
        abort(403)
    msg.is_pinned = not msg.is_pinned
    db.session.commit()
    return redirect(_back_url(msg.project, anchor=f"msg-{msg.id}"))


@chat_bp.route("/chat/<int:msg_id>/save", methods=["POST"])
@login_required
def save_to_project(msg_id):
    msg = ChatMessage.query.get_or_404(msg_id)
    project_id = request.form.get("save_project_id", type=int)
    project = Project.query.get_or_404(project_id)
    if current_user.role != ROLE_ADMIN:
        is_member = ProjectMember.query.filter_by(project_id=project.id, user_id=current_user.id).first()
        if not is_member and project.manager_id != current_user.id:
            abort(403)
    msg.saved_to_project_id = project.id
    log_project_activity(project.id, current_user.id,
                          f"{current_user.full_name} saved a chat message to this project's notes")
    db.session.commit()
    flash(f'Saved to "{project.name}".', "success")
    return redirect(_back_url(msg.project, anchor=f"msg-{msg.id}"))


@chat_bp.route("/chat/<int:msg_id>/unsave", methods=["POST"])
@login_required
def unsave_from_project(msg_id):
    msg = ChatMessage.query.get_or_404(msg_id)
    if not _can_manage_message(msg) and current_user.role != ROLE_ADMIN:
        abort(403)
    msg.saved_to_project_id = None
    db.session.commit()
    return redirect(request.referrer or url_for("chat.general"))


@chat_bp.route("/chat/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    project_id = request.args.get("project_id", type=int)
    project = Project.query.get(project_id) if project_id else None

    results = []
    if q:
        results = (ChatMessage.query.filter(
            ChatMessage.project_id == (project.id if project else None),
            ChatMessage.is_deleted == False,  # noqa: E712
            ChatMessage.body.ilike(f"%{q}%"),
        ).order_by(ChatMessage.created_at.desc()).limit(50).all())

    return render_template("chat_search.html", results=results, q=q, project=project)


@chat_bp.route("/uploads/chat/<path:filename>")
@login_required
def uploaded_file(filename):
    return send_from_directory(_upload_dir(), filename)


@chat_bp.route("/api/chat/unread")
@login_required
def unread_counts():
    """General-chat unread count, for a small nav badge."""
    marker = ChatReadMarker.query.filter_by(user_id=current_user.id, project_id=None).first()
    last_read = marker.last_read_message_id if marker else 0
    count = ChatMessage.query.filter(
        ChatMessage.project_id.is_(None),
        ChatMessage.id > last_read,
        ChatMessage.author_id != current_user.id,
        ChatMessage.is_deleted == False,  # noqa: E712
    ).count()
    return jsonify({"count": count})
