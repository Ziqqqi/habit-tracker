import sqlite3
from datetime import datetime, date
from calendar import monthrange
import streamlit as st
from datetime import timedelta
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


def init_db():
    """Create tables if they do not exist."""
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

    # Add daily_target column if it doesn't exist yet
    cur.execute("PRAGMA table_info(habits)")
    columns = [row[1] for row in cur.fetchall()]
    if "daily_target" not in columns:
        cur.execute("""
            ALTER TABLE habits
            ADD COLUMN daily_target INTEGER DEFAULT 1
        """)

    conn.commit()
    conn.close()


def add_habit(name: str, daily_target: int = 1):
    """Add a new habit, or reactivate it if it already exists but is inactive."""
    clean_name = name.strip()
    if not clean_name:
        return "Habit name cannot be empty."

    if daily_target < 1:
        return "Daily target must be at least 1."

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
                    daily_target = ?
                WHERE id = ?
            """, (daily_target, existing["id"]))
            conn.commit()
            conn.close()
            return "Habit restored."

    cur.execute("""
        INSERT INTO habits (name, created_at, is_active, daily_target)
        VALUES (?, ?, 1, ?)
    """, (clean_name, datetime.now().isoformat(), daily_target))

    conn.commit()
    conn.close()
    return "Habit added."

def get_active_habits():
    """Return all active habits."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, name, created_at
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

# def update_habit_name(habit_id: int, new_name: str):
#     """Update a habit name."""
#     if not new_name.strip():
#         return "Habit name cannot be empty."

#     conn = get_connection()
#     cur = conn.cursor()

#     try:
#         cur.execute("""
#             UPDATE habits
#             SET name = ?
#             WHERE id = ?
#         """, (new_name.strip(), habit_id))
#         conn.commit()
#         return None
#     except sqlite3.IntegrityError:
#         return "Habit name already exists."
#     finally:
#         conn.close()

def update_habit(habit_id: int, new_name: str, daily_target: int):
    """Update habit name and daily target."""
    clean_name = new_name.strip()

    if not clean_name:
        return "Habit name cannot be empty."

    if daily_target < 1:
        return "Daily target must be at least 1."

    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            UPDATE habits
            SET name = ?, daily_target = ?
            WHERE id = ?
        """, (clean_name, daily_target, habit_id))
        conn.commit()
        return None
    except sqlite3.IntegrityError:
        return "Habit name already exists."
    finally:
        conn.close()



def get_today_counts():
    """Return today's total counts grouped by habit."""
    today_str = date.today().isoformat()
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            h.id AS habit_id,
            h.name AS habit_name,
            h.daily_target AS daily_target,
            COALESCE(SUM(l.count), 0) AS total_count,
            MAX(l.logged_at) AS last_logged_at
        FROM habits h
        LEFT JOIN habit_logs l
            ON h.id = l.habit_id
            AND l.log_date = ?
        WHERE h.is_active = 1
        GROUP BY h.id, h.name, h.daily_target
        ORDER BY h.created_at ASC
    """, (today_str,))

    rows = cur.fetchall()
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


def get_monthly_stats():
    """Return monthly stats for each habit, including comparison with last month."""
    today = date.today()

    # Current month
    current_month_start = today.replace(day=1)
    current_month_end = today

    # Previous month
    if today.month == 1:
        prev_month_date = date(today.year - 1, 12, 1)
    else:
        prev_month_date = date(today.year, today.month - 1, 1)

    prev_month_start, prev_month_end = get_month_date_range(prev_month_date)

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            h.id AS habit_id,
            h.name AS habit_name,

            -- current month
            COALESCE(SUM(CASE
                WHEN l.log_date BETWEEN ? AND ? THEN l.count
                ELSE 0
            END), 0) AS current_total,

            COUNT(DISTINCT CASE
                WHEN l.log_date BETWEEN ? AND ? THEN l.log_date
                ELSE NULL
            END) AS current_active_days,

            -- previous month
            COALESCE(SUM(CASE
                WHEN l.log_date BETWEEN ? AND ? THEN l.count
                ELSE 0
            END), 0) AS prev_total,

            COUNT(DISTINCT CASE
                WHEN l.log_date BETWEEN ? AND ? THEN l.log_date
                ELSE NULL
            END) AS prev_active_days

        FROM habits h
        LEFT JOIN habit_logs l ON h.id = l.habit_id
        WHERE h.is_active = 1
        GROUP BY h.id, h.name
        ORDER BY h.created_at ASC
    """, (
        current_month_start.isoformat(), current_month_end.isoformat(),
        current_month_start.isoformat(), current_month_end.isoformat(),
        prev_month_start.isoformat(), prev_month_end.isoformat(),
        prev_month_start.isoformat(), prev_month_end.isoformat(),
    ))

    rows = cur.fetchall()
    conn.close()

    # number of elapsed days in current month
    current_days_elapsed = today.day

    # number of days in previous month
    prev_days_in_month = prev_month_end.day

    stats = []
    for row in rows:
        current_total = row["current_total"]
        prev_total = row["prev_total"]

        current_avg = round(current_total / current_days_elapsed, 2) if current_days_elapsed else 0
        prev_avg = round(prev_total / prev_days_in_month, 2) if prev_days_in_month else 0

        if prev_avg == 0:
            if current_avg > 0:
                change_pct = None  # means new progress from zero
            else:
                change_pct = 0
        else:
            change_pct = round((current_avg - prev_avg) / prev_avg * 100, 1)

        stats.append({
            "habit_id": row["habit_id"],
            "habit_name": row["habit_name"],
            "current_total": current_total,
            "current_active_days": row["current_active_days"],
            "current_avg": current_avg,
            "prev_total": prev_total,
            "prev_active_days": row["prev_active_days"],
            "prev_avg": prev_avg,
            "change_pct": change_pct,
            "streak": get_habit_streak(row["habit_id"]),
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
            SUM(count) as total
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

    # 转成 dict：{date: count}
    data_dict = {row["log_date"]: row["total"] for row in rows}

    # 构造完整 30 天（补 0）
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

def get_habit_streak(habit_id: int):
    """Return current streak (consecutive active days) for a habit."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT log_date
        FROM habit_logs
        WHERE habit_id = ?
        ORDER BY log_date DESC
    """, (habit_id,))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        return 0

    logged_dates = {date.fromisoformat(row["log_date"]) for row in rows}

    today = date.today()

    # If today has a log, streak starts from today.
    # Otherwise, if yesterday has a log, streak starts from yesterday.
    # Otherwise streak is 0.
    if today in logged_dates:
        current_day = today
    elif (today - timedelta(days=1)) in logged_dates:
        current_day = today - timedelta(days=1)
    else:
        return 0

    streak = 0
    while current_day in logged_dates:
        streak += 1
        current_day -= timedelta(days=1)

    return streak


# -----------------------------
# Streamlit UI
# -----------------------------
st.set_page_config(page_title="Habit Tracker", page_icon="✅", layout="centered")

st.markdown("""
<style>
/* 页面宽度与上下留白 */
.block-container {
    max-width: 700px;
    padding-top: 0.8rem !important;
    padding-bottom: 1rem !important;
}

/* 整体字体 */
html, body, [class*="css"] {
    font-size: 14px;
}

/* 标题更紧凑 */
h1 {
    font-size: 28px !important;
    margin-bottom: 0.4rem !important;
}
h2 {
    font-size: 22px !important;
    margin-bottom: 0.3rem !important;
}
h3 {
    font-size: 18px !important;
    margin-bottom: 0.2rem !important;
}
h4 {
    font-size: 16px !important;
    margin-bottom: 0.15rem !important;
}

/* 按钮更小更紧凑 */
.stButton > button {
    height: 2.3rem;
    padding: 0.2rem 0.6rem;
    font-size: 0.95rem;
    border-radius: 0.6rem;
}

/* 输入框更紧凑 */
.stTextInput input {
    font-size: 0.95rem !important;
    padding: 0.45rem 0.6rem !important;
}

/* 减少段落空隙 */
p {
    margin-bottom: 0.35rem !important;
}

/* caption更小 */
[data-testid="stCaptionContainer"] {
    font-size: 12px;
}
</style>
""", unsafe_allow_html=True)

init_db()

st.title("✅ Habit Tracker")
st.caption("A simple Streamlit + SQLite habit check-in app")

# Add new habit
# Add new habit
st.subheader("Add a new habit")
with st.form("add_habit_form", clear_on_submit=True):
    new_habit = st.text_input(
        "Habit name",
        placeholder="e.g. Drink water, Running, Reading"
    )

    daily_target = st.number_input(
        "Daily target",
        min_value=1,
        value=1,
        step=1
    )

    submitted = st.form_submit_button("Add habit")

    if submitted:
        if new_habit.strip():
            result = add_habit(new_habit, daily_target)

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

# Today's habit cards
st.subheader(f"Today's check-ins ({date.today().isoformat()})")

today_counts = get_today_counts()

if not today_counts:
    st.info("No habits yet. Add your first habit above.")
else:
    for row in today_counts:
        st.markdown(f"#### {row['habit_name']}")

        target = row["daily_target"] if row["daily_target"] else 1
        today_count = row["total_count"]
        progress = min(today_count / target, 1.0)

        st.write(f"**Today: {today_count} / {target}**")
        st.progress(progress)

        if row["last_logged_at"]:
            last_time = datetime.fromisoformat(row["last_logged_at"]).strftime("%H:%M:%S")
            st.caption(f"Last check-in: {last_time}")
        else:
            st.caption("No check-in yet today")

        btn_col1, btn_col2, btn_col3 = st.columns([0.5, 0.5, 0.5])

        with btn_col1:
            if st.button("+1", key=f"log_{row['habit_id']}", use_container_width=True):
                log_habit(row["habit_id"], count=1)
                st.rerun()

        with btn_col2:
            if st.button("Edit", key=f"edit_toggle_{row['habit_id']}", use_container_width=True):
                st.session_state[f"editing_{row['habit_id']}"] = not st.session_state.get(
                    f"editing_{row['habit_id']}", False
                )

        with btn_col3:
            if st.button("Delete", key=f"delete_habit_{row['habit_id']}", use_container_width=True):
                deactivate_habit(row["habit_id"])
                st.rerun()

        if st.session_state.get(f"editing_{row['habit_id']}", False):
            st.markdown("#### Edit habit")

            new_name = st.text_input(
                "Habit name",
                value=row["habit_name"],
                key=f"new_name_{row['habit_id']}"
            )

            new_target = st.number_input(
                "Daily target",
                min_value=1,
                value=int(row["daily_target"]) if row["daily_target"] else 1,
                step=1,
                key=f"new_target_{row['habit_id']}"
            )

            save_col1, save_col2 = st.columns(2)

            with save_col1:
                if st.button("Save", key=f"save_habit_{row['habit_id']}", use_container_width=True):
                    error = update_habit(row["habit_id"], new_name, new_target)
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



# Today's log details

with st.expander("Today's log details", expanded=False):
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
                if st.button("Delete", key=f"delete_{log['id']}"):
                    delete_log(log["id"])
                    st.rerun()


with st.expander("Monthly Statistics", expanded=False):
    monthly_stats = get_monthly_stats()

    if not monthly_stats:
        st.write("No habits available for statistics yet.")
    else:
        for stat in monthly_stats:
            st.markdown(f"#### {stat['habit_name']}")

            left_col, right_col = st.columns(2)

            with left_col:
                st.write(f"**This month:** {stat['current_total']}")
                st.write(f"**Avg / day:** {stat['current_avg']}")
                st.write(f"**Active days:** {stat['current_active_days']}")

            with right_col:
                if stat["change_pct"] is None:
                    change_text = "New this month"
                else:
                    change_text = f"{stat['change_pct']}%"

                st.write(f"**Vs last month:** {change_text}")
                st.write(f"**Streak:** {stat['streak']} days")
                st.write(f"**Last month total:** {stat['prev_total']}")

            st.caption(
                f"Last month avg/day: {stat['prev_avg']} · "
                f"Last month active days: {stat['prev_active_days']}"
            )

            df_30 = get_last_30_days_data(stat["habit_id"])
            df_30["date"] = pd.to_datetime(df_30["date"]).dt.strftime("%m-%d")

            with st.expander("View last 30 days", expanded=False):
                st.dataframe(df_30, use_container_width=True, hide_index=True)
                st.bar_chart(df_30.set_index("date"))

            st.divider()