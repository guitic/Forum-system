# AI 协作规范 · GraphFlow 上下文编排

> 本文档统一约定本仓库的 AI 协作规范,合并自原 `AGENTS.md`(Claude Code 用)与 `GEMINI.md`(Context-First Rule)。
> 工具配置规则文件位于:
> - [.trae/rules/git-commit-message.md](.trae/rules/git-commit-message.md) — 提交信息风格自定义(Trae IDE)
> - [.claude/rules/graphflow.md](.claude/rules/graphflow.md) — GraphFlow 规则(Claude Code)

---

## 1. GraphFlow 定位

[GraphFlow](https://github.com/roarpeng/graphflow) 是本地代码知识图谱 + 上下文压缩 MCP 服务,提供 10 个工具,用于在本仓库内做上下文编排、计划、执行与回报。

适用场景:Broad code exploration、implementation、debugging、review、architecture questions。

## 2. 推荐安装

### 2.1 通过 npx 启动(标准)

```json
{
  "mcpServers": {
    "graphflow": {
      "command": "npx",
      "args": ["-y", "--package=@roarpeng/graphflow", "graphflow-mcp"]
    }
  }
}
```

### 2.2 通过本地仓库启动(开发期)

```json
{
  "mcpServers": {
    "graphflow": {
      "command": "npm",
      "args": ["run", "start:mcp"],
      "cwd": "."
    }
  }
}
```

### 2.3 DeepSeek Harness

```bash
dsh plugin --profile web add @roarpeng/graphflow
# 或 ~/.dsh 已存在时
npx @roarpeng/graphflow install
```

## 3. Context-First 工作流(必读)

在本项目进行 broad code exploration、implementation、debugging、review 或 architecture 问题之前,**必须**遵守以下顺序:

1. **先调 `graphflow_context`**:以任务/查询为入参,获取 `summary`、`anchors`、`tokenBudget` 作为主要上下文。
2. **基于 anchors 决定是否深读**:仅当 anchors 指向某文件或压缩上下文不足时,才读取完整文件。
3. **多步或模糊任务先规划**:调用 `graphflow_plan` 后再实施。
4. **项目变更后刷新图谱**:调用 `graphflow_index` 保持图谱新鲜。
5. **闭环回报**:执行 `graphflow_run` 的 `executionDescriptor` 后,**必须**调用 `graphflow_report_outcome`,传入 `episodeId`、`success` 布尔与可选 `lessons`,关闭技能飞轮。
   - 飞轮默认自动关闭:待处理 episode 由 Claude Code hooks(或 `graphflow_report_outcome`)回填;设置 `GRAPHFLOW_AUTO_CAPTURE=0` 可退出。

> **禁止**:在未尝试 GraphFlow 上下文前扫描整个仓库或读取大量大文件。把 GraphFlow 输出当作结构化、节省 token 的上下文。

## 4. 工具清单(10 个)

| 工具 | 用途 |
|------|------|
| `graphflow_context` | 压缩并锚定代码上下文(首选入口) |
| `graphflow_plan` | 多步或模糊任务的规划 |
| `graphflow_run` | 按 `executionDescriptor` 执行 |
| `graphflow_report_outcome` | 闭环回报(episodeId + success + lessons) |
| `graphflow_insight` | 查看图谱洞察 |
| `graphflow_index` | 刷新代码图谱 |
| `graphflow_artifact` | 生成/管理工件 |
| `graphflow_skill_insights` | 查看技能洞察 |
| `graphflow_skill_guide` | 查看技能指引 |
| `graphflow_diagnose` | 诊断问题 |

## 5. `rootDir` 安全约定

- **必须**传入本项目的绝对路径作为 `rootDir`。
- **禁止**传入家目录、AppData 或未展开的 `${workspaceFolder}` 占位符 — GraphFlow 会拒绝不安全的工作区根。
- 若调用返回 `unsafe workspace root`,则**不带 `rootDir`** 重试(服务端改用其已配置的工作区)。

## 6. Cursor / 通用 MCP 客户端调用约定

使用 Cursor `CallMcpTool` 时,始终传 `server` + `toolName` + `arguments`:

- `server`:`"graphflow"` 或 `"user-graphflow"`
- `toolName`:例如 `"graphflow_context"`

## 7. CLI 兜底

仅当 MCP 不可用时才回退到 `graphflow ... --json`,例如:

```bash
graphflow --json outcome report <episodeId> <success>
```

## 8. 相关规则文件

- [`.trae/rules/git-commit-message.md`](.trae/rules/git-commit-message.md) — Trae IDE 提交信息风格规则
- [`.claude/rules/graphflow.md`](.claude/rules/graphflow.md) — Claude Code GraphFlow 规则(与本文件内容一致,工具配置载体)

## 9. 项目文档导航

- [README.md](README.md) — 项目总说明与快速启动
- [CODE_WIKI.md](CODE_WIKI.md) — 结构化代码 Wiki(12 章)
- [需求文档.md](需求文档.md) — V1.1 / V2.0 / V3.0 合并需求
- [deploy/README.md](deploy/README.md) — Rocky Linux 生产部署指南

---

> 文档版本:V1.0 · 合并自 `AGENTS.md` + `GEMINI.md` · 最后更新:2026-09-22
