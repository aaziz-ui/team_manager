from datetime import datetime, date
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user
from models import (
    db, User, Task, Vacation,
    STATUS_LABELS, STATUS_AVAILABLE, STATUS_OFFLINE,
    TASK_DONE, VAC_APPROVED, VAC_PENDING,
)
from status_tracking import record_status_change

dashboard_bp = Blueprint("dashboard", __name__)


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

    team_overview = []
    if current_user.role in ("admin", "manager"):
        reports = [e for e in employees if current_user.can_manage(e) and e.id != current_user.id]
        for r in reports:
            open_tasks = Task.query.filter(Task.assigned_to_id == r.id, Task.status != TASK_DONE).all()
            overdue = [t for t in open_tasks if t.due_date and t.due_date < today]
            team_overview.append({
                "user": r,
                "open_count": len(open_tasks),
                "overdue_count": len(overdue),
                "on_vacation": r.id in on_vacation_ids,
            })

    return render_template(
        "dashboard.html",
        employees=employees,
        my_tasks=my_tasks,
        on_vacation_ids=on_vacation_ids,
        pending_approvals=pending_approvals,
        status_labels=STATUS_LABELS,
        team_overview=team_overview,
    )


@dashboard_bp.route("/api/heartbeat", methods=["POST"])
@login_required
def heartbeat():
    """Called periodically by the browser. Body: {status, manual, hold}
    - manual=true means the person explicitly clicked a status button.
    - hold=true means "keep this status even while the tab is minimized/idle"
      until they manually change it again.
    - When not manual, this is an automatic activity/idle ping; it's ignored
      if the user's status is currently held/locked.
    """
    data = request.get_json(silent=True) or {}
    req_status = data.get("status")
    manual = bool(data.get("manual"))
    hold = bool(data.get("hold"))

    now = datetime.utcnow()
    prev_last_seen = current_user.last_seen_at
    gap = (now - prev_last_seen) if prev_last_seen else None

    if manual and req_status in STATUS_LABELS:
        current_user.status = req_status
        current_user.status_updated_at = now
        # "Offline" is always transient (browser closing) - never treated as a hold.
        current_user.status_locked = hold and req_status != STATUS_OFFLINE
    elif not manual:
        if current_user.status_locked:
            pass  # ignore auto activity/idle detection while status is held
        elif req_status in STATUS_LABELS:
            current_user.status = req_status
        elif not current_user.status:
            current_user.status = STATUS_AVAILABLE

    record_status_change(current_user, current_user.status, at=now, gap_since_last_seen=gap)
    current_user.last_seen_at = now
    db.session.commit()
    return jsonify({"ok": True, "status": current_user.status, "locked": current_user.status_locked})


@dashboard_bp.route("/api/status-note", methods=["POST"])
@login_required
def set_status_note():
    data = request.get_json(silent=True) or {}
    note = (data.get("note") or "").strip()[:200]
    current_user.status_note = note or None
    db.session.commit()
    return jsonify({"ok": True, "note": current_user.status_note})


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
            "locked": e.status_locked,
            "note": e.status_note,
        }
        for e in employees
    ])
