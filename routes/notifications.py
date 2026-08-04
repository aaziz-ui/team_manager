from datetime import date, timedelta
from itertools import groupby
from flask import Blueprint, render_template, redirect, url_for, jsonify, request
from flask_login import login_required, current_user
from models import db, Notification

notifications_bp = Blueprint("notifications", __name__)


def _day_label(day):
    today = date.today()
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    return day.strftime("%A, %b %d, %Y")


def _group_by_day(items):
    groups = []
    for day, group in groupby(items, key=lambda n: n.created_at.date()):
        groups.append((_day_label(day), list(group)))
    return groups


@notifications_bp.route("/notifications")
@login_required
def index():
    items = (Notification.query.filter_by(user_id=current_user.id)
             .order_by(Notification.created_at.desc()).limit(150).all())
    return render_template("notifications.html", grouped_items=_group_by_day(items))


@notifications_bp.route("/notifications/<int:notif_id>/open")
@login_required
def open_notification(notif_id):
    n = Notification.query.get_or_404(notif_id)
    if n.user_id != current_user.id:
        return redirect(url_for("notifications.index"))
    n.is_read = True
    db.session.commit()
    return redirect(n.url or url_for("notifications.index"))


@notifications_bp.route("/notifications/mark-all-read", methods=["POST"])
@login_required
def mark_all_read():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    return redirect(url_for("notifications.index"))


@notifications_bp.route("/api/notifications/count")
@login_required
def unread_count():
    count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    return jsonify({"count": count})


@notifications_bp.route("/api/notifications/recent")
@login_required
def recent_unread():
    """Returns unread notifications with an id greater than ?after=, oldest first - used by the
    browser/voice alert feature to know exactly which new notifications to announce, without
    re-announcing ones already seen on this device."""
    after_id = request.args.get("after", type=int, default=0)
    items = (Notification.query.filter(
        Notification.user_id == current_user.id,
        Notification.is_read == False,  # noqa: E712
        Notification.id > after_id,
    ).order_by(Notification.id.asc()).limit(20).all())
    return jsonify([
        {
            "id": n.id,
            "category": n.category,
            "message": n.message,
            "url": n.url or url_for("notifications.index"),
        }
        for n in items
    ])
