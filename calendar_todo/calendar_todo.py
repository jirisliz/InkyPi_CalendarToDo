import json
import logging
from datetime import datetime, date, timedelta
from icalendar import Calendar
import recurring_ical_events
import requests
from PIL import Image, ImageDraw, ImageFont
import os

from plugins.base_plugin.base_plugin import BasePlugin

logger = logging.getLogger(__name__)

# Full day/month names
DAY_FULL_CS    = ["", "Pondělí", "Úterý", "Středa", "Čtvrtek", "Pátek", "Sobota", "Neděle"]
DAY_FULL_EN    = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTH_FULL_CS  = ["", "Leden", "Únor", "Březen", "Duben", "Květen", "Červen",
                  "Červenec", "Srpen", "Září", "Říjen", "Listopad", "Prosinec"]
MONTH_FULL_EN  = ["", "January", "February", "March", "April", "May", "June",
                  "July", "August", "September", "October", "November", "December"]
# Short names still used in the grid header row (space is limited there)
DAY_SHORT_CS   = ["", "Po", "Út", "St", "Čt", "Pá", "So", "Ne"]
DAY_SHORT_EN   = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]

# Default calendar colours (border, fill) — cycled when multiple iCal feeds
DEFAULT_CAL_COLORS = [
    ("#FBBD02", "#FFF2AD"),  # yellow  (Google Keep)
    ("#1A73E8", "#D2E8FB"),  # blue
    ("#34A853", "#D4EDDA"),  # green
    ("#EA4335", "#FCDBD9"),  # red
    ("#9C27B0", "#EAD5F5"),  # purple
]


def hex_to_rgb(hex_str, fallback=(0, 0, 0)):
    try:
        h = hex_str.strip().lstrip("#")
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    except Exception:
        return fallback


class CalendarTodoPlugin(BasePlugin):
    """
    Landscape split-screen InkyPi plugin.
    Left : monthly grid OR agenda — supports multiple iCal feeds with individual colours.
    Right: todo list.
    """

    # ------------------------------------------------------------------ #
    #  Entry point                                                         #
    # ------------------------------------------------------------------ #

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
        cal_style       = settings.get("cal_style", "grid")
        font_size       = int(settings.get("font_size", 14))
        agenda_days     = int(settings.get("agenda_days", 14))
        language        = settings.get("language", "en")

        # Parse multiple iCal feeds from JSON stored in settings
        # Format: [{"url": "...", "border": "#FBBD02", "fill": "#FFF2AD"}, ...]
        ical_feeds = self._parse_ical_feeds(settings, ical_url)

        if not ical_feeds:
            raise RuntimeError(
                "Please provide at least one iCal URL in the calendar feeds section."
            )

        # Colours
        cal_bg               = hex_to_rgb(settings.get("cal_bg",             "#FFFFFF"), (255, 255, 255))
        cal_text             = hex_to_rgb(settings.get("cal_text",           "#000000"), (0,   0,   0  ))
        cal_header_bg        = hex_to_rgb(settings.get("cal_header_bg",      "#000000"), (0,   0,   0  ))
        cal_header_text      = hex_to_rgb(settings.get("cal_header_text",    "#FFFFFF"), (255, 255, 255))
        todo_bg              = hex_to_rgb(settings.get("todo_bg",            "#FFFFFF"), (255, 255, 255))
        todo_text            = hex_to_rgb(settings.get("todo_text",          "#000000"), (0,   0,   0  ))
        todo_pill_bdr        = hex_to_rgb(settings.get("todo_pill_border",   "#FBBD02"), (251, 189,  2 ))
        todo_pill_fil        = hex_to_rgb(settings.get("todo_pill_fill",     "#FFF2AD"), (255, 242, 173))
        keep_pill_bdr        = hex_to_rgb(settings.get("keep_pill_border",   "#FBBD02"), (251, 189,  2 ))
        keep_pill_fil        = hex_to_rgb(settings.get("keep_pill_fill",     "#FFF2AD"), (255, 242, 173))
        ical_todo_pill_bdr   = hex_to_rgb(settings.get("ical_todo_pill_border","#34A853"),(52, 168,  83))
        ical_todo_pill_fil   = hex_to_rgb(settings.get("ical_todo_pill_fill", "#D4EDDA"), (212, 237, 218))
        divider_color        = hex_to_rgb(settings.get("divider_color",      "#000000"), (0,   0,   0  ))

        colors = dict(
            cal_bg=cal_bg, cal_text=cal_text,
            cal_header_bg=cal_header_bg, cal_header_text=cal_header_text,
            todo_bg=todo_bg, todo_text=todo_text,
            todo_pill_border=todo_pill_bdr,     todo_pill_fill=todo_pill_fil,
            keep_pill_border=keep_pill_bdr,     keep_pill_fill=keep_pill_fil,
            ical_todo_pill_border=ical_todo_pill_bdr, ical_todo_pill_fill=ical_todo_pill_fil,
            divider_color=divider_color,
        )

        today = date.today()

        # Fetch and merge events from all feeds
        if cal_style == "agenda":
            agenda_end = today + timedelta(days=agenda_days)
            all_events = self._fetch_all_agenda(ical_feeds, today, agenda_end)
        else:
            month_start = today.replace(day=1)
            next_month  = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
            month_end   = next_month - timedelta(days=1)
            all_events  = self._fetch_all_grid(ical_feeds, month_start, month_end)

        # Todos
        if todo_source == "combined":
            # Merge Google Keep + iCal VTODOs into one sorted list
            todos = self._get_combined_todos(
                keep_email, keep_token, keep_note_title,
                ical_feeds, show_completed, num_todo_items, settings
            )
        elif todo_source == "keep":
            raw = self._get_keep_todos(keep_email, keep_token, keep_note_title,
                                       show_completed, num_todo_items)
            todos = [dict(t, source="keep") for t in raw]
        elif todo_source == "tasks":
            raw = self._get_google_tasks(tasks_api_key, tasks_list_id,
                                         show_completed, num_todo_items)
            todos = [dict(t, source="tasks") for t in raw]
        elif todo_source == "ical":
            try:
                first_cal = self._fetch_ical(ical_feeds[0]["url"])
                raw = self._get_ical_todos(first_cal, show_completed, num_todo_items)
                todos = [dict(t, source="ical") for t in raw]
            except Exception:
                todos = []
        else:
            todos = [
                {"summary": t.strip(), "completed": False, "source": "manual"}
                for t in manual_todos.splitlines() if t.strip()
            ][:num_todo_items]

        # Render
        if cal_style == "agenda":
            cal_img = self._render_agenda(
                calendar_width, height, today, all_events, font_size, language, colors
            )
        else:
            cal_img = self._render_calendar(
                calendar_width, height, today, month_start, month_end, all_events,
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
    #  Multi-feed helpers                                                  #
    # ------------------------------------------------------------------ #

    def _parse_ical_feeds(self, settings, legacy_ical_url):
        """
        Parse the ical_feeds JSON field.  Falls back to the legacy single ical_url.
        Returns list of dicts: [{url, border_rgb, fill_rgb}, ...]
        """
        feeds_json = settings.get("ical_feeds", "").strip()
        feeds = []

        if feeds_json:
            try:
                raw = json.loads(feeds_json)
                for i, entry in enumerate(raw):
                    url = entry.get("url", "").strip()
                    if not url:
                        continue
                    def_bdr, def_fil = DEFAULT_CAL_COLORS[i % len(DEFAULT_CAL_COLORS)]
                    feeds.append({
                        "url":        url,
                        "border_rgb": hex_to_rgb(entry.get("border", def_bdr)),
                        "fill_rgb":   hex_to_rgb(entry.get("fill",   def_fil)),
                        "name":       entry.get("name", f"Calendar {i+1}"),
                    })
            except Exception as e:
                logger.warning(f"Could not parse ical_feeds JSON: {e}")

        # Legacy single URL fallback
        if not feeds and legacy_ical_url:
            def_bdr, def_fil = DEFAULT_CAL_COLORS[0]
            feeds.append({
                "url":        legacy_ical_url,
                "border_rgb": hex_to_rgb(def_bdr),
                "fill_rgb":   hex_to_rgb(def_fil),
                "name":       "Calendar",
            })

        return feeds

    def _fetch_ical(self, url):
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            return Calendar.from_ical(resp.text)
        except Exception as e:
            raise RuntimeError(f"Failed to fetch calendar ({url}): {e}")

    def _fetch_all_grid(self, feeds, start, end):
        """
        Returns dict: {date: [{"summary", "border_rgb", "fill_rgb"}, ...]}
        """
        result = {}
        for feed in feeds:
            try:
                cal = self._fetch_ical(feed["url"])
                raw = recurring_ical_events.of(cal).between(
                    datetime(start.year, start.month, start.day),
                    datetime(end.year, end.month, end.day, 23, 59, 59),
                )
            except Exception as e:
                logger.warning(f"Grid fetch error for {feed['url']}: {e}")
                continue
            for c in raw:
                if c.name == "VEVENT":
                    dt = c.get("DTSTART").dt
                    d  = dt.date() if isinstance(dt, datetime) else dt
                    result.setdefault(d, []).append({
                        "summary":    str(c.get("SUMMARY", "")),
                        "border_rgb": feed["border_rgb"],
                        "fill_rgb":   feed["fill_rgb"],
                    })
        return result

    def _fetch_all_agenda(self, feeds, start, end):
        """
        Returns sorted list of event dicts with per-feed colour.
        """
        result = []
        for feed in feeds:
            try:
                cal = self._fetch_ical(feed["url"])
                raw = recurring_ical_events.of(cal).between(
                    datetime(start.year, start.month, start.day),
                    datetime(end.year, end.month, end.day, 23, 59, 59),
                )
            except Exception as e:
                logger.warning(f"Agenda fetch error for {feed['url']}: {e}")
                continue
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
                    result.append({
                        "date":       d,
                        "dt":         dt,
                        "summary":    summary,
                        "time_str":   time_str,
                        "allday":     is_allday,
                        "border_rgb": feed["border_rgb"],
                        "fill_rgb":   feed["fill_rgb"],
                    })
        result.sort(key=lambda e: (e["date"], e["time_str"]))
        return result

    # ------------------------------------------------------------------ #
    #  Todo data helpers                                                   #
    # ------------------------------------------------------------------ #

    def _get_ical_todos(self, cal, show_completed, limit):
        todos = []
        for c in cal.walk():
            if c.name == "VTODO":
                completed = str(c.get("STATUS", "")).upper() == "COMPLETED"
                if completed and not show_completed:
                    continue
                # Extract due date/time if present
                due_str  = ""
                due_sort = ""
                due_raw  = c.get("DUE")
                if due_raw:
                    dt = due_raw.dt
                    if isinstance(dt, datetime):
                        due_str  = dt.strftime("%d.%m %H:%M")
                        due_sort = dt.strftime("%Y%m%d%H%M")
                    elif isinstance(dt, date):
                        due_str  = dt.strftime("%d.%m")
                        due_sort = dt.strftime("%Y%m%d0000")
                todos.append({
                    "summary":   str(c.get("SUMMARY", "")),
                    "completed": completed,
                    "due_str":   due_str,
                    "due_sort":  due_sort,
                    "source":    "ical",
                })
                if len(todos) >= limit:
                    break
        return todos

    def _get_combined_todos(self, keep_email, keep_token, keep_note_title,
                            ical_feeds, show_completed, limit, settings):
        """
        Merge Google Keep checklist items + iCal VTODOs into one list.
        iCal tasks with a due date are sorted by due date first, then Keep items.
        Items from each source keep their colour identity for pill rendering.
        """
        combined = []

        # --- Keep items ---
        try:
            keep_raw = self._get_keep_todos(keep_email, keep_token, keep_note_title,
                                            show_completed, limit)
            for item in keep_raw:
                combined.append({
                    "summary":   item["summary"],
                    "completed": item["completed"],
                    "due_str":   "",
                    "due_sort":  "z",       # sorts after dated iCal items
                    "source":    "keep",
                })
        except Exception as e:
            logger.warning(f"Combined todo: Keep fetch failed: {e}")

        # --- iCal VTODOs (from all feeds) ---
        seen_urls = set()
        for feed in ical_feeds:
            url = feed["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            try:
                cal = self._fetch_ical(url)
                ical_raw = self._get_ical_todos(cal, show_completed, limit)
                for item in ical_raw:
                    combined.append(item)   # already has source="ical"
            except Exception as e:
                logger.warning(f"Combined todo: iCal fetch failed ({url}): {e}")

        # Sort: dated iCal items first (by due_sort), then undated / Keep
        combined.sort(key=lambda t: (t.get("due_sort") or "z", t["summary"]))

        # Completed items go to the bottom
        combined.sort(key=lambda t: t["completed"])

        return combined[:limit]

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
            draw.rectangle([x0+r, y0,   x1-r, y1  ], fill=fill)
            draw.rectangle([x0,   y0+r, x1,   y1-r], fill=fill)
            draw.ellipse([x0,     y0,     x0+2*r, y0+2*r], fill=fill)
            draw.ellipse([x1-2*r, y0,     x1,     y0+2*r], fill=fill)
            draw.ellipse([x0,     y1-2*r, x0+2*r, y1    ], fill=fill)
            draw.ellipse([x1-2*r, y1-2*r, x1,     y1    ], fill=fill)
        if outline:
            draw.rounded_rectangle([x0, y0, x1, y1], radius=r, outline=outline, width=width)

    def _truncate(self, draw, text, font, max_w):
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
        bg      = colors["cal_bg"]
        fg      = colors["cal_text"]
        hdr_bg  = colors["cal_header_bg"]
        hdr_fg  = colors["cal_header_text"]

        img  = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(img)
        bold, medium, small, tiny = self._load_fonts(font_size)

        # Full day names for grid header — use short to fit 7 columns
        day_short = DAY_SHORT_CS if language == "cs" else DAY_SHORT_EN
        padding   = 10
        y         = padding

        # Month header bar — full month name
        if language == "cs":
            header = f"{MONTH_FULL_CS[month_start.month]} {month_start.year}"
        else:
            header = f"{MONTH_FULL_EN[month_start.month]} {month_start.year}"

        hdr_h = font_size + 12
        draw.rectangle([0, y, width, y + hdr_h], fill=hdr_bg)
        draw.text((width // 2, y + hdr_h // 2), header,
                  font=bold, fill=hdr_fg, anchor="mm")
        y += hdr_h + 4

        # Day-of-week header row (short names fit; full names are too wide for 7 cols)
        col_w = (width - 2 * padding) // 7
        for i, name in enumerate(day_short):
            x = padding + i * col_w + col_w // 2
            draw.text((x, y + font_size // 2), name, font=medium, fill=fg, anchor="mm")
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
                    [cell_x+1, row_y+1, cell_x+col_w-2, row_y+row_h-2],
                    fill=hdr_bg
                )
                num_color = hdr_fg
            else:
                num_color = fg

            draw.text(
                (cell_x + col_w // 2, row_y + font_size // 2 + 2),
                str(current.day), font=medium, fill=num_color, anchor="mm"
            )

            if current in events:
                ey = row_y + font_size + 6
                for ev in events[current][:2]:
                    if ey + tiny.size + 2 > row_y + row_h:
                        break
                    pw    = col_w - 4
                    label = self._truncate(draw, ev["summary"], tiny, pw - 4)
                    b_rgb = ev["border_rgb"]
                    f_rgb = ev["fill_rgb"]
                    self._draw_rounded_rect(draw, cell_x+2, ey,
                                            cell_x+2+pw, ey+tiny.size+2,
                                            radius=2, fill=f_rgb)
                    draw.rounded_rectangle([cell_x+2, ey, cell_x+2+pw, ey+tiny.size+2],
                                           radius=2, outline=b_rgb, width=1)
                    draw.text((cell_x+4, ey+1), label, font=tiny, fill=fg)
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
        bg      = colors["cal_bg"]
        fg      = colors["cal_text"]
        hdr_bg  = colors["cal_header_bg"]
        hdr_fg  = colors["cal_header_text"]

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
            # Day header — full day name + full month name
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
                    dow   = DAY_FULL_CS[d.isoweekday()]
                    label = f"{dow}  {d.day}. {MONTH_FULL_CS[d.month]}"
                else:
                    dow   = DAY_FULL_EN[d.weekday()]
                    label = f"{dow}  {d.day} {MONTH_FULL_EN[d.month]}"

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

            # Event pill — per-feed colour
            if y + pill_h + pill_pad_y > height - padding:
                break

            pill_bdr = ev["border_rgb"]
            pill_fil = ev["fill_rgb"]
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
        bg              = colors.get("todo_bg",               (255, 255, 255))
        fg              = colors.get("todo_text",             (0,   0,   0  ))
        hdr_bg          = colors.get("cal_header_bg",         (0,   0,   0  ))
        hdr_fg          = colors.get("cal_header_text",       (255, 255, 255))

        # Default pill colours (used when source is "manual", "tasks", or unknown)
        default_bdr     = colors.get("todo_pill_border",      (251, 189,  2 ))
        default_fil     = colors.get("todo_pill_fill",        (255, 242, 173))
        # Per-source pill colours
        keep_bdr        = colors.get("keep_pill_border",      (251, 189,  2 ))
        keep_fil        = colors.get("keep_pill_fill",        (255, 242, 173))
        ical_bdr        = colors.get("ical_todo_pill_border", (52,  168,  83))
        ical_fil        = colors.get("ical_todo_pill_fill",   (212, 237, 218))

        img  = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(img)
        bold, medium, small, tiny = self._load_fonts(font_size)

        padding   = 10
        pill_r    = 4
        pill_padx = 6
        pill_pady = 3
        box_size  = max(10, font_size - 2)
        y         = padding

        # Header bar
        hdr_h = font_size + 12
        draw.rectangle([0, y, width, y + hdr_h], fill=hdr_bg)
        draw.text((width // 2, y + hdr_h // 2), "To-do",
                  font=bold, fill=hdr_fg, anchor="mm")
        y += hdr_h + 6

        if not todos:
            msg = "Vše hotovo!" if language == "cs" else "All done!"
            draw.text((padding, y), msg, font=small, fill=fg)
            return img

        # Pills with due dates are taller (two text lines)
        pill_h_single = max(box_size + pill_pady * 2, font_size + pill_pady * 2)
        pill_h_double = pill_h_single + tiny.size + 2
        item_gap      = 4
        tx            = padding + pill_padx + box_size + 6   # text start x

        for task in todos:
            source    = task.get("source", "manual")
            due_str   = task.get("due_str", "")
            has_due   = bool(due_str)
            pill_h    = pill_h_double if has_due else pill_h_single

            if y + pill_h > height - padding:
                break

            # Pill colours by source
            if source == "keep":
                p_bdr, p_fil = keep_bdr, keep_fil
            elif source == "ical":
                p_bdr, p_fil = ical_bdr, ical_fil
            else:
                p_bdr, p_fil = default_bdr, default_fil

            px0, py0 = padding, y
            px1, py1 = width - padding, y + pill_h

            self._draw_rounded_rect(draw, px0, py0, px1, py1,
                                    radius=pill_r, fill=p_fil)
            draw.rounded_rectangle([px0, py0, px1, py1],
                                   radius=pill_r, outline=p_bdr, width=2)

            # Source tag badge (tiny label at top-right of pill)
            tag_label = {"keep": "Keep", "ical": "Cal", "tasks": "Tasks"}.get(source, "")
            if tag_label:
                tag_w = int(draw.textlength(tag_label, font=tiny)) + 6
                tag_x = px1 - tag_w - 2
                tag_y = py0 + 2
                draw.rounded_rectangle([tag_x, tag_y, px1 - 2, tag_y + tiny.size + 2],
                                       radius=2, fill=p_bdr)
                draw.text((tag_x + 3, tag_y + 1), tag_label, font=tiny, fill=bg)

            # Checkbox
            bx = px0 + pill_padx
            by = py0 + (pill_h_single - box_size) // 2   # align to top line
            draw.rectangle([bx, by, bx+box_size, by+box_size], outline=fg, width=1)
            if task["completed"]:
                draw.rectangle([bx, by, bx+box_size, by+box_size], fill=fg)
                cx, cy = bx + box_size // 2, by + box_size // 2
                draw.line([(bx+2, cy), (cx-1, by+box_size-3), (bx+box_size-2, by+2)],
                          fill=bg, width=max(1, box_size // 6))

            text_color = (140, 140, 140) if task["completed"] else fg

            # Summary text — truncate to avoid tag badge
            max_w = (tag_x if tag_label else px1) - tx - pill_padx - 4
            label = self._truncate(draw, task["summary"], small, max_w)

            if has_due:
                # Two-line layout: summary top, due date bottom
                text_top = py0 + pill_pady + 1
                draw.text((tx, text_top), label, font=small, fill=text_color)
                due_y = text_top + small.size + 2
                draw.text((tx, due_y), "📅 " + due_str if False else "⏰ " + due_str,
                          font=tiny, fill=text_color)
                # plain version without emoji for e-ink compatibility:
                draw.text((tx, due_y), due_str, font=tiny, fill=p_bdr)
            else:
                draw.text((tx, py0 + pill_h // 2), label, font=small,
                          fill=text_color, anchor="lm")

            if task["completed"]:
                tw    = draw.textlength(label, font=small)
                mid_y = (py0 + pill_pady + small.size // 2) if has_due else (py0 + pill_h // 2)
                draw.line([(tx, mid_y), (tx+tw, mid_y)], fill=text_color, width=1)

            y += pill_h + item_gap

        return img
