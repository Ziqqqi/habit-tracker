# Habit Tracker App - README

## Overview

This is a personal habit tracking app built with:

- Python
- Streamlit
- SQLite

The app is designed to support:

- Daily habit tracking
- Flexible habit frequencies
- Multiple check-ins
- Mobile-friendly usage
- Progress tracking and analytics

---

## Current Version

Current stable version:

app_v3_4_regenerated.py

This version includes:

- Daily habits
- X times per week habits
- Weekly habits
- Every N days habits
- Count-based habits
- Completion-based habits
- Mobile-friendly UI
- Collapsible sections
- Period-aware statistics

---

## Supported Habit Types

### 1. Count Habit

Used for habits where the user wants to count a number.

Examples:

- Drink water
- Reading minutes
- Push-ups
- Steps

### 2. Completion Habit

Used for habits where the user only wants to mark something as done.

Examples:

- Running
- Cleaning
- Face mask
- Deep work session

---

## Supported Frequency Types

### Daily

Habit resets every day.

Examples:

- Drink 8 cups of water per day
- Read 20 minutes per day

### X Per Week

Habit must be completed a certain number of times each week.

Examples:

- Run 3 times per week
- Go to the gym 4 times per week

### Weekly

Habit is tracked once per week.

Examples:

- Deep clean the house once per week
- Meal prep once per week

### Every N Days

Habit repeats on a cycle.

Examples:

- Face mask every 2 days
- Water plants every 3 days

---

## Current UI Features

### Add a New Habit

The Add Habit section is collapsible.

Each habit includes:

- Name
- Habit type
- Frequency type
- Frequency value
- Target count

### Current Progress

The Current Progress section is collapsible.

Each habit card includes:

- Habit name
- Rule label
- Current period progress
- Thin progress bar
- Last check-in timestamp
- Check-in button
- Manage section

### Manage Section

Each habit has a hidden Manage section for:

- Edit habit
- Change target
- Change frequency
- Hide habit

---

## Database Structure

### Table: habits

Fields:

- id
- name
- habit_type
- frequency_type
- frequency_value
- target_count
- daily_target
- created_at
- is_active

### Table: habit_logs

Fields:

- id
- habit_id
- logged_at
- log_date
- count

---

## Analytics Features

### Monthly Statistics

For each habit, the app can show:

- Total count
- Average per day / week
- Active days
- Completion rate
- Successful periods
- Vs last month
- Streak

### Recent History

The history section is period-aware:

- Daily habits show recent days
- Weekly habits show recent weeks
- Every N days habits show recent cycles

---

## Mobile-Friendly Design

The app is optimized for mobile browser usage.

Features include:

- Compact spacing
- Alternating gray and white habit cards
- Thin progress bars
- Softer timestamp styling
- Hidden manage area
- Collapsible sections

---

## How to Run

```bash
streamlit run app.py
```

---

## Suggested Future Upgrades

Possible future ideas:

- Dark mode
- Better charts
- Custom check-in amount buttons
- Notifications / reminders
- Export statistics
- User accounts
- Cloud sync
- Habit categories
- Color themes
- Calendar view

---

## Recommended Backup Practice

Before replacing app.py with a new version:

```bash
cp app.py app_backup.py
```

Then replace the file:

```bash
cp app_v3_4_regenerated.py app.py
```

Then run:

```bash
streamlit run app.py
```

