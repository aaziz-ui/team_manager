from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from models import db, User, Note, NoteShare
from notifications import notify

notes_bp = Blueprint("notes", __name__, url_prefix="/notes")


def _can_view_note(user, note):
    if user.role == "admin" or note.owner_id == user.id:
        return True
    return NoteShare.query.filter_by(note_id=note.id, shared_with_id=user.id).first() is not None


def _can_edit_note(user, note):
    """Only the owner (or admin, for cleanup/moderation) can edit or delete a note -
    sharing a note gives view access, not write access."""
    return user.role == "admin" or note.owner_id == user.id


@notes_bp.route("/")
@login_required
def index():
    my_notes = Note.query.filter_by(owner_id=current_user.id).order_by(Note.updated_at.desc()).all()
    shared_note_ids = [s.note_id for s in NoteShare.query.filter_by(shared_with_id=current_user.id).all()]
    shared_with_me = (Note.query.filter(Note.id.in_(shared_note_ids)).order_by(Note.updated_at.desc()).all()
                       if shared_note_ids else [])
    return render_template("notes_list.html", my_notes=my_notes, shared_with_me=shared_with_me)


@notes_bp.route("/new", methods=["POST"])
@login_required
def new():
    title = request.form.get("title", "").strip() or "Untitled note"
    content = request.form.get("content", "").strip()
    note = Note(owner_id=current_user.id, title=title, content=content)
    db.session.add(note)
    db.session.commit()
    flash("Note created.", "success")
    return redirect(url_for("notes.edit", note_id=note.id))


@notes_bp.route("/<int:note_id>/edit", methods=["GET", "POST"])
@login_required
def edit(note_id):
    note = Note.query.get_or_404(note_id)
    if not _can_view_note(current_user, note):
        abort(403)
    can_edit = _can_edit_note(current_user, note)

    if request.method == "POST":
        if not can_edit:
            abort(403)
        note.title = request.form.get("title", "").strip() or "Untitled note"
        note.content = request.form.get("content", "").strip()
        note.updated_at = datetime.utcnow()
        db.session.commit()
        flash("Note saved.", "success")
        return redirect(url_for("notes.edit", note_id=note.id))

    all_users = User.query.filter(User.id != current_user.id, User.is_active_employee == True).order_by(  # noqa: E712
        User.full_name).all()
    already_shared_ids = {s.shared_with_id for s in note.shares}
    shareable_users = [u for u in all_users if u.id not in already_shared_ids]

    return render_template("note_edit.html", note=note, can_edit=can_edit, shareable_users=shareable_users)


@notes_bp.route("/<int:note_id>/delete", methods=["POST"])
@login_required
def delete(note_id):
    note = Note.query.get_or_404(note_id)
    if not _can_edit_note(current_user, note):
        abort(403)
    db.session.delete(note)
    db.session.commit()
    flash("Note deleted.", "success")
    return redirect(url_for("notes.index"))


@notes_bp.route("/<int:note_id>/share", methods=["POST"])
@login_required
def share(note_id):
    note = Note.query.get_or_404(note_id)
    if not _can_edit_note(current_user, note):
        abort(403)
    user_id = request.form.get("user_id", type=int)
    user = User.query.get(user_id)
    if not user:
        abort(400)
    if not NoteShare.query.filter_by(note_id=note.id, shared_with_id=user.id).first():
        db.session.add(NoteShare(note_id=note.id, shared_with_id=user.id))
        db.session.commit()
        notify(user.id, "chat", f'{current_user.full_name} shared a note with you: "{note.title}"',
               url=url_for("notes.edit", note_id=note.id))
        db.session.commit()
        flash(f"Shared with {user.full_name}.", "success")
    return redirect(url_for("notes.edit", note_id=note.id))


@notes_bp.route("/<int:note_id>/unshare/<int:user_id>", methods=["POST"])
@login_required
def unshare(note_id, user_id):
    note = Note.query.get_or_404(note_id)
    if not _can_edit_note(current_user, note):
        abort(403)
    share_row = NoteShare.query.filter_by(note_id=note.id, shared_with_id=user_id).first_or_404()
    db.session.delete(share_row)
    db.session.commit()
    flash("Removed their access.", "success")
    return redirect(url_for("notes.edit", note_id=note.id))
