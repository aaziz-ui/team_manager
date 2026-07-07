from datetime import datetime, date
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user
from models import (
    db, User, Task, Vacation,
    STATUS_LABELS, STATUS_AVAILABLE, STATUS_IDLE, STATUS_AWAY_DESK, STATUS_MEETING, STATUS_OFFLINE,
    TASK_DONE, VAC_APPROVED, VAC_PENDING,
)

dashboard_bp = Blueprint("dashboard", __name__)

MANUAL_STATUSES = {STATUS_AVAILABLE, STATUS_AWAY_DESK, STATUS_MEETING}
AUTO_STATUSES = {STATUS_AVAILABLE, STATUS_IDLE}


@dashboard_bp.route("/")
@login_required
def index():
    employees = User.query.filter_by(is_active_employee=True).order_by(User.full_name).all()

    my_tasks = (Task.query.filter_by(assigned_to_id=current_user.id)
                .filter(Task.status != TASK_DONE)
                .order_by(Task.due_date.asc().nullslast())
                .limit(5).all())

    today = date.today()
    on_vacation_today = (Vacation.query.filter(
        Vacation.status == VAC_APPROVED,
        Vacation.start_date <= today,
        Vacation.end_date >= today,
    ).all())
    on_vacation_ids = {v.user_id for v in on_vacation_today}

    pending_approvals = []
    if current_user.role in ("admin", "manager"):
        pending = Vacation.query.filter_by(status=VAC_PENDING).all()
        pending_approvals = [v for v in pending if current_user.can_manage(v.user)]

    return render_template(
        "dashboard.html",
        employees=employees,
        my_tasks=my_tasks,
        on_vacation_ids=on_vacation_ids,
        pending_approvals=pending_approvals,
        status_labels=STATUS_LABELS,
    )


@dashboard_bp.route("/api/heartbeat", methods=["POST"])
@login_required
def heartbeat():
    """Called periodically by the browser. Body: {status, manual}"""
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    manual = bool(data.get("manual"))

    current_user.last_seen_at = datetime.utcnow()

    if status and (manual or status in AUTO_STATUSES):
        if status in STATUS_LABELS:
            current_user.status = status
            current_user.status_updated_at = datetime.utcnow()
    elif not current_user.status or current_user.status == STATUS_OFFLINE:
        current_user.status = STATUS_AVAILABLE

    db.session.commit()
    return jsonify({"ok": True, "status": current_user.status})


@dashboard_bp.route("/api/status-board")
@login_required
def status_board():
    employees = User.query.filter_by(is_active_employee=True).all()
    return jsonify([
        {
            "id": e.id,
            "name": e.full_name,
            "status": e.display_status,
            "label": e.display_status_label,
            "note": e.status_note,
        }
        for e in employees
    ])
