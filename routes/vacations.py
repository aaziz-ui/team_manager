from datetime import datetime, date
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from models import db, Vacation, User, CompanySettings, VAC_PENDING, VAC_APPROVED, VAC_REJECTED
from notifications import notify, managers_for

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
    all_requests = []
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

        pool_ids = {u.id for u in pool}
        all_requests = (Vacation.query.filter(Vacation.user_id.in_(pool_ids))
                         .order_by(Vacation.start_date.desc()).all())

    return render_template("vacations.html", my_requests=my_requests, to_review=to_review,
                            my_balance=my_balance, team_balances=team_balances, year=year,
                            all_requests=all_requests)


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

    for mgr in managers_for(current_user):
        notify(mgr.id, "vacation", f'{current_user.full_name} requested {vac.vac_type} leave '
               f'({start_date.strftime("%b %d")} – {end_date.strftime("%b %d")})',
               url=url_for("vacations.index", _anchor=f"vacation-{vac.id}"))
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

    notify(vac.user_id, "vacation", f'Your {vac.vac_type} leave request was {decision} by {current_user.full_name}',
           url=url_for("vacations.index", _anchor=f"vacation-{vac.id}"))
    db.session.commit()

    flash(f"Request {decision}.", "success")
    return redirect(url_for("vacations.index"))


@vacations_bp.route("/<int:vac_id>/edit", methods=["GET", "POST"])
@login_required
def edit(vac_id):
    # Admin override: full edit of any vacation request, regardless of whose it is or its status.
    if current_user.role != "admin":
        abort(403)
    vac = Vacation.query.get_or_404(vac_id)

    if request.method == "POST":
        try:
            start_date = datetime.strptime(request.form["start_date"], "%Y-%m-%d").date()
            end_date = datetime.strptime(request.form["end_date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            flash("Please provide valid dates.", "error")
            return redirect(url_for("vacations.edit", vac_id=vac.id))
        if end_date < start_date:
            flash("End date must be after start date.", "error")
            return redirect(url_for("vacations.edit", vac_id=vac.id))

        old_status = vac.status
        new_status = request.form.get("status", vac.status)

        vac.start_date = start_date
        vac.end_date = end_date
        vac.vac_type = request.form.get("vac_type", vac.vac_type)
        vac.reason = request.form.get("reason", "").strip()
        vac.manager_comment = request.form.get("manager_comment", "").strip()
        vac.status = new_status
        if new_status != old_status and new_status in (VAC_APPROVED, VAC_REJECTED):
            vac.decided_by_id = current_user.id
            vac.decided_at = datetime.utcnow()
        db.session.commit()

        if new_status != old_status and new_status in (VAC_APPROVED, VAC_REJECTED):
            notify(vac.user_id, "vacation",
                   f'Your {vac.vac_type} leave request was {new_status} by {current_user.full_name}',
                   url=url_for("vacations.index", _anchor=f"vacation-{vac.id}"))
            db.session.commit()

        flash("Vacation request updated.", "success")
        return redirect(url_for("vacations.index"))

    return render_template("vacation_edit.html", vac=vac)


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
