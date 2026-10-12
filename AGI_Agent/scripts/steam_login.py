"""Steam 登录辅助脚本。

用法：

    cd AGI_Agent
    uv run python scripts/steam_login.py            # 交互式登录（打开浏览器，人工操作）
    uv run python scripts/steam_login.py --verify   # 无头验证已保存的会话是否仍有效

设计约束（见 Memory.md §4「外部能力接入原则」）：

- 本脚本**不接收、不存储、不记录任何账号密码**，登录完全由人工完成。
- 会话状态文件等同于凭据：已在 ``.gitignore`` 中，且被本地文件工具列入黑名单。
- 会话过期时重新运行本脚本即可，不要让 Agent 自动续期。
"""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

from agi_agent.config import BROWSER_PROXY, STEAM_STATE

LOGIN_URL = "https://store.steampowered.com/login/"
HOME_URL = "https://store.steampowered.com/"

#: Steam 登录后写入的凭据 cookie。用它做**辅助判定**，不作为保存的前提——
#: 判定逻辑出错时不应让用户白登录一次。
_LOGIN_COOKIE = "steamLoginSecure"


def _launch(chromium, *, headless: bool, storage: str | None = None):
    proxy = {"server": BROWSER_PROXY} if BROWSER_PROXY else None
    browser = chromium.launch(headless=headless, proxy=proxy)
    context = browser.new_context(storage_state=storage)
    return browser, context


def login() -> int:
    with sync_playwright() as playwright:
        browser, context = _launch(playwright.chromium, headless=False)
        page = context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")

        print("=" * 66)
        print("浏览器已打开，请完成 Steam 登录：")
        print("  推荐 —— 用手机 Steam App 里的 Steam Guard 扫描页面上的二维码")
        print("  或者 —— 输入账号密码，并在页面上完成 Steam Guard 验证")
        print("=" * 66)
        input("\n确认已经登录成功后，回到这里按回车…… ")

        # 取全部 cookie，不做 URL 过滤——按 URL 过滤可能漏掉登录凭据。
        cookies = context.cookies()
        # 必须用同一个 context 保存，否则存下来的是空状态。
        STEAM_STATE.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(STEAM_STATE))
        browser.close()

    names = sorted({c["name"] for c in cookies})
    size_kb = STEAM_STATE.stat().st_size / 1024
    print(f"\n已保存会话状态：{STEAM_STATE}（{size_kb:.1f} KB）")
    print(f"共 {len(cookies)} 个 cookie，名称：{names}")

    if _LOGIN_COOKIE in names:
        print(f"\n检测到 {_LOGIN_COOKIE}，登录凭据已包含在内。")
        return 0
    print(
        f"\n警告：未检测到 {_LOGIN_COOKIE}。状态已保存，但可能未登录成功。\n"
        f"建议运行 `uv run python scripts/steam_login.py --verify` 确认。"
    )
    return 0


def verify() -> int:
    """无头验证已保存的会话；不打印任何 cookie 值。"""
    if not STEAM_STATE.exists():
        print(f"未找到会话状态：{STEAM_STATE}")
        print("请先运行：uv run python scripts/steam_login.py")
        return 1

    with sync_playwright() as playwright:
        browser, context = _launch(
            playwright.chromium, headless=True, storage=str(STEAM_STATE)
        )
        page = context.new_page()
        page.goto(HOME_URL, wait_until="domcontentloaded")

        names = {c["name"] for c in context.cookies()}
        signals = {
            f"cookie {_LOGIN_COOKIE}": _LOGIN_COOKIE in names,
            "元素 #account_pulldown": page.locator("#account_pulldown").count() > 0,
            "元素 .playerAvatar": page.locator(".playerAvatar").count() > 0,
            "全局 g_rgProfileData": bool(
                page.evaluate("() => typeof window.g_rgProfileData !== 'undefined'")
            ),
        }
        title = page.title()
        browser.close()

    print(f"页面标题：{title}")
    for name, hit in signals.items():
        print(f"  [{'✓' if hit else '×'}] {name}")

    ok = any(signals.values())
    print("\n判定：会话有效" if ok else "\n判定：会话无效或已过期，请重新登录")
    return 0 if ok else 1


def main() -> None:
    if "--verify" in sys.argv:
        raise SystemExit(verify())
    raise SystemExit(login())


if __name__ == "__main__":
    main()
