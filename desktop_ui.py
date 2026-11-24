"""Two illustrated settings pages with native, keyboard-accessible form controls."""

import sys
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

from app_config import CW_FIELDS, FEITIAN_GROUPS, KEY_FIELDS, KEY_OPTIONS, SKILLS, TARGET_MODES
from desktop_art import Backdrops


SCHOOLS = {"yunchang": "云裳心经", "feitian": "经典飞天承华"}
PALETTES = {
    "yunchang": ("#8f385a", "#f6dce5", "#fff3f6", "#b46c80"),
    "feitian": ("#604790", "#e5def6", "#f6f3ff", "#927bb5"),
}
INK, MUTED = "#242637", "#545666"
DEFAULT_SIZE = (1160, 716)
MIN_CONTENT = (930, 620)
FONT = "Microsoft YaHei UI"
PAGE_TITLE, SECTION_TITLE, BODY, COLUMN_LABEL, CAPTION = 23, 18, 14, 13, 12
CARD_PAD = 18
FIELD_HEIGHT = 30


def configure_display():
    # Tk 8.6 measures fonts in the startup display's DPI. Request the matching
    # Windows coordinate space before Tk creates any handles, for crisp artwork.
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()


class DesktopUI:
    def __init__(self, app, resource_dir):
        self.app = app
        self.school = app.cfg.get("school", "yunchang")
        if self.school not in SCHOOLS:
            self.school = "yunchang"
        root = app.root
        self.scale = max(.85, float(root.tk.call("tk", "scaling")) * .75)
        available = (max(400, root.winfo_screenwidth() - 24), max(360, root.winfo_screenheight() - 86))
        width, height = [min(self.px(size), limit) for size, limit in zip(DEFAULT_SIZE, available)]
        root.geometry(f"{width}x{height}")
        root.minsize(min(self.px(800), width), min(self.px(490), height))
        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(root, bg="#f2ecf0", highlightthickness=0, bd=0,
                                width=width, height=height, takefocus=False)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vertical = ttk.Scrollbar(root, orient="vertical", command=self.canvas.yview)
        self.horizontal = ttk.Scrollbar(root, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=self.horizontal.set, yscrollcommand=self.vertical.set)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.art = Backdrops(resource_dir / "assets")
        icon_size = self.px(36)
        with Image.open(resource_dir / "assets" / "brand.png") as image:
            mask = Image.new("L", image.size)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width - 1, image.height - 1),
                                                    radius=image.width // 6, fill=255)
            image.putalpha(mask)
            self.brand_icon = ImageTk.PhotoImage(image.resize((icon_size, icon_size), Image.Resampling.LANCZOS),
                                                 master=self.canvas)
        self.widgets = {}
        self.windows = {}
        self.visible = set()
        self.pending_draw = None
        self.last_layout = None
        self.background = self.canvas.create_image(0, 0, anchor="nw")
        self.style = ttk.Style(root)
        self.style.theme_use("clam")
        self._build_controls()
        self.select(self.school)
        self.canvas.bind("<Configure>", self._queue_draw)
        self.canvas.bind("<Destroy>", self._on_destroy)

    def px(self, logical):
        return round(logical * self.scale)

    def font(self, size, bold=False):
        return (FONT, -self.px(size), "bold" if bold else "normal")

    def _build_controls(self):
        app, canvas = self.app, self.canvas
        for school, name in SCHOOLS.items():
            self.widgets[school] = ttk.Button(canvas, text=name, command=lambda s=school: app.select_school(s))
        self.widgets["status"] = app.status_label = tk.Label(
            canvas, text="● 已停止", fg=MUTED, font=self.font(COLUMN_LABEL), anchor="w", justify="left",
            bd=0, highlightthickness=0, padx=self.px(7))
        fields = [*KEY_FIELDS, *(key for group in FEITIAN_GROUPS for key, _, _ in group),
                  *(key for key, _ in CW_FIELDS), "ft_plant", "ft_macro_key"]
        options = [key.upper() for key in KEY_OPTIONS]
        for key in fields:
            cb = ttk.Combobox(canvas, values=options, state="readonly", font=self.font(BODY))
            cb.set(app.cfg[key].upper())
            cb.bind("<<ComboboxSelected>>", lambda event: app.refresh_hotkey_cache())
            self.widgets[key] = app.key_combos[key] = cb
            setattr(app, f"cb_{key}", cb)
            app.all_combos.append(cb)
        app.cb_target_mode = ttk.Combobox(canvas, state="readonly", values=list(TARGET_MODES.values()),
                                          font=self.font(BODY))
        app.cb_target_mode.set(TARGET_MODES.get(app.cfg["target_mode"], app.cfg["target_mode"]))
        app.cb_target_mode.bind("<<ComboboxSelected>>", lambda event: app.update_target_state())
        self.widgets["target_mode"] = app.cb_target_mode
        app.all_combos.append(app.cb_target_mode)
        for key in ("delay_ms", "loop_interval_s", "target_delay_ms", "ft_loop_ms", "ft_burst_ms"):
            entry = ttk.Entry(canvas, font=self.font(BODY))
            entry.insert(0, app.cfg[key])
            self.widgets[key] = app.entries[key] = entry
            app.all_entries.append(entry)
        # Keep the BooleanVars on the controller so imported profiles use the same controls.
        app.ally_hx_low = tk.BooleanVar(canvas, app.cfg["ally_hx_low"] == "1")
        app.use_game_macro = tk.BooleanVar(canvas, app.cfg["ft_use_macro"] == "1")
        app.foreground_guard_enabled = tk.BooleanVar(canvas, app.cfg["foreground_guard"] == "1")
        for field, label, var, command in (
                ("ally_hx_low", "回雪前切到最低血量", app.ally_hx_low, None),
                ("ft_use_macro", "使用游戏内爆发宏", app.use_game_macro, app.update_macro_fields),
                ("foreground_guard", "前台保护", app.foreground_guard_enabled, None)):
            check = tk.Checkbutton(canvas, text=label, variable=var, command=command,
                                   font=self.font(BODY), anchor="w", padx=3, bd=0, highlightthickness=1)
            self.widgets[field] = check
            if field != "ally_hx_low":
                app.config_buttons.append(check)
        app.ally_check = self.widgets["ally_hx_low"]
        app.foreground_check = self.widgets["foreground_guard"]
        for key, label, command in (("import", "导入配置", app.import_config),
                                     ("export", "导出配置", app.export_config),
                                     ("save", "保存当前", app.save_current_config)):
            btn = ttk.Button(canvas, text=label, command=command,
                             style="Primary.TButton" if key == "save" else "Action.TButton")
            self.widgets[key] = btn
            app.config_buttons.append(btn)
        app.update_target_state()
        app.update_macro_fields()
        for key, widget in self.widgets.items():
            self.windows[key] = canvas.create_window(0, 0, window=widget, anchor="w", state="hidden")
            widget.bind("<FocusIn>", self._reveal_focus, add="+")

    def select(self, school):
        self.school = school
        accent, soft, paper, self.rule = PALETTES[school]
        self.accent = accent
        self.paper = paper
        self.divider = "#dfd3da" if school == "yunchang" else "#dad4e4"
        st = self.style
        neutral, edge = "#fffdfd", "#b9b5c0"
        for control in ("TCombobox", "TEntry", "Capture.TCombobox"):
            capturing = control.startswith("Capture.")
            color = soft if capturing else neutral
            inset = 3 if control.endswith("TCombobox") else 6
            st.configure(control, padding=(self.px(inset), 0), borderwidth=1, arrowsize=self.px(12),
                         relief="flat", foreground=INK, arrowcolor=MUTED, fieldbackground=color,
                         background=color, bordercolor=edge, lightcolor=color, darkcolor=color)
            st.map(control, fieldbackground=[("disabled", "#ebe8ed"), ("readonly", color)],
                   foreground=[("disabled", "#676371"), ("!disabled", INK)],
                   bordercolor=[("focus", accent), ("active", self.rule)],
                   selectbackground=[("readonly", soft)], selectforeground=[("readonly", INK)])
        self.app.root.option_add("*TCombobox*Listbox.font", self.font(BODY))
        self.app.root.option_add("*TCombobox*Listbox.selectBackground", accent)
        for name, fill, fg, hover, anchor, font in (
                ("Nav", paper, INK, soft, "w", self.font(15)),
                ("Selected", soft, accent, soft, "w", self.font(15, True)),
                ("Action", neutral, INK, paper, "center", self.font(BODY)),
                ("Primary", accent, "#ffffff", self.rule, "center", self.font(BODY, True))):
            style = name + ".TButton"
            st.configure(style, background=fill, foreground=fg, font=font, anchor=anchor,
                         padding=(self.px(13), 0), relief="flat", borderwidth=int(anchor == "center"),
                         bordercolor=edge if name == "Action" else fill, lightcolor=fill, darkcolor=fill,
                         focusthickness=1, focuscolor=self.rule if name != "Primary" else neutral)
            st.map(style, background=[("disabled", "#e5e2e8"), ("pressed", hover), ("active", hover)],
                   foreground=[("disabled", "#6a6570")])
        for name in SCHOOLS:
            self.widgets[name].configure(style="Selected.TButton" if name == school else "Nav.TButton")
        for name in ("ally_hx_low", "ft_use_macro", "foreground_guard"):
            self.widgets[name].configure(bg=paper, fg=INK, activebackground=soft, activeforeground=INK,
                                         selectcolor=neutral, disabledforeground="#777180",
                                         highlightbackground=paper, highlightcolor=accent)
        self.app.status_label.configure(bg=paper)
        self.last_layout = None
        if self.canvas.winfo_ismapped():
            self.draw()

    def lock_navigation(self, locked):
        for name in SCHOOLS:
            self.widgets[name].configure(state="disabled" if locked and name != self.school else "normal")

    def _queue_draw(self, event=None):
        if self.pending_draw is None:
            self.pending_draw = self.canvas.after_idle(self.draw)

    def _on_destroy(self, event):
        if self.pending_draw is not None:
            self.canvas.after_cancel(self.pending_draw)
            self.pending_draw = None

    def _wheel(self, event):
        if self.vertical.winfo_ismapped():
            self.canvas.yview_scroll(-int(event.delta / 120), "units")
            return "break"

    def _reveal_focus(self, event):
        # Tab traversal must reach offscreen controls in small windows.
        if not self.last_layout:
            return
        widget = event.widget
        for axis, total in zip(("x", "y"), self.last_layout[1:]):
            if axis == "x":
                start, end = widget.winfo_x(), widget.winfo_x() + widget.winfo_width()
                available, view = self.canvas.winfo_width(), self.canvas.canvasx(0)
                move = self.canvas.xview_moveto
            else:
                start, end = widget.winfo_y(), widget.winfo_y() + widget.winfo_height()
                available, view = self.canvas.winfo_height(), self.canvas.canvasy(0)
                move = self.canvas.yview_moveto
            if start < 0:
                move(max(0, (start + view - 3) / total))
            elif end > available:
                move((end + view + 3 - available) / total)

    def text(self, x, y, value, size=BODY, *, bold=False, color=INK, anchor="w"):
        self.canvas.create_text(self.px(x), self.px(y), text=value, fill=color, anchor=anchor,
                                font=self.font(size, bold), tags="ink")

    def line(self, x, y, right):
        self.canvas.create_line(self.px(x), self.px(y), self.px(right), self.px(y),
                                fill=self.divider, tags="ink")

    def control(self, name, x, y, width, height=FIELD_HEIGHT):
        item = self.windows[name]
        self.canvas.coords(item, self.px(x), self.px(y))
        self.canvas.itemconfigure(item, width=self.px(width), height=self.px(height), state="normal")
        self.visible.add(name)

    def draw(self):
        self.pending_draw = None
        canvas = self.canvas
        avail_w, avail_h = canvas.winfo_width(), canvas.winfo_height()
        if avail_w < 2 or avail_h < 2:
            return
        width = max(self.px(MIN_CONTENT[0]), avail_w)
        height = max(self.px(MIN_CONTENT[1]), avail_h)
        root = self.app.root
        if root.winfo_height() < self.px(MIN_CONTENT[1]) + (self.horizontal.winfo_reqheight() if width > avail_w else 0):
            self.vertical.grid(row=0, column=1, sticky="ns")
        else:
            self.vertical.grid_remove()
        if root.winfo_width() < self.px(MIN_CONTENT[0]) + (self.vertical.winfo_reqwidth() if height > avail_h else 0):
            self.horizontal.grid(row=1, column=0, sticky="ew")
        else:
            self.horizontal.grid_remove()
        stamp = self.school, width, height
        if stamp == self.last_layout:
            return
        self.last_layout = stamp
        canvas.configure(scrollregion=(0, 0, width, height))
        w, h = width / self.scale, height / self.scale
        rail = min(282, max(181, w * .213))
        left, right = rail + 20, w - 20
        mid = (left + right) / 2
        foot, bottom = h - 60, h - 75
        split = bottom - min(216, max(170, h * .265))
        top = 77
        # The illustration is strongest in the rail and gutters. Denser input areas
        # get a stronger veil. These opacities apply only to the background layer.
        sections = [(left, 10, right, top - 10, 10, .88),
                    (left, top, right, split - 12, 12, .87),
                    (left, split, mid - 6, bottom, 12, .83),
                    (mid + 6, split, right, bottom, 12, .86),
                    (left, foot, right, h - 10, 9, .92)]
        panels = [(*[self.px(n) for n in box[:5]], box[5]) for box in sections]
        composite = self.art.render(self.school, (width, height), self.px(rail), panels, self.accent)
        self.backdrop = ImageTk.PhotoImage(composite, master=canvas)
        canvas.itemconfigure(self.background, image=self.backdrop)
        canvas.delete("ink")
        old, self.visible = self.visible, set()
        self._navigation(rail, h)
        self._header(left, right)
        self._section_title(left, top, "快捷键设置")
        self.text(right - CARD_PAD, top + 24, "双击键位改键  ·  Esc 取消", CAPTION, color=MUTED, anchor="e")
        if self.school == "yunchang":
            self._yunchang(left, right, top, split - 12)
        else:
            self._feitian(left, right, top, split - 12)
        self._settings(left, right, split, bottom)
        self._footer(left, right, foot, h)
        for name in old - self.visible:
            canvas.itemconfigure(self.windows[name], state="hidden")

    def _navigation(self, rail, h):
        p = self.px
        self.canvas.create_image(p(18), p(33), image=self.brand_icon, anchor="w", tags=("ink", "brand"))
        self.text(62, 33, "妍姿助手", 21, bold=True)
        for i, name in enumerate(SCHOOLS):
            y = 91 + i * 43
            self.control(name, 18, y, rail - 33, 35)
            if self.school == name:
                self.canvas.create_rectangle(p(13), p(y - 16), p(16), p(y + 16),
                                             fill=self.accent, width=0, tags="ink")
        self.text(20, h - 43, "原作  @小柴胡", 12, color=MUTED)
        self.text(20, h - 23, "优化  @汜水月白", 12, color=MUTED)

    def _header(self, left, right):
        self.text(left + CARD_PAD, 38, SCHOOLS[self.school], PAGE_TITLE, bold=True)
        start = left + (134 if self.school == "yunchang" else 182)
        end = right - 177
        self.app.status_label.configure(wraplength=self.px(end - start - 14))
        # Native requested width keeps short status messages compact, while
        # wraplength gives long running sequences the rest of the header.
        self.control("status", start, 38, 0, 0)
        self.text(right - 158, 38, "总开关")
        self.control("toggle" if self.school == "yunchang" else "ft_toggle", right - 100, 38, 82)

    def _section_title(self, left, top, title):
        self.text(left + CARD_PAD, top + 24, title, SECTION_TITLE, bold=True)

    def _column_headers(self, left, right, top, columns):
        p = self.px
        self.canvas.create_rectangle(p(left + 10), p(top + 43), p(right - 10), p(top + 69),
                                     fill=self.paper, width=0, tags="ink")
        for x, title in columns:
            self.text(x, top + 56, title, COLUMN_LABEL, bold=True, color=MUTED)

    def _yunchang(self, left, right, top, bottom):
        x = left + CARD_PAD
        inner = right - left - 2 * CARD_PAD
        columns = [x + inner * fraction for fraction in (0, .22, .46, .73)]
        titles = ("技能", "普通触发键", "游戏第二快捷键", "最低血量专用触发键")
        self._column_headers(left, right, top, zip(columns, titles))
        start = top + 69
        step = (bottom - 8 - start) / len(SKILLS)
        width = min(150, inner * .20)
        for i, (label, prefix) in enumerate(SKILLS):
            y = start + (i + .5) * step
            if i:
                self.line(x, start + i * step, right - CARD_PAD)
            self.text(x, y, label)
            self.control(prefix + "1", columns[1], y, width)
            self.control(prefix + "2", columns[2], y, width)
            if prefix in ("sy", "xl"):
                self.control(prefix + "_low", columns[3], y, width)
            else:
                self.text(columns[3] + width / 2, y, "—", color=MUTED, anchor="center")

    def _feitian(self, left, right, top, bottom):
        # These are display rows. The engine's phase groups remain the source of
        # truth for the two confirmation points and the six ordered bindings.
        rows = (
            ("助手热键", (("ft_begin", "非橙武爆发"), ("ft_cw_begin", "橙武爆发"), ("ft_confirm", "确认／恢复"))),
            ("常态刷血", tuple((key, label) for key, label, _ in FEITIAN_GROUPS[1])),
            ("召宠", tuple((key, label) for key, label, _ in FEITIAN_GROUPS[2])),
            ("献祭召蝶", tuple((key, label) for key, label, _ in FEITIAN_GROUPS[3])),
            ("橙武收尾", tuple((key, label.removeprefix("游戏：")) for key, label in CW_FIELDS if key != "ft_cw_begin")),
        )
        x, end = left + CARD_PAD, right - CARD_PAD
        category_width = min(162, max(133, (end - x) * .19))
        fields_x = x + category_width
        cell = (end - fields_x) / 3
        width = max(80, min(104, cell * .38 + 6))
        start = top + 69
        step = (bottom - 8 - start) / len(rows)
        self._column_headers(left, right, top, ((x, "分类"), (fields_x, "动作与键位")))
        for i, (title, fields) in enumerate(rows):
            y = start + (i + .5) * step
            if i:
                self.line(fields_x if i == 3 else x, start + i * step, end)
            if i == 2:
                self.text(x, y + step / 2, "爆发流程", bold=True)
            if i in (2, 3):
                self.text(fields_x - 12, y, title, CAPTION, color=self.accent, anchor="e")
            else:
                self.text(x, y, title, bold=True)
            for j, (key, label) in enumerate(fields):
                field_left = fields_x + cell * j
                field_end = fields_x + cell * (j + 1) - (12 if j < 2 else 0)
                if key == "ft_pet":
                    label = "宠物栏蝶鸾"
                elif key == "ft_target_key":
                    label = "最低血量队友"
                self.text(field_left, y, label)
                self.control(key, field_end - width, y, width)

    def _settings(self, left, right, top, bottom):
        yun = self.school == "yunchang"
        mid = (left + right) / 2
        card = mid - left - 6
        other = mid + 6
        self._section_title(left, top, "选人设置" if yun else "爆发设置")
        self._section_title(other, top, "时间设置")
        first = top + 56
        gap = (bottom - 20 - first) / 3
        value_offset = max(118, card * .47)
        # All four settings cards use the same label column, value column and
        # baseline grid. Leave space after each value for its unit.
        field_width = min(186, card - value_offset - 52)
        if yun:
            left_fields = (("普通键选人模式", "target_mode"), ("选择最低血量", "target_low_key"),
                           ("选择盟友", "target_ally_key"))
            for i, (label, key) in enumerate(left_fields):
                y = first + i * gap
                self.text(left + CARD_PAD, y, label)
                self.control(key, left + value_offset, y, field_width)
            self.control("ally_hx_low", left + CARD_PAD - 4, first + 3 * gap, 174, 25)
            right_fields = (("接回雪延迟", "delay_ms", "ms"), ("二段回雪时间", "loop_interval_s", "s"),
                            ("选人后等待", "target_delay_ms", "ms"))
            tip = "上元、翔舞长按重复，松开停止重复。"
        else:
            self.control("ft_use_macro", left + CARD_PAD - 4, first, 164, 26)
            self.text(left + CARD_PAD, first + gap, "游戏内宏按键")
            self.control("ft_macro_key", left + value_offset, first + gap, field_width)
            self.text(left + CARD_PAD, first + 2 * gap, "手动爆发时，见圣蝎、碧蝶后依次确认。", CAPTION, color=MUTED)
            self.text(left + CARD_PAD, first + 3 * gap, "使用游戏宏时，见碧蝶后按通用确认键。", CAPTION, color=MUTED)
            right_fields = (("刷血按键间隔", "ft_loop_ms", "ms"), ("爆发按键间隔", "ft_burst_ms", "ms"),
                            ("迷仙引梦", "ft_plant", ""))
            tip = "迷仙引梦每 20 秒补放，留空关闭。"
        for i, (label, key, unit) in enumerate(right_fields):
            y = first + i * gap
            self.text(other + CARD_PAD, y, label)
            self.control(key, other + value_offset, y, field_width)
            if unit:
                self.text(other + value_offset + field_width + 9, y, unit, COLUMN_LABEL, color=MUTED)
        self.text(other + CARD_PAD, first + 3 * gap, tip, CAPTION, color=MUTED)

    def _footer(self, left, right, foot, h):
        y = (foot + h - 10) / 2
        self.control("foreground_guard", left + 14, y, 92, 26)
        self.text(left + 112, y, "在游戏窗口按总开关", 12, color=MUTED)
        for name, offset, width in (("import", 310, 85), ("export", 215, 85), ("save", 120, 104)):
            self.control(name, right - offset, y, width, 32)
