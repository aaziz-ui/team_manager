from datetime import date, datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from models import db, User, DailyReport, ROLE_ADMIN
from notifications import notify, managers_for

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


def _parse_date(date_str, default):
    if not date_str:
        return default
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return default


def _can_review(user, report):
    """Who can mark a report read/unread: admin, or the reviewer(s) of that employee."""
    return user.role == ROLE_ADMIN or (user.role == "manager" and user.can_manage(report.user))


@reports_bp.route("/")
@login_required
def index():
    today = date.today()

    # The calendar/date picker - what day is being browsed/edited.
    filter_date = _parse_date(request.args.get("date"), today)

    # The current person's own report for the browsed date - editable inline below,
    # for any date (past or today), unless a manager has marked it read.
    own_report = DailyReport.query.filter_by(user_id=current_user.id, report_date=filter_date).first()

    team_reports = []
    missing = []
    if current_user.role in ("admin", "manager"):
        pool = User.query.filter_by(is_active_employee=True).all() if current_user.role == "admin" \
            else [u for u in User.query.filter_by(is_active_employee=True).all() if current_user.can_manage(u)]
        pool = [u for u in pool if u.id != current_user.id]

        pool_ids = {u.id for u in pool}
        team_reports = (DailyReport.query.filter(
            DailyReport.user_id.in_(pool_ids), DailyReport.report_date == filter_date)
            .join(User, DailyReport.user_id == User.id).order_by(User.full_name).all())

        submitted_ids = {r.user_id for r in team_reports}
        missing = sorted([u for u in pool if u.id not in submitted_ids], key=lambda u: u.full_name)

    my_reports = (DailyReport.query.filter_by(user_id=current_user.id)
                  .order_by(DailyReport.report_date.desc()).limit(15).all())

    return render_template(
        "reports.html",
        own_report=own_report,
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
    report_date = _parse_date(request.form.get("report_date"), date.today())
    today = date.today()

    if report_date > today:
        flash("You can't submit a report for a future date.", "error")
        return redirect(url_for("reports.index", date=report_date.isoformat()))

    if not content:
        flash("Please write something before submitting.", "error")
        return redirect(url_for("reports.index", date=report_date.isoformat()))

    existing = DailyReport.query.filter_by(user_id=current_user.id, report_date=report_date).first()
    is_new = existing is None

    if existing and existing.is_read:
        flash("This report has already been reviewed and is locked. Ask your manager to mark it "
              "unread if you need to make changes.", "error")
        return redirect(url_for("reports.index", date=report_date.isoformat()))

    if existing:
        existing.content = content
        existing.updated_at = datetime.utcnow()
    else:
        existing = DailyReport(user_id=current_user.id, report_date=report_date, content=content)
        db.session.add(existing)
    db.session.commit()

    if is_new:
        for mgr in managers_for(current_user):
            notify(mgr.id, "report",
                   f'{current_user.full_name} submitted their daily report for {report_date.strftime("%b %d")}',
                   url=url_for("reports.index", date=report_date.isoformat(), _anchor=f"report-{existing.id}"))
        db.session.commit()

    flash("Report submitted." if is_new else "Report updated.", "success")
    return redirect(url_for("reports.index", date=report_date.isoformat()))


@reports_bp.route("/<int:report_id>/mark-read", methods=["POST"])
@login_required
def mark_read(report_id):
    report = DailyReport.query.get_or_404(report_id)
    if not _can_review(current_user, report):
        abort(403)
    report.is_read = True
    report.read_by_id = current_user.id
    report.read_at = datetime.utcnow()
    db.session.commit()
    flash("Marked as read.", "success")
    return redirect(url_for("reports.index", date=report.report_date.isoformat(), _anchor=f"report-{report.id}"))


@reports_bp.route("/<int:report_id>/mark-unread", methods=["POST"])
@login_required
def mark_unread(report_id):
    report = DailyReport.query.get_or_404(report_id)
    if not _can_review(current_user, report):
        abort(403)
    report.is_read = False
    report.read_by_id = None
    report.read_at = None
    db.session.commit()
    flash("Marked as unread - the employee can edit it again.", "success")
    return redirect(url_for("reports.index", date=report.report_date.isoformat(), _anchor=f"report-{report.id}"))


@reports_bp.route("/<int:report_id>/edit", methods=["GET", "POST"])
@login_required
def edit_report(report_id):
    # Admin override: can edit any report, any date, read-locked or not.
    if current_user.role != ROLE_ADMIN:
        abort(403)
    report = DailyReport.query.get_or_404(report_id)

    if request.method == "POST":
        content = request.form.get("content", "").strip()
        if not content:
            flash("Report content can't be empty.", "error")
            return redirect(url_for("reports.edit_report", report_id=report.id))
        report.content = content
        report.updated_at = datetime.utcnow()
        db.session.commit()
        flash("Report updated.", "success")
        return redirect(url_for("reports.index", date=report.report_date.isoformat()))

    return render_template("report_edit.html", report=report)


@reports_bp.route("/<int:report_id>/delete", methods=["POST"])
@login_required
def delete_report(report_id):
    if current_user.role != ROLE_ADMIN:
        abort(403)
    report = DailyReport.query.get_or_404(report_id)
    report_date = report.report_date
    db.session.delete(report)
    db.session.commit()
    flash("Report deleted.", "success")
    return redirect(url_for("reports.index", date=report_date.isoformat()))
