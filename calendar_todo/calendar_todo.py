import logging
from datetime import datetime, date, timedelta
from icalendar import Calendar
import recurring_ical_events
import requests
from PIL import Image, ImageDraw, ImageFont
import os

from plugins.base_plugin.base_plugin import BasePlugin

logger = logging.getLogger(__name__)

DAY_NAMES_CS = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]
DAY_NAMES_EN = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
MONTH_NAMES_CS = ["", "Leden", "Únor", "Březen", "Duben", "Květen", "Červen",
                  "Červenec", "Srpen", "Září", "Říjen", "Listopad", "Prosinec"]
DAY_SHORT_CS   = ["", "Po", "Út", "St", "Čt", "Pá", "So", "Ne"]


class CalendarTodoPlugin(BasePlugin):
    """
    Landscape split-screen plugin for InkyPi.
    Left panel: monthly grid OR agenda list of upcoming events.
    Right panel: todo list (iCal VTODO / Google Keep / Google Tasks / manual).
    """

    def generate_image(self, settings, device_config):
        width, height = device_config.get_resolution()

        ratio          = float(settings.get("split_ratio", "0.67"))
        calendar_width = int(width * ratio)
        todo_width     = width - calendar_width

        ical_url        = settings.get("ical_url", "").strip()
        todo_source     = settings.get("todo_source", "ical")
        show_completed  = settings.get("show_completed", "false") in (True, "true", "True", "1")
        num_todo_items  = int(settings.get("num_todo_items", 8))
        keep_note_title = settings.get("keep_note_title", "").strip()
        keep_token      = settings.get("keep_token", "").strip()
        keep_email      = settings.get("keep_email", "").strip()
        tasks_api_key   = settings.get("tasks_api_key", "").strip()
        tasks_list_id   = settings.get("tasks_list_id", "@default")
        manual_todos    = settings.get("manual_todos", "")
        cal_style       = settings.get("cal_style", "grid")          # "grid" or "agenda"
        font_size       = int(settings.get("font_size", 14))
        agenda_days     = int(settings.get("agenda_days", 14))       # days ahead for agenda view
        language        = settings.get("language", "en")             # "en" or "cs"

        if not ical_url:
            raise RuntimeError(
                "Please provide an iCal URL. "
                "In Google Calendar: Settings → your calendar → 'Secret address in iCal format'."
            )

        cal_data   = self._fetch_ical(ical_url)
        today      = date.today()

        if cal_style == "agenda":
            # Agenda needs events from today forward
            agenda_end = today + timedelta(days=agenda_days)
            events_raw = self._get_events_with_time(cal_data, today, agenda_end)
        else:
            # Grid needs the whole current month
            month_start = today.replace(day=1)
            next_month  = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
            month_end   = next_month - timedelta(days=1)
            events      = self._get_events(cal_data, month_start, month_end)

        # --- Todos ---
        if todo_source == "keep":
            todos = self._get_keep_todos(keep_email, keep_token, keep_note_title,
                                         show_completed, num_todo_items)
        elif todo_source == "tasks":
            todos = self._get_google_tasks(tasks_api_key, tasks_list_id,
                                           show_completed, num_todo_items)
        elif todo_source == "ical":
            todos = self._get_ical_todos(cal_data, show_completed, num_todo_items)
        else:
            todos = [
                {"summary": t.strip(), "completed": False}
                for t in manual_todos.splitlines() if t.strip()
            ][:num_todo_items]

        # --- Render ---
        img = Image.new("RGB", (width, height), "white")

        if cal_style == "agenda":
            cal_img = self._render_agenda(
                calendar_width, height, today, events_raw, font_size, language
            )
        else:
            cal_img = self._render_calendar(
                calendar_width, height, today, month_start, month_end, events,
                font_size, language
            )

        todo_img = self._render_todos(todo_width, height, todos, font_size)

        img.paste(cal_img,  (0, 0))
        img.paste(todo_img, (calendar_width, 0))

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
        """Return dict {date: [summary, ...]} for the grid view."""
        try:
            raw = recurring_ical_events.of(cal).between(
                datetime(start.year, start.month, start.day),
                datetime(end.year, end.month, end.day, 23, 59, 59),
            )
        except Exception as e:
            logger.warning(f"Recurring events error: {e}")
            raw = []
        events = {}
        for c in raw:
            if c.name == "VEVENT":
                dt = c.get("DTSTART").dt
                d  = dt.date() if isinstance(dt, datetime) else dt
                events.setdefault(d, []).append(str(c.get("SUMMARY", "")))
        return events

    def _get_events_with_time(self, cal, start, end):
        """Return list of dicts sorted by date+time for agenda view."""
        try:
            raw = recurring_ical_events.of(cal).between(
                datetime(start.year, start.month, start.day),
                datetime(end.year, end.month, end.day, 23, 59, 59),
            )
        except Exception as e:
            logger.warning(f"Recurring events error: {e}")
            raw = []
        events = []
        for c in raw:
            if c.name == "VEVENT":
                dt      = c.get("DTSTART").dt
                dt_end  = c.get("DTEND")
                is_allday = not isinstance(dt, datetime)
                d       = dt if is_allday else dt.date()
                summary = str(c.get("SUMMARY", ""))

                time_str = ""
                if not is_allday:
                    time_str = dt.strftime("%H:%M")
                    if dt_end:
                        end_dt = dt_end.dt
                        if isinstance(end_dt, datetime):
                            time_str += "–" + end_dt.strftime("%H:%M")

                events.append({
                    "date":     d,
                    "dt":       dt,
                    "summary":  summary,
                    "time_str": time_str,
                    "allday":   is_allday,
                })
        events.sort(key=lambda e: (e["date"], e["time_str"]))
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
    # Google Keep
    # ------------------------------------------------------------------

    def _get_keep_todos(self, email, master_token, note_title, show_completed, limit):
        try:
            import gkeepapi
        except ImportError:
            raise RuntimeError("gkeepapi not installed. Run: pip install gkeepapi")
        if not email or not master_token:
            raise RuntimeError("Google Keep requires email and master token in settings.")
        keep = gkeepapi.Keep()
        try:
            keep.authenticate(email, master_token)
        except Exception as e:
            raise RuntimeError(f"Google Keep auth failed: {e}")
        keep.sync()
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
        if hasattr(note, "items"):
            for item in note.items:
                if item.checked and not show_completed:
                    continue
                todos.append({"summary": item.text, "completed": item.checked})
                if len(todos) >= limit:
                    break
        else:
            for line in note.text.splitlines():
                line = line.strip()
                if line:
                    todos.append({"summary": line, "completed": False})
                    if len(todos) >= limit:
                        break
        return todos

    # ------------------------------------------------------------------
    # Google Tasks
    # ------------------------------------------------------------------

    def _get_google_tasks(self, api_key, tasklist_id, show_completed, limit):
        token = os.environ.get("GOOGLE_TASKS_TOKEN", api_key)
        if not token:
            raise RuntimeError("Google Tasks token not set. Add GOOGLE_TASKS_TOKEN to .env")
        url = (
            f"https://tasks.googleapis.com/tasks/v1/lists/{tasklist_id}/tasks"
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
    # Font loader
    # ------------------------------------------------------------------

    def _load_fonts(self, base_size=14):
        font_dir = os.path.join(os.path.dirname(__file__), "..", "base_plugin", "fonts")
        try:
            bold   = ImageFont.truetype(os.path.join(font_dir, "DejaVuSans-Bold.ttf"), base_size + 4)
            medium = ImageFont.truetype(os.path.join(font_dir, "DejaVuSans-Bold.ttf"), base_size)
            small  = ImageFont.truetype(os.path.join(font_dir, "DejaVuSans.ttf"),      base_size - 2)
            tiny   = ImageFont.truetype(os.path.join(font_dir, "DejaVuSans.ttf"),      base_size - 4)
        except Exception:
            bold = medium = small = tiny = ImageFont.load_default()
        return bold, medium, small, tiny

    # ------------------------------------------------------------------
    # Grid calendar renderer
    # ------------------------------------------------------------------

    def _render_calendar(self, width, height, today, month_start, month_end,
                         events, font_size, language):
        img  = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(img)
        bold, medium, small, tiny = self._load_fonts(font_size)

        day_names = DAY_NAMES_CS if language == "cs" else DAY_NAMES_EN
        padding   = 10
        y         = padding

        # Month header
        if language == "cs":
            header = f"{MONTH_NAMES_CS[month_start.month]} {month_start.year}"
        else:
            header = month_start.strftime("%B %Y")
        draw.text((width // 2, y + (font_size + 4) // 2), header,
                  font=bold, fill="black", anchor="mm")
        y += font_size + 10

        # Day-of-week row
        col_w = (width - 2 * padding) // 7
        for i, name in enumerate(day_names):
            x = padding + i * col_w + col_w // 2
            draw.text((x, y + font_size // 2), name,
                      font=medium, fill="black", anchor="mm")
        y += font_size + 6
        draw.line([(padding, y), (width - padding, y)], fill="black", width=1)
        y += 4

        # Grid rows
        available_h = height - y - padding
        row_h       = max(font_size + 10, available_h // 6)
        col         = month_start.weekday()
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
                (cell_x + col_w // 2, row_y + font_size // 2 + 2),
                str(current.day),
                font=medium, fill=num_color, anchor="mm"
            )

            if current in events:
                ey = row_y + font_size + 4
                for ev in events[current][:2]:
                    max_c = max(4, (col_w - 2) // max(1, tiny.size // 2))
                    label = (ev[:max_c] + "…") if len(ev) > max_c else ev
                    draw.text((cell_x + 2, ey), label, font=tiny, fill="black")
                    ey += tiny.size + 1

            col += 1
            if col > 6:
                col    = 0
                row_y += row_h
            current += timedelta(days=1)

        return img

    # ------------------------------------------------------------------
    # Agenda list renderer
    # ------------------------------------------------------------------

    def _render_agenda(self, width, height, today, events, font_size, language):
        img  = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(img)
        bold, medium, small, tiny = self._load_fonts(font_size)

        padding     = 10
        indent      = padding + font_size          # event rows indented under date header
        line_h      = font_size + 4               # height of a single text line
        date_h      = font_size + 8               # height of a day-header row
        sep_gap     = 4                            # gap around day separators
        y           = padding

        # Panel header
        header = "Nadcházející události" if language == "cs" else "Upcoming events"
        draw.text((width // 2, y + (font_size + 4) // 2), header,
                  font=bold, fill="black", anchor="mm")
        y += font_size + 10
        draw.line([(padding, y), (width - padding, y)], fill="black", width=1)
        y += 6

        if not events:
            msg = "Žádné události" if language == "cs" else "No upcoming events"
            draw.text((padding, y), msg, font=small, fill="gray")
            return img

        last_date = None

        for ev in events:
            # ---- Day header (new date) ----
            if ev["date"] != last_date:
                # separator line between days (skip before very first)
                if last_date is not None:
                    if y + sep_gap + date_h > height - padding:
                        break
                    draw.line([(padding, y + sep_gap // 2),
                               (width - padding, y + sep_gap // 2)],
                              fill="black", width=1)
                    y += sep_gap

                if y + date_h > height - padding:
                    break

                d = ev["date"]
                if language == "cs":
                    dow   = DAY_SHORT_CS[d.isoweekday()]
                    label = f"{dow}  {d.day}. {MONTH_NAMES_CS[d.month][:3]}."
                else:
                    label = d.strftime("%a  %-d %b")

                is_today = (d == today)
                if is_today:
                    # Full-width black bar for today
                    draw.rectangle(
                        [padding - 2, y, width - padding, y + date_h - 2],
                        fill="black"
                    )
                    draw.text((padding + 4, y + date_h // 2), label,
                              font=bold, fill="white", anchor="lm")
                else:
                    draw.text((padding, y + date_h // 2), label,
                              font=bold, fill="black", anchor="lm")

                y        += date_h
                last_date = ev["date"]

            # ---- Event row ----
            # Layout: two lines under the date header
            #   line 1 (if timed):  time string   — same font as summary
            #   line 2:             summary text
            # All-day events: just one line with summary

            if ev["time_str"]:
                # Line 1 — time
                if y + line_h > height - padding:
                    break
                draw.text((indent, y), ev["time_str"], font=small, fill="black")
                y += line_h

            # Line 2 — summary (word-wrap if too long)
            if y + line_h > height - padding:
                break

            summary   = ev["summary"]
            max_w     = width - indent - padding
            # measure and wrap using actual pixel widths
            words     = summary.split()
            line1     = ""
            line2_words = []
            for word in words:
                test = (line1 + " " + word).strip()
                tw   = draw.textlength(test, font=small)
                if tw <= max_w:
                    line1 = test
                else:
                    line2_words.append(word)

            draw.text((indent, y), line1, font=small, fill="black")
            y += line_h

            if line2_words:
                if y + line_h > height - padding:
                    continue
                line2 = " ".join(line2_words)
                if draw.textlength(line2, font=small) > max_w:
                    # Truncate with ellipsis
                    while line2 and draw.textlength(line2 + "…", font=small) > max_w:
                        line2 = line2[:-1]
                    line2 = line2.rstrip() + "…"
                draw.text((indent, y), line2, font=small, fill="black")
                y += line_h

            y += 2   # small gap between events in the same day

        return img

    # ------------------------------------------------------------------
    # Todo renderer
    # ------------------------------------------------------------------

    def _render_todos(self, width, height, todos, font_size=14):
        img  = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(img)
        bold, medium, small, tiny = self._load_fonts(font_size)

        padding  = 10
        y        = padding
        box_size = max(10, font_size - 2)

        draw.text((padding, y + (font_size + 4) // 2), "To-do",
                  font=bold, fill="black", anchor="lm")
        y += font_size + 10
        draw.line([(padding, y), (width - padding, y)], fill="black", width=1)
        y += 6

        if not todos:
            draw.text((padding, y), "All done!", font=small, fill="gray")
            return img

        available = height - y - padding
        item_h    = min(font_size + 10, available // max(len(todos), 1))
        item_h    = max(item_h, font_size + 4)

        for task in todos:
            if y + item_h > height - padding:
                break

            bx = padding
            by = y + (item_h - box_size) // 2
            draw.rectangle([bx, by, bx + box_size, by + box_size],
                           outline="black", width=1)
            if task["completed"]:
                draw.rectangle([bx, by, bx + box_size, by + box_size], fill="black")
                # Checkmark
                cx, cy = bx + box_size // 2, by + box_size // 2
                draw.line([(bx + 2, cy), (cx - 1, by + box_size - 3), (bx + box_size - 2, by + 2)],
                          fill="white", width=max(1, box_size // 6))

            tx    = padding + box_size + 6
            fill  = "gray" if task["completed"] else "black"
            label = task["summary"]

            max_chars = max(8, (width - tx - padding) // max(1, (font_size - 2) // 2))
            if len(label) > max_chars:
                label = label[:max_chars - 1] + "…"

            draw.text((tx, y + item_h // 2), label, font=small, fill=fill, anchor="lm")

            if task["completed"]:
                tw    = draw.textlength(label, font=small)
                mid_y = y + item_h // 2
                draw.line([(tx, mid_y), (tx + tw, mid_y)], fill="gray", width=1)

            y += item_h

        return img
