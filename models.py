from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# How long (minutes) without a heartbeat before we display a user as Offline
OFFLINE_THRESHOLD_MINUTES = 3

ROLE_ADMIN = "admin"
ROLE_MANAGER = "manager"
ROLE_EMPLOYEE = "employee"

STATUS_AVAILABLE = "available"
STATUS_IDLE = "idle"
STATUS_AWAY_DESK = "away_desk"
STATUS_MEETING = "meeting"
STATUS_OFFLINE = "offline"

STATUS_LABELS = {
    STATUS_AVAILABLE: "Available",
    STATUS_IDLE: "Idle",
    STATUS_AWAY_DESK: "Away from desk",
    STATUS_MEETING: "In a meeting",
    STATUS_OFFLINE: "Offline",
}

# The 4 statuses a person can deliberately choose (idle is automatic-only)
MANUAL_SELECTABLE_STATUSES = [STATUS_AVAILABLE, STATUS_AWAY_DESK, STATUS_MEETING, STATUS_OFFLINE]

TASK_PENDING = "pending"
TASK_IN_PROGRESS = "in_progress"
TASK_DONE = "done"

VAC_PENDING = "pending"
VAC_APPROVED = "approved"
VAC_REJECTED = "rejected"


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_EMPLOYEE)
    department = db.Column(db.String(100), nullable=True)
    manager_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    is_active_employee = db.Column(db.Boolean, default=True, nullable=False)
    vacation_days_override = db.Column(db.Integer, nullable=True)  # null = use company default

    status = db.Column(db.String(20), default=STATUS_OFFLINE, nullable=False)
    status_note = db.Column(db.String(200), nullable=True)
    status_updated_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen_at = db.Column(db.DateTime, nullable=True)
    status_locked = db.Column(db.Boolean, default=False, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    manager = db.relationship("User", remote_side=[id], backref="direct_reports")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def display_status(self):
        """Computed status: if no heartbeat recently, force Offline regardless of stored value."""
        if not self.last_seen_at:
            return STATUS_OFFLINE
        if datetime.utcnow() - self.last_seen_at > timedelta(minutes=OFFLINE_THRESHOLD_MINUTES):
            return STATUS_OFFLINE
        return self.status or STATUS_AVAILABLE

    @property
    def display_status_label(self):
        return STATUS_LABELS.get(self.display_status, self.display_status)

    def can_manage(self, other_user):
        """Can this user assign tasks / approve vacation for other_user?"""
        if self.role == ROLE_ADMIN:
            return True
        if self.role == ROLE_MANAGER:
            if other_user.manager_id == self.id:
                return True
            if other_user.manager_id is None and other_user.id != self.id:
                return True
        return False

    def to_export_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "password_hash": self.password_hash,
            "full_name": self.full_name,
            "role": self.role,
            "department": self.department,
            "manager_id": self.manager_id,
            "is_active_employee": self.is_active_employee,
            "vacation_days_override": self.vacation_days_override,
        }


class Task(db.Model):
    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    assigned_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    due_date = db.Column(db.Date, nullable=True)
    priority = db.Column(db.String(10), default="medium")
    status = db.Column(db.String(20), default=TASK_PENDING, nullable=False)
    percent_complete = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    assigned_to = db.relationship("User", foreign_keys=[assigned_to_id])
    assigned_by = db.relationship("User", foreign_keys=[assigned_by_id])
    comments = db.relationship("TaskComment", backref="task", cascade="all, delete-orphan",
                                order_by="TaskComment.created_at")

    def to_export_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "assigned_to_id": self.assigned_to_id,
            "assigned_by_id": self.assigned_by_id,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "priority": self.priority,
            "status": self.status,
            "percent_complete": self.percent_complete,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class TaskComment(db.Model):
    __tablename__ = "task_comments"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")

    def to_export_dict(self):
        return {
            "id": self.id,
            "task_id": self.task_id,
            "user_id": self.user_id,
            "body": self.body,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Vacation(db.Model):
    __tablename__ = "vacations"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    vac_type = db.Column(db.String(20), default="vacation")
    reason = db.Column(db.String(300), nullable=True)
    status = db.Column(db.String(20), default=VAC_PENDING, nullable=False)
    requested_at = db.Column(db.DateTime, default=datetime.utcnow)
    decided_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    decided_at = db.Column(db.DateTime, nullable=True)
    manager_comment = db.Column(db.String(300), nullable=True)

    user = db.relationship("User", foreign_keys=[user_id])
    decided_by = db.relationship("User", foreign_keys=[decided_by_id])

    @property
    def days(self):
        return (self.end_date - self.start_date).days + 1

    def to_export_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "vac_type": self.vac_type,
            "reason": self.reason,
            "status": self.status,
            "requested_at": self.requested_at.isoformat() if self.requested_at else None,
            "decided_by_id": self.decided_by_id,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "manager_comment": self.manager_comment,
        }


class Notification(db.Model):
    """A single in-app notification for a user, e.g. 'you were assigned a task'."""
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    category = db.Column(db.String(20), nullable=False)  # 'task' | 'vacation'
    message = db.Column(db.String(300), nullable=False)
    url = db.Column(db.String(300), nullable=True)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    user = db.relationship("User")


class StatusLog(db.Model):
    """Records each continuous span of time a user held a given status.
    Used to build the admin's daily hours report (available/meeting/away/offline hours)."""
    __tablename__ = "status_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False)
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    ended_at = db.Column(db.DateTime, nullable=True)  # null = still ongoing

    user = db.relationship("User")


class CompanySettings(db.Model):
    """Single-row table holding company-wide configurable settings, e.g. expected working hours."""
    __tablename__ = "company_settings"

    id = db.Column(db.Integer, primary_key=True)
    work_start = db.Column(db.Time, nullable=False, default=lambda: datetime.strptime("09:00", "%H:%M").time())
    work_end = db.Column(db.Time, nullable=False, default=lambda: datetime.strptime("17:00", "%H:%M").time())
    annual_vacation_days = db.Column(db.Integer, nullable=False, default=21)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    @staticmethod
    def get():
        settings = CompanySettings.query.first()
        if not settings:
            settings = CompanySettings()
            db.session.add(settings)
            db.session.commit()
        return settings
