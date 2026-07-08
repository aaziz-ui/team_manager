from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User, STATUS_OFFLINE
from status_tracking import record_status_change

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and user.is_active_employee and user.check_password(password):
            login_user(user)
            user.last_seen_at = datetime.utcnow()
            db.session.commit()
            return redirect(url_for("dashboard.index"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    now = datetime.utcnow()
    current_user.status = STATUS_OFFLINE
    current_user.status_locked = False
    record_status_change(current_user, STATUS_OFFLINE, at=now)
    current_user.last_seen_at = now
    db.session.commit()
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if len(new_password) < 6:
            flash("Password must be at least 6 characters.", "error")
        elif new_password != confirm:
            flash("Passwords do not match.", "error")
        else:
            current_user.set_password(new_password)
            db.session.commit()
            flash("Password updated.", "success")
    return render_template("account.html")
