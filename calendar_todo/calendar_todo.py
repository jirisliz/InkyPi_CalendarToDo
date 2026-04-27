import logging
from datetime import datetime, date, timedelta
from icalendar import Calendar
import recurring_ical_events
import requests
from PIL import Image, ImageDraw, ImageFont
import os

from plugins.base_plugin.base_plugin import BasePlugin

logger = logging.getLogger(__name__)


class CalendarTodoPlugin(BasePlugin):
    """
    Landscape split-screen plugin for InkyPi.
    Calendar (iCal/Google Calendar) on the left, todo list on the right.
    Todo sources: iCal VTODO, Google Keep (gkeepapi), Google Tasks API, or manual.
    """

    def generate_image(self, settings, device_config):
        width, height = device_config.get_resolution()

        # Split ratio (calendar takes the left portion)
        ratio = float(settings.get("split_ratio", 0.67))
        calendar_width = int(width * ratio)
        todo_width = width - calendar_width

        # --- Settings ---
        ical_url        = settings.get("ical_url", "").strip()
        todo_source     = settings.get("todo_source", "ical")
        show_completed  = settings.get("show_completed", False)
        num_todo_items  = int(settings.get("num_todo_items", 8))
        keep_note_title = settings.get("keep_note_title", "").strip()
        keep_token      = settings.get("keep_token", "").strip()
        keep_email      = settings.get("keep_email", "").strip()
        tasks_api_key   = settings.get("tasks_api_key", "").strip()
        tasks_list_id   = settings.get("tasks_list_id", "@default")
        manual_todos    = settings.get("manual_todos", "")

        if not ical_url:
            raise RuntimeError(
                "Please provide an iCal URL. "
                "In Google Calendar: Settings → your calendar → 'Secret address in iCal format'."
            )

        # --- Fetch and parse calendar ---
        cal_data = self._fetch_ical(ical_url)
        today = date.today()
        month_start = today.replace(day=1)
        next_month  = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_end   = next_month - timedelta(days=1)
        events      = self._get_events(cal_data, month_start, month_end)

        # --- Fetch todos ---
        if todo_source == "keep":
            todos = self._get_keep_todos(
                keep_email, keep_token, keep_note_title, show_completed, num_todo_items
            )
        elif todo_source == "tasks":
            todos = self._get_google_tasks(
                tasks_api_key, tasks_list_id, show_completed, num_todo_items
            )
        elif todo_source == "ical":
            todos = self._get_ical_todos(cal_data, show_completed, num_todo_items)
        else:  # manual
            todos = [
                {"summary": t.strip(), "completed": False}
                for t in manual_todos.splitlines() if t.strip()
            ][:num_todo_items]

        # --- Compose final image ---
        img      = Image.new("RGB", (width, height), "white")
        cal_img  = self._render_calendar(
            calendar_width, height, today, month_start, month_end, events
        )
        todo_img = self._render_todos(todo_width, height, todos)

        img.paste(cal_img,  (0, 0))
        img.paste(todo_img, (calendar_width, 0))

        # Vertical divider line
        draw = ImageDraw.Draw(img)
        draw.line([(calendar_width, 0), (calendar_width, height)], fill="black", width=2)

        return img

    # ------------------------------------------------------------------
    # iCal helpers
    # ------------------------------------------------------------------

    def _fetch_ical(self, url):
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            return Calendar.from_ical(resp.text)
        except Exception as e:
            raise RuntimeError(f"Failed to fetch calendar: {e}")

    def _get_events(self, cal, start, end):
        try:
            raw = recurring_ical_events.of(cal).between(
                datetime(start.year, start.month, start.day),
                datetime(end.year,  end.month,   end.day, 23, 59, 59),
            )
        except Exception as e:
            logger.warning(f"Recurring events expansion error: {e}")
            raw = []

        events = {}
        for c in raw:
            if c.name == "VEVENT":
                dt = c.get("DTSTART").dt
                d  = dt.date() if isinstance(dt, datetime) else dt
                events.setdefault(d, []).append(str(c.get("SUMMARY", "")))
        return events

    def _get_ical_todos(self, cal, show_completed, limit):
        todos = []
        for c in cal.walk():
            if c.name == "VTODO":
                completed = str(c.get("STATUS", "")).upper() == "COMPLETED"
                if completed and not show_completed:
                    continue
                todos.append({"summary": str(c.get("SUMMARY", "")), "completed": completed})
                if len(todos) >= limit:
                    break
        return todos

    # ------------------------------------------------------------------
    # Google Keep  (unofficial gkeepapi)
    # ------------------------------------------------------------------

    def _get_keep_todos(self, email, master_token, note_title, show_completed, limit):
        """
        Reads a checklist (or text) note from Google Keep.
        Requires: pip install gkeepapi
        Get master_token via gpsoauth — see README for instructions.
        If you have 2FA, use a Google App Password as the master_token.
        """
        try:
            import gkeepapi
        except ImportError:
            raise RuntimeError(
                "gkeepapi is not installed. "
                "Run: pip install gkeepapi  (inside the InkyPi venv)"
            )

        if not email or not master_token:
            raise RuntimeError(
                "Google Keep requires both 'Email' and 'Master token' in plugin settings."
            )

        keep = gkeepapi.Keep()
        try:
            keep.authenticate(email, master_token)
        except Exception as e:
            raise RuntimeError(f"Google Keep authentication failed: {e}")

        keep.sync()

        # Find note by title (case-insensitive), or fall back to first pinned checklist
        note = None
        if note_title:
            for n in keep.all():
                if not n.trashed and n.title.lower() == note_title.lower():
                    note = n
                    break
        if note is None:
            for n in keep.all():
                if not n.trashed and n.pinned and hasattr(n, "items"):
                    note = n
                    break

        if note is None:
            return [{"summary": "No Keep note found", "completed": False}]

        todos = []
        if hasattr(note, "items"):          # ListNote (checklist)
            for item in note.items:
                if item.checked and not show_completed:
                    continue
                todos.append({"summary": item.text, "completed": item.checked})
                if len(todos) >= limit:
                    break
        else:                               # TextNote — each line is a task
            for line in note.text.splitlines():
                line = line.strip()
                if line:
                    todos.append({"summary": line, "completed": False})
                    if len(todos) >= limit:
                        break

        return todos

    # ------------------------------------------------------------------
    # Google Tasks  (official REST API)
    # ------------------------------------------------------------------

    def _get_google_tasks(self, api_key, tasklist_id, show_completed, limit):
        """
        Reads tasks from Google Tasks API.
        Requires an OAuth2 bearer token stored as GOOGLE_TASKS_TOKEN in .env,
        or pasted directly in the plugin settings.
        """
        token = os.environ.get("GOOGLE_TASKS_TOKEN", api_key)
        if not token:
            raise RuntimeError(
                "Google Tasks token not set. "
                "Add GOOGLE_TASKS_TOKEN=<token> to your InkyPi .env file, "
                "or enter it in the plugin settings."
            )

        url = (
            f"https://tasks.googleapis.com/tasks/v1/lists/"
            f"{tasklist_id}/tasks"
            f"?showCompleted={'true' if show_completed else 'false'}"
            f"&showHidden=false&maxResults={limit}"
        )
        try:
            resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise RuntimeError(f"Google Tasks API error: {e}")

        return [
            {"summary": item.get("title", ""), "completed": item.get("status") == "completed"}
            for item in data.get("items", [])
        ]

    # ------------------------------------------------------------------
    # Rendering helpers
    # ------------------------------------------------------------------

    def _load_fonts(self, base_size=14):
        font_dir = os.path.join(
            os.path.dirname(__file__), "..", "base_plugin", "fonts"
        )
        try:
            bold   = ImageFont.truetype(os.path.join(font_dir, "DejaVuSans-Bold.ttf"), base_size + 4)
            medium = ImageFont.truetype(os.path.join(font_dir, "DejaVuSans-Bold.ttf"), base_size)
            small  = ImageFont.truetype(os.path.join(font_dir, "DejaVuSans.ttf"),      base_size - 2)
            tiny   = ImageFont.truetype(os.path.join(font_dir, "DejaVuSans.ttf"),      base_size - 4)
        except Exception:
            bold = medium = small = tiny = ImageFont.load_default()
        return bold, medium, small, tiny

    def _render_calendar(self, width, height, today, month_start, month_end, events):
        img  = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(img)
        bold, medium, small, tiny = self._load_fonts()

        padding = 10
        y = padding

        # Month / year header
        draw.text(
            (width // 2, y + 12),
            month_start.strftime("%B %Y"),
            font=bold, fill="black", anchor="mm"
        )
        y += 30

        # Day-of-week header row
        col_w     = (width - 2 * padding) // 7
        day_names = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
        for i, name in enumerate(day_names):
            x = padding + i * col_w + col_w // 2
            draw.text((x, y + 8), name, font=medium, fill="black", anchor="mm")
        y += 20
        draw.line([(padding, y), (width - padding, y)], fill="black", width=1)
        y += 6

        # Calendar grid
        available_h = height - y - padding
        row_h       = max(24, available_h // 6)
        col         = month_start.weekday()   # 0 = Monday
        row_y       = y
        current     = month_start

        while current <= month_end:
            cell_x = padding + col * col_w

            if current == today:
                draw.rectangle(
                    [cell_x + 1, row_y + 1, cell_x + col_w - 2, row_y + row_h - 2],
                    fill="black"
                )
                num_color = "white"
            else:
                num_color = "black"

            draw.text(
                (cell_x + col_w // 2, row_y + 9),
                str(current.day),
                font=medium, fill=num_color, anchor="mm"
            )

            # Up to 2 truncated event labels per cell
            if current in events:
                ey = row_y + 16
                for ev in events[current][:2]:
                    label = (ev[:10] + "…") if len(ev) > 10 else ev
                    draw.text((cell_x + 2, ey), label, font=tiny, fill="black")
                    ey += 9

            col += 1
            if col > 6:
                col   = 0
                row_y += row_h

            current += timedelta(days=1)

        return img

    def _render_todos(self, width, height, todos):
        img  = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(img)
        bold, medium, small, tiny = self._load_fonts()

        padding = 10
        y = padding

        draw.text((padding, y + 10), "To-do", font=bold, fill="black", anchor="lm")
        y += 28
        draw.line([(padding, y), (width - padding, y)], fill="black", width=1)
        y += 8

        if not todos:
            draw.text((padding, y), "All done!", font=small, fill="gray")
            return img

        available = height - y - padding
        item_h    = min(26, available // max(len(todos), 1))
        box_size  = 11

        for task in todos:
            bx = padding
            by = y + (item_h - box_size) // 2

            draw.rectangle([bx, by, bx + box_size, by + box_size], outline="black", width=1)

            if task["completed"]:
                draw.rectangle([bx, by, bx + box_size, by + box_size], fill="black")
                draw.line(
                    [(bx + 2, by + 6), (bx + 5, by + 9), (bx + 10, by + 3)],
                    fill="white", width=1
                )

            tx    = padding + box_size + 6
            fill  = "gray" if task["completed"] else "black"
            label = task["summary"]

            # Truncate to fit the panel width
            max_chars = max(8, (width - tx - padding) // 7)
            if len(label) > max_chars:
                label = label[:max_chars - 1] + "…"

            draw.text((tx, y + (item_h - 13) // 2), label, font=small, fill=fill)

            if task["completed"]:
                tw    = draw.textlength(label, font=small)
                mid_y = y + item_h // 2
                draw.line([(tx, mid_y), (tx + tw, mid_y)], fill="gray", width=1)

            y += item_h

        return img

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params["todo_sources"] = ["ical", "keep", "tasks", "manual"]
        return template_params
