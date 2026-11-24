"""Mouse and keyboard bindings for the shortcut selectors."""
from __future__ import annotations

import ctypes

from app_config import normalize_key
from keyboard_hook import VK_TO_KEY


class KeyCapture:
    PROMPT = "请按新键 · Esc 取消 · Backspace 清空"

    def __init__(self, app):
        self.app = app
        self.box = None
        self.pending_box = self.pending_id = None
        self.last_press = None
        self.windows = app.root.tk.call("tk", "windowingsystem") == "win32"
        # Tk itself groups clicks up to 500 ms apart. Respect a slower Windows
        # setting too, before posting the list for a single text-area click.
        self.click_delay = max(500, ctypes.windll.user32.GetDoubleClickTime() if self.windows else 500) + 20
        app.root.bind("<ButtonPress-1>", self._outside_click, add="+")

    @property
    def active(self):
        return self.box is not None

    def bind(self, box):
        box.bind("<ButtonPress-1>", self._single_click, add="+")
        box.bind("<Double-Button-1>", self._double_click, add="+")
        box.bind("<KeyPress>", self._key_press, add="+")
        box.bind("<KeyRelease>", self._key_release, add="+")
        box.bind("<FocusOut>", self._leave, add="+")
        box.bind("<Unmap>", self._leave, add="+")
        box.bind("<MouseWheel>", lambda event: "break" if self.box is box else None, add="+")

    def _cancel_popup(self):
        if self.pending_id is not None:
            self.app.root.after_cancel(self.pending_id)
            self.pending_box = self.pending_id = None

    def _outside_click(self, event):
        if self.box is not event.widget and self.pending_box is not event.widget:
            self.cancel()

    def _leave(self, event):
        box = event.widget
        if self.box is box or self.pending_box is box:
            self.cancel()
        if self.last_press and self.last_press[0] is box:
            self.last_press = None

    def _can_edit(self, box):
        return (not box.instate(("disabled",)) and not self.app.profile_dialog_active
                and not self.app.macro_enabled and not self.app.stopping
                and self.app.pending_enable_config is None and not self.app.closing)

    def _single_click(self, event):
        box = event.widget
        self.cancel()
        if not self._can_edit(box):
            return "break"
        if "arrow" in box.identify(event.x, event.y):
            return None  # Keep the arrow's immediate native dropdown.
        box.focus_set()
        self.pending_box = box
        self.pending_id = self.app.root.after(self.click_delay, self._open_list, box)
        return "break"

    def _open_list(self, box):
        self.pending_box = self.pending_id = None
        if self._can_edit(box) and box.winfo_viewable() and box.focus_get() is box:
            box.event_generate("<Down>")

    def _double_click(self, event):
        box = event.widget
        self.cancel()
        if not self._can_edit(box):
            return "break"
        if "arrow" in box.identify(event.x, event.y):
            return None
        self.box = box
        self.app.refresh_hotkey_cache()
        self.app.discard_pending_presses()
        box.focus_set()
        self.previous_style = box.cget("style")
        self.previous_status = self.app.status_label.cget("text")
        box.configure(style="Capture.TCombobox")
        self._prompt(self.PROMPT)
        return "break"

    def _prompt(self, text):
        self.prompt = text
        self.app.status_label.config(text=text)

    def _key_press(self, event):
        box = event.widget
        if self.last_press == (box, event.keycode):
            return "break"  # A held Space or Tab must not reopen or traverse the control.
        if self.box is not box:
            return None
        if event.keysym == "Escape":
            self.cancel()
            return "break"
        if event.state & (0x1 | 0x4 | 0x8 | 0x20000):
            self._prompt("请松开修饰键，再按一个新键 · Esc 取消")
            return "break"
        if event.keysym in ("BackSpace", "Delete"):
            value = ""
        elif self.windows:
            # Use the same virtual-key codes as the actual global trigger.
            value = VK_TO_KEY.get(event.keycode)
        else:
            value = event.keysym.lower().replace("kp_", "num")
        for option in box.cget("values"):
            if normalize_key(option) == value:
                box.set(option)
                self.last_press = (box, event.keycode)
                self.cancel()
                box.event_generate("<<ComboboxSelected>>")
                break
        else:
            self._prompt("支持字母、数字、F1–F12、小键盘数字、Tab 和空格 · Esc 取消")
        return "break"

    def _key_release(self, event):
        if self.last_press == (event.widget, event.keycode):
            self.last_press = None
            return "break"

    def cancel(self):
        self._cancel_popup()
        if self.box is None:
            return
        self.box.configure(style=self.previous_style)
        if self.app.status_label.cget("text") == self.prompt:
            self.app.status_label.config(text=self.previous_status)
        self.app.discard_pending_presses()
        self.box = None
        self.app.refresh_hotkey_cache()
