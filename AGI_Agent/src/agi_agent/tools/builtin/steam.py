"""Steam 商店工具：搜索、查价、购物车增删查。

设计约束（见 docs/decisions/0005、0006、0009、0010 与 docs/features/steam-tools.md）：

- 登录态来自 ``.secrets/steam_state.json``，由人工登录一次生成。本模块**不接触任何凭据**。
- 风险等级：加购与删除是 ``WRITE``，因此会自动触发 ``authorize`` 节点的人工确认中断。
- 依赖 ``BROWSER_PROXY``：国内直连 Steam 不可达，Playwright 需显式走代理。
- 只操作购物车，**不涉及结账与支付**。

下面三条是实测得出的硬约束，**改本文件前先读**：

1. **所有页面都固定 ``l=english``。**
   实测 ``cart/?l=schinese`` 会把按钮变成「移除」「添加」、把游戏名变成「仁王３」
   （全角数字 3）。界面语言随账号设置变化，固定语言才能让文字标签可预测。
   反过来也说明：**游戏名不能当唯一标识**——它跨语言不一致，一律优先用 appid。

2. **购物车页是 React 应用，类名是构建期哈希**（形如 ``_3F0SnUeC_obtI4WyQtijAA``，
   Steam 每次发版都会变），**不能用类名定位**。页面上真正稳定的锚点只有三个：
   ``a[href*="/app/"]`` 里的 appid、条目封面 ``img[alt]``、以及 ``[role="button"]`` 的文字。
   元素的层级、距离、顺序这些结构性依据一律不可用。

3. **点击之后的失败必须报成"结果不确定"**（``UncertainOutcomeError``），不能报成普通失败。
   加购与删除有真实副作用，报成失败会让 planner 重试，而重试会在真实世界里叠加动作。
   2026-10-12 已真实发生过一次：购物车里出现了 3 份同一个游戏。
"""

from __future__ import annotations

import contextlib
import json
import re
import time
import unicodedata
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import quote

from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel, Field

from agi_agent.config import BROWSER_PROXY, STEAM_STATE
from agi_agent.schemas import RiskLevel
from agi_agent.tools.errors import ToolError, UncertainOutcomeError
from agi_agent.tools.registry import ToolSpec, register

_STORE_URL = "https://store.steampowered.com"
_SEARCH_URL = f"{_STORE_URL}/search/?term={{query}}&l=english"
_CART_URL = f"{_STORE_URL}/cart/?l=english"
_APP_DETAILS_API = f"{_STORE_URL}/api/appdetails"

#: 价格按中国区查询。Steam 的价格分区域，换区需要改这里（见功能文档的「已知局限」）。
_PRICE_CC = "cn"
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

_TIMEOUT_MS = 45_000
#: 等 React 把购物车画出来的上限。旧实现用 1.2 秒读 body 文本，React 还没渲染完，
#: 于是"购物车里找不到"被误判成操作失败——这是重复加购事故的成因之一。
_RENDER_TIMEOUT_S = 20.0
_RENDER_POLL_MS = 500
_PRICE_TIMEOUT_S = 30
#: 动作发生之后，允许花多久去**复核**它。复核可以反复重试，动作不行——
#: 一次读不到就断定"操作失败"，正是重复加购事故的成因。
_VERIFY_TIMEOUT_S = 20.0
_VERIFY_POLL_MS = 1200

_RESULT_SELECTOR = "#search_resultsRows > a"
_CART_LINK_SELECTOR = "#game_area_purchase a[href*='addToCart']"

#: 从搜索结果里考察多少条，用于挑选完全同名的游戏并列候选。
_SEARCH_DEPTH = 5
#: 同名条目可能有多份（重复加购事故就会留下多份），逐个删，给足轮次。
_MAX_REMOVE_ROUNDS = 10

_APPID_RE = re.compile(r"/app/(\d+)")
#: 网页请求失败时 Chromium 给出的错误码片段，用来区分"网络不通"与"其他错误"。
_NETWORK_MARKERS = (
    "ERR_EMPTY_RESPONSE",
    "ERR_CONNECTION",
    "ERR_TIMED_OUT",
    "ERR_NAME_NOT_RESOLVED",
    "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_PROXY_CONNECTION_FAILED",
    "ERR_INTERNET_DISCONNECTED",
    "ERR_NETWORK_CHANGED",
)


# ---------------------------------------------------------------- 数据模型


class SearchHit(BaseModel):
    """一条搜索命中。"""

    appid: str
    title: str
    url: str


class CartItem(BaseModel):
    """购物车里的一条记录。"""

    appid: str | None
    name: str | None
    price: str | None


# ---------------------------------------------------------------- 纯函数


def _normalize(text: str) -> str:
    """归一化游戏名，用于**完全同名**比对。

    先用 NFKC 把全角折成半角：实测中文界面上「仁王３」用的是全角 3，
    英文界面上是「Nioh 3」。少了这一步，换个语言连数字都对不上。
    """
    folded = unicodedata.normalize("NFKC", text or "")
    return "".join(ch for ch in folded.lower() if ch.isalnum())


def _appid_from_href(href: str) -> str | None:
    """从 ``/app/<appid>/<slug>`` 形式的链接里取 appid。"""
    match = _APPID_RE.search(href or "")
    return match.group(1) if match else None


def _format_price(title: str, appid: str, data: dict[str, Any]) -> str:
    """把 appdetails 的返回渲染成一句人话。

    金额单位是**分**（34800 表示 ¥348.00）；无折扣时 ``initial_formatted`` 是空字符串
    （实测），所以只在真打折时才展示原价，否则会渲染出一个空的原价。
    """
    label = f"「{title}」（appid={appid}）"
    price = data.get("price_overview")
    if price:
        final = price.get("final_formatted") or "未知"
        percent = price.get("discount_percent") or 0
        if percent:
            initial = price.get("initial_formatted") or "未知"
            return f"{label} 正在打折：原价 {initial}，现价 {final}（-{percent}%）"
        return f"{label} 当前价格 {final}（{price.get('currency') or '?'}，{_PRICE_CC} 区）"

    if data.get("is_free"):
        return f"{label} 是免费游戏。"

    release = data.get("release_date") or {}
    if release.get("coming_soon"):
        return f"{label} 尚未发售（{release.get('date') or '日期未公布'}），暂无价格。"

    return f"{label} Steam 没有返回中国区的价格（可能未在本区销售或已下架）。"


# ---------------------------------------------------------------- 浏览器


def _network_error(exc: Exception, what: str) -> ToolError:
    """把底层网络异常翻译成一句能指导行动的话。

    这一条是给"未开代理"那次事故补的：旧实现任由 Chromium 的超时往上冒，
    用户看到的是无意义的等待与超时，白烧了两轮重规划。
    """
    text = str(exc)
    first_line = text.splitlines()[0] if text else type(exc).__name__
    if any(marker in text for marker in _NETWORK_MARKERS):
        return ToolError(
            f"无法访问 Steam（{what}）：{first_line}\n"
            f"当前代理：BROWSER_PROXY={BROWSER_PROXY or '（未设置）'}。"
            "请确认代理已开启；未设置时请在 .env 中配置 AGI_BROWSER_PROXY。\n"
            "**本次没有执行任何动作。** 这是环境问题不是计划问题，"
            "重试不会改善——请先解决网络，再重新发起任务。"
        )
    return ToolError(f"访问 Steam 失败（{what}）：{first_line}")


def _goto(page: Page, url: str, *, what: str) -> None:
    try:
        page.goto(url, wait_until="domcontentloaded")
    except PlaywrightError as exc:
        raise _network_error(exc, what) from exc


@contextlib.contextmanager
def _page() -> Iterator[Page]:
    """打开一个带登录态与代理的浏览器页面。"""
    if not STEAM_STATE.exists():
        raise ToolError(
            f"缺少 Steam 登录状态（{STEAM_STATE}）。"
            "请先运行 uv run python scripts/steam_login.py 人工登录一次。"
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            proxy={"server": BROWSER_PROXY} if BROWSER_PROXY else None,
        )
        try:
            context = browser.new_context(storage_state=str(STEAM_STATE))
            page = context.new_page()
            page.set_default_timeout(_TIMEOUT_MS)
            yield page
        finally:
            browser.close()


# ---------------------------------------------------------------- 购物车读取

#: 读购物车。要点见文件头的「硬约束 2」。
_JS_READ_CART = r"""
() => {
  const items = [];
  for (const a of document.querySelectorAll('a[href*="/app/"]')) {
    // 推荐位同样是 /app/ 链接。实测：不排除它们，购物车里的 1 件商品会读出 4 条。
    // ImpressionTrackedElement 是唯一能区分二者的标记——它是语义类名，不是哈希类名。
    if (a.closest('.ImpressionTrackedElement')) continue;
    const href = a.getAttribute('href') || '';
    const m = href.match(/\/app\/(\d+)/);
    const img = a.querySelector('img');
    // 行容器：从链接向上找第一个含 [role="button"] 的祖先。
    let row = a;
    while (row && row.parentElement) {
      row = row.parentElement;
      if (row.querySelector('[role="button"]')) break;
    }
    const rowText = (row ? row.innerText : '').replace(/\s+/g, ' ');
    const price = rowText.match(/[¥$€£]\s?[\d.,]+/);
    items.push({
      appid: m ? m[1] : null,
      name: img ? img.getAttribute('alt') : null,
      price: price ? price[0] : null,
    });
  }
  const body = document.body ? document.body.innerText : '';
  return {
    items: items,
    empty_marked: /your cart is empty/i.test(body) || /购物车是空的/.test(body),
  };
}
"""

#: 给目标删除控件打上临时标记，交给 Playwright 去点（真实点击事件，React 才认）。
_JS_MARK_REMOVE = r"""
(appid) => {
  document.querySelectorAll('[data-agi-remove-target]')
    .forEach(e => e.removeAttribute('data-agi-remove-target'));
  for (const a of document.querySelectorAll('a[href*="/app/"]')) {
    if (a.closest('.ImpressionTrackedElement')) continue;
    const m = (a.getAttribute('href') || '').match(/\/app\/(\d+)/);
    if (!m || m[1] !== appid) continue;
    let row = a;
    while (row && row.parentElement) {
      row = row.parentElement;
      if (row.querySelector('[role="button"]')) break;
    }
    if (!row) break;
    // 行内有**两个** [role="button"]：Add 与 Remove（实测）。
    // 结构上无从区分，只能按文字标签定位。匹配数不为 1 就不标，由调用方报错——
    // 宁可报错，不可点错：点错控件会改动真实账号状态。
    const buttons = [...row.querySelectorAll('[role="button"]')];
    const labels = buttons.map(b => (b.innerText || '').trim());
    const hit = buttons.filter(
      b => /^(remove|移除|删除)$/i.test((b.innerText || '').trim())
    );
    if (hit.length === 1) hit[0].setAttribute('data-agi-remove-target', '1');
    return {matched: hit.length, labels: labels,
            rowText: row.innerText.replace(/\s+/g, ' ').slice(0, 120)};
  }
  return {matched: 0, labels: [], rowText: null};
}
"""


def _read_cart(page: Page) -> list[CartItem]:
    """读购物车条目；读不到就报错，**不猜**。

    必须等到 React 真的画完。旧实现 ``domcontentloaded`` + 1.2 秒就读 body 文本，
    React 尚未渲染，于是「找不到该游戏」被当成操作失败并触发重试。
    """
    _goto(page, _CART_URL, what="打开购物车")

    deadline = time.monotonic() + _RENDER_TIMEOUT_S
    snapshot: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            snapshot = page.evaluate(_JS_READ_CART)
        except PlaywrightError as exc:
            raise _network_error(exc, "读取购物车") from exc
        if snapshot["items"] or snapshot["empty_marked"]:
            break
        page.wait_for_timeout(_RENDER_POLL_MS)

    if not snapshot or not (snapshot["items"] or snapshot["empty_marked"]):
        raise ToolError(
            "读不到购物车内容：页面在 "
            f"{_RENDER_TIMEOUT_S:.0f} 秒内既没出现商品，也没出现空购物车提示。"
            "可能是网络不稳、Steam 页面改版，或登录态已失效。"
        )
    return [CartItem(**item) for item in snapshot["items"]]


def _cart_has(items: list[CartItem], appid: str) -> bool:
    """购物车里是否已有该 appid。**用 appid 而不是名称**——名称跨语言会变。"""
    return any(item.appid == appid for item in items)


def _matching(items: list[CartItem], name: str) -> list[CartItem]:
    """按**完全同名**挑出购物车条目，不做模糊匹配。"""
    wanted = _normalize(name)
    return [item for item in items if wanted and _normalize(item.name or "") == wanted]


def _poll_cart(page: Page, predicate: Callable[[list[CartItem]], bool]) -> str | None:
    """反复读购物车直到 predicate 成立；成立返回 None，超时返回最后的原因。

    **这是整个模块最重要的一处设计。** 动作执行完之后，一次读不到结果
    *不能*推断动作没生效——Steam 的加购是 ajax 提交，购物车页又是 React 渲染，
    慢半拍是常态。所以复核要反复重试，直到超时才可以报「结果不确定」。

    反过来，动作本身**绝不重试**。事故的成因就是这两者被搞反了：
    复核失败被当成动作失败，于是动作被重放，购物车里留下了 3 份同一个游戏。
    """
    deadline = time.monotonic() + _VERIFY_TIMEOUT_S
    reason = "购物车在超时前始终没有反映这次改动"
    while True:
        try:
            if predicate(_read_cart(page)):
                return None
        except ToolError as exc:
            reason = str(exc)
        if time.monotonic() >= deadline:
            return reason
        page.wait_for_timeout(_VERIFY_POLL_MS)


# ---------------------------------------------------------------- 搜索


def _search(page: Page, query: str) -> list[SearchHit]:
    """在商店搜索，返回靠前的若干条命中。"""
    _goto(page, _SEARCH_URL.format(query=quote(query)), what=f"搜索「{query}」")
    rows = page.locator(_RESULT_SELECTOR)
    try:
        # 没有结果时这里永远不会可见，用短超时把它转成明确的「搜不到」，
        # 而不是让用户等满默认超时再收到一个 TimeoutError。
        rows.first.wait_for(timeout=10_000)
    except PlaywrightTimeoutError:
        return []

    hits: list[SearchHit] = []
    for index in range(min(_SEARCH_DEPTH, rows.count())):
        row = rows.nth(index)
        title = row.locator(".title").inner_text().strip()
        href = row.get_attribute("href") or ""
        # data-ds-appid 对捆绑包可能是逗号分隔的多个值，取第一个。
        appid = (row.get_attribute("data-ds-appid") or "").split(",")[0].strip()
        hits.append(
            SearchHit(appid=appid or (_appid_from_href(href) or ""), title=title, url=href)
        )
    return hits


def _pick_result(page: Page, query: str) -> SearchHit:
    """在搜索结果里挑出与查询**完全同名**的那一个。

    试过三种策略，前两种都在真实数据上出过错：

    1. 直接取第一条 —— "Portal" 会被解析成 "Portal Knights"。
    2. 取包含查询且长度占比过半的第一条 —— "Half" 变成 "Half-Life: Alyx"；
       更糟的是 "The Witcher 3" 变成 "The Witcher 3 REDkit"（模组编辑器，不是游戏），
       因为 Steam 把 REDkit 排在更前面，而"最接近"并不等于"正确"。

    结论：名称匹配没法可靠地自动化。加购会真实改动账号状态，而审批发生在
    执行之前——用户批准的只是意图，无法预知会被解析成哪个游戏。所以这里只接受
    完全同名，其余一律拒绝并把候选列出来，让人把名字写全。宁可麻烦，不可含糊。
    """
    hits = _search(page, query)
    if not hits:
        raise LookupError(f"Steam 商店没有搜到「{query}」")

    wanted = _normalize(query)
    for hit in hits:
        if wanted and _normalize(hit.title) == wanted:
            return hit

    top = "、".join(f"「{hit.title}」" for hit in hits)
    raise LookupError(
        f"没有找到与「{query}」完全同名的游戏，未执行任何操作。"
        f"搜索靠前的结果是：{top}。"
        f"请用其中完整准确的名称重试——加入购物车会真实改动账号，不做模糊匹配。"
    )


# ---------------------------------------------------------------- 价格


def _fetch_app_details(appid: str) -> dict[str, Any]:
    """取 appdetails 的 ``data`` 段。

    这是一个长期稳定的公开端点（无需登录，商店页面自己就用它）。它不在官方
    开发者文档里，采用它的理由与边界见 ADR 0009。
    """
    url = f"{_APP_DETAILS_API}?appids={appid}&cc={_PRICE_CC}&l=english"
    handlers: list[Any] = []
    if BROWSER_PROXY:
        handlers.append(
            urllib.request.ProxyHandler({"http": BROWSER_PROXY, "https": BROWSER_PROXY})
        )
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})

    try:
        raw = opener.open(request, timeout=_PRICE_TIMEOUT_S).read().decode("utf-8")
    except Exception as exc:  # noqa: BLE001  网络层什么都可能抛
        raise ToolError(
            f"查询价格失败（appid={appid}）：{type(exc).__name__}: {exc}\n"
            f"当前代理：BROWSER_PROXY={BROWSER_PROXY or '（未设置）'}。"
            "**没有执行任何动作。**"
        ) from exc

    entry = (json.loads(raw) or {}).get(appid) or {}
    if not entry.get("success"):
        raise ToolError(f"Steam 没有返回 appid={appid} 的价格数据。")
    return entry.get("data") or {}


# ---------------------------------------------------------------- 工具


class SearchArgs(BaseModel):
    query: str = Field(
        description=(
            "搜索关键词。商店**不索引中文名**——用中文搜会返回一堆无关游戏，"
            "所以这里也应该用英文原名，例如 Nioh 3 而不是 仁王3。"
        )
    )


def search_games(query: str) -> str:
    """只读：列出搜索结果，用于确认游戏在商店中的正式名称。"""
    with _page() as page:
        hits = _search(page, query)
    if not hits:
        return f"Steam 商店没有搜到「{query}」。注意商店不索引中文名。"
    lines = [
        f"  {i}. {hit.title}（appid={hit.appid}）" for i, hit in enumerate(hits, start=1)
    ]
    return f"「{query}」在 Steam 商店的搜索结果：\n" + "\n".join(lines)


class EmptyArgs(BaseModel):
    """无参数。"""


def list_cart() -> str:
    """只读：列出购物车里的全部商品。"""
    with _page() as page:
        items = _read_cart(page)
    if not items:
        return "购物车是空的。"
    lines = [f"购物车共 {len(items)} 件商品："]
    for index, item in enumerate(items, start=1):
        appid = f"（appid={item.appid}）" if item.appid else ""
        price = f" {item.price}" if item.price else ""
        lines.append(f"  {index}. {item.name or '（名称未知）'}{appid}{price}")
    lines.append("如需删除，请直接引用上面显示的名称（必须完全一致）。")
    return "\n".join(lines)


class GameArgs(BaseModel):
    game: str = Field(
        description=(
            "游戏在 Steam 商店中的正式名称，必须能被商店搜索精确命中。"
            "若用户给的是中文名、简称或俗称，先转换成商店使用的正式名称再传入，"
            "例如「仁王3」→「Nioh 3」、「赛博朋克2077」→「Cyberpunk 2077」。"
            "本工具只接受完全同名的结果，不做模糊匹配。"
        )
    )


def get_price(game: str) -> str:
    """只读：查一个游戏当前在中国区的价格。"""
    with _page() as page:
        hit = _pick_result(page, game)
    if not hit.appid:
        raise ToolError(f"没能确定「{hit.title}」的 appid，无法查询价格。")
    return _format_price(hit.title, hit.appid, _fetch_app_details(hit.appid))


def add_to_cart(game: str) -> str:
    """把游戏加入购物车。**幂等**：已经在购物车里就不会再加一次。"""
    with _page() as page:
        hit = _pick_result(page, game)

        # 幂等闸门。它同时挡掉两类问题：用户重复下单，以及执行失败后的重试。
        # 实测教训：没有这道闸门时，一次复核失败的重试就在购物车里留下了 3 份同一个游戏。
        existing = _read_cart(page)
        if _cart_has(existing, hit.appid):
            return (
                f"「{hit.title}」已经在购物车中（共 "
                f"{sum(1 for i in existing if i.appid == hit.appid)} 条），未重复添加。"
            )

        _goto(page, hit.url, what=f"打开「{hit.title}」的商品页")
        page.wait_for_timeout(1200)

        link = page.locator(_CART_LINK_SELECTOR)
        if link.count() == 0:
            raise ToolError(
                f"「{hit.title}」的页面上没有可用的加入购物车链接——"
                "可能已拥有该游戏、已是免费游戏、尚未发售，或需要先通过年龄验证页面。"
                "**没有执行任何动作。**"
            )

        try:
            link.first.click()
        except PlaywrightError as exc:
            # 点击前 Playwright 会先做可点击性检查，所以走到这里基本可以断定
            # 没有派发点击。退一步说，即使真的点到了，上面的幂等闸门也会在重试时
            # 拦住重复添加——报成确定失败是安全的。
            raise ToolError(
                f"未能点击「{hit.title}」的加入购物车按钮：{str(exc).splitlines()[0]}。"
                "**很可能没有执行任何动作。**"
            ) from exc

        # 从这里往下，动作可能已经生效，任何失败都必须报成"结果不确定"。
        problem = _poll_cart(page, lambda items: _cart_has(items, hit.appid))
        if problem is None:
            return f"已把「{hit.title}」加入购物车，并在购物车页面确认到该商品。"

        raise UncertainOutcomeError(
            f"已点击「{hit.title}」的加入购物车按钮，但在 {_VERIFY_TIMEOUT_S:.0f} 秒内"
            f"始终无法确认它出现在购物车里。原因：{problem}"
            "可能是 Steam 页面结构变化、操作被拦截，或网络中断。"
        )


def remove_from_cart(game: str) -> str:
    """从购物车移除**全部**同名条目。幂等：本来就不在，就什么都不做。"""
    with _page() as page:
        items = _read_cart(page)
        if not _matching(items, game):
            shown = "、".join(f"「{i.name}」" for i in items) or "（空）"
            return (
                f"购物车里没有叫「{game}」的商品，未执行任何操作。"
                f"当前购物车内容：{shown}。"
                "请用 steam.list_cart 显示的名称原样重试。"
            )

        removed = 0
        # 不用 for...else：购物车里恰好有 _MAX_REMOVE_ROUNDS 份时，循环会跑满
        # 且没有 break，else 分支就会把"全部删干净了"误判成"没清完"。
        # 是否清干净一律交给下面的复核来判定。
        for _ in range(_MAX_REMOVE_ROUNDS):
            current = _matching(_after_action_read(page, removed), game)
            if not current:
                break
            appid = current[0].appid
            if not appid:
                raise ToolError(
                    f"购物车里的「{current[0].name}」没有可识别的 appid，"
                    "无法安全地定位它的删除控件。**没有点击任何控件。**"
                )
            _click_remove(page, appid)
            removed += 1

        # 复核：允许反复读，直到确认清空或超时。
        problem = _poll_cart(page, lambda items: not _matching(items, game))
        if problem is None:
            if removed == 0:
                return f"「{game}」已经不在购物车中，未执行任何操作。"
            return f"已从购物车移除「{game}」的全部 {removed} 条记录。"
        raise UncertainOutcomeError(
            f"已经对「{game}」执行了 {removed} 次移除，但在 {_VERIFY_TIMEOUT_S:.0f} 秒内"
            f"仍能读到它没被清空。原因：{problem}**可能只生效了一部分。**"
        )


def _after_action_read(page: Page, actions_done: int) -> list[CartItem]:
    """动作已经发生过之后再读购物车。

    此时读不到**不能**当作"没做成"——可能只是读失败了。所以一旦已经动过手，
    任何读取错误都必须升级成"结果不确定"，而不是原样往上抛成普通失败。
    """
    try:
        return _read_cart(page)
    except ToolError as exc:
        if actions_done == 0:
            raise
        raise UncertainOutcomeError(
            f"已经执行了 {actions_done} 次移除，但随后无法读取购物车，"
            f"无法确认当前状态。原因：{exc}"
        ) from exc


def _click_remove(page: Page, appid: str) -> None:
    """点掉指定 appid 条目的删除控件。

    先读一遍确认能定位到唯一控件再点——**点错控件的后果是真实的**，
    所以这里宁可报错也不猜。
    """
    try:
        marked = page.evaluate(_JS_MARK_REMOVE, appid)
    except PlaywrightError as exc:
        raise _network_error(exc, "定位购物车删除控件") from exc

    if marked.get("matched") != 1:
        raise ToolError(
            f"无法在购物车里唯一定位 appid={appid} 的删除控件"
            f"（匹配到 {marked.get('matched')} 个，行内按钮文字：{marked.get('labels')}）。"
            "Steam 可能改了页面结构或界面语言。**没有点击任何控件。**"
        )

    try:
        page.click("[data-agi-remove-target]")
    except PlaywrightError as exc:
        # Playwright 在派发点击前会先做可点击性检查，走到这里基本可以断定没点成。
        # 且删除是幂等的（重试时若已不在购物车就直接返回），所以报成确定失败是安全的。
        raise ToolError(
            f"未能点击 appid={appid} 的删除控件：{str(exc).splitlines()[0]}。"
            "**很可能没有执行任何动作。**"
        ) from exc


register(
    ToolSpec(
        name="steam.search_games",
        description="在 Steam 商店搜索游戏，返回靠前结果的名称与 appid；只读，不改动账号",
        risk=RiskLevel.READ,
        parameters=SearchArgs,
        func=search_games,
    )
)

register(
    ToolSpec(
        name="steam.list_cart",
        description="列出 Steam 购物车中的全部商品（名称、appid、价格）；只读，不改动账号",
        risk=RiskLevel.READ,
        parameters=EmptyArgs,
        func=list_cart,
    )
)

register(
    ToolSpec(
        name="steam.get_price",
        description="查询指定游戏当前在中国区的价格；只读，不改动账号",
        risk=RiskLevel.READ,
        parameters=GameArgs,
        func=get_price,
    )
)

register(
    ToolSpec(
        name="steam.add_to_cart",
        description="在 Steam 商店把指定游戏加入购物车；已在购物车中则不会重复添加；不涉及结账与支付",
        risk=RiskLevel.WRITE,
        parameters=GameArgs,
        func=add_to_cart,
    )
)

register(
    ToolSpec(
        name="steam.remove_from_cart",
        description=(
            "从 Steam 购物车移除指定游戏的全部记录；"
            "名称要用 steam.list_cart 显示的原样名称；本来就不在购物车则不执行任何操作"
        ),
        risk=RiskLevel.WRITE,
        parameters=GameArgs,
        func=remove_from_cart,
    )
)
