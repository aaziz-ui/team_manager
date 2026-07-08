from datetime import date, datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from models import db, User, DailyReport
from notifications import notify, managers_for

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


@reports_bp.route("/")
@login_required
def index():
    today = date.today()
    todays_report = DailyReport.query.filter_by(user_id=current_user.id, report_date=today).first()

    my_reports = (DailyReport.query.filter_by(user_id=current_user.id)
                  .order_by(DailyReport.report_date.desc()).limit(30).all())

    team_reports = []
    missing = []
    filter_date = today
    if current_user.role in ("admin", "manager"):
        pool = User.query.filter_by(is_active_employee=True).all() if current_user.role == "admin" \
            else [u for u in User.query.filter_by(is_active_employee=True).all() if current_user.can_manage(u)]
        pool = [u for u in pool if u.id != current_user.id]

        date_str = request.args.get("date")
        try:
            filter_date = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else today
        except ValueError:
            filter_date = today

        pool_ids = {u.id for u in pool}
        team_reports = (DailyReport.query.filter(
            DailyReport.user_id.in_(pool_ids), DailyReport.report_date == filter_date)
            .join(User, DailyReport.user_id == User.id).order_by(User.full_name).all())

        submitted_ids = {r.user_id for r in team_reports}
        missing = sorted([u for u in pool if u.id not in submitted_ids], key=lambda u: u.full_name)

    return render_template(
        "reports.html",
        todays_report=todays_report,
        my_reports=my_reports,
        team_reports=team_reports,
        missing=missing,
        filter_date=filter_date,
        prev_date=filter_date - timedelta(days=1),
        next_date=filter_date + timedelta(days=1),
        today=today,
    )


@reports_bp.route("/submit", methods=["POST"])
@login_required
def submit():
    content = request.form.get("content", "").strip()
    if not content:
        flash("Please write something before submitting.", "error")
        return redirect(url_for("reports.index"))

    today = date.today()
    existing = DailyReport.query.filter_by(user_id=current_user.id, report_date=today).first()
    is_new = existing is None

    if existing:
        existing.content = content
        existing.updated_at = datetime.utcnow()
    else:
        existing = DailyReport(user_id=current_user.id, report_date=today, content=content)
        db.session.add(existing)
    db.session.commit()

    if is_new:
        for mgr in managers_for(current_user):
            notify(mgr.id, "report",
                   f'{current_user.full_name} submitted their daily report for {today.strftime("%b %d")}',
                   url=url_for("reports.index"))
        db.session.commit()

    flash("Report submitted." if is_new else "Report updated.", "success")
    return redirect(url_for("reports.index"))
