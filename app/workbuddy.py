from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from .anti_aistyle import light_deai_customer_reply, load_anti_aistyle_block
from .config import Settings
from .kb import format_kb_hint, match_kb_articles
from .models import SuggestResult, UnansweredCase

logger = logging.getLogger(__name__)


_SYSTEM_OUTPUT_RULES = (
    "只输出给内部同事看的建议，不要假装已回复客户；不要改仓库文件。"
    "结构必须且仅含四段，顺序固定："
    "1)问题类型与紧急程度；2)问题内容；3)问题分析（内部机制/排查要点，可稍细）；"
    "4)回复建议（可直接复制发客户，必须简明：一句话或一小段，禁止分点罗列）。"
    "第4段对外口吻必须统一（一线支持同一套话术，不按人设分类）："
    "开场一律用「老师好，」；短句白话；像企微一线同事；不用书面腔、不装口语。"
    "回复规划按题型：机制咨询只答逻辑；故障才要版本/截图/向日葵；有高匹配KB则文档名+URL嵌进同一段。"
    "咨询答完即止；故障才补「把版本和截图发我就行」一类收束；禁止客服套话收尾。"
    "量体裁衣：第3/4段都要紧扣客户原问，问什么答什么，不要凑篇幅。"
    "纯机制/原理咨询（如调用账号逻辑是什么）：第4段只讲清逻辑即可收束；"
    "禁止追问版本号、截图、复现步骤、是否报错、是否选错账号等补充说明。"
    "仅当客户明确报错/连不上/失败/异常时，第4段才可顺带要版本/报错/复现，并对照问题给排查方向。"
    "回答优先级：①本地 jumpserver 源码/摘录 ②客户上下文 ③仅当prompt标明「知识库高度匹配」才给KB直链。"
    "没有高度匹配时禁止贴知识库；禁止把原理类问题答成发布机部署/代填配置教程。"
    "问「远程应用调用/选账号逻辑」时：第3段讲两路账号从哪取、选取顺序；"
    "禁止展开发布机安装、OpenSSH/WinRM、代填脚本、发布机配置步骤；也不要贴这类KB。"
    "源码依据（摘录里有则照此说）：ConnectToken.account_object=授权选的目标资产账号；"
    "get_applet_option→Applet.select_host_account 选发布机登录账号："
    "先私有（同名虚拟/域控/本地js_用户名），再公共jms_*（避开占用），选中后加锁、断开release。"
    "第4段去AI味：禁止弯引号“”‘’、半角引号\"'、Markdown加粗**、反引号`、间隔号·、emoji；"
    "不要写「首先/其次/综上所述」；不编造；勿用 accountobject 这类拼错写法。"
)


def _style_system_prompt(prefix: str, style: str) -> str:
    text = f"{prefix}{_SYSTEM_OUTPUT_RULES}"
    if style:
        text += "\n\n===== jumpserver-support-style SKILL =====\n" + style
    anti = load_anti_aistyle_block()
    if anti:
        text += "\n\n" + anti
    return text


def _finalize_suggestion(text: str, customer_excerpt: str = "") -> str:
    return light_deai_customer_reply((text or "").strip(), customer_excerpt=customer_excerpt)


def _build_prompt(case: UnansweredCase, hint: str, extra: str = "") -> str:
    kb_block = format_kb_hint(case.customer_excerpt or "")
    body = (
        f"{hint}\n\n"
        f"客户群: {case.group_name}\n"
        f"产品线: {case.product or '未知'}\n"
        f"已等待: {case.waiting_minutes} 分钟\n"
        f"最近对话:\n{case.customer_excerpt}\n"
        f"\n{kb_block}\n"
    )
    if extra:
        body += f"\n本地代码/文档摘录（仅此范围内作答，未出现的结论不要编）：\n{extra}\n"
    return body


def _case_payload(case: UnansweredCase, hint: str) -> Dict[str, Any]:
    return {
        "event": "unanswered_alert",
        "room_id": case.room_id,
        "group_name": case.group_name,
        "product": case.product,
        "support_userids": case.support_userids,
        "last_customer_msg_id": case.last_customer_msg_id,
        "last_customer_at": case.last_customer_at,
        "waiting_minutes": case.waiting_minutes,
        "customer_excerpt": case.customer_excerpt,
        "recent_messages": [m.model_dump() for m in case.recent_messages],
        "prompt": _build_prompt(case, hint),
    }


async def suggest_reply(case: UnansweredCase, settings: Settings) -> SuggestResult:
    mode = (settings.workbuddy_mode or "none").lower().strip()
    if mode in ("", "none"):
        return SuggestResult(
            suggestion=_fallback_suggestion(case),
            source="fallback",
        )
    if mode in ("desktop", "workbuddy_desktop", "desktop_gateway"):
        return await _via_desktop_gateway(case, settings)
    if mode == "sync_webhook":
        return await _via_webhook(case, settings)
    if mode == "local_cmd":
        return await _via_local_cmd(case, settings)
    if mode in ("ai_gateway", "openai"):
        return await _via_ai_gateway(case, settings)
    logger.warning("unknown WORKBUDDY_MODE=%s, using fallback", mode)
    return SuggestResult(suggestion=_fallback_suggestion(case), source="fallback")


def _fallback_suggestion(case: UnansweredCase) -> str:
    excerpt = (case.customer_excerpt or "").strip()
    short = excerpt if len(excerpt) <= 120 else excerpt[:120] + "…"
    hits = match_kb_articles(excerpt)
    analysis = (
        "自动兜底建议，需同事结合群上下文与版本信息再判。"
        if not hits
        else f"知识库高度匹配到《{hits[0][0]}》，可按文档引导；仍需确认版本与卡点。"
    )
    reply = (
        f"老师好，先按这篇看下：{hits[0][1]}，卡点把版本和截图发我。"
        if hits
        else "老师好，这个问题我们先对下具体场景，请补充 JumpServer 版本和报错截图，我们继续看。"
    )
    return _finalize_suggestion(
        "1) 问题类型与紧急程度\n"
        "类型：其他（自动兜底，待人工判定）\n"
        f"紧急程度：P3（客户已等待约 {case.waiting_minutes} 分钟，默认普通）\n\n"
        "2) 问题内容\n"
        f"群{case.group_name}客户消息待回复。"
        f"原文要点：{short or '无摘录'}\n\n"
        "3) 问题分析\n"
        f"{analysis}\n\n"
        "4) 回复建议\n"
        f"{reply}",
        customer_excerpt=excerpt,
    )


_WS_SKIP_DIR = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".idea",
    "dist",
    "build",
}
_WS_EXTS = {".py", ".md", ".yml", ".yaml", ".rst", ".txt", ".json"}
_WS_STOP = {
    "的",
    "了",
    "是",
    "在",
    "和",
    "有",
    "吗",
    "呢",
    "啊",
    "请",
    "一下",
    "这个",
    "那个",
    "我们",
    "你们",
    "老师",
    "你好",
    "如何",
    "怎么",
    "什么",
    "还是",
    "一个",
    "the",
    "and",
    "for",
    "with",
}


# 售后高频词：中文无空格时也要从长句里抠出来做检索
_WS_DOMAIN_TERMS = (
    "远程应用",
    "发布机",
    "应用发布机",
    "调用账号",
    "账号逻辑",
    "select_host_account",
    "applet-option",
    "ConnectToken",
    "代填",
    "jms_",
    "RemoteApp",
    "VirtualApp",
    "Tinker",
    "改密",
    "推送账号",
    "收集账号",
    "资产树",
    "组织架构",
    "单点登录",
    "LDAP",
    "CAS",
    "OIDC",
    "SAML",
    "数据库",
    "SQLServer",
    "MySQL",
    "Oracle",
    "Kubernetes",
    "K8s",
    "录像",
    "命令过滤",
    "工单",
    "授权",
    "连接失败",
    "证书",
    "SSO",
    "RDP",
    "SSH",
    "WinRM",
    "OpenSSH",
    "RDS",
    "Chrome",
    "WebLite",
    "Applet",
    "部署",
    "初始化",
)


def _excerpt_keywords(excerpt: str, limit: int = 8) -> List[str]:
    text = (excerpt or "").strip()
    out: List[str] = []
    seen = set()

    def _add(token: str) -> None:
        t = token.strip()
        if len(t) < 2 or t.lower() in _WS_STOP or t in _WS_STOP:
            return
        key = t.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(t)

    # 1) 领域词优先（子串命中）
    for term in _WS_DOMAIN_TERMS:
        if term.lower() in text.lower():
            _add(term)
            if len(out) >= limit:
                return out

    # 2) 英文/数字 token + 中文连续段
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9._-]{1,}|[\u4e00-\u9fff]{2,}", text):
        piece = m.group(0)
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]*", piece):
            _add(piece)
        elif len(piece) <= 6:
            _add(piece)
        else:
            # 长中文：切 2~4 字窗口，优先较长
            for n in (4, 3, 2):
                for i in range(0, len(piece) - n + 1):
                    _add(piece[i : i + n])
                    if len(out) >= limit:
                        return out
        if len(out) >= limit:
            return out

    return out[:limit]


_WS_CODE_ALIASES = {
    "调用账号": ["select_host_account", "get_applet_option", "applet_option", "host_account"],
    "账号逻辑": ["select_host_account", "try_to_use_private_account", "select_a_public_account"],
    "账号": ["select_host_account", "account_object", "generate_accounts", "jms_"],
    "远程应用": ["applet", "RemoteApp", "select_host", "get_applet_option"],
    "发布机": ["AppletHost", "applet_host", "select_host"],
    "代填": ["autofill", "account_object", "ConnectionTokenSecret"],
    "ConnectToken": ["connection_token", "ConnectionToken", "get_applet_option"],
}


def collect_workspace_snippets(workspace: str, excerpt: str, max_chars: int = 4500) -> str:
    root = Path(workspace or "").expanduser()
    if not workspace or not root.is_dir():
        return ""
    keywords = _excerpt_keywords(excerpt)
    expanded: List[str] = []
    seen_kw = set()
    for k in keywords:
        for item in (k, *(_WS_CODE_ALIASES.get(k) or [])):
            key = item.lower()
            if key in seen_kw:
                continue
            seen_kw.add(key)
            expanded.append(item)
    keywords = expanded
    if not keywords:
        return f"（本地仓库已挂载：{root}，但客户原文无法抽出检索词）"

    code_paths = [
        root / "apps" / "terminal" / "models" / "applet",
        root / "apps" / "authentication",
        root / "apps" / "terminal",
        root / "jumpserver" / "apps" / "terminal" / "models" / "applet",
        root / "jumpserver" / "apps" / "authentication",
        root / "jumpserver" / "apps" / "terminal",
        root / "jumpserver" / "apps" / "terminal" / "applets",
        root / "lion",
        root / "koko",
    ]
    doc_paths = [
        root / "docs",
        root / "readmes",
        root / "apps" / "accounts",
        root / "apps" / "assets",
        root / "README.md",
        root / "jumpserver" / "docs",
        root / "jumpserver" / "readmes",
        root / "jumpserver" / "apps" / "accounts",
        root / "jumpserver" / "apps" / "assets",
        root / "jumpserver" / "apps" / "terminal" / "automations" / "deploy_applet_host",
        root / "jumpserver" / "README.md",
        root / "koko" / "README.md",
        root / "lion" / "README.md",
        root / "luna" / "README.md",
        root / "luna" / "applets",
        root / "lina" / "README.md",
    ]
    code_intent = any(
        k in seen_kw
        for k in (
            "select_host_account",
            "get_applet_option",
            "applet",
            "remoteapp",
            "account_object",
            "connection_token",
            "调用账号",
            "账号逻辑",
        )
    )
    prefer = (code_paths + doc_paths) if code_intent else (doc_paths + code_paths)
    hits: List[str] = []
    used = 0
    for base in prefer:
        if used >= max_chars:
            break
        files: List[Path] = []
        if base.is_file():
            files = [base]
        elif base.is_dir():
            for p in base.rglob("*"):
                if not p.is_file():
                    continue
                if any(part in _WS_SKIP_DIR for part in p.parts):
                    continue
                if p.suffix.lower() not in _WS_EXTS:
                    continue
                if p.stat().st_size > 400_000:
                    continue
                files.append(p)
                if len(files) >= 80:
                    break
        for fp in files:
            if used >= max_chars:
                break
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            low = text.lower()
            if not any(k.lower() in low for k in keywords):
                continue
            rel = fp.relative_to(root).as_posix()
            # 取首个命中附近
            idx = min((low.find(k.lower()) for k in keywords if k.lower() in low), default=-1)
            start = max(0, idx - 80)
            snippet = text[start : start + 420].replace("\r\n", "\n").strip()
            block = f"### {rel}\n{snippet}\n"
            hits.append(block)
            used += len(block)
            if len(hits) >= 6:
                break
    if not hits:
        return f"（已挂载仓库 {root}，关键词 {keywords} 未在 docs/accounts/assets 中命中，请转人工并结合官方文档）"
    header = f"本地 JumpServer 仓库：{root}\n检索词：{', '.join(keywords)}\n"
    return header + "\n".join(hits)


def _discover_desktop_gateway_url() -> str:
    """从本机 WorkBuddy 进程命令行解析 codebuddy --serve --port。"""
    import re
    import subprocess

    try:
        # Windows: 枚举命令行含 codebuddy --serve 的进程
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process -Filter \"Name='WorkBuddy.exe'\" | "
                    "ForEach-Object { $_.CommandLine }"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:  # noqa: BLE001
        logger.exception("discover WorkBuddy desktop gateway failed")
        return ""

    for line in (completed.stdout or "").splitlines():
        if "codebuddy" not in line or "--serve" not in line:
            continue
        m = re.search(r"--port\s+(\d+)", line)
        if m:
            return f"http://127.0.0.1:{m.group(1)}"
    return ""


def _load_style_skill(settings: Settings) -> str:
    candidates = []
    if (settings.workbuddy_style_skill or "").strip():
        candidates.append(Path(settings.workbuddy_style_skill.strip()))
    # 默认：用户 WorkBuddy skills + 本仓库副本
    home = Path.home() / ".workbuddy" / "skills" / "jumpserver-support-style" / "SKILL.md"
    candidates.append(home)
    candidates.append(Path(__file__).resolve().parents[1] / "workbuddy" / "jumpserver-support-style" / "SKILL.md")
    for p in candidates:
        try:
            if p.is_file():
                text = p.read_text(encoding="utf-8")
                # 控制注入长度
                return text[:8000]
        except OSError:
            continue
    return ""


async def _via_desktop_gateway(case: UnansweredCase, settings: Settings) -> SuggestResult:
    """调用桌面 WorkBuddy 内置的 codebuddy --serve：POST /api/v1/llm/completions。"""
    base = (settings.workbuddy_desktop_url or "").rstrip("/")
    if not base:
        base = await asyncio.to_thread(_discover_desktop_gateway_url)
    if not base:
        logger.error("WorkBuddy desktop gateway not found (is WorkBuddy running?)")
        return SuggestResult(suggestion=_fallback_suggestion(case), source="fallback")

    workspace = (settings.workbuddy_workspace or "").strip()
    snippets = ""
    if workspace:
        snippets = await asyncio.to_thread(
            collect_workspace_snippets, workspace, case.customer_excerpt
        )
        logger.info("workspace snippets chars=%s root=%s", len(snippets), workspace)

    style = await asyncio.to_thread(_load_style_skill, settings)
    system_prompt = _style_system_prompt(
        "你是飞致云 JumpServer 售后一线助手（经 WorkBuddy 桌面调度）。",
        style,
    )

    url = f"{base}/api/v1/llm/completions"
    payload = {
        "systemPrompt": system_prompt,
        "userPrompt": _build_prompt(case, settings.suggest_system_hint, snippets),
        "temperature": 0.3,
        "maxTokens": 1200,
        "maxTurns": 8,
        "agentName": (settings.workbuddy_desktop_agent or "pulse").strip() or "pulse",
        "cwd": workspace or None,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    headers = {
        "Content-Type": "application/json",
        "X-CodeBuddy-Request": "1",
    }
    try:
        async with httpx.AsyncClient(timeout=settings.workbuddy_timeout_seconds) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
    except Exception:  # noqa: BLE001
        logger.exception("WorkBuddy desktop gateway failed url=%s", url)
        return SuggestResult(suggestion=_fallback_suggestion(case), source="fallback")

    text = ""
    if isinstance(data, dict):
        text = (data.get("text") or data.get("suggestion") or "").strip()
        if not text:
            text = _extract_suggestion(data)
        if data.get("success") is False and data.get("error"):
            logger.warning(
                "WorkBuddy desktop reported error=%s text_len=%s",
                data.get("error"),
                len(text),
            )

    if not text:
        return SuggestResult(
            suggestion=_fallback_suggestion(case),
            source="fallback",
            raw=data if isinstance(data, dict) else None,
        )
    return SuggestResult(
        suggestion=_finalize_suggestion(text, case.customer_excerpt or ""),
        source="workbuddy_desktop",
        raw=data if isinstance(data, dict) else None,
    )


async def _via_ai_gateway(case: UnansweredCase, settings: Settings) -> SuggestResult:
    base = (settings.ai_gateway_base_url or "").rstrip("/")
    key = (settings.ai_gateway_api_key or "").strip()
    model = (settings.ai_gateway_model or "f2c-auto").strip()
    if not base or not key:
        logger.warning("AI gateway base_url/api_key empty, fallback")
        return SuggestResult(suggestion=_fallback_suggestion(case), source="fallback")

    workspace = (settings.workbuddy_workspace or "").strip()
    snippets = ""
    if workspace:
        snippets = await asyncio.to_thread(
            collect_workspace_snippets, workspace, case.customer_excerpt
        )
        logger.info("ai_gateway workspace snippets chars=%s root=%s", len(snippets), workspace)

    style = await asyncio.to_thread(_load_style_skill, settings)
    system_content = _style_system_prompt(
        "你是飞致云 JumpServer 售后一线助手。",
        style,
    )

    url = f"{base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    user_prompt = _build_prompt(case, settings.suggest_system_hint, snippets)
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_prompt},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=settings.workbuddy_timeout_seconds) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception:  # noqa: BLE001
        logger.exception("AI gateway suggest failed")
        return SuggestResult(suggestion=_fallback_suggestion(case), source="fallback")

    suggestion = _extract_suggestion(data)
    if not suggestion:
        return SuggestResult(
            suggestion=_fallback_suggestion(case),
            source="fallback",
            raw=data if isinstance(data, dict) else None,
        )
    return SuggestResult(
        suggestion=_finalize_suggestion(suggestion, case.customer_excerpt or ""),
        source="ai_gateway",
        raw=data,
    )


async def _via_webhook(case: UnansweredCase, settings: Settings) -> SuggestResult:
    url = settings.workbuddy_webhook_url.strip()
    if not url:
        logger.warning("WORKBUDDY_WEBHOOK_URL empty, fallback")
        return SuggestResult(suggestion=_fallback_suggestion(case), source="fallback")

    headers = {"Content-Type": "application/json"}
    if settings.workbuddy_webhook_token:
        headers["Authorization"] = f"Bearer {settings.workbuddy_webhook_token}"

    payload = _case_payload(case, settings.suggest_system_hint)
    try:
        async with httpx.AsyncClient(timeout=settings.workbuddy_timeout_seconds) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
    except Exception:  # noqa: BLE001
        logger.exception("WorkBuddy webhook failed")
        return SuggestResult(suggestion=_fallback_suggestion(case), source="fallback")

    suggestion = _extract_suggestion(data)
    if not suggestion:
        suggestion = _fallback_suggestion(case)
        return SuggestResult(suggestion=suggestion, source="fallback", raw=data if isinstance(data, dict) else None)
    return SuggestResult(
        suggestion=suggestion,
        source="workbuddy_webhook",
        raw=data if isinstance(data, dict) else None,
    )


def _extract_suggestion(data: Any) -> str:
    if isinstance(data, str):
        return data.strip()
    if not isinstance(data, dict):
        return ""
    for key in ("suggestion", "reply", "answer", "content", "text"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    # OpenAI-ish
    try:
        return data["choices"][0]["message"]["content"].strip()
    except Exception:  # noqa: BLE001
        return ""


async def _via_local_cmd(case: UnansweredCase, settings: Settings) -> SuggestResult:
    """调用 WorkBuddy skill 脚本。Windows 上改为进程内加载，避免 asyncio 子进程限制。"""
    import importlib.util
    import os
    from pathlib import Path

    cmd = settings.workbuddy_local_cmd.strip()
    if not cmd:
        logger.warning("WORKBUDDY_LOCAL_CMD empty, fallback")
        return SuggestResult(suggestion=_fallback_suggestion(case), source="fallback")

    body = _case_payload(case, settings.suggest_system_hint)
    args = shlex.split(cmd, posix=False)
    script = next((a for a in args if a.endswith(".py")), "")
    if not script or not Path(script).is_file():
        logger.error("WorkBuddy local_cmd script not found in: %s", cmd)
        return SuggestResult(suggestion=_fallback_suggestion(case), source="fallback")

    env_patch = {
        "AI_GATEWAY_BASE_URL": settings.ai_gateway_base_url or "",
        "AI_GATEWAY_API_KEY": settings.ai_gateway_api_key or "",
        "AI_GATEWAY_MODEL": settings.ai_gateway_model or "f2c-auto",
        "SUGGEST_SYSTEM_HINT": settings.suggest_system_hint or "",
    }

    def _run_inprocess() -> str:
        old = {k: os.environ.get(k) for k in env_patch}
        os.environ.update(env_patch)
        try:
            spec = importlib.util.spec_from_file_location("wb_watchdog_suggest", script)
            if spec is None or spec.loader is None:
                return ""
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            fn = getattr(mod, "suggest_from_body", None)
            if callable(fn):
                return (fn(body) or "").strip()
            return ""
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    try:
        suggestion = await asyncio.to_thread(_run_inprocess)
    except Exception:  # noqa: BLE001
        logger.exception("WorkBuddy local_cmd failed")
        return SuggestResult(suggestion=_fallback_suggestion(case), source="fallback")

    if not suggestion:
        return SuggestResult(suggestion=_fallback_suggestion(case), source="fallback")
    return SuggestResult(suggestion=suggestion, source="workbuddy_local")
