# WeCom Watchdog Console（桌面 GUI）

本目录是 `wecom-group-watchdog` 的原生桌面控制台（CustomTkinter），默认对接 `http://127.0.0.1:8092`。

## 功能

1. 定时校验间隔：5 / 10 / 15 / 30 分钟或自定义  
2. 立即扫描  
3. 模拟话术问答（仅生成 / 生成并推飞书）  

## 打包成 exe（推荐给同事直接用）

先确保本机已装 Python，在 `gui/` 下执行：

```powershell
.\build_exe.bat
```

生成目录：`gui\dist\WatchdogConsole\WatchdogConsole.exe`  
把整个 `WatchdogConsole` 文件夹拷走即可（需本机已启动 Watchdog 8092）。

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
