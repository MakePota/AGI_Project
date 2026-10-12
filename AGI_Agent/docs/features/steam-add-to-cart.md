# 功能：Steam 加购

- 状态：已完成（2026-10-12），三条路径已实测
- 涉及文件：`src/agi_agent/tools/builtin/steam.py`、`scripts/steam_login.py`、
  `tests/unit/test_steam_matching.py`
- 相关决策：[0005 用 Playwright 手写窄工具](../decisions/0005-playwright-for-browser-automation.md)、
  [0006 外部能力接入策略](../decisions/0006-external-capability-policy.md)

## 要解决什么问题

让 Agent 把指定游戏加入 Steam 购物车。这是**第一个会真实改动外部账号状态的工具**
（`RiskLevel.WRITE`），因此也是审批中断第一次真正起作用的地方。

**范围边界：只操作购物车，不涉及结账与支付。**

## 最终怎么做

两个工具：

| 工具 | 风险 | 作用 |
| --- | --- | --- |
| `steam.search_games` | `READ` | 搜索游戏，列出靠前结果的名称与 appid，用于确认正式名称 |
| `steam.add_to_cart` | `WRITE` | 加购；因风险等级自动接入审批中断 |

执行流程：

1. 用 `game` 参数去商店搜索，在**前 5 条**结果里找**完全同名**的那一个；
   找不到就报错并**列出候选**，要求把名字写全。
2. 打开该游戏页面，在 `#game_area_purchase` 内找第一个 `href` 含 `addToCart(` 的链接并点击。
3. **到购物车页面复核**，确认商品真的在里面，然后才返回成功。

**登录**：`scripts/steam_login.py` 打开浏览器由**人工**扫码登录一次，
会话存入 `.secrets/steam_state.json`；之后每次用一个新的 browser context 复用该状态。
`--verify` 可以在无头模式下校验会话是否仍有效。

## 为什么这么做 / 被否决的方案

- **为什么只接受完全同名，不做模糊匹配**：这是整个功能里最关键的取舍，
  三种「更省事」的匹配策略都被真实数据证伪过。**完整的失败过程写在
  `steam.py` 的 `_pick_result()` 注释里——改匹配逻辑前必须先读它。**

  这里只留结论：加购会真实改动账号状态，而审批发生在**执行之前**，
  用户批准的只是意图，无法预知它会被解析成哪个商品。所以宁可麻烦，不可含糊。

- **为什么不自动登录**：见 ADR 0006。脚本不接收、不记录任何凭据。
- **为什么必须走代理**：国内直连 Steam 不可达，Chromium 需显式使用 `AGI_BROWSER_PROXY`。
- **为什么加购后要复核购物车页**：否则「成功」只是「点到了按钮」，
  页面改版导致的静默失效不会被发现。
- **被否决：挂接 Steam 桌面客户端**、**接入 Playwright MCP**、**调用未公开 API** ——
  详见 ADR 0005，那里记录了完整的调研结论。

## 已知局限与坑

- **中英文名转换由 planner 承担**，写在工具 `game` 参数的描述里。实测：
  把「仁王3」直接传给工具**必然失败**（Steam 商店不索引中文名，返回一堆无关结果），
  planner 在重规划中自行改名为 `Nioh 3` 后才成功。所以用户用中文点单时，
  **会多消耗一轮重规划**——这是已知代价。
- **选择器依赖 Steam 页面结构**（`#search_resultsRows`、`#game_area_purchase`、
  `href` 含 `addToCart`）。Steam 改版会让它失效；购物车页复核是发现失效的手段。
- **会话会过期**，届时工具报错要求重新登录。这是设计如此，不要改成自动续期。
- 加购是**幂等性未知**的操作：重复执行可能得到「已在购物车中」的页面状态，
  复核逻辑仍会判为成功。

## 要改它先看哪里

1. **改名称匹配规则** → `steam.py` 的 `_pick_result()` 与 `_normalize()`。
   改之前先读那两个函数的注释，里面记着三次失败尝试。
2. **改选择器** → 文件顶部的 `_RESULT_SELECTOR`、`_CART_LINK_SELECTOR`。
3. **改登录/会话方式** → `scripts/steam_login.py`；改路径或代理 →
   `config.py` 的 `STEAM_STATE`、`BROWSER_PROXY`。
4. **安全相关**：`.secrets/` 同时受 `.gitignore` 与 `files.py` 黑名单保护，
   换存储位置时两处都要改。
5. **测试**：`tests/unit/test_steam_matching.py` 只锁名称归一化规则（不联网、不耗额度）；
   真实链路没有自动化测试——**它有真实副作用，不该进自动测试**。
   验证方式是跑一次真实任务，再在购物车页面确认。
