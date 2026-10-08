from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_host: str = "0.0.0.0"
    app_port: int = 8092
    safe_mode: bool = True
    log_level: str = "INFO"
    data_dir: str = "./data"
    groups_config: str = "./config/groups.yaml"

    scan_interval_minutes: int = 10
    unanswered_minutes: int = 10
    alert_cooldown_minutes: int = 30
    # 未回复客户表（带详情超链接）推送间隔；0=关闭定时汇总
    digest_interval_minutes: int = 30
    # 企微 markdown 超链接用的可访问根地址，如 http://10.1.8.145:8092
    # 留空则用 http://127.0.0.1:{app_port}（手机点不开，建议填局域网 IP）
    public_base_url: str = ""
    work_hours_start: str = "09:00"
    work_hours_end: str = "22:00"
    timezone: str = "Asia/Shanghai"

    # demo | webhook
    message_source: str = "demo"

    wecom_corp_id: str = ""
    wecom_app_secret: str = ""
    wecom_agent_id: int = 0
    wecom_notify_webhook: str = ""
    # 仅用于企微后台「接收消息」URL 校验，解锁企业可信 IP（方案 A 不依赖收消息）
    wecom_callback_token: str = ""
    wecom_encoding_aes_key: str = ""

    # 飞书自定义机器人 Webhook（不需要企微可信 IP）
    feishu_notify_webhook: str = ""

    # 可选：通用 Webhook（任意能收 JSON POST 的地址，如邮件网关/短信网关）
    # 请求体: {"title","text","group_name","room_id","waiting_minutes","suggestion"}
    generic_notify_webhook: str = ""

    # 仅处理指定产品线：js / de / mk / all；多个用逗号：js,de,mk
    watch_product: str = "js,de,mk"
    # WorkBuddy 话术 skill 路径（注入 desktop systemPrompt）
    workbuddy_style_skill: str = ""

    # none | sync_webhook | local_cmd | ai_gateway | desktop
    # sync_webhook: POST WorkBuddy，期望同步返回 suggestion
    # local_cmd: 执行本机命令（可调 WorkBuddy skill 脚本），stdout 为建议
    # ai_gateway: OpenAI 兼容网关 /chat/completions（直连公司 AI 网关，不经桌面）
    # desktop: 调用本机桌面 WorkBuddy 的 codebuddy --serve（/api/v1/llm/completions）
    workbuddy_mode: str = "none"
    workbuddy_webhook_url: str = ""
    workbuddy_webhook_token: str = ""
    workbuddy_timeout_seconds: int = 120
    workbuddy_local_cmd: str = ""
    # 可选：显式指定桌面 Gateway，如 http://127.0.0.1:49702；留空则自动发现 --serve 端口
    workbuddy_desktop_url: str = ""
    workbuddy_desktop_agent: str = "pulse"
    # 本地 GitHub 仓库根目录，桌面 WorkBuddy 检索/引用代码用
    workbuddy_workspace: str = ""
    suggest_system_hint: str = (
        "你是售后一线助手。根据客户群最新未回复内容，结合知识库给出："
        "1)问题摘要 2)建议回复话术 3)仍需向客户确认的信息。"
        "不要编造未在知识库中的结论；不确定时明确写「建议转人工确认」。"
    )

    # OpenAI 兼容 AI 网关（WORKBUDDY_MODE=ai_gateway 时使用）
    ai_gateway_base_url: str = ""
    ai_gateway_api_key: str = ""
    ai_gateway_model: str = "f2c-auto"
    # 超时候选告警前，用 AI 再判一次是否真需跟进（收到/致谢等会跳过）
    followup_ai_judge: bool = True

    ingest_token: str = ""

    # 推送未回复客户表前是否先跑 SentLink 同步 + 强制扫描（避免清单滞后）
    digest_sync_before_send: bool = True

    # 内部 Confluence Wiki（wiki.fit2cloud.cn），Bearer PAT；空 Token=关闭
    confluence_base: str = "https://wiki.fit2cloud.cn"
    confluence_user: str = ""
    confluence_token: str = ""
    # 可选：限制空间，逗号分隔，如 JS,FK；空=全站搜
    confluence_space_keys: str = ""

    @property
    def data_path(self) -> Path:
        path = Path(self.data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def work_hours_enabled(self) -> bool:
        return bool(self.work_hours_start and self.work_hours_end)


@lru_cache
def get_settings() -> Settings:
    return Settings()
