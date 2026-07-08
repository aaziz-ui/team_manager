from models import db, Notification, User


def notify(user_id, category, message, url=None):
    """Create a notification. Does not commit - caller's existing db.session.commit()
    (already happening in the route that triggers this) will persist it too."""
    n = Notification(user_id=user_id, category=category, message=message, url=url)
    db.session.add(n)
    return n


def notify_many(user_ids, category, message, url=None):
    for uid in set(user_ids):
        notify(uid, category, message, url)


def managers_for(employee):
    """Everyone (admins + the employee's direct/fallback manager(s)) who can approve
    this employee's vacation requests or is otherwise responsible for them."""
    candidates = User.query.filter(User.role.in_(["admin", "manager"]), User.is_active_employee == True).all()  # noqa: E712
    return [c for c in candidates if c.can_manage(employee) and c.id != employee.id]
