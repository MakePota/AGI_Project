# 0005 浏览器自动化用 Playwright 手写窄工具

- 日期：2026-10-12
- 状态：已采用

## 背景

需求是让 Agent 把游戏加入 Steam 购物车。动手前按守则 §2 调研了 GitHub 上的现成方案
（TMHSDigital、jjstrat3、heisenberg8055 等 steam-mcp 类项目），结论：

**全部只做查询**——商店信息、价格、玩家数据、成就、库存，**没有一个具备购物车能力**。
原因是 Steam 的公开 Web API 不暴露购物车端点。所以这件事确实要自己写。

## 决策

**用 Playwright 手写窄工具 `steam.add_to_cart`**（风险等级 `WRITE`，因此自动接入审批中断），
另配一个只读的 `steam.search_games` 供确认正式名称。

Playwright 是普通 Python 库，`import` 即用，**不需要 MCP Client 层**。

## 被否决的方案

- **挂接 Steam 桌面客户端的 CDP**：Steam 客户端基于 CEF，技术上可通过
  `-cef-enable-debugging`、`SharedJSContext` 目标、`window.SteamClient.*` 挂接。
  否掉的理由有三条：内部 API 未公开、随客户端版本变化、调试端口冲突时会**静默失败**。
  社区共识是它只适合只读诊断。而且它**本质上仍是浏览器自动化**，只是对着一个支持更差、
  更不稳定的目标做——没有任何优势，只是更难。
- **接入 Playwright MCP Server**：它提供浏览器原语（点击、输入、截图），适合**流程未知、
  需要模型现场探索**的场景。而加购是一个**流程固定、目标界面已知**的动作。用 MCP 的后果是
  计划里会出现「点第 3 个按钮」这种既无法审批也无法审查的步骤（判据见 ADR 0006）。
- **调用未公开的 Steam Web API**：违反 ADR 0006 的第一条原则。

## 后果

- 新增 `playwright` 依赖，并需下载一个 Chromium（数百 MB）。**已下载完毕。**
- **选择器会随 Steam 页面改版失效**——这是手写窄工具的固有代价。缓解方式是加购后
  **到购物车页面复核**，让「成功」可验证，而不是「点了就算成功」。
- 国内直连 Steam 不可达，Chromium 必须显式走代理（`.env` 中的 `AGI_BROWSER_PROXY`）。
- 手写与 MCP **不互斥**：将来若出现需要模型现场探索的浏览器任务，可以再把 Playwright MCP
  作为**另一种工具来源**登记进同一张注册表（登记方式见 ADR 0007）。
