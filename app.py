import os
from flask import Flask
from flask_login import LoginManager
from models import db, User, ROLE_ADMIN

login_manager = LoginManager()
login_manager.login_view = "auth.login"


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    db_url = os.environ.get("DATABASE_URL", "sqlite:///team_manager.db")
    # Render/Heroku give postgres:// but SQLAlchemy 1.4+ needs postgresql://
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}

    db.init_app(app)
    login_manager.init_app(app)

    from routes.auth import auth_bp
    from routes.dashboard import dashboard_bp
    from routes.tasks import tasks_bp
    from routes.vacations import vacations_bp
    from routes.admin import admin_bp
    from routes.notifications import notifications_bp
    from routes.reports import reports_bp
    from routes.projects import projects_bp
    from routes.chat import chat_bp
    from routes.notes import notes_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(vacations_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(notes_bp)

    @app.context_processor
    def inject_unread_notification_count():
        from flask_login import current_user
        from models import Notification
        if current_user.is_authenticated:
            count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
            return {"unread_notification_count": count}
        return {"unread_notification_count": 0}

    with app.app_context():
        db.create_all()
        _run_lightweight_migrations()
        _ensure_default_admin()

    register_cli(app)
    return app


def _add_column_if_missing(inspector, table, column, ddl):
    """Runs one ALTER TABLE ADD COLUMN, isolated so a failure here (e.g. a typo'd type name,
    or a database-specific quirk) can never take down the whole app on startup. Worst case:
    that one column doesn't get added and a warning is printed - everything else still boots."""
    existing_cols = {c["name"] for c in inspector.get_columns(table)}
    if column in existing_cols:
        return
    from sqlalchemy import text
    try:
        db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"WARNING: migration failed to add {table}.{column} ({ddl}): {e}")


def _run_lightweight_migrations():
    """No Alembic here - this app is small enough that a startup column-check is
    simpler and safer for a non-technical deploy. Adds any columns/tables that
    were introduced after someone's first deploy, without touching existing data."""
    from sqlalchemy import inspect

    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())

    if "users" in existing_tables:
        _add_column_if_missing(inspector, "users", "status_locked", "BOOLEAN NOT NULL DEFAULT FALSE")
        _add_column_if_missing(inspector, "users", "vacation_days_override", "INTEGER")
        _add_column_if_missing(inspector, "users", "can_assign_tasks", "BOOLEAN NOT NULL DEFAULT FALSE")

    if "tasks" in existing_tables:
        _add_column_if_missing(inspector, "tasks", "percent_complete", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(inspector, "tasks", "project_id", "INTEGER")

    if "company_settings" in existing_tables:
        _add_column_if_missing(inspector, "company_settings", "annual_vacation_days", "INTEGER NOT NULL DEFAULT 21")

    if "daily_reports" in existing_tables:
        _add_column_if_missing(inspector, "daily_reports", "is_read", "BOOLEAN NOT NULL DEFAULT FALSE")
        _add_column_if_missing(inspector, "daily_reports", "read_by_id", "INTEGER")
        _add_column_if_missing(inspector, "daily_reports", "read_at", "TIMESTAMP")

    if "task_comments" in existing_tables:
        _add_column_if_missing(inspector, "task_comments", "edited_at", "TIMESTAMP")

    if "chat_messages" in existing_tables:
        _add_column_if_missing(inspector, "chat_messages", "saved_by_id", "INTEGER")

    # status_logs / company_settings tables themselves are created by db.create_all() above
    # since they're new model classes - nothing extra needed for those.


def _ensure_default_admin():
    """If the DB has no users at all, create a starter admin account."""
    if User.query.count() == 0:
        admin = User(
            username="admin",
            full_name="Administrator",
            role=ROLE_ADMIN,
        )
        admin.set_password(os.environ.get("DEFAULT_ADMIN_PASSWORD", "ChangeMe123!"))
        db.session.add(admin)
        db.session.commit()
        print("=" * 60)
        print("Created default admin account -> username: admin")
        print("Password: value of DEFAULT_ADMIN_PASSWORD env var, or ChangeMe123! by default")
        print("PLEASE LOG IN AND CHANGE THIS IMMEDIATELY.")
        print("=" * 60)


def register_cli(app):
    @app.cli.command("create-user")
    def create_user_cmd():
        """Interactive: flask create-user"""
        import getpass
        username = input("Username: ").strip()
        full_name = input("Full name: ").strip()
        role = input("Role (admin/manager/employee): ").strip() or "employee"
        password = getpass.getpass("Password: ")
        with app.app_context():
            if User.query.filter_by(username=username).first():
                print("Username already exists.")
                return
            u = User(username=username, full_name=full_name, role=role)
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
            print(f"Created user {username} ({role})")


login_manager.user_loader(lambda user_id: User.query.get(int(user_id)))

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
