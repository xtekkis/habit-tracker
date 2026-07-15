# Habit Tracker

A mobile-first habit tracking web app built with Flask and SQLite. Track your daily habits, earn XP, and build streaks — all running locally in your browser with no account or API keys required.

## Features

- **Daily habit tracking** — check off habits scheduled for today; repeat days are configurable per habit
- **XP & Levels** — earn XP for every logged habit (+10) and a perfect-day bonus (+25); level up through 11 ranks from Seed to Evergreen
- **Streaks** — current and best streaks tracked per habit
- **Insights** — weekly/monthly bar chart, completion stats, and perfect-days highlight
- **Per-habit calendar** — month grid view, streak cards, and notes timeline
- **Icon & color picker** — 12 Lucide icons and 8 earthy color swatches per habit
- **Category filters** — group habits and filter the Today view
- **Reminders** — per-habit notification scheduling via the Web Notifications API
- **Settings** — start-week preference, streak display toggle, CSV export, and data reset
- **PWA** — installable on mobile and desktop via browser (manifest + service worker)
- **Responsive** — mobile-first 440px layout; ≥1024px switches to a two-column sidebar layout
- **Accessible** — keyboard focus states, reduced-motion support, and ARIA labels throughout

## Screenshots

| Today | Add habit | Insights |
|-------|-----------|----------|
| ![Today](screenshots/today.png) | ![Add habit sheet](screenshots/add_sheet_open.png) | ![Insights](screenshots/insights.png) |

| Calendar | Settings | Desktop |
|----------|----------|---------|
| ![Calendar](screenshots/calendar.png) | ![Settings](screenshots/settings.png) | ![Desktop layout](screenshots/desktop.png) |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python + Flask |
| Database | SQLite (`habits.db`) |
| Templates | Jinja2 |
| Icons | Lucide (CDN) |
| Font | Hanken Grotesk (Google Fonts) |
| Styling | Vanilla CSS with custom properties |
| JS | Vanilla JS — no framework |
| PWA icons | Pillow (one-time generation script) |

## Setup

**Requirements:** Python 3.9+

```bash
# Clone the repo
git clone https://github.com/xtekkis/habit-tracker.git
cd habit-tracker

# Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate       # Windows
# source venv/bin/activate  # macOS / Linux

# Install dependencies
pip install -r requirements.txt

# Run the app
python app.py
```

Open [http://localhost:5000](http://localhost:5000) in your browser.

The database (`habits.db`) is created automatically on first run and is excluded from git.

## PWA Installation

With the app running, open it in Chrome or Edge and click **Install** in the address bar (or use the browser menu → "Install Habit Tracker"). The app will open in its own window without browser chrome.

Safari on iOS: tap the Share button → **Add to Home Screen**.

## Hosting

[PythonAnywhere](https://www.pythonanywhere.com) is recommended for self-hosting — it supports persistent SQLite on the free tier and provides HTTPS (required for PWA installation and notifications).
