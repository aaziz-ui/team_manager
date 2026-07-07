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

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(vacations_bp)
    app.register_blueprint(admin_bp)

    with app.app_context():
        db.create_all()
        _ensure_default_admin()

    register_cli(app)
    return app


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
