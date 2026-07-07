from datetime import datetime, date
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from models import db, Vacation, User, CompanySettings, VAC_PENDING, VAC_APPROVED, VAC_REJECTED

vacations_bp = Blueprint("vacations", __name__, url_prefix="/vacations")


def used_vacation_days(user_id, year):
    """Sum of approved 'vacation'-type days for a given calendar year (by start date)."""
    approved = Vacation.query.filter_by(user_id=user_id, status=VAC_APPROVED, vac_type="vacation").all()
    return sum(v.days for v in approved if v.start_date.year == year)


def allotted_days_for(user, settings):
    """Per-employee override if set, otherwise the company-wide default."""
    return user.vacation_days_override if user.vacation_days_override is not None else settings.annual_vacation_days


@vacations_bp.route("/")
@login_required
def index():
    my_requests = Vacation.query.filter_by(user_id=current_user.id).order_by(
        Vacation.start_date.desc()).all()

    to_review = []
    if current_user.role in ("admin", "manager"):
        pending = Vacation.query.filter_by(status=VAC_PENDING).order_by(Vacation.start_date).all()
        to_review = [v for v in pending if current_user.can_manage(v.user)]

    settings = CompanySettings.get()
    year = date.today().year
    used = used_vacation_days(current_user.id, year)
    allotted = allotted_days_for(current_user, settings)
    my_balance = {
        "allotted": allotted,
        "used": used,
        "remaining": allotted - used,
    }

    team_balances = []
    if current_user.role in ("admin", "manager"):
        pool = User.query.filter_by(is_active_employee=True).all() if current_user.role == "admin" \
            else [u for u in User.query.filter_by(is_active_employee=True).all() if current_user.can_manage(u)]
        for u in sorted(pool, key=lambda x: x.full_name):
            u_used = used_vacation_days(u.id, year)
            u_allotted = allotted_days_for(u, settings)
            team_balances.append({
                "user": u,
                "allotted": u_allotted,
                "used": u_used,
                "remaining": u_allotted - u_used,
            })

    return render_template("vacations.html", my_requests=my_requests, to_review=to_review,
                            my_balance=my_balance, team_balances=team_balances, year=year)


@vacations_bp.route("/new", methods=["POST"])
@login_required
def new():
    try:
        start_date = datetime.strptime(request.form["start_date"], "%Y-%m-%d").date()
        end_date = datetime.strptime(request.form["end_date"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        flash("Please provide valid dates.", "error")
        return redirect(url_for("vacations.index"))

    if end_date < start_date:
        flash("End date must be after start date.", "error")
        return redirect(url_for("vacations.index"))

    vac = Vacation(
        user_id=current_user.id,
        start_date=start_date,
        end_date=end_date,
        vac_type=request.form.get("vac_type", "vacation"),
        reason=request.form.get("reason", "").strip(),
        status=VAC_PENDING,
    )
    db.session.add(vac)
    db.session.commit()
    flash("Vacation request submitted.", "success")
    return redirect(url_for("vacations.index"))


@vacations_bp.route("/<int:vac_id>/decide", methods=["POST"])
@login_required
def decide(vac_id):
    vac = Vacation.query.get_or_404(vac_id)
    if not current_user.can_manage(vac.user):
        abort(403)

    decision = request.form.get("decision")
    if decision not in (VAC_APPROVED, VAC_REJECTED):
        abort(400)

    vac.status = decision
    vac.decided_by_id = current_user.id
    vac.decided_at = datetime.utcnow()
    vac.manager_comment = request.form.get("manager_comment", "").strip()
    db.session.commit()
    flash(f"Request {decision}.", "success")
    return redirect(url_for("vacations.index"))


@vacations_bp.route("/<int:vac_id>/delete", methods=["POST"])
@login_required
def delete(vac_id):
    vac = Vacation.query.get_or_404(vac_id)
    is_owner = vac.user_id == current_user.id
    if not (is_owner or current_user.role == "admin"):
        abort(403)
    if vac.status != VAC_PENDING and current_user.role != "admin":
        flash("This request has already been decided and can no longer be deleted.", "error")
        return redirect(url_for("vacations.index"))
    db.session.delete(vac)
    db.session.commit()
    flash("Vacation request deleted.", "success")
    return redirect(url_for("vacations.index"))
