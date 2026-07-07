from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from models import db, Vacation, VAC_PENDING, VAC_APPROVED, VAC_REJECTED

vacations_bp = Blueprint("vacations", __name__, url_prefix="/vacations")


@vacations_bp.route("/")
@login_required
def index():
    my_requests = Vacation.query.filter_by(user_id=current_user.id).order_by(
        Vacation.start_date.desc()).all()

    to_review = []
    if current_user.role in ("admin", "manager"):
        pending = Vacation.query.filter_by(status=VAC_PENDING).order_by(Vacation.start_date).all()
        to_review = [v for v in pending if current_user.can_manage(v.user)]

    return render_template("vacations.html", my_requests=my_requests, to_review=to_review)


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
