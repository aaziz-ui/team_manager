from datetime import datetime, timedelta, time as dtime
from models import db, StatusLog, STATUS_OFFLINE, STATUS_LABELS, OFFLINE_THRESHOLD_MINUTES


def record_status_change(user, new_status, at=None, gap_since_last_seen=None):
    """Close the user's currently-open StatusLog entry and open a new one for new_status.
    If gap_since_last_seen is given and exceeds the offline threshold, we assume the browser
    was closed/unreachable during that gap and backfill an 'offline' log for it.
    No-ops if new_status equals the currently open status (just keeps it open)."""
    at = at or datetime.utcnow()

    open_log = (StatusLog.query.filter_by(user_id=user.id, ended_at=None)
                .order_by(StatusLog.started_at.desc()).first())

    if gap_since_last_seen and gap_since_last_seen > timedelta(minutes=OFFLINE_THRESHOLD_MINUTES):
        # Browser was unreachable for a while - backfill that stretch as offline.
        gap_start = at - gap_since_last_seen
        if open_log:
            open_log.ended_at = gap_start
        db.session.add(StatusLog(user_id=user.id, status=STATUS_OFFLINE,
                                  started_at=gap_start, ended_at=at))
        db.session.add(StatusLog(user_id=user.id, status=new_status, started_at=at))
        return

    if open_log and open_log.status == new_status:
        return  # unchanged, keep the same open span

    if open_log:
        open_log.ended_at = at
    db.session.add(StatusLog(user_id=user.id, status=new_status, started_at=at))


def close_open_log(user, at=None):
    at = at or datetime.utcnow()
    open_log = (StatusLog.query.filter_by(user_id=user.id, ended_at=None)
                .order_by(StatusLog.started_at.desc()).first())
    if open_log:
        open_log.ended_at = at


def compute_daily_hours(user, day):
    """Returns dict {status: seconds} for the given user on the given date (a date object),
    covering 00:00:00 to 23:59:59 local-naive (server) time.

    The still-open log entry (their current live status) is only counted up until they've
    actually gone quiet: if no heartbeat has arrived for OFFLINE_THRESHOLD_MINUTES, we stop
    crediting that status right there and count the rest of the elapsed time as Offline -
    exactly matching what the live presence badge shows, regardless of "hold"."""
    day_start = datetime.combine(day, dtime.min)
    day_end = datetime.combine(day, dtime.max)
    now = datetime.utcnow()
    clip_end = min(day_end, now) if now < day_end else day_end

    if user.last_seen_at:
        offline_since = user.last_seen_at + timedelta(minutes=OFFLINE_THRESHOLD_MINUTES)
    else:
        offline_since = now
    is_stale = now > offline_since
    live_cutoff = offline_since if is_stale else now

    logs = (StatusLog.query.filter(
        StatusLog.user_id == user.id,
        StatusLog.started_at < day_end,
        db.or_(StatusLog.ended_at == None, StatusLog.ended_at > day_start),  # noqa: E711
    ).all())

    totals = {status: 0.0 for status in STATUS_LABELS}
    for log in logs:
        start = max(log.started_at, day_start)
        raw_end = log.ended_at or live_cutoff  # open entries stop at live_cutoff, not `now`
        end = min(raw_end, clip_end)
        if end > start:
            totals[log.status] = totals.get(log.status, 0.0) + (end - start).total_seconds()

    if is_stale:
        # Credit the untracked "gone quiet" stretch as Offline, same as the live badge.
        gap_start = max(offline_since, day_start)
        if clip_end > gap_start:
            totals[STATUS_OFFLINE] = totals.get(STATUS_OFFLINE, 0.0) + (clip_end - gap_start).total_seconds()

    return totals


def seconds_to_hm(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h}h {m:02d}m"
