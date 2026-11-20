"""JumpServer Watchdog — native Windows operator console."""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image

from client import WatchdogClient
from radar import radar_art
from theme import (
    ACCENT, ACCENT_HOVER, ACTION_TEXT, BG, BRAND, FOCUS, GOOD, HERO, HOVER, INSET,
    LINE, MUTED, PANEL, RAIL, TEXT, THEME_LABELS, VIOLET, WARN, WARN_LINE,
    load_appearance, save_appearance,
)

DEFAULT_BASE = "http://127.0.0.1:8092"
PRESETS = {"5 分钟": 5, "10 分钟": 10, "15 分钟": 15, "30 分钟": 30, "暂停定时": 0}
SAMPLES = [
    "JumpServer远程应用发布机怎么部署",
    "登录提示配置文件有问题无法登录 DOMAINS",
    "Tinker 离线怎么办",
    "资产连接超时怎么排查",
]

FONT = "Microsoft YaHei UI"
MONO = "Consolas"
LOGO_PATH = Path(__file__).resolve().parent / "assets" / "jumpserver-logo-white.png"


def _fix_tcl_env() -> None:
    if getattr(sys, "frozen", False):
        return
    for root in (
        Path(sys.base_prefix),
        Path(r"D:\Python313"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python313",
    ):
        tcl = root / "tcl" / "tcl8.6"
        tk_ = root / "tcl" / "tk8.6"
        if (tcl / "init.tcl").is_file() and (tk_ / "tk.tcl").is_file():
            os.environ.setdefault("TCL_LIBRARY", str(tcl))
            os.environ.setdefault("TK_LIBRARY", str(tk_))
            break


class App(ctk.CTk):
    # Keep the native title bar so a theme switch doesn't hide and remap the window.
    _deactivate_windows_window_header_manipulation = True

    def __init__(self) -> None:
        self._appearance = load_appearance()
        ctk.set_appearance_mode(self._appearance)
        super().__init__()
        self.title("JumpServer · Watchdog 值班台")
        self.geometry("1120x680")
        self.minsize(960, 660)
        self.configure(fg_color=BG)
        if sys.platform == "win32":
            try:
                self.iconbitmap(str(LOGO_PATH.with_name("watchdog.ico")))
            except tk.TclError:
                pass

        self.client = WatchdogClient(DEFAULT_BASE)
        self._online = False
        self._busy = False
        self._page = ""
        self._auto_job: str | None = None
        self._last_suggest: dict | None = None
        self._health: dict = {}
        self._results: queue.Queue = queue.Queue()
        self._buttons: list[tuple[ctk.CTkButton, str]] = []
        self._sections: dict[str, ctk.CTkFrame] = {}
        self._nav: dict[str, ctk.CTkButton] = {}
        self._feed_labels: list[ctk.CTkLabel] = []
        self._logo: ctk.CTkImage | None = None

        self.base_var = tk.StringVar(value=DEFAULT_BASE)
        self.interval_choice = tk.StringVar(value="5 分钟")
        self.custom_min = tk.StringVar(value="5")
        self.gui_auto = tk.BooleanVar(value=False)
        self.log_var = tk.StringVar(value="等待连接 Watchdog 服务")
        self.status_var = tk.StringVar(value="未检测连接。默认服务地址：" + DEFAULT_BASE)

        self._build()
        self._show("duty")
        self._update_buttons()
        self.after(40, self._drain_results)
        self.after(200, self.refresh_status)

    def _label(self, parent, text="", *, size=13, color=TEXT, bold=False, family=FONT, **kwargs):
        return ctk.CTkLabel(
            parent, text=text, text_color=color,
            font=ctk.CTkFont(family=family, size=size, weight="bold" if bold else "normal"),
            anchor="w", justify="left", height=0, **kwargs,
        )

    def _panel(self, parent, **kwargs):
        return ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=12, border_width=1,
                            border_color=LINE, **kwargs)

    def _button(self, parent, text, command, *, style="quiet", requires="", **kwargs):
        colors = {
            "quiet": (INSET, HOVER, TEXT, LINE),
            "primary": (ACCENT, ACCENT_HOVER, ACTION_TEXT, ACCENT),
            "send": (INSET, HOVER, WARN, WARN_LINE),
        }
        fill, hover, ink, border = colors[style]
        widget = ctk.CTkButton(
            parent, text=text, command=command, fg_color=fill, hover_color=hover,
            text_color=ink, text_color_disabled=MUTED, border_color=border, border_width=1,
            height=36, corner_radius=7, font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            **kwargs,
        )
        widget.bind("<FocusIn>", lambda _e: widget.configure(border_color=FOCUS, border_width=2))
        widget.bind("<FocusOut>", lambda _e: widget.configure(border_color=border, border_width=1))
        widget.bind("<Return>", lambda _e: widget.invoke())
        widget.bind("<space>", lambda _e: widget.invoke())
        # Tk traverses the button frame; the bindings above operate on its children.
        widget.tk.call(widget._w, "configure", "-takefocus", "1")
        tk.Misc.bind(widget, "<FocusIn>", lambda _e: widget.configure(border_color=FOCUS, border_width=2))
        tk.Misc.bind(widget, "<FocusOut>", lambda _e: widget.configure(border_color=border, border_width=1))
        tk.Misc.bind(widget, "<Return>", lambda _e: widget.invoke())
        tk.Misc.bind(widget, "<space>", lambda _e: widget.invoke())
        if requires:
            self._buttons.append((widget, requires))
        return widget

    def _entry(self, parent, variable, **kwargs):
        return ctk.CTkEntry(
            parent, textvariable=variable, height=36, fg_color=INSET, border_color=LINE,
            text_color=TEXT, font=ctk.CTkFont(family=FONT, size=13), **kwargs,
        )

    def _textbox(self, parent, *, height=80, width=200, family=FONT):
        return ctk.CTkTextbox(
            parent, height=height, width=width, fg_color=INSET, border_width=1, border_color=LINE,
            text_color=TEXT, font=ctk.CTkFont(family=family, size=13), wrap="word",
        )

    def _build(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        rail = ctk.CTkFrame(self, fg_color=RAIL, corner_radius=0, width=202)
        rail.grid(row=0, column=0, sticky="nsew")
        rail.grid_propagate(False)
        rail.grid_columnconfigure(0, weight=1)
        rail.grid_rowconfigure(5, weight=1)

        brand = ctk.CTkFrame(rail, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=16, pady=(24, 26))
        try:
            with Image.open(LOGO_PATH) as image:
                self._logo = ctk.CTkImage(light_image=image.copy(), size=(27, 26))
            plate = ctk.CTkFrame(brand, width=42, height=42, fg_color=BRAND, corner_radius=10)
            plate.pack(side="left", padx=(0, 10))
            plate.pack_propagate(False)
            ctk.CTkLabel(plate, image=self._logo, text="").pack(expand=True)
        except OSError:
            pass
        wordmark = ctk.CTkFrame(brand, fg_color="transparent")
        wordmark.pack(side="left", fill="x")
        self._label(wordmark, "JumpServer", size=17, bold=True, family="Segoe UI").pack(fill="x")
        self._label(wordmark, "Watchdog Console", size=11, color=MUTED, family="Segoe UI").pack(fill="x")
        self._label(rail, "工作区", size=12, color=MUTED).grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 8))
        for row, (key, title) in enumerate(
            (("duty", "值班台"), ("qa", "模拟问答"), ("settings", "连接设置")), start=2,
        ):
            btn = self._button(rail, title, lambda page=key: self._show(page), anchor="w")
            btn.grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 6))
            self._nav[key] = btn

        info = ctk.CTkFrame(rail, fg_color="transparent")
        info.grid(row=6, column=0, sticky="ew", padx=20, pady=(0, 22))
        ctk.CTkFrame(info, fg_color=LINE, height=1).pack(fill="x", pady=(0, 12))
        self._label(info, "Watchdog Console", size=13, bold=True, family="Segoe UI").pack(fill="x")
        self.rail_addr = self._label(info, "127.0.0.1:8092", size=12, color=MUTED, family=MONO, wraplength=160)
        self.rail_addr.pack(fill="x")

        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=22, pady=(14, 9))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(main, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        header.grid_columnconfigure(0, weight=1)
        self.title_lbl = self._label(header, size=29, bold=True)
        self.title_lbl.grid(row=0, column=0, sticky="w")
        self.subtitle_lbl = self._label(header, size=13, color=MUTED)
        self.subtitle_lbl.grid(row=1, column=0, sticky="w", pady=(2, 0))
        theme_switch = self._panel(header)
        theme_switch.grid(row=0, column=1, rowspan=2, sticky="e")
        self._theme_buttons = {}
        for mode, label in THEME_LABELS.items():
            button = self._button(theme_switch, label, lambda selected=mode: self._switch_theme(selected), width=58)
            button.pack(side="left", padx=3, pady=3)
            self._theme_buttons[mode] = button
        self._paint_theme_switch()

        status = self._panel(main)
        status.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        self._metrics = {}
        for col, (key, label) in enumerate((
            ("link", "服务连接"), ("source", "消息源"), ("interval", "定时扫描"), ("safe", "安全模式"),
        )):
            status.grid_columnconfigure(col, weight=1, uniform="metric")
            cell = ctk.CTkFrame(status, fg_color="transparent")
            cell.grid(row=0, column=col, sticky="nsew", padx=14, pady=9)
            self._label(cell, label, size=12, color=MUTED).pack(fill="x")
            value = self._label(cell, "未检测" if key == "link" else "—", size=15, bold=True)
            value.pack(fill="x")
            self._metrics[key] = value

        deck = ctk.CTkFrame(main, fg_color="transparent")
        deck.grid(row=2, column=0, sticky="nsew")
        deck.grid_columnconfigure(0, weight=1)
        deck.grid_rowconfigure(0, weight=1)
        for key, build in (("duty", self._build_duty), ("qa", self._build_qa), ("settings", self._build_settings)):
            page = ctk.CTkFrame(deck, fg_color="transparent")
            page.grid(row=0, column=0, sticky="nsew")
            self._sections[key] = page
            build(page)
            page.grid_remove()

        self._label(main, textvariable=self.log_var, size=12, color=MUTED).grid(
            row=3, column=0, sticky="ew", pady=(8, 0),
        )

    def _paint_theme_switch(self) -> None:
        for mode, button in self._theme_buttons.items():
            selected = mode == self._appearance
            button.configure(
                fg_color=ACCENT if selected else INSET,
                text_color=ACTION_TEXT if selected else TEXT,
                hover_color=ACCENT_HOVER if selected else HOVER,
            )

    def _switch_theme(self, mode: str) -> None:
        if mode == self._appearance:
            return
        self._appearance = mode
        ctk.set_appearance_mode(mode)
        self._paint_theme_switch()
        if not save_appearance(mode):
            self._set_log("主题已切换，无法保存偏好")

    def _build_radar(self, page) -> None:
        hero = ctk.CTkFrame(page, fg_color=HERO, border_color=LINE, border_width=1, corner_radius=15)
        hero.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        hero.grid_columnconfigure(0, weight=1)

        brief = ctk.CTkFrame(hero, fg_color="transparent")
        brief.grid(row=0, column=0, sticky="nsew", padx=(17, 8), pady=8)
        self._label(brief, "待跟进客户群", size=13, color=VIOLET, bold=True).pack(fill="x")
        self.pending_count = self._label(brief, "—", size=38, bold=True, family="Segoe UI")
        self.pending_count.pack(fill="x", pady=(0, 4))
        self.scan_button = self._button(brief, "立即扫描", self.do_scan, style="primary", requires="online", width=105)
        self.scan_button.pack(anchor="w")

        self._radar = ctk.CTkImage(light_image=radar_art(False), dark_image=radar_art(True), size=(360, 136))
        ctk.CTkLabel(hero, text="", image=self._radar, corner_radius=10).grid(row=0, column=1, padx=5, pady=5)
        self.scan_progress = ctk.CTkProgressBar(
            hero, mode="indeterminate", height=3, progress_color=ACCENT, fg_color=HERO,
        )
        self.scan_progress.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 5))
        self.scan_progress.grid_remove()

    def _build_duty(self, page) -> None:
        page.grid_columnconfigure(0, weight=1)
        page.grid_columnconfigure(1, weight=0)
        page.grid_rowconfigure(1, weight=1)
        self._build_radar(page)
        pending = self._panel(page)
        pending.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        pending.grid_columnconfigure(0, weight=1)
        pending.grid_rowconfigure(2, weight=1)

        top = ctk.CTkFrame(pending, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 8))
        labels = ctk.CTkFrame(top, fg_color="transparent")
        labels.pack(side="left", fill="x", expand=True)
        self._label(labels, "待跟进清单", size=17, bold=True).pack(fill="x")
        self.pending_summary = self._label(labels, "尚未读取清单", size=12, color=MUTED)
        self.pending_summary.pack(fill="x")
        self._button(top, "刷新清单", self.refresh_pending, requires="online", width=94).pack(side="right")

        table_head = ctk.CTkFrame(pending, fg_color=INSET, corner_radius=6)
        table_head.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 3))
        self._label(table_head, "客户群 / 最近发言", size=12, color=MUTED).pack(side="left", padx=12, pady=3)
        self._label(table_head, "等待时长", size=12, color=MUTED).pack(side="right", padx=12, pady=3)

        self.feed = ctk.CTkScrollableFrame(
            pending, fg_color="transparent", corner_radius=0,
            scrollbar_button_color=LINE, scrollbar_button_hover_color=MUTED,
        )
        self.feed.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 10))
        self.feed.bind("<Configure>", self._resize_feed)
        self._pending_empty("连接后将自动读取待跟进清单。")

        activity = self._panel(page, width=220)
        activity.grid(row=1, column=1, sticky="nsew")
        activity.grid_propagate(False)
        activity.grid_columnconfigure(0, weight=1)
        activity.grid_rowconfigure(1, weight=1)
        self._label(activity, "操作回执", size=17, bold=True).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 8))
        self.activity = self._textbox(activity, height=62, family=MONO)
        self.activity.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 10))
        self._write_text(self.activity, "扫描和发送结果会显示在这里。")
        self._button(activity, "发送待跟进表", self.do_send_digest, style="send", requires="online").grid(
            row=2, column=0, sticky="ew", padx=14,
        )
        self._label(activity, "手动扫描也可能按服务配置发送提醒。", color=MUTED, size=12, wraplength=188).grid(
            row=3, column=0, sticky="ew", padx=14, pady=(8, 12),
        )

    def _build_qa(self, page) -> None:
        form = self._panel(page)
        form.pack(fill="x")
        form.grid_columnconfigure((0, 1), weight=1, uniform="form")
        self.q_var = tk.StringVar(value=SAMPLES[0])
        self.group_var = tk.StringVar(value="【JS】GUI模拟群")
        self._label(form, "样例问题", size=12, color=MUTED).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 0))
        self._label(form, "模拟群名", size=12, color=MUTED).grid(row=0, column=1, sticky="ew", padx=14, pady=(12, 0))
        self._menu(form, self.q_var, SAMPLES, self._on_sample).grid(row=1, column=0, sticky="ew", padx=14, pady=(2, 6))
        self._entry(form, self.group_var).grid(row=1, column=1, sticky="ew", padx=14, pady=(2, 6))
        self._label(form, "客户问题", size=12, color=MUTED).grid(row=2, column=0, sticky="ew", padx=14)
        self.q_text = self._textbox(form, height=69)
        self.q_text.grid(row=3, column=0, columnspan=2, sticky="ew", padx=14, pady=(2, 10))
        self.q_text.insert("1.0", SAMPLES[0])

        buttons = ctk.CTkFrame(form, fg_color="transparent")
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 12))
        self._button(buttons, "仅生成建议", self.do_suggest, style="primary", requires="online", width=100).pack(side="left", padx=(0, 8))
        self._button(buttons, "生成并推企微", self.do_suggest_and_wecom, style="send", requires="online", width=112).pack(side="left", padx=(0, 8))
        self._button(buttons, "推送上次结果", self.do_wecom_last, style="send", requires="suggestion", width=112).pack(side="left")
        self._button(buttons, "清空输出", self.clear_out, width=85).pack(side="right")

        result = self._panel(page)
        result.pack(fill="both", expand=True, pady=(10, 0))
        self._label(result, "回复建议", size=17, bold=True).pack(fill="x", padx=14, pady=(9, 4))
        self.out = self._textbox(result, height=94)
        self.out.pack(fill="both", expand=True, padx=14, pady=(0, 13))
        self.clear_out()

    def _menu(self, parent, variable, values, command):
        return ctk.CTkOptionMenu(
            parent, variable=variable, values=list(values), command=command, height=36,
            fg_color=INSET, button_color=HOVER, button_hover_color=LINE,
            dropdown_fg_color=RAIL, dropdown_hover_color=HOVER, text_color=TEXT,
            font=ctk.CTkFont(family=FONT, size=13), dropdown_font=ctk.CTkFont(family=FONT, size=13),
        )

    def _build_settings(self, page) -> None:
        link = self._panel(page)
        link.pack(fill="x")
        self._label(link, "Watchdog 服务地址", bold=True, size=17).pack(fill="x", padx=14, pady=(10, 4))
        row = ctk.CTkFrame(link, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(0, 4))
        entry = self._entry(row, self.base_var)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._button(row, "检测连接", self.ping, requires="idle", width=95).pack(side="right")
        self._label(link, textvariable=self.status_var, color=MUTED, size=12, wraplength=580).pack(
            fill="x", padx=14, pady=(3, 10),
        )

        interval = self._panel(page)
        interval.pack(fill="x", pady=(10, 0))
        self._label(interval, "服务端扫描间隔", bold=True, size=17).pack(fill="x", padx=14, pady=(10, 4))
        controls = ctk.CTkFrame(interval, fg_color="transparent")
        controls.pack(fill="x", padx=14)
        self._menu(controls, self.interval_choice, (*PRESETS, "自定义"), self._interval_changed).pack(side="left")
        self.custom_entry = self._entry(controls, self.custom_min, width=95)
        self.custom_entry.pack(side="left", padx=(8, 0))
        self._label(controls, "分钟", color=MUTED).pack(side="left", padx=(6, 12))
        self._button(controls, "应用到服务端", self.apply_interval, style="primary", requires="online", width=112).pack(side="left")
        self._interval_changed(self.interval_choice.get())
        self._label(interval, "0 表示暂停定时扫描；仍可手动扫描。", size=12, color=MUTED).pack(
            fill="x", padx=14, pady=(4, 8),
        )
        ctk.CTkCheckBox(
            interval, text="桌面定时提醒（到点询问是否扫描）", variable=self.gui_auto,
            command=self.toggle_gui_timer, text_color=TEXT, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            checkmark_color=ACTION_TEXT, border_color=MUTED, font=ctk.CTkFont(family=FONT, size=13),
        ).pack(anchor="w", padx=14, pady=(0, 12))

    def _show(self, key: str) -> None:
        if self._page:
            self._sections[self._page].grid_remove()
        self._page = key
        self._sections[key].grid()
        title, subtitle = {
            "duty": ("值班台", "先看哪些客户在等，再决定扫描或通知。"),
            "qa": ("模拟问答", "预览支持话术，确认后才可推送到企微提醒群。"),
            "settings": ("连接设置", "管理服务连接、扫描间隔与本机提醒。"),
        }[key]
        self.title_lbl.configure(text=title)
        self.subtitle_lbl.configure(text=subtitle)
        for name, button in self._nav.items():
            button.configure(fg_color=HOVER if name == key else INSET,
                             text_color=ACCENT if name == key else TEXT)

    def _update_buttons(self) -> None:
        for button, requires in self._buttons:
            ready = requires == "idle" or (
                self._online and (requires != "suggestion" or bool(self._last_suggest))
            )
            button.configure(state="normal" if ready and not self._busy else "disabled")

    def _submit(self, label, request, on_result, on_error=None) -> None:
        if self._busy:
            return
        self._busy = True
        self._set_log(label)
        self._update_buttons()

        def work() -> None:
            try:
                self._results.put((on_result, request()))
            except Exception as exc:  # noqa: BLE001
                self._results.put((on_error or self._operation_error, str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _drain_results(self) -> None:
        try:
            callback, result = self._results.get_nowait()
        except queue.Empty:
            pass
        else:
            self._busy = False
            self._update_buttons()
            callback(result)
        finally:
            self.after(40, self._drain_results)

    def _set_log(self, text: str) -> None:
        self.log_var.set(f"{datetime.now():%H:%M:%S}  {text}")

    def _operation_error(self, error: str) -> None:
        self._set_log("操作失败，请检查服务连接或配置")
        messagebox.showerror("操作失败", error, parent=self)

    @staticmethod
    def _write_text(box, text: str) -> None:
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", text)
        box.configure(state="disabled")

    def _service_base(self) -> str:
        return self.base_var.get().strip().rstrip("/") or DEFAULT_BASE

    def ping(self) -> None:
        self.refresh_status()

    def refresh_status(self) -> None:
        if self._busy:
            return
        base = self._service_base()
        if base != self.client.base_url:
            self._last_suggest = None
            self.clear_out()
            self._write_text(self.activity, "已切换服务，等待新的操作回执。")
        self.client = WatchdogClient(base)
        self.rail_addr.configure(text=base.removeprefix("http://").removeprefix("https://"))
        self._online = False
        self._health = {}
        self._set_metrics("检测中")
        self.status_var.set("正在检测连接…")
        self.pending_summary.configure(text="正在检测连接…")
        self._pending_empty("正在检测服务连接…")
        self._submit("检测服务连接…", self.client.healthz, self._connected, self._disconnected)

    def _set_metrics(self, link, health=None) -> None:
        health = health or {}
        source = health.get("message_source")
        interval = health.get("scan_interval_minutes")
        safe = health.get("safe_mode")
        labels = {"demo": "演示消息", "webhook": "上游接入"}
        self._metrics["link"].configure(text=link, text_color=GOOD if link == "已连接" else WARN)
        self._metrics["source"].configure(text=labels.get(source, source or "—"))
        self._metrics["interval"].configure(
            text="已暂停" if interval == 0 else f"每 {interval} 分钟" if interval is not None else "—",
        )
        self._metrics["safe"].configure(
            text="开启" if safe is True else "关闭" if safe is False else "—",
            text_color=WARN if safe is False else TEXT,
        )

    def _connected(self, data: dict) -> None:
        if not data.get("ok"):
            self._disconnected(str(data.get("error") or "健康检查未通过"))
            return
        self._online = True
        self._health = data
        self._set_metrics("已连接", data)
        mode = data.get("workbuddy_mode") or "未配置"
        webhook = "已配置" if data.get("wecom_webhook_configured") else "未配置"
        self.status_var.set(f"连接成功。话术模式：{mode}；企微通知：{webhook}。")
        if isinstance(data.get("scan_interval_minutes"), int):
            self._set_interval_control(data["scan_interval_minutes"])
        self._update_buttons()
        self.refresh_pending()

    def _disconnected(self, error: str) -> None:
        self._online = False
        self._health = {}
        self._set_metrics("未连接")
        self.status_var.set(f"无法连接：{error}\n请启动 Watchdog 服务，检查地址后重新检测。")
        self._pending_empty("请启动 Watchdog 服务，然后在「连接设置」中重新检测。")
        self.pending_summary.configure(text="服务未连接")
        self._set_log("连接失败，请打开连接设置")
        self._update_buttons()

    def _clear_feed(self) -> None:
        self._feed_labels.clear()
        for child in self.feed.winfo_children():
            child.destroy()

    def _pending_empty(self, text: str) -> None:
        self.pending_count.configure(text="—")
        self._clear_feed()
        self._label(self.feed, text, color=MUTED, wraplength=460).pack(fill="x", padx=12, pady=16)

    def _resize_feed(self, _event=None) -> None:
        if not self._feed_labels:
            return
        scale = self._feed_labels[0]._get_widget_scaling()
        width = max(240, int(self.feed.winfo_width() / scale) - 145)
        for label in self._feed_labels:
            if label.cget("wraplength") != width:
                label.configure(wraplength=width)

    def refresh_pending(self) -> None:
        if self._busy or not self._online:
            return
        self.pending_summary.configure(text="正在读取清单…")
        self._pending_empty("正在从 Watchdog 读取待跟进记录…")
        self._submit("刷新待跟进清单…", self.client.pending, self._render_pending, self._pending_error)

    def _pending_error(self, error: str) -> None:
        self.pending_summary.configure(text="清单读取失败")
        self._pending_empty("读取失败。可点击「刷新清单」重试，或检查服务是否仍在运行。")
        self._set_log("清单读取失败：" + error[:130])

    def _render_pending(self, data: dict) -> None:
        items = data.get("items")
        if not data.get("ok") or not isinstance(items, list):
            self._pending_error(str(data.get("error") or "服务返回无效数据"))
            return
        self._clear_feed()
        self.pending_summary.configure(text=f"{len(items)} 个群待跟进  ·  {datetime.now():%H:%M} 更新")
        if not items:
            self._pending_empty("当前没有待跟进记录。需要检查新消息时，可以手动扫描。")
        for item in items:
            row = ctk.CTkFrame(self.feed, fg_color=INSET, corner_radius=7)
            row.pack(fill="x", pady=(2, 4))
            row.grid_columnconfigure(0, weight=1)
            name = self._label(row, str(item.get("group_name") or "未命名群"), bold=True)
            name.grid(row=0, column=0, sticky="ew", padx=(12, 6), pady=(6, 0))
            preview = self._label(row, str(item.get("preview") or "无消息摘要"), color=MUTED, size=12)
            preview.grid(row=1, column=0, sticky="ew", padx=(12, 6), pady=(0, 7))
            self._feed_labels.extend((name, preview))
            minutes = item.get("waiting_minutes")
            waiting = f"{minutes:g} 分钟" if isinstance(minutes, (int, float)) else "—"
            self._label(row, waiting, color=WARN, size=13, bold=True).grid(
                row=0, column=1, rowspan=2, sticky="ne", padx=(6, 12), pady=(7, 0),
            )
        self.pending_count.configure(text=str(len(items)))
        self._resize_feed()
        self._set_log("待跟进清单已更新")

    def _interval_changed(self, choice: str) -> None:
        if choice in PRESETS:
            self.custom_min.set(str(PRESETS[choice]))
        self.custom_entry.configure(state="normal" if choice == "自定义" else "disabled")

    def _set_interval_control(self, minutes: int) -> None:
        choice = next((label for label, value in PRESETS.items() if value == minutes), "自定义")
        self.interval_choice.set(choice)
        self.custom_min.set(str(minutes))
        self._interval_changed(choice)

    def _minutes(self) -> int:
        minutes = PRESETS.get(self.interval_choice.get())
        if minutes is None:
            minutes = int(self.custom_min.get().strip())
        if not 0 <= minutes <= 1440:
            raise ValueError("请输入 0–1440 分钟。")
        return minutes

    def _input_minutes(self) -> int | None:
        try:
            return self._minutes()
        except ValueError:
            messagebox.showwarning("间隔无效", "请输入 0–1440 的整数分钟。", parent=self)
            return None

    def apply_interval(self) -> None:
        minutes = self._input_minutes()
        if minutes is None:
            return

        def done(data: dict) -> None:
            if not data.get("ok"):
                self._operation_error(str(data.get("error") or "服务未保存扫描间隔"))
                return
            current = data.get("scan_interval_minutes", minutes)
            self._health["scan_interval_minutes"] = current
            self._set_metrics("已连接", self._health)
            self._set_interval_control(current)
            self.toggle_gui_timer()
            self._set_log("定时扫描已暂停" if current == 0 else f"服务端扫描间隔已设为 {current} 分钟")

        self._submit("保存扫描间隔…", lambda: self.client.set_interval(minutes), done)

    def do_scan(self) -> None:
        if not self._online or self._busy:
            return
        self.scan_button.configure(text="扫描中…")
        self.scan_progress.grid()
        self.scan_progress.start()

        def stop_progress() -> None:
            self.scan_progress.stop()
            self.scan_progress.grid_remove()
            self.scan_button.configure(text="立即扫描")

        def failed(error: str) -> None:
            stop_progress()
            self._operation_error(error)

        def done(data: dict) -> None:
            stop_progress()
            summary = f"扫描完成：{data.get('unanswered', '—')} 个超时，{data.get('alerted', '—')} 条提醒记录。"
            details = data.get("details")
            if details:
                summary += "\n\n" + json.dumps(details, ensure_ascii=False, indent=2)
            self._write_text(self.activity, summary)
            self.refresh_pending()

        self._submit("正在扫描，请稍候…", self.client.force_scan, done, failed)

    def do_send_digest(self) -> None:
        if not messagebox.askyesno(
            "发送待跟进表", "将把待跟进表发送到服务配置的企微提醒群。\n"
            "若服务开启了发送前同步，会先同步并扫描。继续吗？", parent=self,
        ):
            return

        def done(data: dict) -> None:
            response = data.get("wecom_resp") or {}
            number = data.get("count", 0)
            if not data.get("ok"):
                text = f"发送失败：{data.get('error') or '服务未确认'}"
            elif data.get("skipped"):
                text = "未发送：服务跳过了本次通知。"
            elif data.get("safe_mode") is True:
                text = f"安全模式：已生成 {number} 条待跟进记录，未实发。"
            elif response.get("errcode") == 0 or response.get("ok") is True:
                text = f"已将 {number} 条待跟进记录发往企微提醒群。"
            else:
                text = f"企微未确认送达：{response.get('errmsg') or response.get('error') or '请检查服务日志'}"
            sync = data.get("sync") or {}
            if sync.get("skipped"):
                text += "\n发送前同步：未执行。"
            elif sync.get("ok") is True:
                text += "\n发送前同步：已完成。"
            elif sync.get("ok") is False:
                text += "\n发送前同步：失败，清单可能不是最新。"
            self._write_text(self.activity, text)
            self.refresh_pending()

        self._submit("正在生成并发送待跟进表…", self.client.push_pending_digest, done)

    def _on_sample(self, value: str) -> None:
        self.q_text.delete("1.0", "end")
        self.q_text.insert("1.0", value)

    def clear_out(self) -> None:
        self._write_text(self.out, "选择样例或输入客户问题，然后生成建议。")

    def do_suggest(self) -> None:
        self._run_suggest(notify_wecom=False)

    def _confirm_simulated_push(self, title: str) -> bool:
        return messagebox.askyesno(
            title, "将真实发送到服务配置的企微提醒群，即使服务开启了安全模式。\n"
            "不会自动回复原客户群。确认发送？", parent=self,
        )

    def do_suggest_and_wecom(self) -> None:
        self._run_suggest(notify_wecom=True)

    def _run_suggest(self, *, notify_wecom: bool) -> None:
        question = self.q_text.get("1.0", "end").strip()
        if not question:
            messagebox.showwarning("缺少问题", "请先输入模拟问题。", parent=self)
            return
        if notify_wecom and not self._confirm_simulated_push("生成并推企微"):
            return
        group = self.group_var.get().strip() or "【JS】GUI模拟群"

        def done(data: dict) -> None:
            suggestion = data.get("suggestion") or ""
            saved = {
                "suggestion": suggestion,
                "question": data.get("question") or question,
                "group_name": data.get("group_name") or group,
                "source": data.get("source") or "gui",
            }
            self._last_suggest = saved if suggestion else None
            self._update_buttons()
            lines = [f"群：{saved['group_name']}", f"问题：{saved['question']}", f"来源：{saved['source']}"]
            if notify_wecom:
                lines.append(self._push_result(data))
            self._write_text(self.out, "\n".join(lines) + "\n\n" + (suggestion or "未返回建议。"))
            self._set_log(self._push_result(data) if notify_wecom else "建议已生成" if suggestion else "未返回建议")

        self._submit("正在生成建议…", lambda: self.client.suggest(
            question, group_name=group, notify_wecom=notify_wecom, force_real=notify_wecom,
        ), done)

    @staticmethod
    def _push_result(data: dict) -> str:
        if data.get("wecom_pushed") or data.get("feishu_pushed"):
            return "企微：已推送"
        if data.get("safe_mode"):
            return "企微：安全模式未实发"
        reason = data.get("error") or data.get("note") or data.get("wecom_resp")
        return f"企微：未推送（{reason or '请检查服务日志'}）"

    def do_wecom_last(self) -> None:
        if not self._last_suggest or not self._confirm_simulated_push("推送上次结果"):
            return
        payload = dict(self._last_suggest)
        payload["source"] += "+replay"

        def done(data: dict) -> None:
            result = self._push_result(data)
            self._set_log(result)
            self._write_text(self.out, result + "\n\n" + payload["suggestion"])

        self._submit("正在推送上次建议…", lambda: self.client.wecom_push(**payload, force_real=True), done)

    def toggle_gui_timer(self) -> None:
        if self._auto_job is not None:
            self.after_cancel(self._auto_job)
            self._auto_job = None
        if self.gui_auto.get():
            minutes = self._input_minutes()
            if minutes is None:
                self.gui_auto.set(False)
                return
            self._auto_job = self.after(max(1, minutes) * 60 * 1000, self._gui_fire)

    def _gui_fire(self) -> None:
        self._auto_job = None
        if self._online and not self._busy and messagebox.askyesno(
            "桌面定时提醒", "校验间隔已到。是否立即扫描？", parent=self,
        ):
            self.do_scan()
        self.toggle_gui_timer()


def main() -> None:
    _fix_tcl_env()
    try:
        app = App()
    except tk.TclError as e:
        print(f"[error] 无法启动桌面 GUI（Tcl/Tk）: {e}")
        print("可改用浏览器版: python app/web_ui.py")
        raise SystemExit(1) from e
    app.mainloop()


if __name__ == "__main__":
    main()
