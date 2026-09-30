# WorkBuddy 侧：接收 watchdog 超时告警并返回建议话术

将本文件内容放入 WorkBuddy skill（例如 `wecom-watchdog-suggest/SKILL.md`），
并提供 HTTP 入口，或把 `WORKBUDDY_LOCAL_CMD` 指到本 skill 的脚本。

## 何时触发

`wecom-group-watchdog` 判定：

> 客户最后一条消息超过 N 分钟，且中间没有内部同事发言

后，按 `WORKBUDDY_MODE` 调用你：

- `sync_webhook`：`POST {WORKBUDDY_WEBHOOK_URL}`，同步 JSON 响应
- `local_cmd`：执行本地命令，stdout 输出建议

## 请求体（sync_webhook）

```json
{
  "event": "unanswered_alert",
  "room_id": "wr_xxx",
  "group_name": "【JS】某客户支持群",
  "product": "jumpserver",
  "support_userids": ["zhangsan"],
  "last_customer_msg_id": "...",
  "last_customer_at": 1710000000.0,
  "waiting_minutes": 18.5,
  "customer_excerpt": "[customer] ...\n[staff] ...",
  "recent_messages": [],
  "prompt": "拼好的系统提示 + 对话上下文"
}
```

Header（可选）：`Authorization: Bearer {WORKBUDDY_WEBHOOK_TOKEN}`

## 响应体（必须）

```json
{
  "suggestion": "1) 问题摘要...\n2) 建议回复话术...\n3) 还需确认..."
}
```

也接受字段名：`reply` / `answer` / `content` / `text`。

## 建议行为

1. 用现有四源检索（本地教学库 → kb → wiki → 飞书表）生成口径  
2. **不要自动往客户群发消息**（本方案只提醒内部支持号）  
3. 不确定 / 商务排期类：建议话术里写清「转人工」，并给安抚模板  
4. 可同步把难题记到 TODO / 飞书，与现有值班 skill 一致  

## 挂本地 GitHub 目录（JumpServer 源码）

桌面 WorkBuddy **可以打开本地目录**，但 watchdog 调的 `codebuddy --serve` 默认 cwd 是临时目录，**不会自动等于你 UI 里打开的项目**。

做法：

1. 本机已有仓库即可（不必再 clone），例如当前工作区 jumpserver 根目录  
2. watchdog `.env` 设 `WORKBUDDY_WORKSPACE=` 上述路径  
3. 扫描时会按客户问题关键词检索 `docs/`、`apps/accounts` 等，把摘录塞进 WorkBuddy 生成话术  
4. 你自己在 WorkBuddy 里深度分析代码：侧边栏「项目」→ 打开同一目录即可（和 watchdog 独立）  

## local_cmd 示例

```text
WORKBUDDY_MODE=local_cmd
WORKBUDDY_LOCAL_CMD=C:/Users/you/.workbuddy/binaries/python/envs/default/Scripts/python.exe C:/Users/you/.workbuddy/skills/wecom-customer-watch/scripts/suggest_for_watchdog.py
```

脚本从 argv 最后一个参数读取 JSON，向 stdout 打印 `{"suggestion":"..."}`。

## 联调

1. 先起 mock：`uvicorn scripts.mock_workbuddy_webhook:app --port 8093`  
2. `.env` 设 `WORKBUDDY_MODE=sync_webhook` 与上述 URL  
3. `POST /admin/scan?force=true`，看告警里 `suggest_source=workbuddy_webhook`
