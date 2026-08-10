from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from models import db, User, NewsPost, ROLE_ADMIN, ROLE_MANAGER
from notifications import notify

news_bp = Blueprint("news", __name__, url_prefix="/news")


def _can_post_news(user):
    return user.role in (ROLE_ADMIN, ROLE_MANAGER)


def _can_edit_news(user, post):
    """Admin can edit/delete anything; a manager can edit/delete only their own posts,
    not another manager's."""
    return user.role == ROLE_ADMIN or post.author_id == user.id


@news_bp.route("/")
@login_required
def index():
    posts = NewsPost.query.order_by(NewsPost.created_at.desc()).all()
    return render_template("news_list.html", posts=posts, can_post=_can_post_news(current_user))


@news_bp.route("/new", methods=["POST"])
@login_required
def new():
    if not _can_post_news(current_user):
        abort(403)
    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()
    if not title:
        flash("A title is required for every news post.", "error")
        return redirect(url_for("news.index"))

    post = NewsPost(title=title, content=content, author_id=current_user.id)
    db.session.add(post)
    db.session.commit()

    recipients = User.query.filter(User.is_active_employee == True, User.id != current_user.id).all()  # noqa: E712
    for u in recipients:
        notify(u.id, "news", f'{current_user.full_name} posted company news: "{title}"',
               url=url_for("news.index", _anchor=f"news-{post.id}"))
    db.session.commit()

    flash("News posted.", "success")
    return redirect(url_for("news.index"))


@news_bp.route("/<int:post_id>/edit", methods=["POST"])
@login_required
def edit(post_id):
    post = NewsPost.query.get_or_404(post_id)
    if not _can_edit_news(current_user, post):
        abort(403)
    title = request.form.get("title", "").strip()
    if not title:
        flash("A title is required for every news post.", "error")
        return redirect(url_for("news.index"))
    post.title = title
    post.content = request.form.get("content", "").strip()
    post.updated_at = datetime.utcnow()
    db.session.commit()
    flash("News updated.", "success")
    return redirect(url_for("news.index"))


@news_bp.route("/<int:post_id>/delete", methods=["POST"])
@login_required
def delete(post_id):
    post = NewsPost.query.get_or_404(post_id)
    if not _can_edit_news(current_user, post):
        abort(403)
    db.session.delete(post)
    db.session.commit()
    flash("News post deleted.", "success")
    return redirect(url_for("news.index"))
