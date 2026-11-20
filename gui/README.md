# JumpServer Watchdog 值班台

本目录是 `wecom-group-watchdog` 的原生 Windows 控制台（CustomTkinter）。它通过 HTTP 连接另行运行的 Watchdog 服务，默认地址为 `http://127.0.0.1:8092`。桌面客户端不包含消息同步、分析或通知服务。

## 白昼与黑夜主题

点击窗口右上角的「白昼」或「黑夜」即可切换，当前页面和内容会保留。客户端默认使用黑夜主题，并把上次选择保存到 `%LOCALAPPDATA%\JumpServer\WatchdogConsole\appearance.txt`。

## 工作区

- **值班台**：查看连接、消息源、扫描间隔和安全模式；读取待跟进客户群，立即扫描，或确认后发送待跟进表。
- **模拟问答**：选择样例或输入问题，生成回复建议。发送模拟建议到企微需要另行确认，且沿用原有联调行为：即使服务开启安全模式，也会尝试实发到已配置的提醒群。
- **连接设置**：检测服务地址，调整服务端扫描间隔及本机计时提醒。

只读取待跟进清单不会触发发送。手动扫描沿用服务端现有行为；对接生产服务时，是否发提醒取决于服务配置。

## 打包成 exe（推荐给同事直接用）

先确保本机已装 Python，在 `gui/` 下执行：

```powershell
.\build_exe.bat
```

生成目录：`gui\dist\WatchdogConsole\WatchdogConsole.exe`，包含本地 Logo 和运行依赖。

分发时请打包整个 `WatchdogConsole` 文件夹（包括 `_internal`），不要只拷贝 exe。启动后在「连接设置」检测 Watchdog 服务连接。

## 开发启动（源码）

先启动仓库根目录的 Watchdog 服务（8092），再：

```powershell
cd gui
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\run.bat
```

`run.bat` 默认打开桌面 GUI；失败时回退浏览器版 `app\web_ui.py`。

```powershell
# 仅桌面
.\.venv\Scripts\python.exe app\main.py

# 仅浏览器
.\.venv\Scripts\python.exe app\web_ui.py
```

## 说明

- 原独立仓库 [wecom-watchdog-gui](https://github.com/Wanpl-Java/wecom-watchdog-gui) 已合并到本目录。  
- 主仓库：https://github.com/Wanpl-Java/wecom-group-watchdog  
