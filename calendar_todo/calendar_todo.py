import logging
from datetime import datetime, date, timedelta
from icalendar import Calendar
import recurring_ical_events
import requests
from PIL import Image, ImageDraw, ImageFont
import os

from plugins.base_plugin.base_plugin import BasePlugin

logger = logging.getLogger(__name__)

DAY_NAMES_CS   = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]
DAY_NAMES_EN   = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
MONTH_NAMES_CS = ["", "Leden", "Únor", "Březen", "Duben", "Květen", "Červen",
                  "Červenec", "Srpen", "Září", "Říjen", "Listopad", "Prosinec"]
DAY_SHORT_CS   = ["", "Po", "Út", "St", "Čt", "Pá", "So", "Ne"]


def hex_to_rgb(hex_str, fallback=(0, 0, 0)):
    """Convert #RRGGBB string to (R, G, B) tuple."""
    try:
        h = hex_str.strip().lstrip("#")
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    except Exception:
        return fallback


def lighten(rgb, factor=0.35):
    """Mix colour with white to produce a pale fill version."""
    r, g, b = rgb
    return (
        int(r + (255 - r) * factor),
        int(g + (255 - g) * factor),
        int(b + (255 - b) * factor),
    )


class CalendarTodoPlugin(BasePlugin):
    """
    Landscape split-screen InkyPi plugin.
    Left panel : monthly grid OR agenda list.
    Right panel: todo list.
    Fully configurable colours and font size.
    """

    # ------------------------------------------------------------------ #
    #  Entry point                                                         #
    # ------------------------------------------------------------------ #

    def generate_image(self, settings, device_config):
        width, height = device_config.get_resolution()

        ratio          = float(settings.get("split_ratio", "0.67"))
        calendar_width = int(width * ratio)
        todo_width     = width - calendar_width

        # Core settings
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
        cal_style       = settings.get("cal_style", "grid")
        font_size       = int(settings.get("font_size", 14))
        agenda_days     = int(settings.get("agenda_days", 14))
        language        = settings.get("language", "en")

        # Colour settings — all as (R,G,B) tuples
        cal_bg          = hex_to_rgb(settings.get("cal_bg",       "#FFFFFF"), (255, 255, 255))
        cal_text        = hex_to_rgb(settings.get("cal_text",     "#000000"), (0,   0,   0  ))
        cal_header_bg   = hex_to_rgb(settings.get("cal_header_bg","#000000"), (0,   0,   0  ))
        cal_header_text = hex_to_rgb(settings.get("cal_header_text","#FFFFFF"),(255,255,255))
        pill_border     = hex_to_rgb(settings.get("pill_border",  "#FBBD02"), (251, 189,  2 ))
        pill_fill       = hex_to_rgb(settings.get("pill_fill",    "#FFF2AD"), (255, 242, 173))
        todo_bg         = hex_to_rgb(settings.get("todo_bg",      "#FFFFFF"), (255, 255, 255))
        todo_text       = hex_to_rgb(settings.get("todo_text",    "#000000"), (0,   0,   0  ))
        divider_color   = hex_to_rgb(settings.get("divider_color","#000000"), (0,   0,   0  ))

        colors = dict(
            cal_bg=cal_bg, cal_text=cal_text,
            cal_header_bg=cal_header_bg, cal_header_text=cal_header_text,
            pill_border=pill_border, pill_fill=pill_fill,
            todo_bg=todo_bg, todo_text=todo_text,
            divider_color=divider_color,
        )

        if not ical_url:
            raise RuntimeError(
                "Please provide an iCal URL. "
                "In Google Calendar: Settings → your calendar → 'Secret address in iCal format'."
            )

        cal_data = self._fetch_ical(ical_url)
        today    = date.today()

        if cal_style == "agenda":
            agenda_end = today + timedelta(days=agenda_days)
            events_raw = self._get_events_with_time(cal_data, today, agenda_end)
        else:
            month_start = today.replace(day=1)
            next_month  = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
            month_end   = next_month - timedelta(days=1)
            events      = self._get_events(cal_data, month_start, month_end)

        # Todos
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

        # Render panels
        if cal_style == "agenda":
            cal_img = self._render_agenda(
                calendar_width, height, today, events_raw, font_size, language, colors
            )
        else:
            cal_img = self._render_calendar(
                calendar_width, height, today, month_start, month_end, events,
                font_size, language, colors
            )

        todo_img = self._render_todos(todo_width, height, todos, font_size, language, colors)

        img = Image.new("RGB", (width, height), "white")
        img.paste(cal_img,  (0, 0))
        img.paste(todo_img, (calendar_width, 0))

        draw = ImageDraw.Draw(img)
        draw.line([(calendar_width, 0), (calendar_width, height)],
                  fill=divider_color, width=2)
        return img

    # ------------------------------------------------------------------ #
    #  iCal helpers                                                        #
    # ------------------------------------------------------------------ #

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
                dt        = c.get("DTSTART").dt
                dt_end    = c.get("DTEND")
                is_allday = not isinstance(dt, datetime)
                d         = dt if is_allday else dt.date()
                summary   = str(c.get("SUMMARY", ""))
                time_str  = ""
                if not is_allday:
                    time_str = dt.strftime("%H:%M")
                    if dt_end:
                        end_dt = dt_end.dt
                        if isinstance(end_dt, datetime):
                            time_str += "–" + end_dt.strftime("%H:%M")
                events.append({"date": d, "dt": dt, "summary": summary,
                                "time_str": time_str, "allday": is_allday})
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

    # ------------------------------------------------------------------ #
    #  Google Keep                                                         #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    #  Google Tasks                                                        #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    #  Shared rendering helpers                                            #
    # ------------------------------------------------------------------ #

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

    def _draw_rounded_rect(self, draw, x0, y0, x1, y1, radius, fill=None, outline=None, width=1):
        r = radius
        if fill:
            draw.rectangle([x0 + r, y0,     x1 - r, y1    ], fill=fill)
            draw.rectangle([x0,     y0 + r, x1,     y1 - r], fill=fill)
            draw.ellipse([x0,       y0,       x0+2*r, y0+2*r], fill=fill)
            draw.ellipse([x1-2*r,   y0,       x1,     y0+2*r], fill=fill)
            draw.ellipse([x0,       y1-2*r,   x0+2*r, y1    ], fill=fill)
            draw.ellipse([x1-2*r,   y1-2*r,   x1,     y1    ], fill=fill)
        if outline:
            draw.rounded_rectangle([x0, y0, x1, y1], radius=r, outline=outline, width=width)

    def _truncate(self, draw, text, font, max_w):
        """Truncate text with ellipsis to fit max_w pixels."""
        if draw.textlength(text, font=font) <= max_w:
            return text
        while text and draw.textlength(text + "…", font=font) > max_w:
            text = text[:-1]
        return text.rstrip() + "…"

    # ------------------------------------------------------------------ #
    #  Grid calendar renderer                                              #
    # ------------------------------------------------------------------ #

    def _render_calendar(self, width, height, today, month_start, month_end,
                         events, font_size, language, colors):
        bg       = colors["cal_bg"]
        fg       = colors["cal_text"]
        hdr_bg   = colors["cal_header_bg"]
        hdr_fg   = colors["cal_header_text"]
        pill_bdr = colors["pill_border"]
        pill_fil = colors["pill_fill"]

        img  = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(img)
        bold, medium, small, tiny = self._load_fonts(font_size)

        day_names = DAY_NAMES_CS if language == "cs" else DAY_NAMES_EN
        padding   = 10
        y         = padding

        # Month header bar
        if language == "cs":
            header = f"{MONTH_NAMES_CS[month_start.month]} {month_start.year}"
        else:
            header = month_start.strftime("%B %Y")
        hdr_h = font_size + 12
        draw.rectangle([0, y, width, y + hdr_h], fill=hdr_bg)
        draw.text((width // 2, y + hdr_h // 2), header,
                  font=bold, fill=hdr_fg, anchor="mm")
        y += hdr_h + 4

        # Day-of-week row
        col_w = (width - 2 * padding) // 7
        for i, name in enumerate(day_names):
            x = padding + i * col_w + col_w // 2
            draw.text((x, y + font_size // 2), name,
                      font=medium, fill=fg, anchor="mm")
        y += font_size + 6
        draw.line([(padding, y), (width - padding, y)], fill=fg, width=1)
        y += 4

        # Grid
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
                    fill=hdr_bg
                )
                num_color = hdr_fg
            else:
                num_color = fg

            draw.text(
                (cell_x + col_w // 2, row_y + font_size // 2 + 2),
                str(current.day), font=medium, fill=num_color, anchor="mm"
            )

            # Event pills in cell (up to 2)
            if current in events:
                ey = row_y + font_size + 6
                for ev in events[current][:2]:
                    if ey + tiny.size + 2 > row_y + row_h:
                        break
                    pw = col_w - 4
                    label = self._truncate(draw, ev, tiny, pw - 4)
                    self._draw_rounded_rect(draw, cell_x + 2, ey,
                                            cell_x + 2 + pw, ey + tiny.size + 2,
                                            radius=2, fill=pill_fil)
                    draw.rounded_rectangle([cell_x + 2, ey,
                                            cell_x + 2 + pw, ey + tiny.size + 2],
                                           radius=2, outline=pill_bdr, width=1)
                    draw.text((cell_x + 4, ey + 1), label, font=tiny, fill=fg)
                    ey += tiny.size + 3

            col += 1
            if col > 6:
                col    = 0
                row_y += row_h
            current += timedelta(days=1)

        return img

    # ------------------------------------------------------------------ #
    #  Agenda renderer                                                     #
    # ------------------------------------------------------------------ #

    def _render_agenda(self, width, height, today, events, font_size, language, colors):
        bg       = colors["cal_bg"]
        fg       = colors["cal_text"]
        hdr_bg   = colors["cal_header_bg"]
        hdr_fg   = colors["cal_header_text"]
        pill_bdr = colors["pill_border"]
        pill_fil = colors["pill_fill"]

        img  = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(img)
        bold, medium, small, tiny = self._load_fonts(font_size)

        padding    = 10
        indent     = padding + 6
        pill_r     = 4
        pill_pad_x = 6
        pill_pad_y = 3
        event_gap  = 3
        date_h     = font_size + 10
        sep_gap    = 6
        y          = padding

        # Fixed time-slot width measured from sample string
        sample_time_w = int(draw.textlength("00:00–00:00", font=small))
        time_w        = sample_time_w + pill_pad_x
        summary_x     = indent + time_w + pill_pad_x + 4

        # Panel header
        header = "Nadcházející události" if language == "cs" else "Upcoming events"
        hdr_h  = font_size + 12
        draw.rectangle([0, y, width, y + hdr_h], fill=hdr_bg)
        draw.text((width // 2, y + hdr_h // 2), header,
                  font=bold, fill=hdr_fg, anchor="mm")
        y += hdr_h + 6

        if not events:
            msg = "Žádné události" if language == "cs" else "No upcoming events"
            draw.text((padding, y), msg, font=small, fill=fg)
            return img

        last_date = None
        pill_h    = font_size + pill_pad_y * 2

        for ev in events:
            # Day header
            if ev["date"] != last_date:
                if last_date is not None:
                    y += sep_gap
                    if y + date_h > height - padding:
                        break
                    draw.line([(padding, y), (width - padding, y)], fill=fg, width=1)
                    y += sep_gap

                if y + date_h > height - padding:
                    break

                d = ev["date"]
                if language == "cs":
                    dow   = DAY_SHORT_CS[d.isoweekday()]
                    label = f"{dow}  {d.day}. {MONTH_NAMES_CS[d.month][:3]}."
                else:
                    label = d.strftime("%a  %-d %b")

                if d == today:
                    draw.rectangle(
                        [padding - 2, y, width - padding, y + date_h - 2],
                        fill=hdr_bg
                    )
                    draw.text((padding + 4, y + date_h // 2), label,
                              font=bold, fill=hdr_fg, anchor="lm")
                else:
                    draw.text((padding, y + date_h // 2), label,
                              font=bold, fill=fg, anchor="lm")

                y        += date_h
                last_date = ev["date"]

            # Event pill
            if y + pill_h + pill_pad_y > height - padding:
                break

            px0, px1 = indent, width - padding
            py0, py1 = y, y + pill_h

            self._draw_rounded_rect(draw, px0, py0, px1, py1,
                                    radius=pill_r, fill=pill_fil)
            draw.rounded_rectangle([px0, py0, px1, py1],
                                   radius=pill_r, outline=pill_bdr, width=2)

            if ev["time_str"]:
                draw.text((px0 + pill_pad_x, py0 + pill_h // 2),
                          ev["time_str"], font=small, fill=fg, anchor="lm")

            summary = self._truncate(draw, ev["summary"], small, px1 - summary_x - pill_pad_x)
            draw.text((summary_x, py0 + pill_h // 2),
                      summary, font=small, fill=fg, anchor="lm")

            y += pill_h + event_gap

        return img

    # ------------------------------------------------------------------ #
    #  Todo renderer                                                       #
    # ------------------------------------------------------------------ #

    def _render_todos(self, width, height, todos, font_size=14, language="en", colors=None):
        if colors is None:
            colors = {}
        bg       = colors.get("todo_bg",      (255, 255, 255))
        fg       = colors.get("todo_text",    (0,   0,   0  ))
        pill_bdr = colors.get("pill_border",  (251, 189,  2 ))
        pill_fil = colors.get("pill_fill",    (255, 242, 173))

        img  = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(img)
        bold, medium, small, tiny = self._load_fonts(font_size)

        padding   = 10
        pill_r    = 4
        pill_padx = 6
        pill_pady = 3
        box_size  = max(10, font_size - 2)
        y         = padding

        # Header bar (reuse cal_header_bg/text so they match when same color is chosen)
        hdr_bg  = colors.get("cal_header_bg",   (0,   0,   0  ))
        hdr_fg  = colors.get("cal_header_text",  (255, 255, 255))
        hdr_h   = font_size + 12
        draw.rectangle([0, y, width, y + hdr_h], fill=hdr_bg)
        draw.text((width // 2, y + hdr_h // 2), "To-do",
                  font=bold, fill=hdr_fg, anchor="mm")
        y += hdr_h + 6

        if not todos:
            msg = "Vše hotovo!" if language == "cs" else "All done!"
            draw.text((padding, y), msg, font=small, fill=fg)
            return img

        pill_h   = max(box_size + pill_pady * 2, font_size + pill_pady * 2)
        item_gap = 4
        tx       = padding + pill_padx + box_size + 6

        for task in todos:
            if y + pill_h > height - padding:
                break

            px0, py0 = padding, y
            px1, py1 = width - padding, y + pill_h

            self._draw_rounded_rect(draw, px0, py0, px1, py1,
                                    radius=pill_r, fill=pill_fil)
            draw.rounded_rectangle([px0, py0, px1, py1],
                                   radius=pill_r, outline=pill_bdr, width=2)

            # Checkbox
            bx = px0 + pill_padx
            by = py0 + (pill_h - box_size) // 2
            draw.rectangle([bx, by, bx + box_size, by + box_size],
                           outline=fg, width=1)
            if task["completed"]:
                draw.rectangle([bx, by, bx + box_size, by + box_size], fill=fg)
                cx, cy = bx + box_size // 2, by + box_size // 2
                draw.line([(bx + 2, cy), (cx - 1, by + box_size - 3),
                           (bx + box_size - 2, by + 2)],
                          fill=bg, width=max(1, box_size // 6))

            # Label
            text_color = (128, 128, 128) if task["completed"] else fg
            label = self._truncate(draw, task["summary"], small, px1 - tx - pill_padx)
            draw.text((tx, py0 + pill_h // 2), label, font=small,
                      fill=text_color, anchor="lm")

            if task["completed"]:
                tw    = draw.textlength(label, font=small)
                mid_y = py0 + pill_h // 2
                draw.line([(tx, mid_y), (tx + tw, mid_y)], fill=text_color, width=1)

            y += pill_h + item_gap

        return img
