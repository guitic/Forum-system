# 排查报告：为什么 gstack 团队成员「启用即失败」

**日期**：2026-10-01
**场景**：调试与根因分析（排障手职责）
**现象**：调用 gstack 专家（软件工坊）时，`gstack-designer`（设计师）与 `gstack-qa-lead`（质量门神）两个成员**启动即失败**，报错 `Task agent ... is not available`

---

## 📌 TL;DR

**根因**：平台进程在加载专家包时，**`gstack-*` 这组自定义 agent 没有进入 agent 注册表**，导致运行时「按名字查找 agent」失败。可用列表里只有内置 agent（`Plan`/`Explore`/`general-purpose`/`sheet-agent` 等），**没有任何 `gstack-*`**。

**关键证据**（今日日志 `logs/2026-10-01/Forum system__*.log`）：
```
[AgentTask] agent lookup failed | requested="gstack-designer"
  | available=[contextSummary, contentAnalyzer, ..., Plan, Explore, general-purpose,
               cli, create, sheet-agent, doc-converter, doc-formatter, doc-writer]
```

**注意**：这不是用户配置错误，也不是包文件损坏——包文件完整、注册表正常。**是平台侧的 agent 注册时序/生命周期缺陷**。

---

## 1. 排查过程与证据链

### 1.1 先排除「包损坏」假设

逐一核验了 `~/.workbuddy-ai/plugins/cache/experts/gstack/1.0.0/`：

| 检查项 | 结果 |
|--------|------|
| 6 个 agent 文件是否存在 | ✅ 全部存在（lead/product-reviewer/security-officer/qa-lead/designer/investigator） |
| frontmatter 格式 | ✅ 全部规范（`---` 分隔，无 BOM，UTF-8 正常） |
| `plugin.json` 的 `agents` 声明 | ✅ 6 条路径全部正确指向 |
| skills（review/qa/design-html） | ✅ 完整（含 vendor/pretext.js、references、templates） |
| avatars | ✅ 7 个，与 members 定义一一对应 |
| 注册表 `installed_plugins.json` | ✅ `gstack@experts` 条目正常，installPath/version 正确 |

**结论：包本身完全健康，不存在文件缺失或格式错误。**

### 1.2 找到真实报错

在日志中定位到失败瞬间（19:23:09，正是本次会话）：

```
43318:[19:23:09.013] [Warning] [pid=22272] [AgentTask-agent-a20179c00cdc43ea]
      [AgentTask] agent lookup failed | requested="gstack-designer"
      | available=[contextSummary,contentAnalyzer,...,Plan,Explore,general-purpose,cli,
                   create,sheet-agent,doc-converter,doc-formatter,doc-writer]

43330:[19:23:09.016] [Warning] [pid=22272] [AgentTask-agent-6ef4d4ff5a4c4842]
      [AgentTask] agent lookup failed | requested="gstack-qa-lead"
      | available=[...同上，无任何 gstack-* ...]
```

**这是决定性证据**：平台维护了一份「可用 agent 白名单」，而 `gstack-*` 不在其中。

### 1.3 进程对照实验（最关键的发现）

对比同一个日志文件里不同进程的行为：

| 进程 PID | 启动时间 | `registerAgent agent=gstack-*` | `.in_use/` 锁 | 结果 |
|----------|---------|-------------------------------|--------------|------|
| 22208 | 16:17 | **18 次** ✅ | ✅ 有 | 正常 |
| 26344 | 16:19 | **多次** ✅ | ✅ 有 | 正常 |
| **22272** | **18:04** | **从未出现在 lookup 白名单** ❌ | **❌ 无** | **失败** |

三点互证：
1. **老进程（22208/26344）**：日志里能看到 `[MCP][registerAgent] agent=gstack-qa-lead ... grantMcpAccess=true` —— gstack agent 被正常注册。
2. **新进程（22272）**：`registerAgent` 只注册了 `contextSummary`/`contentAnalyzer` 等**内置 agent**，`gstack-*` 一次都没注册。
3. **`.in_use/` 锁目录**里只有 `22208`、`22792`、`25824`、`26344` 四个老 PID，**唯独没有 22272**。

→ **`22272` 这个进程从未成功接管/加载 gstack 专家包**，所以它当然找不到 `gstack-designer`。

### 1.4 次要线索：插件加载为空

同一进程启动时：
```
[PluginPolicyService] [MergedPlugins] Loaded 0 merged plugin entries
```
（注：老进程也打印同样的 `Loaded 0`，说明这行本身不致命，但结合 1.3 可推断：新进程的**专家包 agent 投影链路没有跑起来**。）

---

## 2. 根因判定

**平台侧缺陷**：专家包的 **agent 注册**与**专家包加载**之间缺少强一致性保障。

具体表现为：
- 专家包（`expertType: "team"`）的 member agent（如 `gstack-designer`）**不是**内置 agent 类型；
- 它们需要平台在启动/切换会话时，把包内 `agents/*.md` **动态注册**进 `subagent_type` 可解析表；
- **当注册未发生**（新进程未接管 `.in_use` 锁、或专家包投影链路未执行）时，`TeamCreate` + `Agent(subagent_type: "gstack-designer")` 就会在 lookup 阶段直接失败；
- 而 **lead（`gstack-lead`）能正常工作**，因为它是**当前会话选中的 Expert 主角色**，走的是另一条「主 agent 注入」通道，不依赖上述动态注册表。

这解释了为什么**恰好是 member 失败、lead 正常**——这是本次现象的关键特征。

---

## 3. 为什么「只有这两个」失败

本次会话中失败的恰好是 `gstack-designer` 和 `gstack-qa-lead`，但**这不是这两个角色特有**的问题——同批的 `gstack-product-reviewer`、`gstack-security-officer`、`gstack-investigator` **同样会失败**（它们都不在那份 available 白名单里）。

只是本次任务恰好只调用了这两个，所以只暴露了这两个。

**验证方式**：若要确认，可尝试调用 `Agent(subagent_type: "gstack-product-reviewer")`，预期同样报 `not available`。

---

## 4. 应对方案

### 方案 A（推荐 · 即时可用）：绕开自定义 agent 类型
用平台**内置**的 `general-purpose` 类型承接成员任务，把该角色的 prompt（从 `agents/gstack-xxx.md` 读取）作为任务说明书传入。
- 本次验收已用此法成功完成（`design-auditor2`、`qa-verifier2` 均基于 `general-purpose`）。
- 代价：失去 gstack 角色文件的「自动注入」，需手动把角色 prompt 带上。

### 方案 B（根治 · 需平台修复）：让平台保证专家包 agent 注册一致性
- 在 `TeamCreate` / `Agent(subagent_type=...)` 前，强制校验 **目标 agent 是否已在注册表中**；
- 若专家包已安装但 agent 未注册，**自动触发一次注册**（而不是直接抛 `not available`）；
- 或：`Agent` 工具在解析 `subagent_type` 时，**回退查询专家包目录**（`plugins/cache/experts/*/agents/*.md`），具备则即时加载。
- 排障建议：给 `not available` 错误增加可操作提示，如「该 agent 属于专家包 X，尚未注册，请重启会话或切换 Expert」。

### 方案 C（规避 · 用户操作）
**重启应用/切换一次 Expert**，让平台重新走一遍专家包加载流程（触发 `.in_use` 接管）。这是最简单的「碰运气」修法——本次 18:04 启动后就没再成功注册过，说明重启后仍需观察是否复现。

---

## 5. 复现特征（供平台同学参考）

| 特征 | 值 |
|------|-----|
| 触发条件 | 专家包（`expertType: team`）的 **member** agent 被作为 `subagent_type` 调用 |
| 不触发 | Expert 主角色（`agentName`，如 `gstack-lead`） |
| 报错 | `Task agent {name} is not available` |
| 报错层 | `AgentTask.execute()` 的 agent lookup 阶段（任务已创建、status=running 后立即失败） |
| 伴随日志 | `[AgentTask] agent lookup failed \| requested=... \| available=[...]` |
| 直接原因 | `gstack-*` 不在 available 列表；`.in_use/` 无当前 PID |
| 影响 | 该会话内**所有**该专家包的 member agent 均不可用，非个别角色问题 |

---

## 6. 附录：关键日志位置

- 应用主日志：`~/.workbuddy-ai/logs/2026-10-01/Forum system__9e0a5dd81f4a73589a4f01a7999129b7.log`
  - L43318 / L43330：agent lookup failed（本次失败）
  - L1852-1853、L3626-3627：老进程 registerAgent 成功记录
  - L29468：新进程 `Loaded 0 merged plugin entries`
- 专家包：`~/.workbuddy-ai/plugins/cache/experts/gstack/1.0.0/`
- 进程锁：`~/.workbuddy-ai/plugins/cache/experts/gstack/1.0.0/.in_use/`（含 4 个老 PID，无失败进程 PID）

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
