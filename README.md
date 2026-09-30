# 企微客户群 · 超时未回复值班助手（WorkBuddy 建议话术）

一线值班忙不过来时：每隔 N 分钟扫描客户群消息。若 **客户最后一条消息超过阈值，且中间没有内部同事发言**，则给对应支持号推送提醒，并附上 **WorkBuddy** 生成的建议回复。

> **不用 MaxKB。** 建议话术只走 WorkBuddy（Webhook / AI Gateway / 本机 skill）。  
> 桌面控制台在 [`gui/`](gui/)（原独立仓库 wecom-watchdog-gui 已合并至此）。

## 架构（推荐：方案一）

```text
会话存档 / 上游推送
  → POST /ingest/messages
  → 每 N 分钟扫描
  → 判定「超时未回复」
  → 调用 WorkBuddy（建议话术）
  → 企微应用消息 → 支持号
  → （可选）内部值班群 / 飞书 Webhook
```

- **本服务**：消息入库、超时判定、冷却、工作时段、通知支持号  
- **WorkBuddy**：结合本地 jumpserver 源码摘录 + 高匹配知识库产出建议话术（**默认不自动回客户群**）  
- **gui/**：本地桌面窗口，改扫描间隔、模拟问答、推飞书  

## 快速开始

```powershell
cd deploy\wecom-group-watchdog
copy .env.example .env
copy config\groups.example.yaml config\groups.yaml

python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# 冒烟（不启 HTTP、不依赖企微）
python scripts\smoke_test.py

# 启动服务（默认 MESSAGE_SOURCE=demo + SAFE_MODE=true）
uvicorn app.main:app --host 0.0.0.0 --port 8092 --reload
```

桌面 GUI（另开终端）：

```powershell
cd gui
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\run.bat
```

手动扫一次：

```powershell
curl -X POST "http://127.0.0.1:8092/admin/scan?force=true"
```

健康检查：`GET /healthz`

## 配置要点

| 变量 | 说明 |
|------|------|
| `UNANSWERED_MINUTES` | 客户说话后多久无同事回复算待跟进 |
| `SCAN_INTERVAL_MINUTES` | 扫描周期 |
| `ALERT_COOLDOWN_MINUTES` | 同一条客户消息提醒冷却 |
| `WORKBUDDY_MODE` | `none` / `sync_webhook` / `local_cmd` / `ai_gateway` |
| `SAFE_MODE` | `true` 只打日志不真发消息 |
| `GROUPS_CONFIG` | 群 → `support_userids` 映射 |

群配置见 `config/groups.example.yaml`。更多 GUI 说明见 [`gui/README.md`](gui/README.md)。

## 接入 WorkBuddy

### 方式 A：同步 Webhook（推荐联调）

1. WorkBuddy 提供 HTTP 接口，契约见 [`workbuddy/SKILL.md`](workbuddy/SKILL.md)  
2. 或先起 mock：

```powershell
uvicorn scripts.mock_workbuddy_webhook:app --port 8093
```

3. `.env`：

```text
WORKBUDDY_MODE=sync_webhook
WORKBUDDY_WEBHOOK_URL=http://127.0.0.1:8093/workbuddy/suggest
```

### 方式 B：本机命令（对接现有 skill 脚本）

```text
WORKBUDDY_MODE=local_cmd
WORKBUDDY_LOCAL_CMD=python workbuddy/suggest_for_watchdog.py
```

正式环境把命令换成你们 `~/.workbuddy/skills/.../scripts/` 下的检索脚本；样例适配器：`workbuddy/suggest_for_watchdog.py`。

### 方式 C：先不通 WorkBuddy

```text
WORKBUDDY_MODE=none
```

仍会通知支持号，建议话术为本地摘要兜底。

## 消息接入（生产）

生产把 `MESSAGE_SOURCE=webhook`，由会话存档同步器 / 转发器推送：

```http
POST /ingest/messages
Content-Type: application/json
X-Ingest-Token: <INGEST_TOKEN>

{
  "messages": [
    {
      "msg_id": "unique-id",
      "room_id": "wr_xxx",
      "room_name": "【JS】客户群",
      "sender_id": "wm_xxx",
      "sender_name": "客户",
      "sender_kind": "customer",
      "content": "改密失败怎么排查",
      "sent_at": 1710000000
    }
  ]
}
```

`sender_kind` 可省略，服务会按 `staff_userids` / 外部联系人特征推断。

可与现有 [`wecom-customer-group-report`](../wecom-customer-group-report) 会话存档流水线对接：存档落库后批量 POST 到本服务。

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/healthz` | 健康检查 |
| POST | `/ingest/messages` | 写入消息 |
| POST | `/admin/scan?force=true` | 立即扫描 |
| POST | `/admin/load-demo` | 加载演示消息 |
| POST | `/admin/reload-groups` | 热加载群配置 |
| GET | `/admin/rooms` | 查看已知群 |

## 目录

```text
deploy/wecom-group-watchdog/
├── app/                 # FastAPI 服务
├── gui/                 # 桌面控制台（CustomTkinter）
├── config/groups.example.yaml
├── workbuddy/           # WorkBuddy 契约、售后风格 skill、anti-aistyle
├── scripts/smoke_test.py
├── scripts/mock_workbuddy_webhook.py
├── .env.example
├── docker-compose.yml
└── README.md
```

## 上线检查

1. `SAFE_MODE=true` 空跑，确认判群、冷却、WorkBuddy 返回正常  
2. 填好 `groups.yaml` 与企微应用发消息权限  
3. WorkBuddy 只出建议、**不自动回客户群**（避免误发）  
4. 再设 `SAFE_MODE=false`
