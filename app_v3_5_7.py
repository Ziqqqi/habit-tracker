# v3.5

import sqlite3
from datetime import datetime, date, timedelta
from calendar import monthrange
import streamlit as st
import pandas as pd

DB_NAME = "habit_tracker.db"


# -----------------------------
# Database helpers
# -----------------------------
def get_connection():
    """Create a SQLite connection."""
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def column_exists(cur, table_name: str, column_name: str) -> bool:
    cur.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cur.fetchall()]
    return column_name in columns


def init_db():
    """Create tables if they do not exist and apply lightweight v2 migrations."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS habit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            habit_id INTEGER NOT NULL,
            logged_at TEXT NOT NULL,
            log_date TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (habit_id) REFERENCES habits (id)
        )
    """)

    # v1 compatibility
    if not column_exists(cur, "habits", "daily_target"):
        cur.execute("""
            ALTER TABLE habits
            ADD COLUMN daily_target INTEGER DEFAULT 1
        """)

    # v2 fields
    if not column_exists(cur, "habits", "habit_type"):
        cur.execute("""
            ALTER TABLE habits
            ADD COLUMN habit_type TEXT DEFAULT 'count'
        """)

    if not column_exists(cur, "habits", "frequency_type"):
        cur.execute("""
            ALTER TABLE habits
            ADD COLUMN frequency_type TEXT DEFAULT 'daily'
        """)

    if not column_exists(cur, "habits", "frequency_value"):
        cur.execute("""
            ALTER TABLE habits
            ADD COLUMN frequency_value INTEGER DEFAULT 1
        """)

    if not column_exists(cur, "habits", "target_count"):
        cur.execute("""
            ALTER TABLE habits
            ADD COLUMN target_count INTEGER DEFAULT 1
        """)

    # Backfill old rows so v1 habits become valid v2 habits
    cur.execute("""
        UPDATE habits
        SET habit_type = COALESCE(habit_type, 'count')
    """)
    cur.execute("""
        UPDATE habits
        SET frequency_type = COALESCE(frequency_type, 'daily')
    """)
    cur.execute("""
        UPDATE habits
        SET frequency_value = COALESCE(frequency_value, 1)
    """)
    cur.execute("""
        UPDATE habits
        SET target_count = CASE
            WHEN target_count IS NULL OR target_count < 1 THEN COALESCE(daily_target, 1)
            ELSE target_count
        END
    """)

    conn.commit()
    conn.close()


def normalize_habit_inputs(
    habit_type: str,
    frequency_type: str,
    frequency_value: int,
    target_count: int
):
    """Basic validation and normalization for v3 habit settings."""
    valid_habit_types = {"count", "completion"}
    valid_frequency_types = {"daily", "x_per_week", "every_n_days", "weekly"}

    if habit_type not in valid_habit_types:
        return None, "Invalid habit type."

    if frequency_type not in valid_frequency_types:
        return None, "Invalid frequency type."

    if frequency_type in {"daily", "weekly"}:
        frequency_value = 1
    elif frequency_value < 1:
        return None, "Frequency value must be at least 1."

    if target_count < 1:
        return None, "Target must be at least 1."

    normalized = {
        "habit_type": habit_type,
        "frequency_type": frequency_type,
        "frequency_value": int(frequency_value),
        "target_count": int(target_count),
        "daily_target": int(target_count) if frequency_type == "daily" else max(1, int(target_count)),
    }
    return normalized, None


def add_habit(
    name: str,
    habit_type: str = "count",
    frequency_type: str = "daily",
    frequency_value: int = 1,
    target_count: int = 1,
):
    """Add a new habit, or reactivate it if it already exists but is inactive."""
    clean_name = name.strip()
    if not clean_name:
        return "Habit name cannot be empty."

    normalized, error = normalize_habit_inputs(
        habit_type=habit_type,
        frequency_type=frequency_type,
        frequency_value=frequency_value,
        target_count=target_count
    )
    if error:
        return error

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, is_active
        FROM habits
        WHERE name = ?
    """, (clean_name,))
    existing = cur.fetchone()

    if existing:
        if existing["is_active"] == 1:
            conn.close()
            return "Habit name already exists."
        else:
            cur.execute("""
                UPDATE habits
                SET is_active = 1,
                    habit_type = ?,
                    frequency_type = ?,
                    frequency_value = ?,
                    target_count = ?,
                    daily_target = ?
                WHERE id = ?
            """, (
                normalized["habit_type"],
                normalized["frequency_type"],
                normalized["frequency_value"],
                normalized["target_count"],
                normalized["daily_target"],
                existing["id"],
            ))
            conn.commit()
            conn.close()
            return "Habit restored."

    cur.execute("""
        INSERT INTO habits (
            name, created_at, is_active,
            daily_target, habit_type, frequency_type, frequency_value, target_count
        )
        VALUES (?, ?, 1, ?, ?, ?, ?, ?)
    """, (
        clean_name,
        datetime.now().isoformat(),
        normalized["daily_target"],
        normalized["habit_type"],
        normalized["frequency_type"],
        normalized["frequency_value"],
        normalized["target_count"],
    ))

    conn.commit()
    conn.close()
    return "Habit added."


def get_active_habits():
    """Return all active habits."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            name,
            created_at,
            daily_target,
            habit_type,
            frequency_type,
            frequency_value,
            target_count
        FROM habits
        WHERE is_active = 1
        ORDER BY created_at ASC
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def deactivate_habit(habit_id: int):
    """Soft delete a habit by marking it inactive."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE habits
        SET is_active = 0
        WHERE id = ?
    """, (habit_id,))

    conn.commit()
    conn.close()


def log_habit(habit_id: int, count: int = 1):
    """Insert one habit log row."""
    now = datetime.now()
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO habit_logs (habit_id, logged_at, log_date, count)
        VALUES (?, ?, ?, ?)
        """,
        (habit_id, now.isoformat(timespec="seconds"), now.date().isoformat(), count)
    )

    conn.commit()
    conn.close()


def get_habit_by_id(habit_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM habits WHERE id = ?", (habit_id,))
    row = cur.fetchone()
    conn.close()
    return row


def get_current_period_total_for_habit(habit) -> int:
    frequency_type, frequency_value, _, created_date = get_period_targets(habit)
    period = get_current_period_info(frequency_type, frequency_value, created_date)
    query_end = min(period["end_date"], date.today())

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COALESCE(SUM(count), 0) AS total_count
        FROM habit_logs
        WHERE habit_id = ?
          AND log_date BETWEEN ? AND ?
        """,
        (habit["id"], period["start_date"].isoformat(), query_end.isoformat()),
    )
    row = cur.fetchone()
    conn.close()
    return int(row["total_count"] or 0)


def get_latest_log_id_in_current_period(habit) -> int | None:
    frequency_type, frequency_value, _, created_date = get_period_targets(habit)
    period = get_current_period_info(frequency_type, frequency_value, created_date)
    query_end = min(period["end_date"], date.today())

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id
        FROM habit_logs
        WHERE habit_id = ?
          AND log_date BETWEEN ? AND ?
        ORDER BY logged_at DESC, id DESC
        LIMIT 1
        """,
        (habit["id"], period["start_date"].isoformat(), query_end.isoformat()),
    )
    row = cur.fetchone()
    conn.close()
    return row["id"] if row else None


def log_completion_once_for_current_period(habit_id: int):
    """For completion habits with target=1, allow only one completion per current period."""
    habit = get_habit_by_id(habit_id)
    if not habit:
        return "Habit not found."

    current_total = get_current_period_total_for_habit(habit)
    if current_total >= 1:
        return "Already completed for this period."

    log_habit(habit_id, count=1)
    return None


def undo_completion_for_current_period(habit_id: int):
    """Undo the latest completion event in the current period."""
    habit = get_habit_by_id(habit_id)
    if not habit:
        return "Habit not found."

    latest_log_id = get_latest_log_id_in_current_period(habit)
    if latest_log_id is None:
        return "Nothing to undo."

    delete_log(latest_log_id)
    return None


def update_habit(
    habit_id: int,
    new_name: str,
    habit_type: str,
    frequency_type: str,
    frequency_value: int,
    target_count: int
):
    """Update habit settings for v2."""
    clean_name = new_name.strip()

    if not clean_name:
        return "Habit name cannot be empty."

    normalized, error = normalize_habit_inputs(
        habit_type=habit_type,
        frequency_type=frequency_type,
        frequency_value=frequency_value,
        target_count=target_count
    )
    if error:
        return error

    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            UPDATE habits
            SET
                name = ?,
                habit_type = ?,
                frequency_type = ?,
                frequency_value = ?,
                target_count = ?,
                daily_target = ?
            WHERE id = ?
        """, (
            clean_name,
            normalized["habit_type"],
            normalized["frequency_type"],
            normalized["frequency_value"],
            normalized["target_count"],
            normalized["daily_target"],
            habit_id
        ))
        conn.commit()
        return None
    except sqlite3.IntegrityError:
        return "Habit name already exists."
    finally:
        conn.close()


def get_created_date_from_habit(habit) -> date:
    created_raw = habit["created_at"] if "created_at" in habit.keys() else None
    if not created_raw:
        return date.today()
    return datetime.fromisoformat(created_raw).date()


def get_cycle_start(anchor_date: date, today: date, every_n_days: int) -> date:
    if today <= anchor_date:
        return anchor_date
    delta_days = (today - anchor_date).days
    cycle_index = delta_days // every_n_days
    return anchor_date + timedelta(days=cycle_index * every_n_days)


def get_current_period_info(frequency_type: str, frequency_value: int, anchor_date: date | None = None):
    """Return period range and display text for supported v3 frequency types."""
    today = date.today()
    anchor_date = anchor_date or today

    if frequency_type in {"x_per_week", "weekly"}:
        week_start = get_week_start(today)
        week_end = week_start + timedelta(days=6)
        return {
            "start_date": week_start,
            "end_date": week_end,
            "label": f"This week ({week_start.isoformat()} → {week_end.isoformat()})"
        }

    if frequency_type == "every_n_days":
        cycle_start = get_cycle_start(anchor_date, today, frequency_value)
        cycle_end = cycle_start + timedelta(days=frequency_value - 1)
        return {
            "start_date": cycle_start,
            "end_date": cycle_end,
            "label": f"Current cycle ({cycle_start.isoformat()} → {cycle_end.isoformat()})"
        }

    return {
        "start_date": today,
        "end_date": today,
        "label": f"Today ({today.isoformat()})"
    }


def format_rule_text(habit_type: str, frequency_type: str, frequency_value: int, target_count: int) -> str:
    """Human-friendly rule text."""
    time_unit = "time" if target_count == 1 else "times"
    day_unit = "day" if frequency_value == 1 else "days"

    if frequency_type == "x_per_week":
        return f"{target_count} {time_unit} / week"

    if frequency_type == "weekly":
        return f"{target_count} {time_unit} / week"

    if frequency_type == "every_n_days":
        return f"{target_count} every {frequency_value} {day_unit}"

    if habit_type == "completion":
        return f"{target_count} {time_unit} / day"

    return f"{target_count} / day"


def get_frequency_form_config(frequency_type: str, frequency_value: int | None = None, target_count: int | None = None):
    """Return UI labels/defaults for each frequency type."""
    if frequency_type == "x_per_week":
        return {
            "show_frequency_input": True,
            "frequency_label": "How many times per week?",
            "frequency_value": int(frequency_value or 3),
            "target_label": "Target count for this week",
            "target_value": int(target_count or frequency_value or 3),
            "period_note": "This habit resets every Monday.",
        }
    if frequency_type == "every_n_days":
        return {
            "show_frequency_input": True,
            "frequency_label": "Repeat every how many days?",
            "frequency_value": int(frequency_value or 2),
            "target_label": "Target count for this cycle",
            "target_value": int(target_count or 1),
            "period_note": "This habit is tracked by cycle, not by week.",
        }
    if frequency_type == "weekly":
        return {
            "show_frequency_input": False,
            "frequency_label": None,
            "frequency_value": 1,
            "target_label": "Target count for this week",
            "target_value": int(target_count or 1),
            "period_note": "This habit is tracked once per week window.",
        }
    return {
        "show_frequency_input": False,
        "frequency_label": None,
        "frequency_value": 1,
        "target_label": "Daily target",
        "target_value": int(target_count or 1),
        "period_note": "This habit resets every day.",
    }


def get_current_progress():
    """Return current period progress for each active habit."""
    habits = get_active_habits()
    conn = get_connection()
    cur = conn.cursor()

    rows = []

    for habit in habits:
        habit_type = habit["habit_type"] or "count"
        frequency_type = habit["frequency_type"] or "daily"
        frequency_value = int(habit["frequency_value"] or 1)
        target_count = int(habit["target_count"] or habit["daily_target"] or 1)
        anchor_date = get_created_date_from_habit(habit)

        period = get_current_period_info(frequency_type, frequency_value, anchor_date)
        query_end = min(period["end_date"], date.today())

        cur.execute("""
            SELECT
                COALESCE(SUM(count), 0) AS total_count
            FROM habit_logs
            WHERE habit_id = ?
              AND log_date BETWEEN ? AND ?
        """, (
            habit["id"],
            period["start_date"].isoformat(),
            query_end.isoformat(),
        ))
        log_row = cur.fetchone()

        cur.execute("""
            SELECT MAX(logged_at) AS last_logged_at
            FROM habit_logs
            WHERE habit_id = ?
        """, (habit["id"],))
        latest_log_row = cur.fetchone()

        total_count = int(log_row["total_count"] or 0)

        rows.append({
            "habit_id": habit["id"],
            "habit_name": habit["name"],
            "habit_type": habit_type,
            "frequency_type": frequency_type,
            "frequency_value": frequency_value,
            "target_count": target_count,
            "current_count": total_count,
            "last_logged_at": latest_log_row["last_logged_at"],
            "period_label": period["label"],
            "rule_text": format_rule_text(habit_type, frequency_type, frequency_value, target_count),
        })

    conn.close()
    return rows


def get_recent_logs(limit: int = 20):
    """Return recent logs in reverse chronological order for active habits only."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            l.id,
            l.habit_id,
            h.name AS habit_name,
            h.habit_type,
            h.target_count,
            h.frequency_type,
            l.logged_at,
            l.count
        FROM habit_logs l
        JOIN habits h ON l.habit_id = h.id
        WHERE h.is_active = 1
        ORDER BY l.logged_at DESC, l.id DESC
        LIMIT ?
    """, (limit,))

    rows = cur.fetchall()
    conn.close()
    return rows


def format_recent_log_event_text(log) -> str:
    """Human-friendly text for recent log entries based on habit type."""
    habit_type = log["habit_type"] if "habit_type" in log.keys() else "count"
    target_count = int(log["target_count"] or 1) if "target_count" in log.keys() else 1
    count = int(log["count"] or 0)

    if habit_type == "completion" and target_count == 1:
        return "done"
    if habit_type == "completion" and target_count > 1:
        if count == 1:
            return "session logged"
        return f"{count} sessions logged"

    if count == 1:
        return "+1"
    return f"+{count}"


def delete_log(log_id: int):
    """Delete one log row."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM habit_logs WHERE id = ?", (log_id,))
    conn.commit()
    conn.close()


def get_month_date_range(target_date: date):
    """Return the first and last day of the month for a given date."""
    first_day = target_date.replace(day=1)
    last_day = target_date.replace(day=monthrange(target_date.year, target_date.month)[1])
    return first_day, last_day


def get_week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def daterange(start_date: date, end_date: date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def get_habit_logs_summary(habit_id: int, start_date: date, end_date: date):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT log_date, SUM(count) AS total
        FROM habit_logs
        WHERE habit_id = ?
          AND log_date BETWEEN ? AND ?
        GROUP BY log_date
    """, (habit_id, start_date.isoformat(), end_date.isoformat()))
    rows = cur.fetchall()
    conn.close()
    return {date.fromisoformat(row["log_date"]): int(row["total"] or 0) for row in rows}



def format_last_checkin_text(logged_at: str | None) -> str:
    """Return a softer, human-friendly last check-in label."""
    if not logged_at:
        return "No check-ins yet"

    dt = datetime.fromisoformat(logged_at)
    now = datetime.now()
    today = now.date()
    if dt.date() == today:
        return f"Today {dt.strftime('%I:%M %p').lstrip('0')}"
    if dt.date() == (today - timedelta(days=1)):
        return f"Yesterday {dt.strftime('%I:%M %p').lstrip('0')}"
    if dt.year == today.year:
        return dt.strftime("%b %d, %I:%M %p").replace(" 0", " ").lstrip("0")
    return dt.strftime("%Y-%m-%d %I:%M %p").replace(" 0", " ").lstrip("0")


def get_period_targets(habit):
    # sqlite3.Row supports dict-style indexing but not .get()
    frequency_type = (habit["frequency_type"] if "frequency_type" in habit.keys() else "daily") or "daily"
    target_count = int(
        (habit["target_count"] if "target_count" in habit.keys() else None)
        or (habit["daily_target"] if "daily_target" in habit.keys() else None)
        or 1
    )
    frequency_value = int((habit["frequency_value"] if "frequency_value" in habit.keys() else None) or 1)
    created_date = get_created_date_from_habit(habit)
    return frequency_type, frequency_value, target_count, created_date


def get_period_ranges_for_window(
    frequency_type: str,
    frequency_value: int,
    range_start: date,
    range_end: date,
    anchor_date: date,
):
    periods = []

    if frequency_type == "daily":
        cursor = range_start
        while cursor <= range_end:
            periods.append((cursor, cursor))
            cursor += timedelta(days=1)
        return periods

    if frequency_type in {"x_per_week", "weekly"}:
        cursor = get_week_start(range_start)
        while cursor <= range_end:
            periods.append((cursor, cursor + timedelta(days=6)))
            cursor += timedelta(weeks=1)
        return periods

    if frequency_type == "every_n_days":
        cursor = get_cycle_start(anchor_date, range_start, frequency_value)
        while cursor <= range_end:
            periods.append((cursor, cursor + timedelta(days=frequency_value - 1)))
            cursor += timedelta(days=frequency_value)
        return periods

    return periods


def get_period_stat_labels(frequency_type: str, frequency_value: int):
    if frequency_type in {"x_per_week", "weekly"}:
        return "Successful weeks", "Avg / week", "weeks"
    if frequency_type == "every_n_days":
        return "Successful cycles", "Avg / cycle", "cycles"
    return "Successful days", "Avg / day", "days"


def get_period_total(log_map: dict, period_start: date, period_end: date, clip_start: date | None = None, clip_end: date | None = None):
    effective_start = max(period_start, clip_start) if clip_start else period_start
    effective_end = min(period_end, clip_end) if clip_end else period_end
    if effective_start > effective_end:
        return 0
    return sum(log_map.get(d, 0) for d in daterange(effective_start, effective_end))


def get_successful_period_streak(
    habit_id: int,
    frequency_type: str,
    frequency_value: int,
    target_count: int,
    anchor_date: date,
):
    today = date.today()

    if frequency_type == "daily":
        lookback_start = today - timedelta(days=730)
    elif frequency_type in {"x_per_week", "weekly"}:
        lookback_start = get_week_start(today) - timedelta(weeks=104)
    else:
        lookback_start = anchor_date
        while (today - lookback_start).days < frequency_value * 104:
            break
        approx_days = max(frequency_value * 104, 365)
        lookback_start = max(anchor_date, today - timedelta(days=approx_days))

    log_map = get_habit_logs_summary(habit_id, lookback_start, today)
    periods = get_period_ranges_for_window(frequency_type, frequency_value, lookback_start, today, anchor_date)

    streak = 0
    for period_start, period_end in reversed(periods):
        total = get_period_total(log_map, period_start, min(period_end, today))
        if total >= target_count:
            streak += 1
        else:
            break
    return streak


def get_monthly_stats():
    """Return period-aware monthly stats for daily, weekly, and cycle-based habits."""
    today = date.today()
    current_month_start = today.replace(day=1)
    current_month_end = today

    if today.month == 1:
        prev_month_date = date(today.year - 1, 12, 1)
    else:
        prev_month_date = date(today.year, today.month - 1, 1)
    prev_month_start, prev_month_end = get_month_date_range(prev_month_date)

    habits = get_active_habits()
    stats = []

    for habit in habits:
        frequency_type, frequency_value, target_count, created_date = get_period_targets(habit)
        log_map = get_habit_logs_summary(habit["id"], prev_month_start, current_month_end)

        current_total = sum(log_map.get(d, 0) for d in daterange(current_month_start, current_month_end))
        prev_total = sum(log_map.get(d, 0) for d in daterange(prev_month_start, prev_month_end))
        current_active_days = sum(1 for d in daterange(current_month_start, current_month_end) if log_map.get(d, 0) > 0)
        prev_active_days = sum(1 for d in daterange(prev_month_start, prev_month_end) if log_map.get(d, 0) > 0)

        current_periods = get_period_ranges_for_window(
            frequency_type, frequency_value, current_month_start, current_month_end, created_date
        )
        prev_periods = get_period_ranges_for_window(
            frequency_type, frequency_value, prev_month_start, prev_month_end, created_date
        )

        current_period_totals = [
            get_period_total(log_map, ps, pe, current_month_start, current_month_end)
            for ps, pe in current_periods
        ]
        prev_period_totals = [
            get_period_total(log_map, ps, pe, prev_month_start, prev_month_end)
            for ps, pe in prev_periods
        ]

        current_successful_periods = sum(1 for total in current_period_totals if total >= target_count)
        prev_successful_periods = sum(1 for total in prev_period_totals if total >= target_count)
        current_period_count = len(current_periods)
        prev_period_count = len(prev_periods)

        current_avg = round(sum(current_period_totals) / current_period_count, 2) if current_period_count else 0
        prev_avg = round(sum(prev_period_totals) / prev_period_count, 2) if prev_period_count else 0
        current_completion_rate = round(current_successful_periods / current_period_count * 100, 1) if current_period_count else 0
        prev_completion_rate = round(prev_successful_periods / prev_period_count * 100, 1) if prev_period_count else 0

        current_period_label, avg_label, streak_unit = get_period_stat_labels(frequency_type, frequency_value)
        streak_value = f"{get_successful_period_streak(habit['id'], frequency_type, frequency_value, target_count, created_date)} {streak_unit}"

        if prev_completion_rate == 0:
            change_pct = None if current_completion_rate > 0 else 0
        else:
            change_pct = round((current_completion_rate - prev_completion_rate) / prev_completion_rate * 100, 1)

        stats.append({
            "habit_id": habit["id"],
            "habit_name": habit["name"],
            "habit_type": habit["habit_type"] or "count",
            "frequency_type": frequency_type,
            "frequency_value": frequency_value,
            "target_count": target_count,
            "current_total": current_total,
            "current_active_days": current_active_days,
            "current_avg": current_avg,
            "prev_total": prev_total,
            "prev_active_days": prev_active_days,
            "prev_avg": prev_avg,
            "change_pct": change_pct,
            "current_successful_periods": current_successful_periods,
            "prev_successful_periods": prev_successful_periods,
            "current_period_count": current_period_count,
            "prev_period_count": prev_period_count,
            "current_completion_rate": current_completion_rate,
            "prev_completion_rate": prev_completion_rate,
            "current_period_label": current_period_label,
            "avg_label": avg_label,
            "streak_label": "Streak",
            "streak_value": streak_value,
        })

    return stats


def get_recent_period_data(habit, max_periods: int = 12):
    """Return a period-aware DataFrame for charts/tables.

    Daily habits show recent days, weekly habits show recent weeks,
    and every-n-days habits show recent cycles.
    """
    frequency_type, frequency_value, target_count, created_date = get_period_targets(habit)
    today = date.today()

    if frequency_type == "daily":
        range_start = today - timedelta(days=29)
        periods = [(d, d) for d in daterange(range_start, today)]
        label_col = "day"
        chart_title = "Last 30 days"
        target_label = "Daily target"
    elif frequency_type in {"x_per_week", "weekly"}:
        current_week_start = get_week_start(today)
        first_week_start = current_week_start - timedelta(weeks=max_periods - 1)
        periods = []
        cursor = first_week_start
        while cursor <= current_week_start:
            periods.append((cursor, cursor + timedelta(days=6)))
            cursor += timedelta(weeks=1)
        label_col = "week"
        chart_title = f"Last {len(periods)} weeks"
        target_label = "Weekly target"
    else:
        cycle_start = get_cycle_start(created_date, today, frequency_value)
        periods = []
        cursor = cycle_start
        for _ in range(max_periods):
            periods.append((cursor, cursor + timedelta(days=frequency_value - 1)))
            cursor -= timedelta(days=frequency_value)
        periods = list(reversed(periods))
        label_col = "cycle"
        chart_title = f"Last {len(periods)} cycles"
        target_label = f"Target / {frequency_value}-day cycle"

    query_start = min(ps for ps, _ in periods)
    query_end = max(min(pe, today) for _, pe in periods)
    log_map = get_habit_logs_summary(habit["id"], query_start, query_end)

    records = []
    for period_start, period_end in periods:
        effective_end = min(period_end, today)
        total = get_period_total(log_map, period_start, effective_end)
        success = total >= target_count

        if frequency_type == "daily":
            label = period_start.strftime("%m-%d")
        elif frequency_type in {"x_per_week", "weekly"}:
            label = f"{period_start.strftime('%m-%d')}"
        else:
            label = f"{period_start.strftime('%m-%d')}"

        records.append({
            label_col: label,
            "period_start": period_start.isoformat(),
            "period_end": effective_end.isoformat(),
            "count": total,
            "target": target_count,
            "success": "✓" if success else "",
        })

    df = pd.DataFrame(records)
    return df, label_col, chart_title, target_label


# -----------------------------
# Streamlit UI
# -----------------------------

st.set_page_config(page_title="Habit Tracker", page_icon="✅", layout="centered")

st.markdown("""
<style>
.block-container {
    max-width: 760px;
    padding-top: 0.55rem !important;
    padding-bottom: 1rem !important;
    padding-left: 0.8rem !important;
    padding-right: 0.8rem !important;
}

html, body, [class*="css"] {
    font-size: 14px;
}

h1 {
    font-size: 1.8rem !important;
    margin-bottom: 0.1rem !important;
}
h2 {
    font-size: 1.15rem !important;
    margin-bottom: 0.2rem !important;
}
h3, h4 {
    font-size: 1rem !important;
    margin-bottom: 0.15rem !important;
}

p {
    margin-bottom: 0.28rem !important;
}

.stButton > button {
    height: 2.2rem;
    padding: 0.24rem 0.62rem;
    font-size: 0.92rem;
    border-radius: 0.75rem;
}

.stTextInput input, .stNumberInput input {
    font-size: 0.95rem !important;
    padding: 0.42rem 0.58rem !important;
}

[data-testid="stCaptionContainer"] {
    font-size: 12px;
}

.compact-card {
    border: 1px solid rgba(128,128,128,0.18);
    border-radius: 14px;
    padding: 0.62rem 0.72rem 0.5rem 0.72rem;
    margin-bottom: 0.42rem;
    background: transparent;
}

.compact-title-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    margin-bottom: 0.02rem;
}

.compact-title {
    font-weight: 650;
    font-size: 1rem;
    margin-bottom: 0.02rem;
}

.status-badge {
    display: inline-block;
    white-space: nowrap;
    padding: 0.18rem 0.5rem;
    border-radius: 999px;
    font-size: 0.72rem;
    font-weight: 600;
    line-height: 1.1;
    border: 1px solid transparent;
}

.status-badge.done {
    color: #0f766e;
    background: rgba(20, 184, 166, 0.10);
    border-color: rgba(20, 184, 166, 0.20);
}

.status-badge.pending {
    color: #9a6700;
    background: rgba(245, 158, 11, 0.10);
    border-color: rgba(245, 158, 11, 0.20);
}

.status-badge.complete {
    color: #166534;
    background: rgba(34, 197, 94, 0.10);
    border-color: rgba(34, 197, 94, 0.20);
}

.status-badge.on-track {
    color: #1d4ed8;
    background: rgba(59, 130, 246, 0.10);
    border-color: rgba(59, 130, 246, 0.20);
}

.status-badge.not-started {
    color: #6b7280;
    background: rgba(107, 114, 128, 0.08);
    border-color: rgba(107, 114, 128, 0.16);
}

.compact-subtle {
    color: rgba(120,120,120,1);
    font-size: 0.84rem;
    margin-bottom: 0.2rem;
}

.last-line {
    color: rgba(145,145,145,1);
    font-size: 0.82rem;
    text-align: right;
    margin-top: 0.18rem;
}

.chip-row {
    margin: 0.12rem 0 0.22rem 0;
}

.chip {
    display: inline-block;
    padding: 0.14rem 0.46rem;
    border-radius: 999px;
    font-size: 0.75rem;
    margin-right: 0.28rem;
    margin-bottom: 0.18rem;
    border: 1px solid rgba(128,128,128,0.18);
    background: rgba(240,240,240,0.04);
}

.metric-line {
    font-size: 0.91rem;
    margin: 0.02rem 0 0.08rem 0;
}

.section-note {
    color: rgba(120,120,120,1);
    font-size: 0.85rem;
    margin-top: -0.2rem;
    margin-bottom: 0.45rem;
}

.expander-note {
    color: rgba(120,120,120,1);
    font-size: 0.82rem;
    margin-top: -0.08rem;
    margin-bottom: 0.35rem;
}

.action-note {
    color: rgba(120,120,120,1);
    font-size: 0.76rem;
    margin-top: 0.02rem;
    margin-bottom: 0.02rem;
}

.manage-box {
    margin-top: 0.06rem;
    padding-top: 0.05rem;
}

.edit-box {
    margin-top: 0.12rem;
    padding: 0.36rem 0.42rem 0.08rem 0.42rem;
    border-radius: 12px;
    background: rgba(240,240,240,0.03);
    border: 1px solid rgba(128,128,128,0.10);
}

.mobile-primary-row {
    margin-top: 0.16rem;
    margin-bottom: 0.00rem;
}

.progress-track {
    width: 100%;
    height: 0.36rem;
    background: rgba(15, 23, 42, 0.08);
    border-radius: 999px;
    overflow: hidden;
    margin: 0.16rem 0 0.22rem 0;
}

.progress-fill {
    height: 100%;
    border-radius: 999px;
    transition: width 0.25s ease;
}

.progress-fill.done,
.progress-fill.complete {
    background: linear-gradient(90deg, #34d399 0%, #22c55e 100%);
}

.progress-fill.pending,
.progress-fill.not-started {
    background: linear-gradient(90deg, #d1d5db 0%, #9ca3af 100%);
}

.progress-fill.on-track {
    background: linear-gradient(90deg, #60a5fa 0%, #2563eb 100%);
}

[data-testid="stExpander"] {
    border: none !important;
    background: transparent !important;
}

[data-testid="stExpander"] details {
    border: none !important;
    background: transparent !important;
    box-shadow: none !important;
}

[data-testid="stExpander"] summary {
    padding-left: 0 !important;
    padding-right: 0 !important;
}

.manage-expander {
    margin-top: 0.08rem;
}

@media (max-width: 640px) {
    .block-container {
        padding-left: 0.55rem !important;
        padding-right: 0.55rem !important;
    }
    h1 {
        font-size: 1.55rem !important;
    }
    .compact-card {
        padding: 0.58rem 0.62rem 0.48rem 0.62rem;
        border-radius: 13px;
    }
    .stButton > button {
        font-size: 0.87rem;
        height: 1.95rem;
    }
}
</style>
""", unsafe_allow_html=True)

init_db()

st.title("✅ Habit Tracker")
st.caption("Flexible habit tracking with period-aware progress, improved completion logic, and a cleaner mobile UI.")


with st.expander("Add a New Habit", expanded=False):
    st.markdown('<div class="expander-note">Start simple. You can always edit the rule later.</div>', unsafe_allow_html=True)
    with st.form("add_habit_form", clear_on_submit=True):
        new_habit = st.text_input(
            "Habit name",
            placeholder="e.g. Drink water, Running, Face mask"
        )

        col1, col2 = st.columns(2)
        with col1:
            habit_type = st.selectbox(
                "Habit type",
                options=["count", "completion"],
                format_func=lambda x: "Count" if x == "count" else "Completion"
            )
        with col2:
            frequency_type = st.selectbox(
                "Frequency",
                options=["daily", "x_per_week", "every_n_days", "weekly"],
                format_func=lambda x: {
                    "daily": "Daily",
                    "x_per_week": "X times / week",
                    "every_n_days": "Every N days",
                    "weekly": "Weekly"
                }[x]
            )

        add_cfg = get_frequency_form_config(frequency_type)
        if add_cfg["show_frequency_input"]:
            frequency_value = st.number_input(
                add_cfg["frequency_label"],
                min_value=1,
                value=int(add_cfg["frequency_value"]),
                step=1,
            )
        else:
            frequency_value = int(add_cfg["frequency_value"])
            st.caption(add_cfg["period_note"])

        target_count = st.number_input(
            add_cfg["target_label"],
            min_value=1,
            value=int(add_cfg["target_value"]),
            step=1,
        )
        if add_cfg["show_frequency_input"]:
            st.caption(add_cfg["period_note"])
        submitted = st.form_submit_button("Add habit", use_container_width=True)

        if submitted:
            if new_habit.strip():
                result = add_habit(
                    name=new_habit,
                    habit_type=habit_type,
                    frequency_type=frequency_type,
                    frequency_value=int(frequency_value),
                    target_count=int(target_count),
                )
                if result == "Habit added.":
                    st.success(f"Added habit: {new_habit.strip()}")
                    st.rerun()
                elif result == "Habit restored.":
                    st.success(f"Restored habit: {new_habit.strip()}")
                    st.rerun()
                else:
                    st.warning(result)
            else:
                st.warning("Please enter a habit name.")

st.divider()

with st.expander("Current Progress", expanded=True):
    st.markdown('<div class="expander-note">Your active habits for the current period.</div>', unsafe_allow_html=True)

    progress_rows = get_current_progress()

    if not progress_rows:
        st.info("No habits yet. Add your first habit above.")
    else:
        for idx, row in enumerate(progress_rows):
            target = row["target_count"] if row["target_count"] else 1
            current_count = row["current_count"]
            progress = min(current_count / target, 1.0)
            period_short = row["period_label"].split(" (")[0]
            last_text = format_last_checkin_text(row["last_logged_at"])

            card_bg = "#ffffff" if idx % 2 == 0 else "#f7f7f8"
            with st.container():
                is_count_habit = row["habit_type"] == "count"
                is_single_completion = row["habit_type"] == "completion" and target == 1
                is_multi_completion = row["habit_type"] == "completion" and target > 1
                done_this_period = is_single_completion and current_count >= 1

                status_badge_html = ""
                if is_single_completion:
                    badge_class = "done" if done_this_period else "pending"
                    badge_label = "Done" if done_this_period else "Not done"
                    status_badge_html = f'<span class="status-badge {badge_class}">{badge_label}</span>'
                else:
                    if current_count >= target:
                        badge_class = "complete"
                        badge_label = "Complete"
                    elif current_count > 0:
                        badge_class = "on-track"
                        badge_label = "On track"
                    else:
                        badge_class = "not-started"
                        badge_label = "Pending"
                    status_badge_html = f'<span class="status-badge {badge_class}">{badge_label}</span>'

                st.markdown(
                    f'<div class="compact-card" style="background:{card_bg};">',
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f'<div class="compact-title-row"><div class="compact-title">{row["habit_name"]}</div>{status_badge_html}</div>',
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f"""
                    <div class="chip-row">
                        <span class="chip">{row["rule_text"]}</span>
                        <span class="chip">{period_short}</span>
                        <span class="chip">{current_count} / {target}</span>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

                meta_col1, meta_col2 = st.columns([1, 1])
                with meta_col1:
                    st.markdown(f'<div class="metric-line"><strong>Progress:</strong> {current_count} / {target}</div>', unsafe_allow_html=True)
                with meta_col2:
                    st.markdown(f'<div class="last-line">Last: {last_text}</div>', unsafe_allow_html=True)

                st.markdown(f'<div class="progress-track"><div class="progress-fill {badge_class}" style="width:{progress * 100:.1f}%"></div></div>', unsafe_allow_html=True)

                st.markdown('<div class="mobile-primary-row">', unsafe_allow_html=True)

                if is_count_habit:
                    if st.button("Check in +1", key=f"log_{row['habit_id']}", use_container_width=True):
                        log_habit(row["habit_id"], count=1)
                        st.rerun()
                elif is_single_completion:
                    action_col1, action_col2 = st.columns([3.2, 1.2])
                    with action_col1:
                        if done_this_period:
                            st.button("Done", key=f"done_{row['habit_id']}", use_container_width=True, disabled=True)
                        else:
                            if st.button("Mark done", key=f"log_{row['habit_id']}", use_container_width=True):
                                error = log_completion_once_for_current_period(row["habit_id"])
                                if error:
                                    st.info(error)
                                st.rerun()
                    with action_col2:
                        if st.button("Undo", key=f"undo_{row['habit_id']}", use_container_width=True, disabled=not done_this_period):
                            error = undo_completion_for_current_period(row["habit_id"])
                            if error:
                                st.info(error)
                            st.rerun()
                    st.markdown(
                        f'<div class="action-note">{("Completed for this period" if done_this_period else "Not done yet for this period")}</div>',
                        unsafe_allow_html=True,
                    )
                elif is_multi_completion:
                    if st.button("Log session", key=f"log_{row['habit_id']}", use_container_width=True):
                        log_habit(row["habit_id"], count=1)
                        st.rerun()
                    st.markdown('<div class="action-note">Each tap records one completion event.</div>', unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)

                st.markdown('<div class="manage-expander">', unsafe_allow_html=True)
                with st.expander("Manage", expanded=False):
                    st.markdown('<div class="edit-box">', unsafe_allow_html=True)

                    habit_id = row["habit_id"]
                    name_key = f"manage_name_{habit_id}"
                    type_key = f"manage_type_{habit_id}"
                    freq_key = f"manage_freq_{habit_id}"
                    freq_value_key = f"manage_freq_value_{habit_id}"
                    target_key = f"manage_target_{habit_id}"

                    if name_key not in st.session_state:
                        st.session_state[name_key] = row["habit_name"]
                    if type_key not in st.session_state:
                        st.session_state[type_key] = row["habit_type"]
                    if freq_key not in st.session_state:
                        st.session_state[freq_key] = row["frequency_type"]
                    if freq_value_key not in st.session_state:
                        st.session_state[freq_value_key] = int(row["frequency_value"] or 1)
                    if target_key not in st.session_state:
                        st.session_state[target_key] = int(row["target_count"] or row["daily_target"] or 1)

                    edit_name = st.text_input("Habit name", key=name_key)

                    edit_col1, edit_col2 = st.columns(2)
                    with edit_col1:
                        edit_habit_type = st.selectbox(
                            "Habit type",
                            options=["count", "completion"],
                            format_func=lambda x: "Count" if x == "count" else "Completion",
                            key=type_key,
                        )
                    with edit_col2:
                        edit_frequency_type = st.selectbox(
                            "Frequency",
                            options=["daily", "x_per_week", "every_n_days", "weekly"],
                            format_func=lambda x: {
                                "daily": "Daily",
                                "x_per_week": "X times / week",
                                "every_n_days": "Every N days",
                                "weekly": "Weekly",
                            }[x],
                            key=freq_key,
                        )

                    current_frequency_value = int(st.session_state.get(freq_value_key, 1) or 1)
                    current_target_value = int(st.session_state.get(target_key, 1) or 1)
                    edit_cfg = get_frequency_form_config(
                        edit_frequency_type,
                        frequency_value=current_frequency_value,
                        target_count=current_target_value,
                    )

                    if edit_cfg["show_frequency_input"]:
                        edit_frequency_value = st.number_input(
                            edit_cfg["frequency_label"],
                            min_value=1,
                            step=1,
                            key=freq_value_key,
                        )
                    else:
                        st.session_state[freq_value_key] = int(edit_cfg["frequency_value"])
                        edit_frequency_value = int(edit_cfg["frequency_value"])

                    edit_target = st.number_input(
                        edit_cfg["target_label"],
                        min_value=1,
                        step=1,
                        key=target_key,
                    )
                    st.caption(edit_cfg["period_note"])

                    save_col1, save_col2, save_col3 = st.columns([1, 1, 1])
                    with save_col1:
                        save_clicked = st.button("Save", key=f"save_manage_{habit_id}", use_container_width=True)
                    with save_col2:
                        close_clicked = st.button("Close", key=f"close_manage_{habit_id}", use_container_width=True)
                    with save_col3:
                        hide_clicked = st.button("Hide habit", key=f"hide_manage_{habit_id}", use_container_width=True)

                    if save_clicked:
                        error = update_habit(
                            habit_id=habit_id,
                            new_name=st.session_state[name_key],
                            habit_type=st.session_state[type_key],
                            frequency_type=st.session_state[freq_key],
                            frequency_value=int(st.session_state.get(freq_value_key, 1) or 1),
                            target_count=int(st.session_state.get(target_key, 1) or 1),
                        )
                        if error:
                            st.error(error)
                        else:
                            st.success("Habit updated.")
                            st.rerun()

                    if close_clicked:
                        st.rerun()

                    if hide_clicked:
                        deactivate_habit(habit_id)
                        st.rerun()

                    st.markdown('</div>', unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)

                st.markdown('</div>', unsafe_allow_html=True)

with st.expander("Recent log details", expanded=False):
    today_logs = get_recent_logs()
    if not today_logs:
        st.write("No recent logs yet.")
    else:
        for log in today_logs:
            log_dt = datetime.fromisoformat(log["logged_at"])
            log_time = log_dt.strftime("%m-%d %I:%M %p")
            event_text = format_recent_log_event_text(log)
            left, right = st.columns([6, 1])
            with left:
                st.write(f"**{log['habit_name']}** — {log_time} · {event_text}")
            with right:
                if st.button("✕", key=f"delete_{log['id']}", use_container_width=True):
                    delete_log(log["id"])
                    st.rerun()

with st.expander("Monthly Statistics", expanded=False):
    monthly_stats = get_monthly_stats()
    active_habit_map = {h["id"]: h for h in get_active_habits()}
    if not monthly_stats:
        st.write("No habits available for statistics yet.")
    else:
        for stat in monthly_stats:
            rule_text = format_rule_text(
                stat["habit_type"], stat["frequency_type"], stat["frequency_value"], stat["target_count"]
            )

            st.markdown('<div class="compact-card">', unsafe_allow_html=True)
            st.markdown(f'<div class="compact-title">{stat["habit_name"]}</div>', unsafe_allow_html=True)
            st.markdown(
                f"""
                <div class="chip-row">
                    <span class="chip">{rule_text}</span>
                    <span class="chip">Completion {stat["current_completion_rate"]}%</span>
                    <span class="chip">{stat["streak_value"]} streak</span>
                </div>
                """,
                unsafe_allow_html=True
            )

            left_col, right_col = st.columns(2)
            with left_col:
                st.markdown(f'<div class="metric-line"><strong>This month total:</strong> {stat["current_total"]}</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="metric-line"><strong>{stat["avg_label"]}:</strong> {stat["current_avg"]}</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="metric-line"><strong>Active days:</strong> {stat["current_active_days"]}</div>', unsafe_allow_html=True)
            with right_col:
                change_text = "New this month" if stat["change_pct"] is None else f'{stat["change_pct"]}%'
                st.markdown(f'<div class="metric-line"><strong>{stat["current_period_label"]}:</strong> {stat["current_successful_periods"]} / {stat["current_period_count"]}</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="metric-line"><strong>Vs last month:</strong> {change_text}</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="metric-line"><strong>Last month total:</strong> {stat["prev_total"]}</div>', unsafe_allow_html=True)

            st.caption(
                f"Last month {stat['avg_label'].lower()}: {stat['prev_avg']} · "
                f"Last month success: {stat['prev_successful_periods']} / {stat['prev_period_count']} · "
                f"Last month completion rate: {stat['prev_completion_rate']}%"
            )

            habit_row = active_habit_map.get(stat["habit_id"])
            if habit_row:
                recent_df, label_col, chart_title, target_label = get_recent_period_data(habit_row)
            else:
                recent_df = pd.DataFrame()
                label_col = "period"
                chart_title = "Recent periods"
                target_label = "Target"

            with st.expander(chart_title, expanded=False):
                if recent_df.empty:
                    st.write("No recent data yet.")
                else:
                    show_df = recent_df.copy()
                    st.dataframe(show_df, use_container_width=True, hide_index=True)
                    st.caption(f"{target_label}: {stat['target_count']}")
                    st.bar_chart(show_df.set_index(label_col)["count"])

            st.markdown('</div>', unsafe_allow_html=True)
