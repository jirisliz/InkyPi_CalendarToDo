# calendar_todo — InkyPi Plugin

Landscape split-screen plugin: **monthly calendar on the left** (iCal / Google Calendar),
**todo list on the right** (Google Keep, Google Tasks, iCal VTODO, or manual).

<img width="573" height="361" alt="image" src="https://github.com/user-attachments/assets/c7290e4b-9166-477a-b7cc-d7da1262631a" />


## Installation via PluginManager

Install with the InkyPi CLI:

```bash
inkypi plugin install calendar_todo https://github.com/<your-username>/InkyPi-Plugin-CalendarTodo
```

Then install the Python dependencies inside the InkyPi virtualenv:

```bash
source /usr/local/inkypi/venv_inkypi/bin/activate
pip install -r /usr/local/inkypi/src/plugins/calendar_todo/requirements.txt
```

For Google Keep support also run:

```bash
pip install gkeepapi gpsoauth
```

## Manual installation

1. Copy the `calendar_todo/` folder into `src/plugins/`
2. Install dependencies (see above)
3. Restart InkyPi

## Configuration

| Setting | Description |
|---------|-------------|
| **iCal URL** | Google/Outlook/Apple `.ics` secret address |
| **Split ratio** | 2/3 · 3/4 · 1/2 — how much width the calendar takes |
| **Todo source** | iCal VTODO · Google Keep · Google Tasks · Manual |
| **Max items** | Number of todo items to display |
| **Show completed** | Toggle strikethrough completed tasks |

### iCal URL
Google Calendar → Settings → your calendar → *Secret address in iCal format*

### Google Keep setup

Google Keep has no official API for personal accounts. This plugin uses [gkeepapi](https://github.com/kiwiz/gkeepapi), an unofficial Python client.

**Get your master token (one-time):**

```bash
pip install gpsoauth
python3 -c "
import gpsoauth
email      = input('Gmail address: ')
oauth_tok  = input('OAuth token (from https://accounts.google.com/EmbeddedSetup): ')
android_id = input('Android ID (any 16-char hex, e.g. 1234567890abcdef): ')
print(gpsoauth.exchange_token(email, oauth_tok, android_id))
"
```

If you have **2-Step Verification**, create an [App Password](https://myaccount.google.com/apppasswords) and use it as the master token directly (skip the step above).

Enter the token in the plugin settings under *Master token*.

### Google Tasks setup

1. Enable the **Tasks API** in [Google Cloud Console](https://console.cloud.google.com)
2. Create an **OAuth 2.0 Client ID** (Desktop app type)
3. Complete the OAuth flow to get a bearer token
4. Add to InkyPi's `.env`:
   ```
   GOOGLE_TASKS_TOKEN=ya29.your_token_here
   ```
   Or paste it directly in the plugin settings.

## License

GPL-3.0
