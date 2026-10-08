"""WeCom Watchdog Console — native desktop GUI (cyber dark)."""
from __future__ import annotations

import json
import os
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox
from typing import Optional

# Fix common Windows Python embed where Tcl path resolves to .../lib/tcl8.6
def _fix_tcl_env() -> None:
    candidates = [
        Path(r"D:\Python313"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python313",
        Path(sys.base_prefix),
        Path(sys.prefix),
    ]
    for root in candidates:
        tcl = root / "tcl" / "tcl8.6"
        tk_ = root / "tcl" / "tk8.6"
        if (tcl / "init.tcl").is_file():
            os.environ["TCL_LIBRARY"] = str(tcl)
            if (tk_ / "tk.tcl").is_file():
                os.environ["TK_LIBRARY"] = str(tk_)
            break


_fix_tcl_env()

_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import customtkinter as ctk

from client import WatchdogClient

PRESETS = [("5 分钟", 5), ("10 分钟", 10), ("15 分钟", 15), ("30 分钟", 30), ("自定义", -1)]
SAMPLES = [
    "JumpServer远程应用发布机怎么部署",
    "登录提示配置文件有问题无法登录 DOMAINS",
    "Tinker 离线怎么办",
    "资产连接超时怎么排查",
]

# Cyber HUD palette
BG = "#05060f"
PANEL = "#0c1224"
PANEL2 = "#101830"
CYAN = "#22d3ee"
MINT = "#34d399"
TEXT = "#e8eefc"
MUTED = "#8b9bb8"
LINE = "#1a3a4a"


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.title("WATCHDOG CONSOLE // JumpServer")
        self.geometry("1080x860")
        self.minsize(960, 720)
        self.configure(fg_color=BG)

        self.client = WatchdogClient()
        self._auto_job: Optional[str] = None
        self._last_suggest: Optional[dict] = None

        self._build()
        self.after(400, self.refresh_status)

    def _card(self, parent: ctk.CTkFrame, title: str) -> ctk.CTkFrame:
        wrap = ctk.CTkFrame(
            parent,
            fg_color=PANEL,
            corner_radius=16,
            border_width=1,
            border_color=LINE,
        )
        head = ctk.CTkLabel(
            wrap,
            text=title,
            text_color=CYAN,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            anchor="w",
        )
        head.pack(fill="x", padx=16, pady=(14, 6))
        body = ctk.CTkFrame(wrap, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        return body

    def _build(self) -> None:
        # Hero
        hero = ctk.CTkFrame(self, fg_color="transparent")
        hero.pack(fill="x", padx=18, pady=(16, 8))
        ctk.CTkLabel(
            hero,
            text="FIT2CLOUD  ·  JUMPSERVER OPS",
            text_color=CYAN,
            font=ctk.CTkFont(size=11),
            anchor="w",
        ).pack(fill="x")
        row = ctk.CTkFrame(hero, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(
            row,
            text="WATCHDOG CONSOLE",
            text_color=TEXT,
            font=ctk.CTkFont(size=26, weight="bold"),
            anchor="w",
        ).pack(side="left")
        self.live_lbl = ctk.CTkLabel(
            row,
            text="● STANDBY",
            text_color="#fbbf24",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.live_lbl.pack(side="right", padx=(8, 0))

        ctk.CTkLabel(
            hero,
            text="原生桌面窗口 · 定时校验 · 模拟话术问答",
            text_color=MUTED,
            font=ctk.CTkFont(size=13),
            anchor="w",
        ).pack(fill="x", pady=(2, 0))

        # Link + interval
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=18, pady=8)
        top.grid_columnconfigure(0, weight=1)
        top.grid_columnconfigure(1, weight=1)

        link_body = self._card(top, "01  //  LINK")
        link_body.master.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ctk.CTkLabel(link_body, text="Watchdog 地址", text_color=MUTED, anchor="w").pack(fill="x")
        self.base_var = tk.StringVar(value="http://127.0.0.1:8092")
        ctk.CTkEntry(
            link_body,
            textvariable=self.base_var,
            fg_color=PANEL2,
            border_color=LINE,
            text_color=TEXT,
            height=36,
        ).pack(fill="x", pady=(4, 8))
        ctk.CTkButton(
            link_body,
            text="检测连接  PING",
            command=self.ping,
            fg_color="#0e7490",
            hover_color="#0891b2",
            text_color=TEXT,
            height=36,
        ).pack(fill="x")
        ctk.CTkLabel(
            link_body,
            text="作用：请求 /healthz，确认本机 Watchdog 是否可访问",
            text_color="#5b6b7c",
            font=ctk.CTkFont(size=11),
            anchor="w",
        ).pack(fill="x", pady=(6, 0))
        self.status_var = tk.StringVar(value="状态：未检测")
        ctk.CTkLabel(
            link_body,
            textvariable=self.status_var,
            text_color=MUTED,
            font=ctk.CTkFont(size=12),
            anchor="w",
            wraplength=420,
            justify="left",
        ).pack(fill="x", pady=(10, 0))

        int_body = self._card(top, "02  //  INTERVAL")
        int_body.master.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        ctk.CTkLabel(int_body, text="定时校验间隔", text_color=MUTED, anchor="w").pack(fill="x")
        self.interval_choice = tk.StringVar(value="5 分钟")
        ctk.CTkOptionMenu(
            int_body,
            variable=self.interval_choice,
            values=[p[0] for p in PRESETS],
            fg_color=PANEL2,
            button_color="#0e7490",
            button_hover_color="#0891b2",
            dropdown_fg_color=PANEL,
            text_color=TEXT,
            height=36,
        ).pack(fill="x", pady=(4, 8))
        ctk.CTkLabel(int_body, text="自定义（分钟）", text_color=MUTED, anchor="w").pack(fill="x")
        self.custom_min = tk.StringVar(value="5")
        ctk.CTkEntry(
            int_body,
            textvariable=self.custom_min,
            fg_color=PANEL2,
            border_color=LINE,
            text_color=TEXT,
            height=36,
        ).pack(fill="x", pady=(4, 8))
        btn_row = ctk.CTkFrame(int_body, fg_color="transparent")
        btn_row.pack(fill="x")
        ctk.CTkButton(
            btn_row,
            text="应用到服务端",
            command=self.apply_interval,
            fg_color=CYAN,
            hover_color="#67e8f9",
            text_color="#041018",
            height=36,
        ).pack(side="left", expand=True, fill="x", padx=(0, 6))
        ctk.CTkButton(
            btn_row,
            text="立即扫描",
            command=self.do_scan,
            fg_color=PANEL2,
            hover_color="#16324a",
            border_width=1,
            border_color=CYAN,
            text_color=CYAN,
            height=36,
        ).pack(side="left", expand=True, fill="x", padx=(6, 0))

        self.gui_auto = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            int_body,
            text="GUI 计时提醒（到点询问是否扫描）",
            variable=self.gui_auto,
            command=self.toggle_gui_timer,
            text_color=MUTED,
            fg_color=CYAN,
            hover_color="#67e8f9",
            border_color=LINE,
        ).pack(anchor="w", pady=(10, 0))

        # Simulate + output (scrollable column so buttons never crush)
        mid_wrap = ctk.CTkFrame(self, fg_color="transparent")
        mid_wrap.pack(fill="both", expand=True, padx=18, pady=8)
        mid_wrap.grid_columnconfigure(0, weight=1)
        mid_wrap.grid_rowconfigure(1, weight=1)

        sim = self._card(mid_wrap, "03  //  SIMULATE Q&A")
        sim.master.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        form = ctk.CTkFrame(sim, fg_color="transparent")
        form.pack(fill="x")
        form.grid_columnconfigure(0, weight=1)
        form.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(form, text="样例问题", text_color=MUTED, anchor="w").grid(
            row=0, column=0, sticky="ew", padx=(0, 8)
        )
        ctk.CTkLabel(form, text="模拟群名", text_color=MUTED, anchor="w").grid(
            row=0, column=1, sticky="ew", padx=(8, 0)
        )
        self.q_var = tk.StringVar(value=SAMPLES[0])
        ctk.CTkOptionMenu(
            form,
            variable=self.q_var,
            values=SAMPLES,
            command=self._on_sample,
            fg_color=PANEL2,
            button_color="#0e7490",
            button_hover_color="#0891b2",
            dropdown_fg_color=PANEL,
            text_color=TEXT,
            height=36,
        ).grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(4, 8))
        self.group_var = tk.StringVar(value="【JS】GUI模拟群")
        ctk.CTkEntry(
            form,
            textvariable=self.group_var,
            fg_color=PANEL2,
            border_color=LINE,
            text_color=TEXT,
            height=36,
        ).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(4, 8))

        ctk.CTkLabel(sim, text="自定义问题", text_color=MUTED, anchor="w").pack(fill="x")
        self.q_text = ctk.CTkTextbox(
            sim,
            height=72,
            fg_color=PANEL2,
            border_color=LINE,
            border_width=1,
            text_color=TEXT,
            font=ctk.CTkFont(family="Consolas", size=13),
        )
        self.q_text.pack(fill="x", pady=(4, 10))
        self.q_text.insert("1.0", SAMPLES[0])

        # 2×2 grid — avoids 4-button side-by-side overlap on Windows CTk
        act = ctk.CTkFrame(sim, fg_color="transparent")
        act.pack(fill="x", pady=(0, 4))
        act.grid_columnconfigure(0, weight=1, uniform="act")
        act.grid_columnconfigure(1, weight=1, uniform="act")

        btn_kw = dict(height=40, corner_radius=10)
        ctk.CTkButton(
            act,
            text="仅生成建议",
            command=self.do_suggest,
            fg_color=PANEL2,
            hover_color="#16324a",
            border_width=1,
            border_color=CYAN,
            text_color=CYAN,
            **btn_kw,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 8))
        ctk.CTkButton(
            act,
            text="生成并推企微",
            command=self.do_suggest_and_wecom,
            fg_color=CYAN,
            hover_color="#67e8f9",
            text_color="#041018",
            font=ctk.CTkFont(size=13, weight="bold"),
            **btn_kw,
        ).grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=(0, 8))
        ctk.CTkButton(
            act,
            text="推送上次到企微",
            command=self.do_wecom_last,
            fg_color=PANEL2,
            hover_color="#16324a",
            border_width=1,
            border_color=MINT,
            text_color=MINT,
            **btn_kw,
        ).grid(row=1, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(
            act,
            text="清空输出",
            command=self.clear_out,
            fg_color=PANEL2,
            hover_color="#16324a",
            border_width=1,
            border_color=LINE,
            text_color=MUTED,
            **btn_kw,
        ).grid(row=1, column=1, sticky="ew", padx=(6, 0))

        ctk.CTkLabel(
            sim,
            text="「生成并推企微」走 WECOM_NOTIFY_WEBHOOK（内部群→自定义消息推送）；带【GUI 模拟推送】前缀",
            text_color="#5b6b7c",
            font=ctk.CTkFont(size=11),
            anchor="w",
        ).pack(fill="x", pady=(8, 0))

        out_body = self._card(mid_wrap, "04  //  OUTPUT")
        out_body.master.grid(row=1, column=0, sticky="nsew")
        self.out = ctk.CTkTextbox(
            out_body,
            fg_color="#06141e",
            border_color="#1a4a3a",
            border_width=1,
            text_color="#d1fae5",
            font=ctk.CTkFont(family="Consolas", size=13),
        )
        self.out.pack(fill="both", expand=True)
        self.out.insert("1.0", "等待指令…\n")

        self.log_var = tk.StringVar(value="")
        ctk.CTkLabel(
            self,
            textvariable=self.log_var,
            text_color=MUTED,
            font=ctk.CTkFont(size=12),
            anchor="w",
        ).pack(fill="x", padx=22, pady=(0, 12))

    def _on_sample(self, value: str) -> None:
        self.q_text.delete("1.0", "end")
        self.q_text.insert("1.0", value)

    def _minutes(self) -> int:
        choice = self.interval_choice.get()
        for label, m in PRESETS:
            if choice == label and m > 0:
                return m
        try:
            return max(0, int(self.custom_min.get().strip()))
        except ValueError:
            return 5

    def _sync_client(self) -> None:
        self.client = WatchdogClient(self.base_var.get().strip() or "http://127.0.0.1:8092")

    def _append(self, text: str) -> None:
        self.out.insert("end", text + "\n")
        self.out.see("end")

    def _set_log(self, msg: str) -> None:
        self.log_var.set(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def _set_live(self, online: bool, text: str) -> None:
        self.live_lbl.configure(text=f"● {text}", text_color=MINT if online else "#fb7185")

    def refresh_status(self, *, notify: bool = False) -> None:
        self._sync_client()

        def work() -> None:
            try:
                data = self.client.healthz()
                msg = (
                    f"ONLINE · mode={data.get('workbuddy_mode')} · "
                    f"scan={data.get('scan_interval_minutes')}min · "
                    f"source={data.get('message_source')}"
                )
                detail = (
                    "连接成功！\n\n"
                    f"地址：{self.base_var.get().strip()}\n"
                    f"模式：{data.get('workbuddy_mode')}\n"
                    f"扫描间隔：{data.get('scan_interval_minutes')} 分钟\n"
                    f"消息源：{data.get('message_source')}\n"
                    f"safe_mode：{data.get('safe_mode')}"
                )
                self.after(0, lambda: self.status_var.set(msg))
                self.after(0, lambda: self._set_live(True, "ONLINE"))
                self.after(0, lambda: self._set_log("healthz ok"))
                if notify:
                    self.after(0, lambda: messagebox.showinfo("检测连接", detail))
            except Exception as e:  # noqa: BLE001
                err = str(e)
                self.after(0, lambda: self.status_var.set(f"OFFLINE — {err}"))
                self.after(0, lambda: self._set_live(False, "OFFLINE"))
                self.after(0, lambda: self._set_log(f"healthz failed: {err}"))
                if notify:
                    self.after(
                        0,
                        lambda: messagebox.showerror(
                            "检测连接失败",
                            "无法连通 Watchdog 服务。\n\n"
                            f"地址：{self.base_var.get().strip()}\n"
                            f"错误：{err}\n\n"
                            "请确认已启动 wecom-group-watchdog（默认 8092）。",
                        ),
                    )

        threading.Thread(target=work, daemon=True).start()

    def ping(self) -> None:
        """手动点击「检测连接」时弹窗提示结果。"""
        self.refresh_status(notify=True)

    def apply_interval(self) -> None:
        minutes = self._minutes()
        self._sync_client()

        def work() -> None:
            try:
                data = self.client.set_interval(minutes)
                self.after(
                    0,
                    lambda: self._set_log(
                        f"已设置服务端定时校验: {data.get('scan_interval_minutes')} 分钟"
                    ),
                )
                self.after(0, self.refresh_status)
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda: messagebox.showerror("设置失败", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def do_scan(self) -> None:
        self._sync_client()
        self._set_log("正在 force scan…")

        def work() -> None:
            try:
                data = self.client.force_scan()
                text = json.dumps(data, ensure_ascii=False, indent=2)
                self.after(0, lambda: self._append("--- force scan ---\n" + text + "\n"))
                self.after(0, lambda: self._set_log("scan 完成"))
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda: messagebox.showerror("扫描失败", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def do_suggest(self) -> None:
        self._run_suggest(notify_wecom=False)

    def do_suggest_and_wecom(self) -> None:
        if not messagebox.askyesno(
            "推送到企微",
            "将生成建议并推送到 WECOM_NOTIFY_WEBHOOK（内部群消息推送）。\n确认继续？",
        ):
            return
        self._run_suggest(notify_wecom=True)

    def _run_suggest(self, *, notify_wecom: bool) -> None:
        q = self.q_text.get("1.0", "end").strip()
        if not q:
            messagebox.showwarning("提示", "请先填写模拟问题")
            return
        group = self.group_var.get().strip() or "【JS】GUI模拟群"
        self._sync_client()
        self._set_log(
            "正在生成建议并推企微…" if notify_wecom else "正在生成建议（可能需 10–60 秒）…"
        )

        def work() -> None:
            try:
                data = self.client.suggest(
                    q, group_name=group, notify_wecom=notify_wecom, force_real=True
                )
                self._last_suggest = {
                    "suggestion": data.get("suggestion") or "",
                    "question": data.get("question") or q,
                    "group_name": data.get("group_name") or group,
                    "source": data.get("source") or "gui",
                }
                pushed = bool(data.get("wecom_pushed") or data.get("feishu_pushed"))
                wecom_line = ""
                if notify_wecom:
                    if pushed:
                        wecom_line = "企微：已推送成功\n"
                    elif data.get("safe_mode"):
                        wecom_line = f"企微：safe_mode 未实发 — {data.get('note')}\n"
                    else:
                        err = data.get("error") or data.get("wecom_resp") or data.get("feishu_resp")
                        wecom_line = f"企微：失败 — {err}\n"
                block = (
                    f"=== 模拟问答 ===\n"
                    f"群: {data.get('group_name')}\n"
                    f"问: {data.get('question')}\n"
                    f"来源: {data.get('source')}\n"
                    f"{wecom_line}\n"
                    f"{data.get('suggestion')}\n"
                )
                self.after(0, lambda: self._append(block))
                self.after(
                    0,
                    lambda: self._set_log(
                        f"suggest ok source={data.get('source')} wecom_pushed={pushed}"
                    ),
                )
                if notify_wecom:
                    if pushed:
                        self.after(
                            0,
                            lambda: messagebox.showinfo("企微", "已推送到企微群消息推送。"),
                        )
                    else:
                        self.after(
                            0,
                            lambda: messagebox.showwarning(
                                "企微",
                                data.get("error")
                                or data.get("note")
                                or str(data.get("wecom_resp") or data.get("feishu_resp")),
                            ),
                        )
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda: messagebox.showerror("模拟失败", str(e)))
                self.after(0, lambda: self._set_log(f"suggest failed: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def do_wecom_last(self) -> None:
        if not self._last_suggest or not self._last_suggest.get("suggestion"):
            messagebox.showwarning("提示", "还没有可推送的建议，请先点「仅生成建议」或「生成并推企微」。")
            return
        if not messagebox.askyesno("推送上次结果", "将把上次生成的建议推送到企微，确认？"):
            return
        self._sync_client()
        payload = dict(self._last_suggest)
        self._set_log("正在推送上次建议到企微…")

        def work() -> None:
            try:
                data = self.client.wecom_push(
                    suggestion=payload["suggestion"],
                    question=payload.get("question") or "",
                    group_name=payload.get("group_name") or "【JS】GUI模拟群",
                    source=str(payload.get("source") or "gui") + "+replay",
                    force_real=True,
                )
                pushed = bool(data.get("wecom_pushed") or data.get("feishu_pushed"))
                self.after(
                    0,
                    lambda: self._append(
                        f"--- wecom push ---\n{json.dumps(data, ensure_ascii=False, indent=2)}\n"
                    ),
                )
                if pushed:
                    self.after(0, lambda: messagebox.showinfo("企微", "上次建议已推送到企微。"))
                    self.after(0, lambda: self._set_log("wecom push ok"))
                else:
                    self.after(
                        0,
                        lambda: messagebox.showwarning(
                            "企微",
                            data.get("error") or data.get("note") or str(data),
                        ),
                    )
                    self.after(0, lambda: self._set_log("wecom push failed"))
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda: messagebox.showerror("推送失败", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def clear_out(self) -> None:
        self.out.delete("1.0", "end")
        self.out.insert("1.0", "等待指令…\n")

    def toggle_gui_timer(self) -> None:
        if self._auto_job is not None:
            try:
                self.after_cancel(self._auto_job)
            except Exception:  # noqa: BLE001
                pass
            self._auto_job = None
        if self.gui_auto.get():
            self._gui_tick()

    def _gui_tick(self) -> None:
        if not self.gui_auto.get():
            return
        minutes = max(1, self._minutes())
        self._set_log(f"GUI 计时：{minutes} 分钟后提醒")
        self._auto_job = self.after(minutes * 60 * 1000, self._gui_fire)

    def _gui_fire(self) -> None:
        self._set_log("GUI 计时到点")
        if messagebox.askyesno("定时提醒", "校验间隔已到，是否立即 force scan？"):
            self.do_scan()
        self._gui_tick()


def main() -> None:
    try:
        app = App()
    except tk.TclError as e:
        print(f"[error] 无法启动桌面 GUI（Tcl/Tk）: {e}")
        print("可改用浏览器版: python app/web_ui.py")
        raise SystemExit(1) from e
    app.mainloop()


if __name__ == "__main__":
    main()
