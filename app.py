# v4.8.0 PostgreSQL migration

import psycopg2
import psycopg2.extras
from datetime import datetime, date, timedelta
from calendar import monthrange
import streamlit as st
import pandas as pd

WEEKDAY_OPTIONS = [(0, "Mon"), (1, "Tue"), (2, "Wed"), (3, "Thu"), (4, "Fri"), (5, "Sat"), (6, "Sun")]
WEEKDAY_LABEL_MAP = {idx: label for idx, label in WEEKDAY_OPTIONS}

REMINDER_BUCKET_OPTIONS = [
    ("anytime", "Anytime"),
    ("morning", "Morning"),
    ("afternoon", "Afternoon"),
    ("evening", "Evening"),
]
REMINDER_BUCKET_LABEL_MAP = {key: label for key, label in REMINDER_BUCKET_OPTIONS}
DEFAULT_HABIT_GROUP = "General"


def normalize_habit_group(value: str | None) -> str:
    clean_value = str(value or "").strip()
    return clean_value if clean_value else DEFAULT_HABIT_GROUP


def parse_scheduled_days(value) -> list[int]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        values = value
    else:
        values = str(value).split(",")
    parsed = []
    for item in values:
        item = str(item).strip()
        if item == "":
            continue
        day = int(item)
        if 0 <= day <= 6:
            parsed.append(day)
    return sorted(set(parsed))


def serialize_scheduled_days(days) -> str:
    return ",".join(str(day) for day in parse_scheduled_days(days))


def format_weekday_short_list(days) -> str:
    parsed = parse_scheduled_days(days)
    return ", ".join(WEEKDAY_LABEL_MAP[d] for d in parsed)


def normalize_reminder_bucket(value: str | None) -> str:
    if value in REMINDER_BUCKET_LABEL_MAP:
        return value
    return "anytime"


def format_reminder_bucket_label(value: str | None) -> str:
    return REMINDER_BUCKET_LABEL_MAP[normalize_reminder_bucket(value)]


def format_target_date_short(target_date: date) -> str:
    today = date.today()
    if target_date == today:
        return "today"
    if target_date == (today - timedelta(days=1)):
        return "yesterday"
    return target_date.strftime("%b %d")


# -----------------------------
# Database helpers
# -----------------------------
def get_connection():
    """Create a PostgreSQL connection using Streamlit secrets."""
    url = st.secrets["DATABASE_URL"]
    conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
    return conn


def column_exists(cur, table_name: str, column_name: str) -> bool:
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
    """, (table_name, column_name))
    return cur.fetchone() is not None


def init_db():
    """Create tables and apply migrations for PostgreSQL."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS habits (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            daily_target INTEGER DEFAULT 1,
            habit_type TEXT DEFAULT 'count',
            frequency_type TEXT DEFAULT 'daily',
            frequency_value INTEGER DEFAULT 1,
            target_count INTEGER DEFAULT 1,
            schedule_mode TEXT DEFAULT 'none',
            scheduled_days TEXT DEFAULT '',
            reminder_bucket TEXT DEFAULT 'anytime',
            habit_group TEXT DEFAULT 'General',
            habit_link TEXT DEFAULT '',
            track_time INTEGER DEFAULT 0,
            estimated_minutes INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS habit_logs (
            id SERIAL PRIMARY KEY,
            habit_id INTEGER NOT NULL REFERENCES habits(id),
            user_id TEXT NOT NULL DEFAULT '',
            logged_at TEXT NOT NULL,
            log_date TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 1
        )
    """)

    # Migrations — add any missing columns
    for col, defn in [
        ("user_id", "TEXT NOT NULL DEFAULT ''"),
        ("daily_target", "INTEGER DEFAULT 1"),
        ("habit_type", "TEXT DEFAULT 'count'"),
        ("frequency_type", "TEXT DEFAULT 'daily'"),
        ("frequency_value", "INTEGER DEFAULT 1"),
        ("target_count", "INTEGER DEFAULT 1"),
        ("schedule_mode", "TEXT DEFAULT 'none'"),
        ("scheduled_days", "TEXT DEFAULT ''"),
        ("reminder_bucket", "TEXT DEFAULT 'anytime'"),
        ("habit_group", "TEXT DEFAULT 'General'"),
        ("habit_link", "TEXT DEFAULT ''"),
        ("track_time", "INTEGER DEFAULT 0"),
        ("estimated_minutes", "INTEGER DEFAULT 0"),
    ]:
        if not column_exists(cur, "habits", col):
            cur.execute(f"ALTER TABLE habits ADD COLUMN {col} {defn}")

    for col, defn in [
        ("user_id", "TEXT NOT NULL DEFAULT ''"),
    ]:
        if not column_exists(cur, "habit_logs", col):
            cur.execute(f"ALTER TABLE habit_logs ADD COLUMN {col} {defn}")

    # Backfill duration habits
    cur.execute("""
        UPDATE habits
        SET track_time = 1, estimated_minutes = target_count
        WHERE habit_type = 'duration' AND track_time = 0
    """)

    conn.commit()
    conn.close()



def normalize_habit_inputs(
    habit_type: str,
    frequency_type: str,
    frequency_value: int,
    target_count: int,
    schedule_mode: str = "none",
    scheduled_days=None,
    reminder_bucket: str = "anytime",
    habit_group: str = DEFAULT_HABIT_GROUP,
    habit_link: str = "",
    track_time: bool = False,
    estimated_minutes: int = 0,
):
    """Basic validation and normalization for v4 habit settings."""
    valid_habit_types = {"count", "completion", "duration"}
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

    parsed_days = parse_scheduled_days(scheduled_days)
    if frequency_type not in {"x_per_week", "weekly"}:
        schedule_mode = "none"
        parsed_days = []
    elif schedule_mode == "weekdays":
        if not parsed_days:
            if frequency_type == "weekly":
                parsed_days = [0]
            else:
                return None, "Please choose at least one scheduled weekday."
        if frequency_type == "weekly":
            parsed_days = [parsed_days[0]]
    else:
        schedule_mode = "none"
        parsed_days = []

    # Duration habits always track time using target_count as estimate
    if habit_type == "duration":
        track_time = True
        estimated_minutes = int(target_count)
    else:
        track_time = bool(track_time)
        estimated_minutes = max(0, int(estimated_minutes or 0))

    normalized = {
        "habit_type": habit_type,
        "frequency_type": frequency_type,
        "frequency_value": int(frequency_value),
        "target_count": int(target_count),
        "daily_target": int(target_count) if frequency_type == "daily" else max(1, int(target_count)),
        "schedule_mode": schedule_mode,
        "scheduled_days": serialize_scheduled_days(parsed_days),
        "reminder_bucket": normalize_reminder_bucket(reminder_bucket),
        "habit_group": normalize_habit_group(habit_group),
        "habit_link": (habit_link or "").strip(),
        "track_time": 1 if track_time else 0,
        "estimated_minutes": estimated_minutes,
    }
    return normalized, None


def add_habit(
    name: str,
    user_id: str = "",
    habit_type: str = "count",
    frequency_type: str = "daily",
    frequency_value: int = 1,
    target_count: int = 1,
    schedule_mode: str = "none",
    scheduled_days=None,
    reminder_bucket: str = "anytime",
    habit_group: str = DEFAULT_HABIT_GROUP,
    habit_link: str = "",
    track_time: bool = False,
    estimated_minutes: int = 0,
):
    """Add a new habit, or reactivate it if it already exists but is inactive."""
    clean_name = name.strip()
    if not clean_name:
        return "Habit name cannot be empty."

    normalized, error = normalize_habit_inputs(
        habit_type=habit_type,
        frequency_type=frequency_type,
        frequency_value=frequency_value,
        target_count=target_count,
        schedule_mode=schedule_mode,
        scheduled_days=scheduled_days,
        reminder_bucket=reminder_bucket,
        habit_group=habit_group,
        habit_link=habit_link,
        track_time=track_time,
        estimated_minutes=estimated_minutes,
    )
    if error:
        return error

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, is_active
        FROM habits
        WHERE name = %s AND user_id = %s
    """, (clean_name, user_id))
    existing = cur.fetchone()

    if existing:
        if existing["is_active"] == 1:
            conn.close()
            return "Habit name already exists."
        else:
            cur.execute("""
                UPDATE habits
                SET is_active = 1,
                    habit_type = %s,
                    frequency_type = %s,
                    frequency_value = %s,
                    target_count = %s,
                    daily_target = %s,
                    schedule_mode = %s,
                    scheduled_days = %s,
                    reminder_bucket = %s,
                    habit_group = %s,
                    habit_link = %s,
                    track_time = %s,
                    estimated_minutes = %s
                WHERE id = %s
            """, (
                normalized["habit_type"],
                normalized["frequency_type"],
                normalized["frequency_value"],
                normalized["target_count"],
                normalized["daily_target"],
                normalized["schedule_mode"],
                normalized["scheduled_days"],
                normalized["reminder_bucket"],
                normalized["habit_group"],
                normalized["habit_link"],
                normalized["track_time"],
                normalized["estimated_minutes"],
                existing["id"],
            ))
            conn.commit()
            conn.close()
            return "Habit restored."

    cur.execute("""
        INSERT INTO habits (
            user_id, name, created_at, is_active,
            daily_target, habit_type, frequency_type, frequency_value, target_count,
            schedule_mode, scheduled_days, reminder_bucket, habit_group, habit_link,
            track_time, estimated_minutes
        )
        VALUES (%s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        user_id,
        clean_name,
        datetime.now().isoformat(),
        normalized["daily_target"],
        normalized["habit_type"],
        normalized["frequency_type"],
        normalized["frequency_value"],
        normalized["target_count"],
        normalized["schedule_mode"],
        normalized["scheduled_days"],
        normalized["reminder_bucket"],
        normalized["habit_group"],
        normalized["habit_link"],
        normalized["track_time"],
        normalized["estimated_minutes"],
    ))

    conn.commit()
    conn.close()
    return "Habit added."


def get_active_habits(user_id: str = ""):
    """Return all active habits for a user."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id, name, created_at, daily_target,
            habit_type, frequency_type, frequency_value, target_count,
            schedule_mode, scheduled_days, reminder_bucket, habit_group,
            habit_link, track_time, estimated_minutes
        FROM habits
        WHERE is_active = 1 AND user_id = %s
        ORDER BY created_at ASC
    """, (user_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_existing_habit_groups(user_id: str = "", include_default: bool = True) -> list[str]:
    """Return existing active habit groups for dropdowns."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT COALESCE(NULLIF(TRIM(habit_group), ''), %s) AS habit_group
        FROM habits
        WHERE is_active = 1 AND user_id = %s
        ORDER BY habit_group
    """, (DEFAULT_HABIT_GROUP, user_id))
    groups = [normalize_habit_group(row["habit_group"]) for row in cur.fetchall()]
    conn.close()

    if include_default and DEFAULT_HABIT_GROUP not in groups:
        groups.insert(0, DEFAULT_HABIT_GROUP)

    clean_groups = []
    for group in groups:
        if group not in clean_groups:
            clean_groups.append(group)
    return clean_groups


def deactivate_habit(habit_id: int):
    """Soft delete a habit by marking it inactive."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE habits
        SET is_active = 0
        WHERE id = %s
    """, (habit_id,))

    conn.commit()
    conn.close()


def log_habit(habit_id: int, user_id: str = "", count: int = 1, log_date: date | None = None):
    """Insert one habit log row."""
    now = datetime.now()
    event_date = log_date or now.date()
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO habit_logs (habit_id, user_id, logged_at, log_date, count)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (habit_id, user_id, now.isoformat(timespec="seconds"), event_date.isoformat(), count)
    )

    conn.commit()
    conn.close()


def get_habit_by_id(habit_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM habits WHERE id = %s", (habit_id,))
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
        WHERE habit_id = %s
          AND log_date BETWEEN %s AND %s
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
        WHERE habit_id = %s
          AND log_date BETWEEN %s AND %s
        ORDER BY logged_at DESC, id DESC
        LIMIT 1
        """,
        (habit["id"], period["start_date"].isoformat(), query_end.isoformat()),
    )
    row = cur.fetchone()
    conn.close()
    return row["id"] if row else None


def log_completion_once_for_current_period(habit_id: int, user_id: str = ""):
    """For completion habits with target=1, allow only one completion per current period."""
    habit = get_habit_by_id(habit_id)
    if not habit:
        return "Habit not found."

    current_total = get_current_period_total_for_habit(habit)
    if current_total >= 1:
        return "Already completed for this period."

    log_habit(habit_id, user_id=user_id, count=1)
    return None


def log_completion_once_for_date(habit_id: int, target_date: date, user_id: str = ""):
    """For completion habits with target=1, allow only one completion in the period containing target_date."""
    habit = get_habit_by_id(habit_id)
    if not habit:
        return "Habit not found."

    frequency_type, frequency_value, _, created_date = get_period_targets(habit)
    period = get_period_info_for_date(frequency_type, frequency_value, target_date, created_date)
    query_end = min(period["end_date"], date.today())

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COALESCE(SUM(count), 0) AS total_count
        FROM habit_logs
        WHERE habit_id = %s
          AND log_date BETWEEN %s AND %s
        """,
        (habit_id, period["start_date"].isoformat(), query_end.isoformat()),
    )
    row = cur.fetchone()
    conn.close()

    if int(row["total_count"] or 0) >= 1:
        return "Already completed for that period."

    log_habit(habit_id, user_id=user_id, count=1, log_date=target_date)
    return None


def undo_completion_for_current_period(habit_id: int, user_id: str = ""):
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
    target_count: int,
    schedule_mode: str = "none",
    scheduled_days=None,
    reminder_bucket: str = "anytime",
    habit_group: str = DEFAULT_HABIT_GROUP,
    habit_link: str = "",
    track_time: bool = False,
    estimated_minutes: int = 0,
):
    """Update habit settings."""
    clean_name = new_name.strip()
    if not clean_name:
        return "Habit name cannot be empty."

    normalized, error = normalize_habit_inputs(
        habit_type=habit_type,
        frequency_type=frequency_type,
        frequency_value=frequency_value,
        target_count=target_count,
        schedule_mode=schedule_mode,
        scheduled_days=scheduled_days,
        reminder_bucket=reminder_bucket,
        habit_group=habit_group,
        habit_link=habit_link,
        track_time=track_time,
        estimated_minutes=estimated_minutes,
    )
    if error:
        return error

    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            UPDATE habits
            SET name = %s, habit_type = %s, frequency_type = %s, frequency_value = %s,
                target_count = %s, daily_target = %s, schedule_mode = %s, scheduled_days = %s,
                reminder_bucket = %s, habit_group = %s, habit_link = %s,
                track_time = %s, estimated_minutes = %s
            WHERE id = %s
        """, (
            clean_name,
            normalized["habit_type"], normalized["frequency_type"],
            normalized["frequency_value"], normalized["target_count"],
            normalized["daily_target"], normalized["schedule_mode"],
            normalized["scheduled_days"], normalized["reminder_bucket"],
            normalized["habit_group"], normalized["habit_link"],
            normalized["track_time"], normalized["estimated_minutes"],
            habit_id,
        ))
        conn.commit()
        return None
    except psycopg2.IntegrityError:
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


def get_period_info_for_date(
    frequency_type: str,
    frequency_value: int,
    reference_date: date,
    anchor_date: date | None = None,
):
    """Return period range and display text for a specific reference date."""
    anchor_date = anchor_date or reference_date

    if frequency_type in {"x_per_week", "weekly"}:
        week_start = get_week_start(reference_date)
        week_end = week_start + timedelta(days=6)
        label_prefix = "This week" if reference_date == date.today() else "Selected week"
        return {
            "start_date": week_start,
            "end_date": week_end,
            "label": f"{label_prefix} ({week_start.isoformat()} → {week_end.isoformat()})"
        }

    if frequency_type == "every_n_days":
        cycle_start = get_cycle_start(anchor_date, reference_date, frequency_value)
        cycle_end = cycle_start + timedelta(days=frequency_value - 1)
        label_prefix = "Current cycle" if reference_date == date.today() else "Selected cycle"
        return {
            "start_date": cycle_start,
            "end_date": cycle_end,
            "label": f"{label_prefix} ({cycle_start.isoformat()} → {cycle_end.isoformat()})"
        }

    label_prefix = "Today" if reference_date == date.today() else reference_date.strftime("%b %d")
    return {
        "start_date": reference_date,
        "end_date": reference_date,
        "label": f"{label_prefix} ({reference_date.isoformat()})"
    }


def get_current_period_info(frequency_type: str, frequency_value: int, anchor_date: date | None = None):
    """Return period range and display text for supported v3 frequency types."""
    return get_period_info_for_date(frequency_type, frequency_value, date.today(), anchor_date)


def format_minutes(minutes: int) -> str:
    """Convert minutes to human-friendly string, e.g. 90 -> '90 min'."""
    return f"{minutes} min"


def format_rule_text(habit_type: str, frequency_type: str, frequency_value: int, target_count: int) -> str:
    """Human-friendly rule text."""
    day_unit = "day" if frequency_value == 1 else "days"

    if habit_type == "duration":
        dur = format_minutes(target_count)
        if frequency_type == "x_per_week":
            return f"{dur} / week"
        if frequency_type == "weekly":
            return f"{dur} / week"
        if frequency_type == "every_n_days":
            return f"{dur} every {frequency_value} {day_unit}"
        return f"{dur} / day"

    time_unit = "time" if target_count == 1 else "times"

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


def format_schedule_text(schedule_mode: str, scheduled_days) -> str:
    if schedule_mode != "weekdays":
        return ""
    labels = format_weekday_short_list(scheduled_days)
    return f"Scheduled: {labels}" if labels else ""


def get_schedule_status(frequency_type: str, current_count: int, target_count: int, schedule_mode: str, scheduled_days):
    days = parse_scheduled_days(scheduled_days)
    if schedule_mode != "weekdays" or not days or frequency_type not in {"x_per_week", "weekly"}:
        return None

    today = date.today()
    today_idx = today.weekday()

    if frequency_type == "weekly":
        due_day = days[0]
        due_date = get_week_start(today) + timedelta(days=due_day)
        if current_count >= target_count:
            return "Done"
        if today < due_date:
            return "Upcoming"
        if today == due_date:
            return "Due today"
        return "Missed"

    if current_count >= target_count:
        return "Done"
    if today_idx in days:
        return "Due today"
    if any(day > today_idx for day in days):
        return "Not scheduled today"
    return "Missed"


def get_current_progress(user_id: str = ""):
    """Return current period progress for each active habit."""
    habits = get_active_habits(user_id=user_id)
    conn = get_connection()
    cur = conn.cursor()

    rows = []

    for habit in habits:
        habit_type = habit["habit_type"] or "count"
        frequency_type = habit["frequency_type"] or "daily"
        frequency_value = int(habit["frequency_value"] or 1)
        target_count = int(habit["target_count"] or habit["daily_target"] or 1)
        schedule_mode = (habit["schedule_mode"] if "schedule_mode" in habit.keys() else "none") or "none"
        scheduled_days = (habit["scheduled_days"] if "scheduled_days" in habit.keys() else "") or ""
        reminder_bucket = (habit["reminder_bucket"] if "reminder_bucket" in habit.keys() else "anytime") or "anytime"
        habit_group = normalize_habit_group(habit["habit_group"] if "habit_group" in habit.keys() else DEFAULT_HABIT_GROUP)
        habit_link = (habit["habit_link"] if "habit_link" in habit.keys() else "") or ""
        track_time = int(habit["track_time"] if "track_time" in habit.keys() else 0) == 1
        estimated_minutes = int(habit["estimated_minutes"] if "estimated_minutes" in habit.keys() else 0) or 0
        anchor_date = get_created_date_from_habit(habit)

        period = get_current_period_info(frequency_type, frequency_value, anchor_date)
        query_end = min(period["end_date"], date.today())

        cur.execute("""
            SELECT
                COALESCE(SUM(count), 0) AS total_count
            FROM habit_logs
            WHERE habit_id = %s
              AND log_date BETWEEN %s AND %s
        """, (
            habit["id"],
            period["start_date"].isoformat(),
            query_end.isoformat(),
        ))
        log_row = cur.fetchone()

        cur.execute("""
            SELECT MAX(logged_at) AS last_logged_at
            FROM habit_logs
            WHERE habit_id = %s
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
            "schedule_mode": schedule_mode,
            "scheduled_days": scheduled_days,
            "schedule_text": format_schedule_text(schedule_mode, scheduled_days),
            "schedule_status": get_schedule_status(frequency_type, total_count, target_count, schedule_mode, scheduled_days),
            "reminder_bucket": normalize_reminder_bucket(reminder_bucket),
            "reminder_label": format_reminder_bucket_label(reminder_bucket),
            "habit_group": habit_group,
            "habit_link": habit_link,
            "track_time": track_time,
            "estimated_minutes": estimated_minutes,
        })

    conn.close()
    return rows


def get_recent_logs(user_id: str = "", limit: int = 20):
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
            l.log_date,
            l.count
        FROM habit_logs l
        JOIN habits h ON l.habit_id = h.id
        WHERE h.is_active = 1 AND h.user_id = %s
        ORDER BY l.logged_at DESC, l.id DESC
        LIMIT %s
    """, (user_id, limit))

    rows = cur.fetchall()
    conn.close()
    return rows


def format_recent_log_event_text(log) -> str:
    """Human-friendly text for recent log entries based on habit type."""
    habit_type = log["habit_type"] if "habit_type" in log.keys() else "count"
    target_count = int(log["target_count"] or 1) if "target_count" in log.keys() else 1
    count = int(log["count"] or 0)

    if habit_type == "completion" and target_count == 1:
        base_text = "done"
    elif habit_type == "completion" and target_count > 1:
        base_text = "session logged" if count == 1 else f"{count} sessions logged"
    else:
        base_text = "+1" if count == 1 else f"+{count}"

    log_date_value = log["log_date"] if "log_date" in log.keys() else None
    if not log_date_value:
        return base_text

    event_date = date.fromisoformat(str(log_date_value))
    today = date.today()
    if event_date == today:
        date_text = "today"
    elif event_date == (today - timedelta(days=1)):
        date_text = "yesterday"
    else:
        date_text = event_date.strftime("%b %d")
    return f"{base_text} for {date_text}"


def delete_log(log_id: int):
    """Delete one log row."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM habit_logs WHERE id = %s", (log_id,))
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
        WHERE habit_id = %s
          AND log_date BETWEEN %s AND %s
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


def get_monthly_stats(user_id: str = ""):
    """Return period-aware monthly stats for daily, weekly, and cycle-based habits."""
    today = date.today()
    current_month_start = today.replace(day=1)
    current_month_end = today

    if today.month == 1:
        prev_month_date = date(today.year - 1, 12, 1)
    else:
        prev_month_date = date(today.year, today.month - 1, 1)
    prev_month_start, prev_month_end = get_month_date_range(prev_month_date)

    habits = get_active_habits(user_id=user_id)
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



def get_recent_month_options(num_months: int = 6):
    today = date.today().replace(day=1)
    months = []
    cursor = today
    for _ in range(num_months):
        months.append({
            "key": cursor.isoformat(),
            "label": cursor.strftime("%B %Y"),
            "start": cursor,
            "end": cursor.replace(day=monthrange(cursor.year, cursor.month)[1]),
        })
        if cursor.month == 1:
            cursor = date(cursor.year - 1, 12, 1)
        else:
            cursor = date(cursor.year, cursor.month - 1, 1)
    return months


def get_habit_daily_counts(habit_id: int, start_date: date, end_date: date):
    return get_habit_logs_summary(habit_id, start_date, end_date)


def get_calendar_cell_state(habit, day: date, daily_count: int) -> tuple[str, str]:
    frequency_type, _, target_count, _ = get_period_targets(habit)
    target_count = max(1, int(target_count))

    if frequency_type == "daily":
        if daily_count >= target_count:
            return "complete", f"{daily_count}/{target_count}"
        if daily_count > 0:
            return "partial", f"{daily_count}/{target_count}"
        return "empty", ""

    if daily_count > 0:
        if daily_count >= target_count:
            return "complete", f"{daily_count}"
        return "partial", f"{daily_count}"
    return "empty", ""


def build_calendar_heatmap_html(habit, month_start: date, month_end: date):
    daily_counts = get_habit_daily_counts(habit["id"], month_start, month_end)
    weekday_headers = [label for _, label in WEEKDAY_OPTIONS]
    leading_blanks = month_start.weekday()
    total_days = month_end.day

    cells = []
    for _ in range(leading_blanks):
        cells.append('<div class="calendar-cell other-month"></div>')

    for day_num in range(1, total_days + 1):
        current_day = month_start.replace(day=day_num)
        count = int(daily_counts.get(current_day, 0) or 0)
        state, value_text = get_calendar_cell_state(habit, current_day, count)
        value_html = f'<div class="calendar-day-value">{value_text}</div>' if value_text else '<div class="calendar-day-value">&nbsp;</div>'
        cells.append(
            f'<div class="calendar-cell {state}">'
            f'<div class="calendar-day-number">{day_num}</div>'
            f'{value_html}'
            f'</div>'
        )

    while len(cells) % 7 != 0:
        cells.append('<div class="calendar-cell other-month"></div>')

    header_html = ''.join(f'<div class="calendar-weekday">{label}</div>' for label in weekday_headers)
    cells_html = ''.join(cells)
    return f'<div class="calendar-grid">{header_html}{cells_html}</div>'


def get_review_preview(progress_rows: list) -> tuple[list, list, list]:
    """Return (today_all, today_done, tomorrow_due) for Review & Preview card.

    today_all    — all habits relevant today (done + not done), for display.
    today_done   — habits completed this period, for time calculation only.
    tomorrow_due — habits that will need a check-in tomorrow.
    """
    today = date.today()
    tomorrow = today + timedelta(days=1)
    tomorrow_idx = tomorrow.weekday()

    today_all = []
    today_done = []
    tomorrow_due = []

    for row in progress_rows:
        frequency_type = row["frequency_type"]
        target_count = int(row["target_count"] or 1)
        current_count = int(row["current_count"] or 0)
        schedule_mode = row.get("schedule_mode", "none") or "none"
        scheduled_days = parse_scheduled_days(row.get("scheduled_days", ""))
        is_done = current_count >= target_count

        # ── Today all ──
        # Daily: always relevant today
        # Weekly/x_per_week scheduled: only if today is a scheduled day or due day
        # Every_n_days: always relevant if period is active
        include_today = False
        if frequency_type == "daily":
            include_today = True
        elif frequency_type in {"x_per_week", "weekly"}:
            if schedule_mode == "weekdays" and scheduled_days:
                today_idx = today.weekday()
                if frequency_type == "weekly":
                    due_day = scheduled_days[0]
                    due_date = get_week_start(today) + timedelta(days=due_day)
                    include_today = (today == due_date or is_done)
                else:
                    include_today = (today_idx in scheduled_days or is_done)
            else:
                include_today = True  # unscheduled weekly — always show
        elif frequency_type == "every_n_days":
            include_today = True

        if include_today:
            today_all.append(row)
            if is_done:
                today_done.append(row)

        # ── Tomorrow due ──
        if not is_done:
            if frequency_type == "daily":
                tomorrow_due.append(row)
            elif frequency_type in {"x_per_week", "weekly"}:
                if schedule_mode == "weekdays" and scheduled_days:
                    if frequency_type == "weekly":
                        due_day = scheduled_days[0]
                        due_date = get_week_start(tomorrow) + timedelta(days=due_day)
                        if due_date == tomorrow:
                            tomorrow_due.append(row)
                    else:
                        if tomorrow_idx in scheduled_days:
                            tomorrow_due.append(row)
                else:
                    remaining = max(0, target_count - current_count)
                    days_left_after_tomorrow = 7 - tomorrow_idx
                    if remaining > 0 and remaining >= days_left_after_tomorrow:
                        tomorrow_due.append(row)
            elif frequency_type == "every_n_days":
                tomorrow_due.append(row)

    bucket_order = {"morning": 0, "afternoon": 1, "evening": 2, "anytime": 3}
    today_all.sort(key=lambda x: bucket_order.get(x["reminder_bucket"], 3))
    today_done.sort(key=lambda x: bucket_order.get(x["reminder_bucket"], 3))
    tomorrow_due.sort(key=lambda x: bucket_order.get(x["reminder_bucket"], 3))
    return today_all, today_done, tomorrow_due


def compute_time_summary(rows: list) -> tuple[int, dict]:
    """Return (total_minutes, {group: minutes}) for rows that track time."""
    total = 0
    by_group: dict[str, int] = {}
    for row in rows:
        mins = int(row.get("estimated_minutes", 0) or 0)
        if not row.get("track_time") or mins <= 0:
            continue
        total += mins
        group = row.get("habit_group", DEFAULT_HABIT_GROUP)
        by_group[group] = by_group.get(group, 0) + mins
    return total, by_group


def get_calendar_note_text(habit) -> str:
    frequency_type = (habit["frequency_type"] if "frequency_type" in habit.keys() else "daily") or "daily"
    target_count = int((habit["target_count"] if "target_count" in habit.keys() else None) or 1)
    if frequency_type == "daily":
        return f"Calendar colors for daily habits reflect that day's progress toward the {target_count}-per-day target."
    return "For non-daily habits, the calendar shows activity by day: blue means some activity, green means heavier activity."


# -----------------------------
# Streamlit UI
# -----------------------------

st.set_page_config(page_title="Habit Tracker", page_icon="🔵", layout="centered")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;1,9..40,300&family=DM+Mono:wght@400;500&display=swap');

:root {
    --ht-ink: #1c1612;
    --ht-ink-2: #3d2e24;
    --ht-ink-3: #7a6355;
    --ht-ink-4: #b0988a;
    --ht-bg: #faf8f5;
    --ht-bg-2: #f4f0ea;
    --ht-bg-3: #ebe4da;
    --ht-line: rgba(28,22,18,0.09);
    --ht-line-2: rgba(28,22,18,0.05);
    --ht-accent: #c2622d;
    --ht-accent-2: #fef3eb;
    --ht-accent-text: #9a3e15;
    --ht-green: #2d7d46;
    --ht-green-bg: #dcf0e4;
    --ht-green-text: #1a5c30;
    --ht-amber: #c27c1a;
    --ht-amber-bg: #fef3c7;
    --ht-amber-text: #92580d;
    --ht-red: #c0392b;
    --ht-red-bg: #fde8e6;
    --ht-red-text: #8e1f15;
    --ht-blue: #c2622d;
    --ht-blue-bg: #fef3eb;
    --ht-blue-text: #9a3e15;
    --ht-radius: 12px;
    --ht-radius-sm: 8px;
    --ht-radius-lg: 16px;
    --ht-radius-xl: 20px;
    --ht-shadow: 0 1px 2px rgba(28,22,18,0.06), 0 4px 12px rgba(28,22,18,0.05);
    --ht-shadow-md: 0 2px 4px rgba(28,22,18,0.07), 0 8px 24px rgba(28,22,18,0.07);
    --ht-font: 'DM Sans', -apple-system, BlinkMacSystemFont, sans-serif;
    --ht-mono: 'DM Mono', 'SF Mono', monospace;
}

.block-container {
    max-width: 820px;
    padding-top: 0.25rem !important;
    padding-bottom: 2rem !important;
    padding-left: 1rem !important;
    padding-right: 1rem !important;
    font-family: var(--ht-font) !important;
}

html, body, [class*="css"] {
    font-size: 14px;
    font-family: var(--ht-font) !important;
}

h1 {
    font-size: 1.75rem !important;
    font-weight: 600 !important;
    letter-spacing: -0.03em !important;
    color: var(--ht-ink) !important;
    margin-bottom: 0.1rem !important;
    font-family: var(--ht-font) !important;
}

h2 {
    font-size: 1.1rem !important;
    font-weight: 600 !important;
    letter-spacing: -0.015em !important;
    margin-bottom: 0.15rem !important;
}

h3, h4 {
    font-size: 0.95rem !important;
    font-weight: 600 !important;
    margin-bottom: 0.1rem !important;
}

p {
    margin-bottom: 0.25rem !important;
    color: var(--ht-ink-2) !important;
    line-height: 1.55 !important;
}

[data-testid="stCaptionContainer"] {
    font-size: 11.5px !important;
    color: var(--ht-ink-3) !important;
}

[data-testid="stCaptionContainer"] p {
    color: var(--ht-ink-3) !important;
}

/* ─── Buttons ─── */
.stButton > button,
[data-testid="stFormSubmitButton"] > button {
    height: 2.1rem;
    padding: 0 1rem;
    font-size: 0.875rem;
    font-weight: 500;
    font-family: var(--ht-font) !important;
    border-radius: var(--ht-radius) !important;
    border: 1px solid var(--ht-line) !important;
    box-shadow: var(--ht-shadow) !important;
    transition: all 0.15s ease !important;
    letter-spacing: -0.01em;
}

.stButton > button[kind="primary"],
[data-testid="stFormSubmitButton"] > button[kind="primary"] {
    background: #c2622d !important;
    color: #ffffff !important;
    border-color: #c2622d !important;
    font-weight: 600 !important;
    box-shadow: 0 1px 2px rgba(194,98,45,0.15) !important;
}

.stButton > button[kind="primary"]:hover,
[data-testid="stFormSubmitButton"] > button[kind="primary"]:hover {
    background: #a84e20 !important;
    border-color: #a84e20 !important;
    box-shadow: 0 2px 8px rgba(194,98,45,0.25) !important;
    transform: translateY(-1px);
}

/* Extra specificity to beat Streamlit's injected theme */
div[data-testid="stForm"] [data-testid="stFormSubmitButton"] > button,
div[data-testid="stForm"] [data-testid="stFormSubmitButton"] > button:focus,
div[data-testid="stForm"] [data-testid="stFormSubmitButton"] > button:active {
    background: #c2622d !important;
    background-color: #c2622d !important;
    color: #ffffff !important;
    border-color: #c2622d !important;
}

.stButton > button[kind="secondary"],
[data-testid="stFormSubmitButton"] > button[kind="secondary"] {
    background: var(--ht-bg) !important;
    color: var(--ht-ink-2) !important;
    border: 1px solid var(--ht-line) !important;
}

.stButton > button[kind="secondary"]:hover,
[data-testid="stFormSubmitButton"] > button[kind="secondary"]:hover {
    background: var(--ht-bg-2) !important;
    border-color: rgba(28,22,18,0.14) !important;
}

.stButton > button:disabled,
[data-testid="stFormSubmitButton"] > button:disabled {
    background: var(--ht-bg-2) !important;
    color: var(--ht-ink-4) !important;
    border-color: var(--ht-line-2) !important;
    box-shadow: none !important;
    transform: none !important;
}

/* ─── Form inputs ─── */
.stTextInput input,
.stNumberInput input,
.stSelectbox [data-baseweb="select"] > div,
.stDateInput input {
    font-size: 0.9rem !important;
    font-family: var(--ht-font) !important;
    border-radius: var(--ht-radius-sm) !important;
    border: 1px solid var(--ht-line) !important;
    background: var(--ht-bg) !important;
    color: var(--ht-ink) !important;
}

.stTextInput input:focus,
.stNumberInput input:focus {
    border-color: var(--ht-accent) !important;
    box-shadow: 0 0 0 3px var(--ht-accent-2) !important;
}

/* ─── Divider ─── */
hr {
    border: none !important;
    border-top: 1px solid var(--ht-line) !important;
    margin: 0.5rem 0 1rem 0 !important;
}

/* ─── Summary strip ─── */
.summary-strip {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 0.5rem;
    margin: 0.5rem 0 0.75rem 0;
}

.summary-card {
    padding: 0.75rem 0.9rem 0.7rem;
    border-radius: var(--ht-radius);
    border: 1px solid var(--ht-line);
    background: var(--ht-bg);
    box-shadow: var(--ht-shadow);
    position: relative;
    overflow: hidden;
}

.summary-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: var(--ht-accent);
    opacity: 0.5;
    border-radius: 2px 2px 0 0;
}

.summary-label {
    font-size: 0.7rem;
    font-weight: 500;
    color: var(--ht-ink-3);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 0.3rem;
}

.summary-value {
    font-size: 1.5rem;
    font-weight: 600;
    color: var(--ht-ink);
    line-height: 1;
    letter-spacing: -0.03em;
}

/* ─── Compact card (habit cards) ─── */
.compact-card {
    border: 1px solid var(--ht-line);
    border-radius: var(--ht-radius-lg);
    padding: 1rem 1.1rem 0.85rem;
    margin-bottom: 0.75rem;
    background: var(--ht-bg);
    box-shadow: var(--ht-shadow);
}

.compact-card.card-even {
    background: var(--ht-bg);
}

.compact-card.card-odd {
    background: var(--ht-bg-2);
}

.compact-title-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    margin-bottom: 0.35rem;
}

.compact-title {
    font-weight: 600;
    font-size: 1rem;
    letter-spacing: -0.02em;
    color: var(--ht-ink);
    margin-bottom: 0;
}

/* ─── Status badges ─── */
.status-badge {
    display: inline-flex;
    align-items: center;
    white-space: nowrap;
    padding: 0.2rem 0.6rem;
    border-radius: 999px;
    font-size: 0.69rem;
    font-weight: 600;
    line-height: 1;
    letter-spacing: 0.01em;
    border: 1px solid transparent;
}

.status-badge.done {
    color: var(--ht-green-text);
    background: var(--ht-green-bg);
    border-color: rgba(23,178,106,0.2);
}

.status-badge.pending {
    color: var(--ht-amber-text);
    background: var(--ht-amber-bg);
    border-color: rgba(247,144,9,0.2);
}

.status-badge.complete {
    color: var(--ht-green-text);
    background: var(--ht-green-bg);
    border-color: rgba(23,178,106,0.2);
}

.status-badge.on-track {
    color: var(--ht-blue-text);
    background: var(--ht-blue-bg);
    border-color: rgba(194,98,45,0.2);
}

.status-badge.not-started {
    color: var(--ht-ink-3);
    background: var(--ht-bg-3);
    border-color: var(--ht-line);
}

/* ─── Bucket / group headers ─── */
.bucket-header {
    margin: 0.85rem 0 0.45rem 0;
    padding: 0.1rem 0;
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--ht-ink-3);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    display: flex;
    align-items: center;
    gap: 0.5rem;
}

.bucket-header::after {
    content: '';
    flex: 1;
    height: 1px;
    background: var(--ht-line);
}

.bucket-subtle {
    color: var(--ht-ink-4);
    font-weight: 500;
    font-size: 0.75rem;
    text-transform: none;
    letter-spacing: 0;
}

.compact-subtle {
    color: var(--ht-ink-3);
    font-size: 0.82rem;
    margin-bottom: 0.2rem;
}

.last-line {
    color: var(--ht-ink-3);
    font-size: 0.78rem;
    text-align: right;
    font-variant-numeric: tabular-nums;
    font-family: var(--ht-mono);
}

/* ─── Chips ─── */
.chip-row {
    margin: 0.1rem 0 0.3rem 0;
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem;
}

.chip {
    display: inline-block;
    padding: 0.18rem 0.55rem;
    border-radius: 999px;
    font-size: 0.72rem;
    font-weight: 500;
    border: 1px solid var(--ht-line);
    color: var(--ht-ink-2);
    background: var(--ht-bg-2);
    letter-spacing: -0.005em;
}

/* ─── Metric lines ─── */
.metric-line {
    font-size: 0.875rem;
    margin: 0.04rem 0 0.1rem 0;
    color: var(--ht-ink-2);
}

.metric-line strong {
    color: var(--ht-ink);
    font-weight: 600;
}

/* ─── Notes ─── */
.expander-note, .section-note, .action-note {
    color: var(--ht-ink-3);
    line-height: 1.5;
}

.expander-note {
    font-size: 0.82rem;
    margin-top: -0.06rem;
    margin-bottom: 0.6rem;
}

.section-note {
    font-size: 0.84rem;
    margin-top: -0.1rem;
    margin-bottom: 0.5rem;
}

.action-note {
    font-size: 0.74rem;
    margin-top: 0.3rem;
    margin-bottom: 0.1rem;
}

/* ─── Edit box ─── */
.edit-box {
    margin-top: 0.2rem;
    padding: 0.75rem 0.75rem 0.4rem;
    border-radius: var(--ht-radius);
    background: var(--ht-bg-2);
    border: 1px solid var(--ht-line);
}

.mobile-primary-row {
    margin-top: 0.35rem;
    margin-bottom: 0.05rem;
}

/* ─── Progress bar ─── */
.progress-track {
    width: 100%;
    height: 4px;
    background: var(--ht-bg-3);
    border-radius: 999px;
    overflow: hidden;
    margin: 0.45rem 0 0.5rem 0;
}

.progress-fill {
    height: 100%;
    border-radius: 999px;
    transition: width 0.3s ease;
}

.progress-fill.done,
.progress-fill.complete {
    background: var(--ht-green);
}

.progress-fill.pending,
.progress-fill.not-started {
    background: var(--ht-bg-3);
}

.progress-fill.on-track {
    background: var(--ht-accent);
}

/* ─── Expanders ─── */
[data-testid="stExpander"] {
    border: 1px solid var(--ht-line) !important;
    border-radius: var(--ht-radius-lg) !important;
    background: var(--ht-bg) !important;
    box-shadow: var(--ht-shadow) !important;
    margin-bottom: 0.6rem !important;
    overflow: hidden !important;
}

[data-testid="stExpander"] details {
    border: none !important;
    background: transparent !important;
    box-shadow: none !important;
}

[data-testid="stExpander"] summary {
    padding: 0.75rem 1rem !important;
    font-weight: 600 !important;
    font-size: 0.9rem !important;
    color: var(--ht-ink) !important;
    letter-spacing: -0.01em !important;
    background: var(--ht-bg) !important;
}

[data-testid="stExpander"] summary:hover {
    background: var(--ht-bg-2) !important;
}

[data-testid="stExpander"] > details > div {
    padding: 0.1rem 1rem 0.75rem !important;
}

.manage-expander {
    margin-top: 0.1rem;
}

/* ─── Analytics shell ─── */
.analytics-shell {
    margin: 0.75rem 0 0.5rem 0;
    padding: 1rem 1.1rem 0.65rem;
    border-radius: var(--ht-radius-lg);
    border: 1px solid var(--ht-line);
    background: var(--ht-bg);
    box-shadow: var(--ht-shadow);
}

.analytics-title {
    font-size: 0.875rem;
    font-weight: 600;
    color: var(--ht-ink);
    margin-bottom: 0.05rem;
    letter-spacing: -0.01em;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-size: 0.72rem;
    color: var(--ht-ink-3);
}

.analytics-note {
    font-size: 0.8rem;
    color: var(--ht-ink-3);
    margin-bottom: 0.4rem;
}

/* ─── Calendar ─── */
.calendar-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
    margin: 0.35rem 0 0.65rem 0;
}

.calendar-legend-item {
    display: inline-flex;
    align-items: center;
    gap: 0.32rem;
    font-size: 0.75rem;
    color: var(--ht-ink-3);
    font-weight: 500;
}

.calendar-swatch {
    width: 12px;
    height: 12px;
    border-radius: 4px;
    border: 1px solid var(--ht-line);
}

.calendar-grid {
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    gap: 0.3rem;
    margin-top: 0.3rem;
}

.calendar-weekday {
    text-align: center;
    font-size: 0.68rem;
    color: var(--ht-ink-3);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding-bottom: 0.15rem;
}

.calendar-cell {
    min-height: 3rem;
    border-radius: var(--ht-radius-sm);
    border: 1px solid var(--ht-line);
    padding: 0.28rem 0.32rem;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    transition: all 0.12s ease;
}

.calendar-cell.empty {
    background: var(--ht-bg-2);
}

.calendar-cell.partial {
    background: rgba(194,98,45,0.1);
    border-color: rgba(194,98,45,0.2);
}

.calendar-cell.complete {
    background: var(--ht-green-bg);
    border-color: rgba(23,178,106,0.2);
}

.calendar-cell.other-month {
    visibility: hidden;
}

.calendar-day-number {
    font-size: 0.76rem;
    font-weight: 600;
    color: var(--ht-ink-2);
}

.calendar-day-value {
    font-size: 0.68rem;
    color: var(--ht-ink-3);
    font-family: var(--ht-mono);
    font-variant-numeric: tabular-nums;
}

.calendar-note {
    color: var(--ht-ink-3);
    font-size: 0.74rem;
    margin-top: 0.5rem;
    line-height: 1.5;
}

/* ─── Streamlit container borders ─── */
[data-testid="stVerticalBlockBorderWrapper"] > div > div {
    border-radius: var(--ht-radius-lg) !important;
    border: 1px solid var(--ht-line) !important;
    background: var(--ht-bg) !important;
    box-shadow: var(--ht-shadow) !important;
    padding: 0.85rem 1rem 0.75rem !important;
    margin-bottom: 0.5rem !important;
}

/* ─── Checkbox ─── */
.stCheckbox label span {
    font-size: 0.875rem !important;
    font-family: var(--ht-font) !important;
    color: var(--ht-ink-2) !important;
}

/* ─── Multiselect ─── */
[data-baseweb="tag"] {
    border-radius: 999px !important;
    background: var(--ht-accent-2) !important;
    border: 1px solid rgba(194,98,45,0.2) !important;
}

[data-baseweb="tag"] span {
    color: var(--ht-accent-text) !important;
    font-size: 0.8rem !important;
    font-weight: 500 !important;
}

/* ─── Label overrides ─── */
[data-testid="stWidgetLabel"] {
    font-size: 0.82rem !important;
    font-weight: 600 !important;
    color: var(--ht-ink-2) !important;
    letter-spacing: -0.005em !important;
    margin-bottom: 0.2rem !important;
}

/* ─── Slim habit card ─── */
.habit-group-block {
    border-radius: var(--ht-radius);
    padding: 0.1rem 0 0.4rem 0;
}

.habit-group-block.group-deep {
    background: transparent;
}

.habit-group-block.group-light {
    background: transparent;
}

.slim-card {
    display: flex;
    align-items: center;
    gap: 0;
    border-radius: var(--ht-radius);
    border: 1px solid var(--ht-line);
    background: var(--ht-bg);
    margin-bottom: 0.4rem;
    overflow: hidden;
    min-height: 62px;
    box-shadow: var(--ht-shadow);
}

.habit-group-block.group-deep .slim-card {
    background: var(--ht-bg);
    border-color: rgba(194,98,45,0.14);
}

.habit-group-block.group-light .slim-card {
    background: var(--ht-bg-2);
    border-color: var(--ht-line);
}

.slim-bar {
    width: 4px;
    align-self: stretch;
    flex-shrink: 0;
    border-radius: 0;
}

.slim-body {
    flex: 1;
    min-width: 0;
    padding: 0.55rem 0.75rem 0.5rem;
}

.slim-name {
    font-size: 0.92rem;
    font-weight: 600;
    color: var(--ht-ink);
    letter-spacing: -0.015em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-bottom: 0.12rem;
}

.slim-sub {
    font-size: 0.72rem;
    color: var(--ht-ink-3);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-bottom: 0.28rem;
}

.slim-progress-track {
    height: 3px;
    background: var(--ht-bg-3);
    border-radius: 999px;
    overflow: hidden;
}

.slim-progress-fill {
    height: 100%;
    border-radius: 999px;
    transition: width 0.3s ease;
}

.slim-right {
    flex-shrink: 0;
    padding: 0.5rem 0.85rem 0.5rem 0.5rem;
    text-align: right;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    justify-content: center;
    gap: 0.18rem;
}

.slim-count {
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--ht-ink);
    letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums;
    line-height: 1;
}

.slim-count.slim-count-done {
    color: var(--ht-green-text);
}

.slim-target {
    font-size: 0.72rem;
    font-weight: 400;
    color: var(--ht-ink-4);
}

.slim-last {
    font-size: 0.68rem;
    color: var(--ht-ink-4);
    font-family: var(--ht-mono);
    white-space: nowrap;
}

/* ─── Review & Preview card ─── */
.rp-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.75rem;
    margin-top: 0.1rem;
}

.rp-col {
    border: 1px solid var(--ht-line);
    border-radius: var(--ht-radius);
    padding: 0.7rem 0.8rem 0.6rem;
    background: var(--ht-bg);
}

.rp-col-title {
    font-size: 0.7rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 0.5rem;
    display: flex;
    align-items: center;
    gap: 0.35rem;
}

.rp-col-title.today { color: var(--ht-green-text); }
.rp-col-title.tomorrow { color: var(--ht-accent-text); }

.rp-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    flex-shrink: 0;
}

.rp-dot.today { background: var(--ht-green); }
.rp-dot.tomorrow { background: var(--ht-accent); }

.rp-item {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.32rem 0;
    border-bottom: 1px solid var(--ht-line-2);
}

.rp-item:last-child { border-bottom: none; }

.rp-item-name {
    font-size: 0.84rem;
    font-weight: 500;
    color: var(--ht-ink);
    flex: 1;
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.rp-item-name.done {
    color: var(--ht-ink-3);
    text-decoration: line-through;
}

.rp-item-meta {
    font-size: 0.7rem;
    color: var(--ht-ink-4);
    white-space: nowrap;
    font-family: var(--ht-mono);
}

.rp-empty {
    font-size: 0.8rem;
    color: var(--ht-ink-4);
    padding: 0.5rem 0 0.2rem;
    font-style: italic;
}

.rp-grand-total {
    font-size: 0.78rem;
    margin-bottom: 0.55rem;
    padding: 0.3rem 0.5rem;
    border-radius: var(--ht-radius-sm);
    background: var(--ht-bg-2);
    border: 1px solid var(--ht-line);
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
}

.rp-group-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin: 0.55rem 0 0.15rem 0;
    padding: 0.1rem 0;
    border-bottom: 1px solid var(--ht-line);
}

.rp-group-label {
    font-size: 0.67rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--ht-ink-3);
}

.rp-group-subtotal {
    font-size: 0.72rem;
    font-weight: 600;
    font-family: var(--ht-mono);
}

.rp-item-indented {
    padding-left: 0.4rem;
}

.rp-item-mins {
    font-size: 0.7rem;
    font-weight: 600;
    font-family: var(--ht-mono);
    white-space: nowrap;
    flex-shrink: 0;
}

@media (max-width: 520px) {
    .rp-grid { grid-template-columns: 1fr; }
}

/* ─── Habit link button ─── */
.habit-link-btn {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    padding: 0.18rem 0.6rem;
    border-radius: 999px;
    font-size: 0.7rem;
    font-weight: 600;
    text-decoration: none !important;
    color: var(--ht-accent-text) !important;
    background: var(--ht-accent-2);
    border: 1px solid rgba(194,98,45,0.2);
    transition: all 0.15s ease;
    white-space: nowrap;
    letter-spacing: 0.01em;
}

.habit-link-btn:hover {
    background: #fde5d3;
    border-color: rgba(194,98,45,0.35);
    color: var(--ht-accent-text) !important;
}

/* ─── Alerts ─── */
[data-testid="stAlert"] {
    border-radius: var(--ht-radius) !important;
    border-width: 1px !important;
    font-size: 0.875rem !important;
}

@media (max-width: 640px) {
    .block-container {
        padding-left: 0.6rem !important;
        padding-right: 0.6rem !important;
    }
    h1 {
        font-size: 1.4rem !important;
    }
    .summary-strip {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .compact-card {
        padding: 0.75rem 0.8rem 0.6rem;
        border-radius: var(--ht-radius);
    }
    .stButton > button,
    [data-testid="stFormSubmitButton"] > button {
        font-size: 0.84rem;
        height: 2rem;
        padding: 0 0.75rem;
    }
    .calendar-cell {
        min-height: 2.6rem;
        padding: 0.2rem 0.22rem;
    }
    .calendar-day-number {
        font-size: 0.7rem;
    }
    .calendar-day-value {
        font-size: 0.62rem;
    }
}
</style>
""", unsafe_allow_html=True)

init_db()

# ── Login wall ──
if not st.user.is_logged_in:
    st.markdown("""
    <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:60vh;gap:1.5rem;">
        <svg width="56" height="56" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">
            <circle cx="16" cy="16" r="15" fill="#fef3eb" stroke="#e8a87c" stroke-width="1.5"/>
            <path d="M9 16.5l5 5 9-9" stroke="#c2622d" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
        </svg>
        <div style="text-align:center;">
            <h1 style="font-size:1.8rem;font-weight:600;letter-spacing:-0.03em;color:#1c1612;margin-bottom:0.3rem;">Habit Tracker</h1>
            <p style="color:#7a6355;font-size:0.9rem;margin:0;">Sign in to track your habits</p>
        </div>
    </div>
    """, unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        st.button("Sign in with Google", on_click=st.login, use_container_width=True, type="primary")
    st.stop()

# User is logged in — get their ID
current_user_id = st.user.email or st.user.sub or ""

# Custom SVG favicon — terracotta checkmark circle
st.markdown("""
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='15' fill='%23fef3eb' stroke='%23e8a87c' stroke-width='1.5'/%3E%3Cpath d='M9 16.5l5 5 9-9' stroke='%23c2622d' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round' fill='none'/%3E%3C/svg%3E">
""", unsafe_allow_html=True)

st.markdown("""
<div style="padding: 1.25rem 0 0.5rem 0; border-bottom: 1px solid rgba(28,22,18,0.08); margin-bottom: 0.75rem;">
    <div style="display: flex; align-items: center; gap: 0.65rem; margin-bottom: 0.15rem;">
        <svg width="28" height="28" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg" style="flex-shrink:0;">
            <circle cx="16" cy="16" r="15" fill="#fef3eb" stroke="#e8a87c" stroke-width="1.5"/>
            <path d="M9 16.5l5 5 9-9" stroke="#c2622d" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
        </svg>
        <h1 style="margin: 0 !important; font-size: 1.6rem !important; font-weight: 600 !important; letter-spacing: -0.03em !important; color: #1c1612 !important;">Habit Tracker</h1>
    </div>
    <p style="margin: 0 !important; font-size: 0.82rem !important; color: #b0988a !important; padding-left: 2.6rem;">Track your habits with period-aware progress and smart scheduling.</p>
</div>
""", unsafe_allow_html=True)

# User info + logout in top right
_user_col, _logout_col = st.columns([4, 1])
with _user_col:
    st.markdown(f'<div style="font-size:0.78rem;color:var(--ht-ink-3);padding:0.2rem 0;">👤 {st.user.name or st.user.email}</div>', unsafe_allow_html=True)
with _logout_col:
    st.button("Sign out", on_click=st.logout, type="secondary")


with st.expander("Add a New Habit", expanded=False):
    st.markdown('<div class="expander-note">Start simple. You can always edit the rule later.</div>', unsafe_allow_html=True)
    with st.form("add_habit_form", clear_on_submit=True):
        new_habit = st.text_input(
            "Habit name",
            placeholder="e.g. Drink water, Running, Face mask",
        )

        col1, col2 = st.columns(2)
        with col1:
            habit_type = st.selectbox(
                "Habit type",
                options=["count", "completion", "duration"],
                format_func=lambda x: {"count": "Count", "completion": "Completion", "duration": "Duration (min)"}[x],
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
                }[x],
            )

        freq_col1, freq_col2 = st.columns(2)
        with freq_col1:
            frequency_value = st.number_input(
                "Frequency value (N)",
                min_value=1,
                value=1,
                step=1,
                help="For 'X times/week' or 'Every N days'. Ignored for Daily/Weekly.",
            )
        with freq_col2:
            target_label = "Target (min)" if habit_type == "duration" else "Target count per period"
            target_step = 15 if habit_type == "duration" else 1
            target_default = 60 if habit_type == "duration" else 1
            target_count = st.number_input(
                target_label,
                min_value=1,
                value=target_default,
                step=target_step,
            )

        reminder_bucket = st.selectbox(
            "Time of day",
            options=[key for key, _ in REMINDER_BUCKET_OPTIONS],
            format_func=lambda x: REMINDER_BUCKET_LABEL_MAP[x],
            index=0,
        )

        existing_groups = get_existing_habit_groups(user_id=current_user_id)
        create_new_group_label = "+ Create new group"
        group_choice = st.selectbox(
            "Group",
            options=existing_groups + [create_new_group_label],
            index=0,
            help="Choose an existing group, or select '+ Create new group'.",
        )
        habit_group_custom = st.text_input(
            "New group name",
            placeholder="e.g. Lifestyle, Fitness, Learning",
            help="Only used if '+ Create new group' is selected above.",
        )

        schedule_mode = "none"
        use_weekdays = st.checkbox(
            "Use scheduled weekdays",
            value=False,
            help="For weekly/x-per-week habits — show due/upcoming states on specific days.",
        )
        selected_days = st.multiselect(
            "Scheduled weekdays",
            options=[idx for idx, _ in WEEKDAY_OPTIONS],
            default=[],
            format_func=lambda x: WEEKDAY_LABEL_MAP[x],
        )

        track_time = st.checkbox(
            "Track time for this habit",
            value=False,
            help="Include this habit's estimated duration in Review & Preview time totals.",
        )
        estimated_minutes = 0
        if track_time and habit_type != "duration":
            estimated_minutes = st.number_input(
                "Estimated duration (min)",
                min_value=1, max_value=480, value=30, step=5,
                help="How many minutes does this habit take?",
            )

        submitted = st.form_submit_button("Add habit", use_container_width=True, type="primary")

        if submitted:
            # Resolve group
            if group_choice == create_new_group_label:
                habit_group = habit_group_custom.strip() or DEFAULT_HABIT_GROUP
            else:
                habit_group = group_choice

            # Resolve schedule
            if use_weekdays and frequency_type in {"x_per_week", "weekly"}:
                schedule_mode = "weekdays"
            else:
                schedule_mode = "none"
                selected_days = []

            if new_habit.strip():
                result = add_habit(
                    name=new_habit,
                    user_id=current_user_id,
                    habit_type=habit_type,
                    frequency_type=frequency_type,
                    frequency_value=int(frequency_value),
                    target_count=int(target_count),
                    schedule_mode=schedule_mode,
                    scheduled_days=selected_days,
                    reminder_bucket=reminder_bucket,
                    habit_group=habit_group,
                    track_time=bool(track_time),
                    estimated_minutes=int(estimated_minutes),
                )
                if result == "Habit added.":
                    st.success(f"Added: {new_habit.strip()}")
                    st.rerun()
                elif result == "Habit restored.":
                    st.success(f"Restored: {new_habit.strip()}")
                    st.rerun()
                else:
                    st.warning(result)
            else:
                st.warning("Please enter a habit name.")

# st.divider()

# ── Review & Preview ──
with st.expander("Review & Preview", expanded=False):
    _progress_for_rp = get_current_progress(user_id=current_user_id)
    today_all, today_done, tomorrow_due = get_review_preview(_progress_for_rp)

    today_label = date.today().strftime("%b %d")
    tomorrow_label = (date.today() + timedelta(days=1)).strftime("%b %d")

    # Time summaries — today uses today_all for display, today_done for "done" calc
    today_total_mins, today_by_group = compute_time_summary(today_all)
    tomorrow_total_mins, tomorrow_by_group = compute_time_summary(tomorrow_due)

    today_done_mins = sum(
        int(r.get("estimated_minutes", 0) or 0)
        for r in today_done
        if r.get("track_time") and int(r.get("estimated_minutes", 0) or 0) > 0
    )
    today_remaining_mins = sum(
        int(r.get("estimated_minutes", 0) or 0)
        for r in today_all
        if r.get("track_time")
        and int(r.get("estimated_minutes", 0) or 0) > 0
        and int(r.get("current_count", 0) or 0) < int(r.get("target_count", 1) or 1)
    )

    def rp_col_html(rows, side, total_mins, by_group, done_mins, remaining_mins, color, accent):
        out = []

        # 1. Grand total line
        if side == "today":
            if done_mins > 0 or remaining_mins > 0:
                parts = []
                if done_mins > 0:
                    parts.append(f'<span style="color:var(--ht-green-text);font-weight:700;">✓ {done_mins} min done</span>')
                if remaining_mins > 0:
                    parts.append(f'<span style="color:{accent};font-weight:700;">{remaining_mins} min left</span>')
                out.append(f'<div class="rp-grand-total">{" · ".join(parts)}</div>')
        else:
            if total_mins > 0:
                out.append(f'<div class="rp-grand-total"><span style="color:{accent};font-weight:700;">{total_mins} min planned</span></div>')

        if not rows:
            label = "All clear — nothing yet" if side == "today" else "Nothing scheduled"
            out.append(f'<div class="rp-empty">{label}</div>')
            return "".join(out)

        # 2. Group by habit_group preserving sort order
        from collections import OrderedDict
        groups_ordered: dict = OrderedDict()
        for row in rows:
            g = row.get("habit_group") or DEFAULT_HABIT_GROUP
            groups_ordered.setdefault(g, []).append(row)

        for group_name, group_rows in groups_ordered.items():
            group_mins = by_group.get(group_name, 0)

            # Group header with subtotal
            subtotal_html = f'<span class="rp-group-subtotal" style="color:{accent};">{group_mins} min</span>' if group_mins > 0 else ""
            out.append(
                f'<div class="rp-group-header">'
                f'<span class="rp-group-label">{group_name}</span>'
                f'{subtotal_html}'
                f'</div>'
            )

            # Habits under this group
            for row in group_rows:
                name = row["habit_name"]
                reminder = row["reminder_label"]
                mins = int(row.get("estimated_minutes", 0) or 0)
                is_done = int(row["current_count"] or 0) >= int(row["target_count"] or 1)
                habit_type = row.get("habit_type", "count")
                name_class = "done" if (side == "today" and is_done) else ""
                if row.get("track_time") and mins > 0:
                    # Show estimated time for all track_time habits
                    right_html = f'<span class="rp-item-mins" style="color:{color};">{mins} min</span>'
                elif habit_type == "completion" or (habit_type == "duration"):
                    # Completion: show ✓ or ○
                    symbol = "✓" if is_done else "○"
                    sym_color = "var(--ht-green-text)" if is_done else "var(--ht-ink-4)"
                    right_html = f'<span class="rp-item-meta" style="color:{sym_color};font-weight:700;">{symbol}</span>'
                else:
                    target = int(row.get("target_count") or 1)
                    current = int(row.get("current_count") or 0)
                    right_html = f'<span class="rp-item-meta">{current}/{target}</span>'
                out.append(
                    f'<div class="rp-item rp-item-indented">'
                    f'<div class="rp-item-name {name_class}">{name}</div>'
                    f'{right_html}'
                    f'</div>'
                )

        return "".join(out)

    st.markdown(
        f"""
        <div class="rp-grid">
            <div class="rp-col">
                <div class="rp-col-title today">
                    <span class="rp-dot today"></span>Today · {today_label}
                </div>
                {rp_col_html(today_all, "today", today_total_mins, today_by_group, today_done_mins, today_remaining_mins, "#1a5c30", "#1a5c30")}
            </div>
            <div class="rp-col">
                <div class="rp-col-title tomorrow">
                    <span class="rp-dot tomorrow"></span>Tomorrow · {tomorrow_label}
                </div>
                {rp_col_html(tomorrow_due, "tomorrow", tomorrow_total_mins, tomorrow_by_group, 0, 0, "#9a3e15", "#9a3e15")}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


with st.expander("Current Progress", expanded=False):
    st.markdown('<div class="expander-note">Your active habits for the current period.</div>', unsafe_allow_html=True)

    progress_rows = get_current_progress(user_id=current_user_id)

    if progress_rows:
        completed_count = 0
        on_track_count = 0
        due_today_count = 0
        for item in progress_rows:
            target = int(item["target_count"] or 1)
            current = int(item["current_count"] or 0)
            is_single_completion = item["habit_type"] == "completion" and target == 1
            if is_single_completion:
                if current >= 1:
                    completed_count += 1
            else:
                if current >= target:
                    completed_count += 1
                elif current > 0:
                    on_track_count += 1
            if item.get("schedule_status") == "Due today":
                due_today_count += 1

        st.markdown(
            f"""
            <div class="summary-strip">
                <div class="summary-card"><div class="summary-label">Active habits</div><div class="summary-value">{len(progress_rows)}</div></div>
                <div class="summary-card"><div class="summary-label">Completed</div><div class="summary-value">{completed_count}</div></div>
                <div class="summary-card"><div class="summary-label">On track</div><div class="summary-value">{on_track_count}</div></div>
                <div class="summary-card"><div class="summary-label">Due today</div><div class="summary-value">{due_today_count}</div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    if not progress_rows:
        st.info("No habits yet. Add your first habit above.")
    else:
        grouped_by_habit_group = {}
        for row in progress_rows:
            group_label = normalize_habit_group(row.get("habit_group", DEFAULT_HABIT_GROUP))
            grouped_by_habit_group.setdefault(group_label, []).append(row)

        running_index = 0
        group_index = 0
        for habit_group_label, group_rows in grouped_by_habit_group.items():
            group_accent = "group-deep" if group_index % 2 == 0 else "group-light"
            group_index += 1
            with st.expander(f"{habit_group_label}  ·  {len(group_rows)} habit{('s' if len(group_rows) != 1 else '')}", expanded=False):
                st.markdown(f'<div class="habit-group-block {group_accent}">', unsafe_allow_html=True)

                grouped_rows = {key: [] for key, _ in REMINDER_BUCKET_OPTIONS}
                for row in group_rows:
                    grouped_rows[normalize_reminder_bucket(row.get("reminder_bucket"))].append(row)

                for bucket_key, bucket_label in REMINDER_BUCKET_OPTIONS:
                    bucket_rows = grouped_rows.get(bucket_key, [])
                    if not bucket_rows:
                        continue

                    st.markdown(f'<div class="bucket-header">{bucket_label}<span class="bucket-subtle">{len(bucket_rows)} habit{("s" if len(bucket_rows) != 1 else "")}</span></div>', unsafe_allow_html=True)

                    for row in bucket_rows:
                        idx = running_index
                        running_index += 1
                        target = row["target_count"] if row["target_count"] else 1
                        current_count = row["current_count"]
                        progress = min(current_count / target, 1.0)
                        last_text = format_last_checkin_text(row["last_logged_at"])
                        habit_id = row["habit_id"]

                        is_count_habit = row["habit_type"] == "count"
                        is_single_completion = row["habit_type"] == "completion" and target == 1
                        is_multi_completion = row["habit_type"] == "completion" and target > 1
                        is_duration = row["habit_type"] == "duration"
                        done_this_period = current_count >= target

                        # Display format for count vs duration
                        if is_duration:
                            count_display = f"{current_count}"
                            target_display = f"/{target} min"
                        else:
                            count_display = f"{current_count}"
                            target_display = f"/{target}"

                        # Status colors
                        if done_this_period:
                            bar_color = "var(--ht-green)"
                            badge_class = "complete" if not is_single_completion else "done"
                        elif current_count > 0:
                            bar_color = "var(--ht-accent)"
                            badge_class = "on-track"
                        else:
                            bar_color = "var(--ht-bg-3)"
                            badge_class = "not-started"

                        # Slim card header row (pure HTML, no buttons yet)
                        habit_link = row.get("habit_link", "") or ""
                        subtitle = f"{row['rule_text']} · {row['reminder_label']}"
                        if row.get("schedule_text"):
                            subtitle += f" · {row['schedule_text']}"

                        st.markdown(f"""
                        <div class="slim-card">
                            <div class="slim-bar" style="background:{bar_color};"></div>
                            <div class="slim-body">
                                <div class="slim-name">{row['habit_name']}</div>
                                <div class="slim-sub">{subtitle}</div>
                                <div class="slim-progress-track">
                                    <div class="slim-progress-fill" style="width:{progress*100:.1f}%;background:{bar_color};"></div>
                                </div>
                            </div>
                            <div class="slim-right">
                                <div class="slim-count {'slim-count-done' if done_this_period else ''}">{count_display}<span class="slim-target">{target_display}</span></div>
                                <div class="slim-last">{last_text}</div>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)

                        # Primary action button + optional link button
                        habit_link = row.get("habit_link", "") or ""
                        if habit_link:
                            safe_link = habit_link if habit_link.startswith(("http://", "https://")) else f"https://{habit_link}"
                            btn_col, link_col, _ = st.columns([1.2, 0.8, 1])
                        else:
                            btn_col, _ = st.columns([1, 2])
                            link_col = None

                        with btn_col:
                            if is_duration:
                                if done_this_period:
                                    st.button("✓ Done", key=f"log_{habit_id}", width="stretch", disabled=True)
                                else:
                                    # Quick +15 button as primary
                                    if st.button("+ 15 min", key=f"log_{habit_id}", width="stretch", type="primary"):
                                        log_habit(habit_id, user_id=current_user_id, count=15)
                                        st.rerun()
                            elif is_single_completion:
                                if done_this_period:
                                    st.button("✓ Done", key=f"log_{habit_id}", width="stretch", disabled=True)
                                else:
                                    if st.button("Mark done", key=f"log_{habit_id}", width="stretch", type="primary"):
                                        err = log_completion_once_for_current_period(habit_id, user_id=current_user_id)
                                        if err:
                                            st.info(err)
                                        st.rerun()
                            elif is_count_habit:
                                if st.button("+ Check in", key=f"log_{habit_id}", width="stretch", type="primary"):
                                    log_habit(habit_id, user_id=current_user_id, count=1)
                                    st.rerun()
                            else:
                                if st.button("+ Session", key=f"log_{habit_id}", width="stretch", type="primary"):
                                    log_habit(habit_id, user_id=current_user_id, count=1)
                                    st.rerun()

                        if link_col:
                            with link_col:
                                st.markdown(
                                    f'<a href="{safe_link}" target="_blank" class="habit-link-btn" style="display:flex;align-items:center;justify-content:center;height:2.1rem;border-radius:var(--ht-radius);font-size:0.82rem;">▶ Open</a>',
                                    unsafe_allow_html=True,
                                )

                        # Secondary actions + Manage inside expander
                        with st.expander("···", expanded=False):

                            if is_duration:
                                st.markdown('<div class="action-note">Quick log</div>', unsafe_allow_html=True)
                                d_col1, d_col2, d_col3, d_col4 = st.columns(4)
                                for _col, _mins in zip([d_col1, d_col2, d_col3, d_col4], [15, 30, 45, 60]):
                                    with _col:
                                        if st.button(f"+{_mins}", key=f"dur_{habit_id}_{_mins}", width="stretch", type="primary"):
                                            log_habit(habit_id, user_id=current_user_id, count=_mins)
                                            st.rerun()

                                st.markdown('<div class="action-note" style="margin-top:0.4rem;">Custom duration</div>', unsafe_allow_html=True)
                                cust_col1, cust_col2 = st.columns([2, 1])
                                with cust_col1:
                                    custom_mins = st.number_input(
                                        "Minutes",
                                        min_value=1,
                                        max_value=480,
                                        value=20,
                                        step=5,
                                        key=f"custom_mins_{habit_id}",
                                        label_visibility="collapsed",
                                    )
                                with cust_col2:
                                    if st.button("Log", key=f"log_custom_mins_{habit_id}", width="stretch"):
                                        log_habit(habit_id, user_id=current_user_id, count=int(custom_mins))
                                        st.rerun()

                                # Yesterday custom
                                st.markdown('<div class="action-note" style="margin-top:0.4rem;">Log for yesterday</div>', unsafe_allow_html=True)
                                y_col1, y_col2, y_col3, y_col4 = st.columns(4)
                                for _col, _mins in zip([y_col1, y_col2, y_col3, y_col4], [15, 30, 45, 60]):
                                    with _col:
                                        if st.button(f"+{_mins}", key=f"dur_y_{habit_id}_{_mins}", width="stretch"):
                                            log_habit(habit_id, user_id=current_user_id, count=_mins, log_date=date.today() - timedelta(days=1))
                                            st.rerun()

                            else:
                                sec_col1, sec_col2, sec_col3 = st.columns(3)

                                if is_count_habit or is_multi_completion:
                                    btn_label_y = "Yesterday +1" if is_count_habit else "Yesterday session"
                                    with sec_col1:
                                        if st.button(btn_label_y, key=f"log_yesterday_{habit_id}", width="stretch"):
                                            log_habit(habit_id, user_id=current_user_id, count=1, log_date=date.today() - timedelta(days=1))
                                            st.rerun()
                                    with sec_col2:
                                        pass
                                    with sec_col3:
                                        pass
                                elif is_single_completion:
                                    with sec_col1:
                                        if st.button("Done yesterday", key=f"log_yesterday_{habit_id}", width="stretch"):
                                            err = log_completion_once_for_date(habit_id, date.today() - timedelta(days=1), user_id=current_user_id)
                                            if err:
                                                st.info(err)
                                            st.rerun()
                                    with sec_col2:
                                        if st.button("Undo", key=f"undo_{habit_id}", width="stretch", disabled=not done_this_period, type="secondary"):
                                            err = undo_completion_for_current_period(habit_id, user_id=current_user_id)
                                            if err:
                                                st.info(err)
                                            st.rerun()
                                    with sec_col3:
                                        pass

                            # Pick a date (non-duration only)
                            if not is_duration:
                                with st.expander("Pick a date", expanded=False):
                                    _min_d = get_created_date_from_habit(get_habit_by_id(habit_id))
                                    _def_d = max(_min_d, min(date.today() - timedelta(days=1), date.today()))
                                    _custom_d = st.date_input(
                                        "Log for date",
                                        value=_def_d,
                                        min_value=_min_d,
                                        max_value=date.today(),
                                        key=f"custom_date_{habit_id}",
                                    )
                                    if is_single_completion:
                                        if st.button("Mark done for date", key=f"log_custom_{habit_id}", width="stretch"):
                                            err = log_completion_once_for_date(habit_id, _custom_d, user_id=current_user_id)
                                            if err:
                                                st.info(err)
                                            st.rerun()
                                    else:
                                        if st.button("Log for date", key=f"log_custom_{habit_id}", width="stretch"):
                                            log_habit(habit_id, user_id=current_user_id, count=1, log_date=_custom_d)
                                            st.rerun()

                            # Manage
                            with st.expander("Manage", expanded=False):
                                st.markdown('<div class="edit-box">', unsafe_allow_html=True)

                                name_key = f"manage_name_{habit_id}"
                                type_key = f"manage_type_{habit_id}"
                                freq_key = f"manage_freq_{habit_id}"
                                freq_value_key = f"manage_freq_value_{habit_id}"
                                target_key = f"manage_target_{habit_id}"
                                schedule_enabled_key = f"manage_schedule_enabled_{habit_id}"
                                schedule_days_key = f"manage_schedule_days_{habit_id}"
                                reminder_bucket_key = f"manage_reminder_bucket_{habit_id}"
                                habit_group_key = f"manage_habit_group_{habit_id}"
                                habit_link_key = f"manage_habit_link_{habit_id}"

                                if name_key not in st.session_state:
                                    st.session_state[name_key] = row["habit_name"]
                                if type_key not in st.session_state:
                                    st.session_state[type_key] = row["habit_type"]
                                if freq_key not in st.session_state:
                                    st.session_state[freq_key] = row["frequency_type"]
                                if freq_value_key not in st.session_state:
                                    st.session_state[freq_value_key] = int(row["frequency_value"] or 1)
                                if target_key not in st.session_state:
                                    st.session_state[target_key] = int(row["target_count"] or 1)
                                if schedule_enabled_key not in st.session_state:
                                    st.session_state[schedule_enabled_key] = (row.get("schedule_mode", "none") == "weekdays")
                                if schedule_days_key not in st.session_state:
                                    st.session_state[schedule_days_key] = parse_scheduled_days(row.get("scheduled_days", ""))
                                if reminder_bucket_key not in st.session_state:
                                    st.session_state[reminder_bucket_key] = normalize_reminder_bucket(row.get("reminder_bucket", "anytime"))
                                if habit_group_key not in st.session_state:
                                    st.session_state[habit_group_key] = normalize_habit_group(row.get("habit_group", DEFAULT_HABIT_GROUP))
                                if habit_link_key not in st.session_state:
                                    st.session_state[habit_link_key] = row.get("habit_link", "") or ""

                                track_time_key = f"manage_track_time_{habit_id}"
                                est_mins_key = f"manage_est_mins_{habit_id}"
                                if track_time_key not in st.session_state:
                                    st.session_state[track_time_key] = bool(row.get("track_time", False))
                                if est_mins_key not in st.session_state:
                                    st.session_state[est_mins_key] = int(row.get("estimated_minutes", 0) or 0)

                                edit_name = st.text_input("Habit name", key=name_key)

                                edit_col1, edit_col2 = st.columns(2)
                                with edit_col1:
                                    edit_habit_type = st.selectbox(
                                        "Habit type",
                                        options=["count", "completion", "duration"],
                                        format_func=lambda x: {"count": "Count", "completion": "Completion", "duration": "Duration (min)"}[x],
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

                                edit_meta_col1, edit_meta_col2 = st.columns(2)
                                with edit_meta_col1:
                                    st.selectbox(
                                        "Time of day",
                                        options=[key for key, _ in REMINDER_BUCKET_OPTIONS],
                                        format_func=lambda x: REMINDER_BUCKET_LABEL_MAP[x],
                                        key=reminder_bucket_key,
                                    )
                                with edit_meta_col2:
                                    existing_groups = get_existing_habit_groups(user_id=current_user_id)
                                    current_group = normalize_habit_group(st.session_state.get(habit_group_key, DEFAULT_HABIT_GROUP))
                                    if current_group not in existing_groups:
                                        existing_groups.append(current_group)
                                    create_new_group_label = "+ Create new group"
                                    group_select_key = f"{habit_group_key}_select"
                                    group_custom_key = f"{habit_group_key}_custom"
                                    if group_select_key not in st.session_state:
                                        st.session_state[group_select_key] = current_group
                                    if group_custom_key not in st.session_state:
                                        st.session_state[group_custom_key] = ""
                                    st.selectbox(
                                        "Group",
                                        options=existing_groups + [create_new_group_label],
                                        key=group_select_key,
                                    )
                                    if st.session_state[group_select_key] == create_new_group_label:
                                        st.text_input("New group name", key=group_custom_key, placeholder="e.g. Fitness")
                                        st.session_state[habit_group_key] = normalize_habit_group(st.session_state.get(group_custom_key, ""))
                                    else:
                                        st.session_state[habit_group_key] = normalize_habit_group(st.session_state[group_select_key])

                                if edit_frequency_type in {"x_per_week", "weekly"}:
                                    st.checkbox("Use scheduled weekdays", key=schedule_enabled_key)
                                    if st.session_state[schedule_enabled_key]:
                                        st.multiselect(
                                            "Scheduled weekdays",
                                            options=[idx for idx, _ in WEEKDAY_OPTIONS],
                                            format_func=lambda x: WEEKDAY_LABEL_MAP[x],
                                            key=schedule_days_key,
                                        )
                                else:
                                    st.session_state[schedule_enabled_key] = False
                                    st.session_state[schedule_days_key] = []

                                st.text_input(
                                    "Link (optional)",
                                    key=habit_link_key,
                                    placeholder="https://youtube.com/watch?v=...",
                                )

                                edit_habit_type_val = st.session_state.get(type_key, row["habit_type"])
                                st.checkbox(
                                    "Track time for this habit",
                                    key=track_time_key,
                                    help="Include in Review & Preview time totals.",
                                )
                                if st.session_state.get(track_time_key) and edit_habit_type_val != "duration":
                                    st.number_input(
                                        "Estimated duration (min)",
                                        min_value=1, max_value=480, step=5,
                                        key=est_mins_key,
                                    )

                                save_col1, save_col2 = st.columns(2)
                                with save_col1:
                                    save_clicked = st.button("Save", key=f"save_manage_{habit_id}", width="stretch", type="primary")
                                with save_col2:
                                    hide_clicked = st.button("Hide habit", key=f"hide_manage_{habit_id}", width="stretch", type="secondary")

                                if save_clicked:
                                    error = update_habit(
                                        habit_id=habit_id,
                                        new_name=st.session_state[name_key],
                                        habit_type=st.session_state[type_key],
                                        frequency_type=st.session_state[freq_key],
                                        frequency_value=int(st.session_state.get(freq_value_key, 1) or 1),
                                        target_count=int(st.session_state.get(target_key, 1) or 1),
                                        schedule_mode=("weekdays" if st.session_state.get(schedule_enabled_key) else "none"),
                                        scheduled_days=st.session_state.get(schedule_days_key, []),
                                        reminder_bucket=st.session_state.get(reminder_bucket_key, "anytime"),
                                        habit_group=st.session_state.get(habit_group_key, DEFAULT_HABIT_GROUP),
                                        habit_link=st.session_state.get(habit_link_key, ""),
                                        track_time=bool(st.session_state.get(track_time_key, False)),
                                        estimated_minutes=int(st.session_state.get(est_mins_key, 0) or 0),
                                    )
                                    if error:
                                        st.error(error)
                                    else:
                                        st.success("Saved.")
                                        st.rerun()
                                if hide_clicked:
                                    deactivate_habit(habit_id)
                                    st.rerun()

                                st.markdown('</div>', unsafe_allow_html=True)

                st.markdown('</div>', unsafe_allow_html=True)
with st.expander("Recent log details", expanded=False):
    today_logs = get_recent_logs(user_id=current_user_id)
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
                if st.button("✕", key=f"delete_{log['id']}", width="stretch", type="secondary"):
                    delete_log(log["id"])
                    st.rerun()


analytics_habits = get_active_habits(user_id=user_id)
analytics_habit_map = {habit["id"]: habit for habit in analytics_habits}
analytics_options = [habit["id"] for habit in analytics_habits]
selected_analytics_habit_id = None
selected_analytics_habit = None

with st.expander("Analytics", expanded=False):
    if not analytics_habits:
        st.markdown('<div class="expander-note">No habits available for analytics yet.</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="expander-note">Pick a habit — Calendar Heatmap and Monthly Statistics below will update together.</div>', unsafe_allow_html=True)
        default_analytics_habit_id = st.session_state.get("analytics_selected_habit_id")
        if default_analytics_habit_id not in analytics_habit_map:
            default_analytics_habit_id = analytics_options[0]
        selected_analytics_habit_id = st.selectbox(
            "View analytics for",
            options=analytics_options,
            index=analytics_options.index(default_analytics_habit_id),
            format_func=lambda habit_id: (
                f"{analytics_habit_map[habit_id]['name']} · "
                f"{format_reminder_bucket_label(analytics_habit_map[habit_id]['reminder_bucket'] if 'reminder_bucket' in analytics_habit_map[habit_id].keys() else 'anytime')}"
            ),
            key="analytics_selected_habit_id",
        )
        selected_analytics_habit = analytics_habit_map[selected_analytics_habit_id]

        selected_rule_text = format_rule_text(
            selected_analytics_habit["habit_type"] or "count",
            selected_analytics_habit["frequency_type"] or "daily",
            int(selected_analytics_habit["frequency_value"] or 1),
            int(selected_analytics_habit["target_count"] or selected_analytics_habit["daily_target"] or 1),
        )
        selected_schedule_text = format_schedule_text(
            (selected_analytics_habit["schedule_mode"] if "schedule_mode" in selected_analytics_habit.keys() else "none") or "none",
            (selected_analytics_habit["scheduled_days"] if "scheduled_days" in selected_analytics_habit.keys() else "") or "",
        )
        selected_reminder_label = format_reminder_bucket_label(
            selected_analytics_habit["reminder_bucket"] if "reminder_bucket" in selected_analytics_habit.keys() else "anytime"
        )
        st.markdown(
            f"""
            <div class="chip-row" style="margin-bottom: 0.5rem;">
                <span class="chip">{selected_rule_text}</span>
                <span class="chip">{selected_reminder_label}</span>
                {f'<span class="chip">{selected_schedule_text}</span>' if selected_schedule_text else ''}
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown('<div style="height:0.1rem;border-top:1px solid rgba(28,22,18,0.07);margin:0.2rem 0 0.6rem 0;"></div>', unsafe_allow_html=True)

        # ── Calendar Heatmap ──
        with st.expander("Calendar Heatmap", expanded=False):
            month_options = get_recent_month_options(6)
            month_option_map = {item["key"]: item for item in month_options}
            selected_month_key = st.selectbox(
                "Month",
                options=[item["key"] for item in month_options],
                format_func=lambda key: month_option_map[key]["label"],
                key="calendar_heatmap_month",
            )
            selected_month = month_option_map[selected_month_key]

            st.markdown(
                "<div class='calendar-legend'>"
                "<span class='calendar-legend-item'><span class='calendar-swatch' style='background:var(--ht-bg-2);'></span>No activity</span>"
                "<span class='calendar-legend-item'><span class='calendar-swatch' style='background:rgba(194,98,45,0.12);border-color:rgba(194,98,45,0.2);'></span>Some progress</span>"
                "<span class='calendar-legend-item'><span class='calendar-swatch' style='background:var(--ht-green-bg);border-color:rgba(45,125,70,0.2);'></span>Target met</span>"
                "</div>",
                unsafe_allow_html=True,
            )

            rule_text = format_rule_text(
                selected_analytics_habit["habit_type"] or "count",
                selected_analytics_habit["frequency_type"] or "daily",
                int(selected_analytics_habit["frequency_value"] or 1),
                int(selected_analytics_habit["target_count"] or selected_analytics_habit["daily_target"] or 1),
            )
            schedule_text = format_schedule_text(
                (selected_analytics_habit["schedule_mode"] if "schedule_mode" in selected_analytics_habit.keys() else "none") or "none",
                (selected_analytics_habit["scheduled_days"] if "scheduled_days" in selected_analytics_habit.keys() else "") or "",
            )
            reminder_label = format_reminder_bucket_label(selected_analytics_habit["reminder_bucket"] if "reminder_bucket" in selected_analytics_habit.keys() else "anytime")
            st.markdown('<div class="compact-card">', unsafe_allow_html=True)
            st.markdown(f'<div class="compact-title">{selected_analytics_habit["name"]}</div>', unsafe_allow_html=True)
            st.markdown(
                f"""
                <div class="chip-row">
                    <span class="chip">{rule_text}</span>
                    <span class="chip">{reminder_label}</span>
                    <span class="chip">{selected_month['label']}</span>
                    {f'<span class="chip">{schedule_text}</span>' if schedule_text else ''}
                </div>
                """,
                unsafe_allow_html=True
            )
            st.markdown(
                build_calendar_heatmap_html(selected_analytics_habit, selected_month["start"], selected_month["end"]),
                unsafe_allow_html=True,
            )
            st.markdown(f'<div class="calendar-note">{get_calendar_note_text(selected_analytics_habit)}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

        # ── Monthly Statistics ──
        with st.expander("Monthly Statistics", expanded=False):
            monthly_stats = get_monthly_stats(user_id=current_user_id)
            monthly_stat_map = {stat["habit_id"]: stat for stat in monthly_stats}
            if not monthly_stats:
                st.write("No habits available for statistics yet.")
            else:
                selected_stat = monthly_stat_map.get(selected_analytics_habit["id"])
                if selected_stat is None:
                    st.write("No statistics available for this habit yet.")
                else:
                    rule_text = format_rule_text(
                        selected_stat["habit_type"], selected_stat["frequency_type"], selected_stat["frequency_value"], selected_stat["target_count"]
                    )

                    st.markdown('<div class="compact-card">', unsafe_allow_html=True)
                    st.markdown(f'<div class="compact-title">{selected_stat["habit_name"]}</div>', unsafe_allow_html=True)
                    st.markdown(
                        f"""
                        <div class="chip-row">
                            <span class="chip">{rule_text}</span>
                            <span class="chip">Completion {selected_stat["current_completion_rate"]}%</span>
                            <span class="chip">{selected_stat["streak_value"]} streak</span>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

                    left_col, right_col = st.columns(2)
                    with left_col:
                        st.markdown(f'<div class="metric-line"><strong>This month total:</strong> {selected_stat["current_total"]}</div>', unsafe_allow_html=True)
                        st.markdown(f'<div class="metric-line"><strong>{selected_stat["avg_label"]}:</strong> {selected_stat["current_avg"]}</div>', unsafe_allow_html=True)
                        st.markdown(f'<div class="metric-line"><strong>Active days:</strong> {selected_stat["current_active_days"]}</div>', unsafe_allow_html=True)
                    with right_col:
                        change_text = "New this month" if selected_stat["change_pct"] is None else f'{selected_stat["change_pct"]}%'
                        st.markdown(f'<div class="metric-line"><strong>{selected_stat["current_period_label"]}:</strong> {selected_stat["current_successful_periods"]} / {selected_stat["current_period_count"]}</div>', unsafe_allow_html=True)
                        st.markdown(f'<div class="metric-line"><strong>Vs last month:</strong> {change_text}</div>', unsafe_allow_html=True)
                        st.markdown(f'<div class="metric-line"><strong>Last month total:</strong> {selected_stat["prev_total"]}</div>', unsafe_allow_html=True)

                    st.caption(
                        f"Last month {selected_stat['avg_label'].lower()}: {selected_stat['prev_avg']} · "
                        f"Last month success: {selected_stat['prev_successful_periods']} / {selected_stat['prev_period_count']} · "
                        f"Last month completion rate: {selected_stat['prev_completion_rate']}%"
                    )

                    recent_df, label_col, chart_title, target_label = get_recent_period_data(selected_analytics_habit)

                    with st.expander(chart_title, expanded=False):
                        if recent_df.empty:
                            st.write("No recent data yet.")
                        else:
                            show_df = recent_df.copy()
                            st.dataframe(show_df, width="stretch", hide_index=True)
                            st.caption(f"{target_label}: {selected_stat['target_count']}")
                            st.bar_chart(show_df.set_index(label_col)["count"])

                    st.markdown('</div>', unsafe_allow_html=True)