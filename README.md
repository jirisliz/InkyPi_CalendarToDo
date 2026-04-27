# calendar_todo — InkyPi Plugin

Landscape split-screen plugin: **monthly calendar on the left** (iCal / Google Calendar),
**todo list on the right** (Google Keep, Google Tasks, iCal VTODO, or manual).

```
┌─────────────────────────┬──────────────┐
│     April 2026          │  To-do       │
│  Mo Tu We Th Fr Sa Su   │  ☐ Groceries │
│         1  2  3  4  5   │  ☑ Report    │
│   6  7  8  9 10 11 12   │  ☐ Dentist   │
│  13 14 15 16 17 18 19   │  ☐ Auth bug  │
│  20 21 22 23 24 25 26   │  ☐ Update CV │
│  27 28 29 30            │              │
└─────────────────────────┴──────────────┘
       2/3 width                1/3 width
```

---

## Installation

1. Copy the `calendar_todo/` folder into `src/plugins/`:
   ```bash
   cp -r calendar_todo/ /path/to/InkyPi/src/plugins/
   ```

2. Install Python dependencies:
   ```bash
   pip install icalendar recurring-ical-events requests pillow
   ```

3. Optionally install todo-source extras:
   ```bash
   # For Google Keep:
   pip install gkeepapi gpsoauth

   # (Google Tasks uses only requests, already installed)
   ```

4. Restart InkyPi. The plugin will appear in the web UI as **Calendar + Todo**.

---

## Configuration

### iCal URL (required)
- **Google Calendar**: Settings → your calendar → *Secret address in iCal format*
- **Apple Calendar / Outlook**: any public `.ics` URL

### Split ratio
Choose how much width the calendar takes: 2/3, 3/4, or 1/2.

### Todo sources

| Source | What you need |
|--------|---------------|
| **iCal VTODO** | Same iCal URL — zero extra setup. Works with Apple Reminders. |
| **Google Keep** | `gkeepapi` + master token (see below) |
| **Google Tasks** | Google Cloud Console OAuth2 bearer token |
| **Manual** | Type tasks directly in the settings form |

---

## Google Keep setup

Google Keep has no official API for personal accounts.
This plugin uses [gkeepapi](https://github.com/kiwiz/gkeepapi), an unofficial client.

### 1. Get your master token (one-time)

**With Docker:**
```bash
docker run --rm -it --entrypoint /bin/sh python:3 -c \
  'pip install gpsoauth; python3 -c '\''print(__import__("gpsoauth").exchange_token(input("Email: "), input("OAuth Token: "), input("Android ID: ")))'\''
```

When prompted:
- **Email**: your Gmail address
- **OAuth Token**: get this from https://accounts.google.com/EmbeddedSetup (log in, then copy the `oauth_token` from the URL)
- **Android ID**: any 16-char hex string, e.g. `1234567890abcdef`

**Without Docker:**
```bash
pip install gpsoauth
python3 -c "
import gpsoauth
email = input('Email: ')
token = input('OAuth Token: ')
android_id = input('Android ID: ')
print(gpsoauth.exchange_token(email, token, android_id))
"
```

### 2. If you have 2-Step Verification
Create an **App Password** at https://myaccount.google.com/apppasswords
and use it as the master token directly (skip the gpsoauth step).

### 3. Enter in plugin settings
- **Email**: your Gmail address
- **Master token**: the token from step 1 or 2
- **Note title**: title of the Keep note to use (e.g. `Shopping list`).
  Leave blank to auto-select the first pinned checklist.

---

## Google Tasks setup

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a project → Enable **Tasks API**
3. Create an **OAuth 2.0 Client ID** (Desktop app)
4. Run the OAuth flow once to get a bearer token
5. Add to InkyPi's `.env`:
   ```
   GOOGLE_TASKS_TOKEN=ya29.your_token_here
   ```
   Or paste it directly in the plugin settings form.

---

## Files

```
calendar_todo/
├── calendar_todo.py   — plugin logic
├── settings.html      — web UI settings form
├── plugin.json        — metadata
└── README.md          — this file
```
