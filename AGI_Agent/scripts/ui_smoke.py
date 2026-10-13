"""用真实浏览器把整条链路点一遍（前端 + 业务服务 + 智能体引擎）。

它验两个任务：一个纯文本的（假工具，触发审批），一个**带图片的**（真的走多模态）。
图片那一条是关键——图里画着一个随机数字，只有真的看清了才答得对；
随便说一段通顺的话是蒙不出来的。

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
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

#: 纯文本任务：用假工具 `wechat.send_message`。它是 WRITE（会触发审批），
#: 但不碰网络、不需要登录、没有真实副作用——所以这个场景可以反复跑。
DEFAULT_GOAL = "给张三发一条微信，内容是：明天见"

#: 图片任务的提问。**必须问一个只有看图才能回答的问题**，
#: 否则"答得像模像样"和"真的看见了"分不出来。
IMAGE_QUESTION = "这张图片里写的是哪 4 位数字？只回答数字"


def make_probe_image(dest: Path) -> str:
    """生成一张写着随机 4 位数字的 PNG，返回那个数字。

    用 PowerShell 的 System.Drawing，是为了**不引入新依赖**（项目里没有 Pillow）。
    代价是这段只在 Windows 上能用——本项目的开发环境本来就绑定了 Windows
    （HBuilderX、Steam 客户端都在本机）。
    """
    script = (
        "Add-Type -AssemblyName System.Drawing;"
        "$n = Get-Random -Minimum 1000 -Maximum 9999;"
        f"$bmp = New-Object System.Drawing.Bitmap(600,240);"
        "$g = [System.Drawing.Graphics]::FromImage($bmp);"
        "$g.Clear([System.Drawing.Color]::White);"
        "$g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit;"
        "$font = New-Object System.Drawing.Font('Consolas',96,[System.Drawing.FontStyle]::Bold);"
        "$g.DrawString($n.ToString(), $font, [System.Drawing.Brushes]::Black, 40, 40);"
        "$g.Dispose();"
        f"$bmp.Save('{dest}', [System.Drawing.Imaging.ImageFormat]::Png);"
        "$bmp.Dispose();"
        "Write-Output $n"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=120,
    )
    digits = re.search(r"\d{4}", result.stdout or "")
    if not digits:
        raise SystemExit(f"生成测试图片失败：{result.stdout} {result.stderr}")
    return digits.group(0)


def main() -> int:
    parser = argparse.ArgumentParser(description="界面端到端测试")
    parser.add_argument("--url", default="http://localhost:5173/",
                        help="前端地址（HBuilderX 运行到浏览器后控制台会打印）")
    parser.add_argument("--goal", default=DEFAULT_GOAL)
    parser.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "agi_ui_out"),
                        help="截图与测试图片的输出目录（默认在系统临时目录）")
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

        # ---------- A. 纯文本任务：审批 → 刷新 → 批准 → 完成 ----------
        page.goto(args.url, wait_until="networkidle", timeout=30000)
        page.fill("input", args.goal)
        page.click("uni-button:has-text('发送')")
        print("[A1] 已提交任务，等待审批面板…")

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

        print("[A2] 批准并等待任务结束…")
        page.click("uni-button:has-text('批准执行')")
        page.wait_for_selector(".result-text", timeout=180000)
        check("任务已完成", "已完成" in page.inner_text(".status"))
        check("页面显示了执行结果", "张三" in page.inner_text(".result-text"))
        check("事件列表渲染了多条进度",
              len(page.query_selector_all(".event")) >= 5)
        page.screenshot(path=str(out / "ui-3-done.png"), full_page=True)

        # ---------- B. 带图片的任务：上传 → 审批 → 读出图里的数字 ----------
        print("[B1] 图片任务：生成一张写着随机数字的图并上传…")
        image = out / "probe.png"
        number = make_probe_image(image)
        page.click("uni-button:has-text('新任务')")
        page.wait_for_timeout(600)
        page.fill("input", IMAGE_QUESTION)

        # uni.chooseImage 在 H5 端会临时造一个 <input type=file> 并点它，
        # 所以没法预先选中那个元素——只能等文件选择框弹出来再塞文件。
        with page.expect_file_chooser(timeout=15000) as chooser:
            page.click("uni-button:has-text('选图')")
        chooser.value.set_files(str(image))
        page.wait_for_timeout(1200)
        check("选中后出现图片预览", page.query_selector(".thumb") is not None)

        page.click("uni-button:has-text('发送')")
        print("      等待审批（图像工具会把图片发给千问，所以属于要确认的操作）…")
        page.wait_for_selector(".approval", timeout=120000)
        panel = page.inner_text(".approval")
        check("图片任务走的审批提到了图像工具", "vision." in panel)
        # 注意这里断言的是 `uploads/`，而不是上传时那个 `probe.png`：
        # Agent 会给上传的文件**重新起名**（时间戳 + 随机后缀），
        # 审批里显示的是**磁盘上真实的那个文件**——这才是将要被读的东西。
        check("审批里能看到图片的完整路径", "uploads/" in panel and ".png" in panel)
        page.screenshot(path=str(out / "ui-4-image-approval.png"), full_page=True)

        page.click("uni-button:has-text('批准执行')")
        print("      已批准，等待模型读图…")
        page.wait_for_selector(".result-text", timeout=180000)
        result_text = page.inner_text(".result-text")
        # **这是整条链路唯一的硬证据**：图里的数字只有真的看见了才知道。
        check(f"模型读出了图里的数字 {number}", number in result_text)
        page.screenshot(path=str(out / "ui-5-image-done.png"), full_page=True)

        check("浏览器无 JS 报错", not errors)
        if errors:
            print("      JS 报错：", errors[:3])

        browser.close()

    print(f"\nUI 端到端：{'PASS' if passed else 'FAIL'}（截图在 {out}）")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
