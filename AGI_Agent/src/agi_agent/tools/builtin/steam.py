"""Steam 商店工具：把指定游戏加入购物车。

设计约束（见 Memory.md §4「外部能力接入原则」）：

- 登录态来自 ``.secrets/steam_state.json``，由人工登录一次生成。本模块**不接触任何凭据**。
- 风险等级为 ``WRITE``，因此会触发 ``authorize`` 节点的人工确认中断。
- 依赖 ``BROWSER_PROXY``：国内直连 Steam 不可达，Playwright 需显式走代理。
- 只操作购物车，**不涉及结账与支付**。

选择器依据（2026-10-12 对 appid 400 与 620 实测）：主游戏的加购按钮是
``#game_area_purchase`` 内第一个 ``href`` 含 ``addToCart(`` 的链接。
用 href 而非按钮文字判断，因为文字随语言变化；demo 块没有 addToCart，会被自动跳过。
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from urllib.parse import quote

from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel, Field

from agi_agent.config import BROWSER_PROXY, STEAM_STATE
from agi_agent.schemas import RiskLevel
from agi_agent.tools.registry import ToolSpec, register

_SEARCH_URL = "https://store.steampowered.com/search/?term={query}"
_CART_URL = "https://store.steampowered.com/cart/"

_TIMEOUT_MS = 45_000
_RESULT_SELECTOR = "#search_resultsRows > a"
_CART_LINK_SELECTOR = "#game_area_purchase a[href*='addToCart']"

#: 从搜索结果里考察多少条，用于挑选完全同名的游戏并列候选。
_SEARCH_DEPTH = 5


class AddToCartArgs(BaseModel):
    game: str = Field(
        description=(
            "游戏在 Steam 商店中的正式名称，必须能被商店搜索精确命中。"
            "若用户给的是中文名、简称或俗称，先转换成商店使用的正式名称再传入，"
            "例如「仁王3」→「Nioh 3」、「赛博朋克2077」→「Cyberpunk 2077」。"
            "本工具只接受完全同名的结果，不做模糊匹配。"
        )
    )


@contextlib.contextmanager
def _page() -> Iterator[Page]:
    """打开一个带登录态与代理的浏览器页面。"""
    if not STEAM_STATE.exists():
        raise RuntimeError(
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


def _normalize(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _pick_result(page: Page, query: str) -> tuple[str, str]:
    """在搜索结果里挑出与查询**完全同名**的那一个。

    试过三种策略，前两种都在真实数据上出过错：

    1. 直接取第一条 —— "Portal" 会被解析成 "Portal Knights"。
    2. 取包含查询且长度占比过半的第一条 —— "Half" 变成 "Half-Life: Alyx"；
       更糟的是 "The Witcher 3" 变成 "The Witcher 3 REDkit"（模组编辑器，不是游戏），
       因为 Steam 把 REDkit 排在更前面，而"最接近"并不等于"正确"。

    结论：名称匹配没法可靠地自动化。加入购物车会真实改动账号状态，而审批发生在
    执行之前——用户批准的只是意图，无法预知会被解析成哪个游戏。所以这里只接受
    完全同名，其余一律拒绝并把候选列出来，让人把名字写全。宁可麻烦，不可含糊。
    """
    page.goto(_SEARCH_URL.format(query=quote(query)), wait_until="domcontentloaded")
    rows = page.locator(_RESULT_SELECTOR)
    try:
        # 没有结果时这里永远不会可见，用短超时把它转成明确的"搜不到"，
        # 而不是让用户等满默认超时再收到一个 TimeoutError。
        rows.first.wait_for(timeout=10_000)
    except PlaywrightTimeoutError:
        raise LookupError(f"Steam 商店没有搜到「{query}」") from None

    wanted = _normalize(query)
    candidates: list[str] = []

    for index in range(min(_SEARCH_DEPTH, rows.count())):
        row = rows.nth(index)
        title = row.locator(".title").inner_text().strip()
        candidates.append(title)
        if wanted and _normalize(title) == wanted:
            return title, row.get_attribute("href") or ""

    if not candidates:
        raise LookupError(f"Steam 商店没有搜到「{query}」")

    top = "、".join(f"「{title}」" for title in candidates)
    raise LookupError(
        f"没有找到与「{query}」完全同名的游戏，未执行任何操作。"
        f"搜索靠前的结果是：{top}。"
        f"请用其中完整准确的名称重试——加入购物车会真实改动账号，不做模糊匹配。"
    )


def _add_to_cart_link(page: Page):
    """主游戏的加购链接；页面上没有则为 0。"""
    return page.locator(_CART_LINK_SELECTOR)


def _cart_contains(page: Page, title: str) -> bool:
    page.goto(_CART_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1200)
    return _normalize(title) in _normalize(page.inner_text("body"))


def add_to_cart(game: str) -> str:
    with _page() as page:
        title, href = _pick_result(page, game)

        page.goto(href, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)

        link = _add_to_cart_link(page)
        if link.count() == 0:
            raise RuntimeError(
                f"「{title}」的页面上没有可用的加入购物车链接——"
                "可能已拥有该游戏、已是免费游戏、尚未发售，或需要先通过年龄验证页面。"
            )

        link.first.click()
        page.wait_for_timeout(2500)  # 等 Steam 的 ajax 提交完成

        # 到购物车页复核，让结论可验证而不是靠"点了就算成功"。
        if not _cart_contains(page, title):
            raise RuntimeError(
                f"已点击「{title}」的加入购物车按钮，但在购物车页面没有找到它。"
                "可能是 Steam 页面结构变化或操作被拦截，需要人工确认。"
            )

        return f"已把「{title}」加入购物车，并在购物车页面确认到该商品。"


class SearchGamesArgs(BaseModel):
    query: str = Field(
        description=(
            "搜索关键词。商店**不索引中文名**——用中文搜会返回一堆无关游戏，"
            "所以这里也应该用英文原名，例如 Nioh 3 而不是 仁王3。"
        )
    )


def search_games(query: str) -> str:
    """只读：列出搜索结果，用于确认游戏在商店中的正式名称。"""
    with _page() as page:
        page.goto(_SEARCH_URL.format(query=quote(query)), wait_until="domcontentloaded")
        rows = page.locator(_RESULT_SELECTOR)
        try:
            rows.first.wait_for(timeout=10_000)
        except PlaywrightTimeoutError:
            return f"Steam 商店没有搜到「{query}」。注意商店不索引中文名。"

        lines = []
        for index in range(min(_SEARCH_DEPTH, rows.count())):
            row = rows.nth(index)
            title = row.locator(".title").inner_text().strip()
            appid = row.get_attribute("data-ds-appid")
            lines.append(f"  {index + 1}. {title}（appid={appid}）")
        return f"「{query}」在 Steam 商店的搜索结果：\n" + "\n".join(lines)


register(
    ToolSpec(
        name="steam.search_games",
        description="在 Steam 商店搜索游戏，返回靠前结果的名称与 appid；只读，不改动账号",
        risk=RiskLevel.READ,
        parameters=SearchGamesArgs,
        func=search_games,
    )
)

register(
    ToolSpec(
        name="steam.add_to_cart",
        description="在 Steam 商店把指定游戏加入购物车；不涉及结账与支付",
        risk=RiskLevel.WRITE,
        parameters=AddToCartArgs,
        func=add_to_cart,
    )
)
