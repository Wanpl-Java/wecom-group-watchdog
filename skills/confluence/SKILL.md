---
name: confluence
description: 访问私有化部署的 Confluence (wiki.fit2cloud.cn)。当用户需要查询飞致云内部 Wiki/Confluence 文档、用 CQL 搜索空间内容、获取页面正文、列出空间页面时使用。认证方式为 Personal Access Token (Bearer)。
version: 1.0.0
---

# Confluence 私有化部署技能

## 适用场景
- 用户提到「飞致云 wiki」「fit2cloud wiki」「Confluence」「内部知识库」并要查内容。
- 需要搜索内部文档、读取某页正文、列出某空间下的页面。

## 关键事实
- **Base URL**：`https://wiki.fit2cloud.cn`（http 会被 302 强制跳转 https，请直接用 https）。
- **认证**：`Authorization: Bearer <token>`（Personal Access Token）。Basic Auth 会返回 401，不要用。
- 凭证放同目录 `confluence.env`（`CONFLUENCE_BASE` / `CONFLUENCE_USER` / `CONFLUENCE_TOKEN`）。

## 调用方式
统一通过 helper 脚本（source 凭证并自动加认证头）：

```bash
bash ~/.workbuddy/skills/confluence/confluence.sh <subcommand> [args]
```

| 子命令 | 说明 | 示例 |
|---|---|---|
| `spaces [limit]` | 列出空间 | `confluence.sh spaces 10` |
| `search "<cql>" [limit]` | CQL 搜索 | `confluence.sh search "space=FK and type=page" 10` |
| `get <contentId>` | 取页面正文(view)+元信息 | `confluence.sh get 35259813` |
| `tree <spaceKey> [limit]` | 列空间页面 | `confluence.sh tree FK 20` |
| `page <spaceKey> <title>` | 按标题定位页面 | `confluence.sh page FK "JumpServer 运维手册"` |

Watchdog 服务侧已集成：配置 `.env` 的 `CONFLUENCE_TOKEN` 后，建议话术会自动 CQL 检索并注入 prompt。

## 输出处理
- 结果为 JSON；提取要点再返回用户，不要整段粘贴原始 JSON。
- `body.view.value` 为页面正文（含 HTML），需转成可读文本/摘要。
- 搜索结果的 `results[]._links.webui` 为页面 Web 路径；`results[].id` 为 contentId。

## 安全
- `confluence.env` / `.env` 含 Token，勿提交到仓库。
- 默认只读 GET；不要创建/更新页面，除非用户明确同意。
