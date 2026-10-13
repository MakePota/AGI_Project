"""用真实浏览器把整条链路点一遍（前端 + 业务服务 + 智能体引擎）。

与 `smoke_api.py` 的分工：那个验的是**接口**（用 curl 式请求，不碰界面），
这个验的是**界面能不能用**——请求通了但界面白屏、按钮点不动、刷新后状态丢了，
只有真的开一个浏览器才看得出来。

要先把三样都跑起来：

    docker compose -f AGI_Server/docker-compose.yml up -d      # MySQL
    cd AGI_Agent && uv run agi-agent-api                       # 引擎，8123
    cd AGI_Server && mvn spring-boot:run                       # 业务服务，8090
    # 前端：HBuilderX 里"运行到浏览器"，或
    #   cd HBuilderX && ./cli.exe launch web --project AGI_Client

然后：

    cd AGI_Agent && uv run python scripts/ui_smoke.py

它验四件只有界面上才看得出来的事：

1. 发一个 WRITE 任务，审批面板**自己弹出来**；
2. 面板上必须显示**参数**（工具名 + `to=`/`text=`），而不只是模型写的描述；
3. **刷新页面后审批仍在**——这是"中断是可查询状态"（ADR 0015）在界面上的样子，
   也是最容易被实现成"挂在连接上"从而一刷新就丢的地方；
4. 点批准后任务跑完，结果出现在页面上，且事件列表有多条进度。

用假工具 `wechat.send_message` 作任务：它是 WRITE（会触发审批），
但**不碰网络、不需要登录、没有真实副作用**，所以这个测试可以反复跑。
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

#: 假工具任务：WRITE 风险会触发审批，但没有任何真实副作用。
DEFAULT_GOAL = "给张三发一条微信，内容是：明天见"


def main() -> int:
    parser = argparse.ArgumentParser(description="界面端到端测试")
    parser.add_argument("--url", default="http://localhost:5173/",
                        help="前端地址（HBuilderX 运行到浏览器后控制台会打印）")
    parser.add_argument("--goal", default=DEFAULT_GOAL)
    parser.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "agi_ui_out"),
                        help="截图输出目录（默认在系统临时目录，不污染仓库）")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    passed = True

    def check(label: str, ok: bool) -> None:
        nonlocal passed
        passed = passed and ok
        print(f"  [{'OK' if ok else 'FAIL'}] {label}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        # JS 报错必须抓：白屏或按钮没反应时，控制台的报错就是唯一的线索。
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(args.url, wait_until="networkidle", timeout=30000)
        page.fill("input", args.goal)
        page.click("uni-button:has-text('发送')")
        print("[1] 已提交任务，等待审批面板…")

        page.wait_for_selector(".approval", timeout=120000)
        panel = page.inner_text(".approval")
        check("审批面板显示了工具名", "wechat.send_message" in panel)
        check("审批面板显示了参数（不只是描述）", "to=" in panel and "text=" in panel)
        check("审批面板标出了风险等级", "需确认" in panel)
        page.screenshot(path=str(out / "ui-1-approval.png"), full_page=True)

        page.reload(wait_until="networkidle")
        page.wait_for_timeout(3000)
        check("刷新页面后审批面板仍在（中断是状态，不是挂着的连接）",
              page.query_selector(".approval") is not None)
        page.screenshot(path=str(out / "ui-2-after-reload.png"), full_page=True)

        print("[3] 批准并等待任务结束…")
        page.click("uni-button:has-text('批准执行')")
        page.wait_for_selector(".result-text", timeout=180000)
        check("任务已完成", "已完成" in page.inner_text(".status"))
        check("页面显示了执行结果", "张三" in page.inner_text(".result-text"))
        check("事件列表渲染了多条进度",
              len(page.query_selector_all(".event")) >= 5)
        check("浏览器无 JS 报错", not errors)
        if errors:
            print("      JS 报错：", errors[:3])
        page.screenshot(path=str(out / "ui-3-done.png"), full_page=True)

        browser.close()

    print(f"\nUI 端到端：{'PASS' if passed else 'FAIL'}（截图在 {out}）")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
