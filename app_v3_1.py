
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
    unit = "time" if target_count == 1 else "times"

    if frequency_type == "x_per_week":
        return f"{target_count} {unit}/week"

    if frequency_type == "weekly":
        return f"Weekly · target {target_count}"

    if frequency_type == "every_n_days":
        day_word = "day" if frequency_value == 1 else "days"
        return f"Every {frequency_value} {day_word} · target {target_count}"

    if habit_type == "completion":
        return f"{target_count} {unit}/day"

    return f"{target_count}/day"


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
                COALESCE(SUM(count), 0) AS total_count,
                MAX(logged_at) AS last_logged_at
            FROM habit_logs
            WHERE habit_id = ?
              AND log_date BETWEEN ? AND ?
        """, (
            habit["id"],
            period["start_date"].isoformat(),
            query_end.isoformat(),
        ))
        log_row = cur.fetchone()

        total_count = int(log_row["total_count"] or 0)

        rows.append({
            "habit_id": habit["id"],
            "habit_name": habit["name"],
            "habit_type": habit_type,
            "frequency_type": frequency_type,
            "frequency_value": frequency_value,
            "target_count": target_count,
            "current_count": total_count,
            "last_logged_at": log_row["last_logged_at"],
            "period_label": period["label"],
            "rule_text": format_rule_text(habit_type, frequency_type, frequency_value, target_count),
        })

    conn.close()
    return rows


def get_today_logs():
    """Return today's logs in reverse chronological order for active habits only."""
    today_str = date.today().isoformat()
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            l.id,
            h.name AS habit_name,
            l.logged_at,
            l.count
        FROM habit_logs l
        JOIN habits h ON l.habit_id = h.id
        WHERE l.log_date = ?
          AND h.is_active = 1
        ORDER BY l.logged_at DESC, l.id DESC
    """, (today_str,))

    rows = cur.fetchall()
    conn.close()
    return rows


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


def get_last_30_days_data(habit_id: int):
    """Return a DataFrame with last 30 days counts for a habit."""
    today = date.today()
    start_date = today - timedelta(days=29)

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            log_date,
            SUM(count) AS total
        FROM habit_logs
        WHERE habit_id = ?
          AND log_date BETWEEN ? AND ?
        GROUP BY log_date
    """, (
        habit_id,
        start_date.isoformat(),
        today.isoformat()
    ))

    rows = cur.fetchall()
    conn.close()

    data_dict = {row["log_date"]: row["total"] for row in rows}

    all_dates = []
    all_counts = []

    for i in range(30):
        d = start_date + timedelta(days=i)
        d_str = d.isoformat()

        all_dates.append(d_str)
        all_counts.append(data_dict.get(d_str, 0))

    df = pd.DataFrame({
        "date": all_dates,
        "count": all_counts
    })

    return df


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
    height: 2.15rem;
    padding: 0.2rem 0.55rem;
    font-size: 0.92rem;
    border-radius: 0.65rem;
}

.stTextInput input, .stNumberInput input {
    font-size: 0.95rem !important;
    padding: 0.42rem 0.58rem !important;
}

[data-testid="stCaptionContainer"] {
    font-size: 12px;
}

.compact-card {
    border: 1px solid rgba(128,128,128,0.22);
    border-radius: 16px;
    padding: 0.75rem 0.8rem 0.65rem 0.8rem;
    margin-bottom: 0.65rem;
    background: rgba(255,255,255,0.01);
}

.compact-title {
    font-weight: 650;
    font-size: 1rem;
    margin-bottom: 0.1rem;
}

.compact-subtle {
    color: rgba(120,120,120,1);
    font-size: 0.84rem;
    margin-bottom: 0.2rem;
}

.chip-row {
    margin: 0.18rem 0 0.38rem 0;
}

.chip {
    display: inline-block;
    padding: 0.16rem 0.48rem;
    border-radius: 999px;
    font-size: 0.76rem;
    margin-right: 0.35rem;
    margin-bottom: 0.28rem;
    border: 1px solid rgba(128,128,128,0.2);
    background: rgba(240,240,240,0.04);
}

.metric-line {
    font-size: 0.92rem;
    margin: 0.12rem 0;
}

.section-note {
    color: rgba(120,120,120,1);
    font-size: 0.85rem;
    margin-top: -0.2rem;
    margin-bottom: 0.45rem;
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
        padding: 0.68rem 0.68rem 0.58rem 0.68rem;
        border-radius: 14px;
    }
    .stButton > button {
        font-size: 0.88rem;
        height: 2.05rem;
    }
}
</style>
""", unsafe_allow_html=True)

init_db()

st.title("✅ Habit Tracker")
st.caption("Flexible habit tracking for daily, weekly, and cycle-based routines.")

st.subheader("Add a new habit")
st.markdown('<div class="section-note">Start simple. You can always edit the rule later.</div>', unsafe_allow_html=True)
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

    if frequency_type == "x_per_week":
        frequency_value = st.number_input("Times per week", min_value=1, value=3, step=1)
        target_label = "Target count for this week"
        default_target = int(frequency_value)
    elif frequency_type == "every_n_days":
        frequency_value = st.number_input("Every how many days", min_value=1, value=2, step=1)
        target_label = "Target count for this cycle"
        default_target = 1
    elif frequency_type == "weekly":
        frequency_value = 1
        target_label = "Weekly target"
        default_target = 1
    else:
        frequency_value = 1
        target_label = "Daily target"
        default_target = 1

    target_count = st.number_input(target_label, min_value=1, value=default_target, step=1)
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

st.subheader("Current Progress")
st.markdown('<div class="section-note">Your active habits for the current period.</div>', unsafe_allow_html=True)

progress_rows = get_current_progress()

if not progress_rows:
    st.info("No habits yet. Add your first habit above.")
else:
    for row in progress_rows:
        target = row["target_count"] if row["target_count"] else 1
        current_count = row["current_count"]
        progress = min(current_count / target, 1.0)
        period_short = row["period_label"].split(" (")[0]

        with st.container():
            st.markdown('<div class="compact-card">', unsafe_allow_html=True)
            st.markdown(f'<div class="compact-title">{row["habit_name"]}</div>', unsafe_allow_html=True)
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
                if row["last_logged_at"]:
                    last_time = datetime.fromisoformat(row["last_logged_at"]).strftime("%m-%d %H:%M")
                    st.markdown(f'<div class="metric-line"><strong>Last:</strong> {last_time}</div>', unsafe_allow_html=True)
                else:
                    st.markdown('<div class="metric-line"><strong>Last:</strong> —</div>', unsafe_allow_html=True)

            st.progress(progress)

            btn_col1, btn_col2, btn_col3 = st.columns([1, 1, 1])
            with btn_col1:
                action_label = "+1" if row["habit_type"] == "count" else "Done"
                if st.button(action_label, key=f"log_{row['habit_id']}", use_container_width=True):
                    log_habit(row["habit_id"], count=1)
                    st.rerun()
            with btn_col2:
                if st.button("Edit", key=f"edit_toggle_{row['habit_id']}", use_container_width=True):
                    st.session_state[f"editing_{row['habit_id']}"] = not st.session_state.get(f"editing_{row['habit_id']}", False)
            with btn_col3:
                if st.button("Hide", key=f"delete_habit_{row['habit_id']}", use_container_width=True):
                    deactivate_habit(row["habit_id"])
                    st.rerun()

            if st.session_state.get(f"editing_{row['habit_id']}", False):
                st.markdown('<div class="compact-subtle">Edit habit</div>', unsafe_allow_html=True)

                edit_name = st.text_input("Habit name", value=row["habit_name"], key=f"new_name_{row['habit_id']}")

                edit_col1, edit_col2 = st.columns(2)
                with edit_col1:
                    edit_habit_type = st.selectbox(
                        "Habit type",
                        options=["count", "completion"],
                        index=0 if row["habit_type"] == "count" else 1,
                        format_func=lambda x: "Count" if x == "count" else "Completion",
                        key=f"edit_type_{row['habit_id']}"
                    )
                with edit_col2:
                    edit_frequency_type = st.selectbox(
                        "Frequency",
                        options=["daily", "x_per_week", "every_n_days", "weekly"],
                        index=["daily", "x_per_week", "every_n_days", "weekly"].index(row["frequency_type"]),
                        format_func=lambda x: {
                            "daily": "Daily",
                            "x_per_week": "X times / week",
                            "every_n_days": "Every N days",
                            "weekly": "Weekly"
                        }[x],
                        key=f"edit_freq_{row['habit_id']}"
                    )

                if edit_frequency_type == "x_per_week":
                    edit_frequency_value = st.number_input(
                        "Times per week",
                        min_value=1,
                        value=int(row["frequency_value"]) if row["frequency_value"] else 1,
                        step=1,
                        key=f"edit_freq_value_{row['habit_id']}"
                    )
                    edit_target_label = "Target count for this week"
                elif edit_frequency_type == "every_n_days":
                    edit_frequency_value = st.number_input(
                        "Every how many days",
                        min_value=1,
                        value=int(row["frequency_value"]) if row["frequency_value"] else 2,
                        step=1,
                        key=f"edit_freq_value_{row['habit_id']}"
                    )
                    edit_target_label = "Target count for this cycle"
                elif edit_frequency_type == "weekly":
                    edit_frequency_value = 1
                    edit_target_label = "Weekly target"
                else:
                    edit_frequency_value = 1
                    edit_target_label = "Daily target"

                edit_target = st.number_input(
                    edit_target_label,
                    min_value=1,
                    value=int(row["target_count"]) if row["target_count"] else 1,
                    step=1,
                    key=f"new_target_{row['habit_id']}"
                )

                save_col1, save_col2 = st.columns(2)
                with save_col1:
                    if st.button("Save", key=f"save_habit_{row['habit_id']}", use_container_width=True):
                        error = update_habit(
                            habit_id=row["habit_id"],
                            new_name=edit_name,
                            habit_type=edit_habit_type,
                            frequency_type=edit_frequency_type,
                            frequency_value=int(edit_frequency_value),
                            target_count=int(edit_target),
                        )
                        if error:
                            st.error(error)
                        else:
                            st.success("Habit updated.")
                            st.session_state[f"editing_{row['habit_id']}"] = False
                            st.rerun()
                with save_col2:
                    if st.button("Cancel", key=f"cancel_edit_{row['habit_id']}", use_container_width=True):
                        st.session_state[f"editing_{row['habit_id']}"] = False
                        st.rerun()

            st.markdown('</div>', unsafe_allow_html=True)

with st.expander("Recent log details", expanded=False):
    today_logs = get_today_logs()
    if not today_logs:
        st.write("No logs yet today.")
    else:
        for log in today_logs:
            log_time = datetime.fromisoformat(log["logged_at"]).strftime("%H:%M:%S")
            left, right = st.columns([5, 1])
            with left:
                st.write(f"**{log['habit_name']}** — {log_time} (+{log['count']})")
            with right:
                if st.button("✕", key=f"delete_{log['id']}", use_container_width=True):
                    delete_log(log["id"])
                    st.rerun()

with st.expander("Monthly Statistics", expanded=False):
    monthly_stats = get_monthly_stats()
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

            df_30 = get_last_30_days_data(stat["habit_id"])
            df_30["date"] = pd.to_datetime(df_30["date"]).dt.strftime("%m-%d")

            with st.expander("Last 30 days", expanded=False):
                st.dataframe(df_30, use_container_width=True, hide_index=True)
                st.bar_chart(df_30.set_index("date"))

            st.markdown('</div>', unsafe_allow_html=True)
