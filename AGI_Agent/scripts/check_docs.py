"""文档腐化检查。**收工前跑一次**，见 CLAUDE.md 的「收工时」。

## 为什么要它

`Memory.md` 是**唯一**的状态来源——下一个会话（或下一个人）全靠它知道"现在做到哪了"。
而它是**手工维护**的：漏更新一次，下一个会话就会拿着旧状态开工，
而且**没有任何东西会报错**。

2026-10-13 就发生过：它的进度表写着"150 个单测"，实际是 161，
同一份文件的另一处却写着"修掉了过期的 131"——**它自己内部就矛盾**。
那次是靠一次刻意的"空白会话冷读"才发现的，不是靠机制。

这个脚本把**能被机械验证的那部分**变成确定的：数字对不对、索引全不全、链接通不通。
它管不了"描述是否还算准确"——那仍然只能靠人/会话的判断。

## 它检查什么

| 检查 | 为什么这能腐化 |
| --- | --- |
| 相对链接是否有效 | 文件改名/移动后，链接**不会报错**，只在有人点时才发现 |
| 文档里写的单测数 vs 实际 | 加了测试但忘了改文档，或反过来 |
| ADR 篇数 vs 实际文件数 | 新增 ADR 时容易忘——它就写在 `Memory.md` 的一句话里 |
| `decisions/README.md` 索引是否漏条目 | 索引是手工加的，新 ADR 忘了登记就搜不到 |
| `features/README.md` 索引是否漏条目 | 同上 |
| （警告）Java 测试数 | 用 `@Test` 计数近似，参数化测试会让它不准，所以只警告 |

## 它认不出"哪句是断言"

它靠**字面模式**判断「这里声明了一个数字」，因此**叙述性文字里出现同样的字样会被误判**。
比如想举历史例子而写下「当时写着 150 个单测」，它会把 150 当成当前断言。

这是这个做法的固有代价，不是 bug。规避办法很简单：**举例子时换个说法**
（「那个数比实际少了十一个」），别把那个模式原样写进叙述里。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENT = REPO / "AGI_Agent"

SKIP_DIRS = {".git", "node_modules", "target", "unpackage", ".venv", "__pycache__", ".pytest_cache"}

# 形如 [文字](目标)，跳过外链与纯锚点
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
FENCE = re.compile(r"^```")

# 文档里"161 个单测 / 161 个单元测试"这类说法。**两种语序都要认**：
#   - 数字在前：「161 个单测」（出现在命令示例里）
#   - 词在前：「单元测试 | ✅ 161 个 |」（Memory.md 的进度表就是这么写的）
# 第二种必须覆盖——**今天出错的那一处正是这个形状**（表里写着 150），
# 认不出来就等于这个检查白做。
#
# 词在前的那种要限定得很紧（两个词之间不超过 20 字、且数字后紧跟「个」），
# 否则会把「单元测试数停在 131（实际 161）」这类叙述也当成断言，产生假阳性。
#
# 中间那一段**必须允许竖线**：`Memory.md` 的进度表写成
# 「| 单元测试 | ✅ 161 个 |」，而那一行正是今天出错的形状——
# 用 `[^|]` 把它排除掉，就等于这个检查白做。
TEST_COUNT_AFTER = re.compile(r"(\d+)\s*个\s*(?:单测|单元测试)")
TEST_COUNT_BEFORE = re.compile(r"(?:单测|单元测试)[^\n]{0,20}?(\d+)\s*个")
# Memory.md 里的"（ADR，17 篇）"
ADR_COUNT = re.compile(r"ADR，(\d+)\s*篇")
# ADR 文件名：0001-xxx.md
ADR_FILE = re.compile(r"^(\d{4})-.+\.md$")


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []
        self.checks = 0

    def ok(self, label: str) -> None:
        self.checks += 1
        print(f"  [OK]   {label}")

    def fail(self, label: str, detail: str = "") -> None:
        self.checks += 1
        self.failures.append(f"{label}{('：' + detail) if detail else ''}")
        print(f"  [FAIL] {label}")
        if detail:
            print(f"         {detail}")

    def warn(self, label: str, detail: str = "") -> None:
        self.warnings.append(f"{label}{('：' + detail) if detail else ''}")
        print(f"  [WARN] {label}")
        if detail:
            print(f"         {detail}")


def strip_code_blocks(text: str) -> str:
    """去掉 ``` 围起来的代码块。

    文档里的**模板示例**会写出形如 `[0001 标题](../decisions/0001-xxx.md)` 的占位符，
    它本来就不该指向真实文件。不跳过的话会永远报一条假阳性，
    然后所有人学会忽略输出——那它就彻底没用了。
    """
    out, inside = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            inside = not inside
            continue
        if not inside:
            out.append(line)
    return "\n".join(out)


def all_docs() -> list[Path]:
    return [
        md for md in REPO.rglob("*.md")
        if not any(part in SKIP_DIRS for part in md.parts)
    ]


def check_links(report: Report) -> None:
    total, broken = 0, []
    for md in all_docs():
        text = strip_code_blocks(md.read_text(encoding="utf-8", errors="replace"))
        for raw in LINK.findall(text):
            target = raw.split("#")[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            total += 1
            if not (md.parent / target).resolve().exists():
                broken.append(f"{md.relative_to(REPO)} → {raw}")

    if broken:
        report.fail(f"相对链接失效（{len(broken)}/{total} 条）", "；".join(broken[:6]))
    else:
        report.ok(f"相对链接全部有效（{total} 条）")


def actual_python_tests() -> int | None:
    """跑一次 pytest 收集，拿到真实的单测数。

    用 `--collect-only` 而不是真的跑测试：收集不执行任何用例，几秒就完，
    也不会消耗 API 额度。
    """
    result = subprocess.run(
        ["uv", "run", "pytest", "--collect-only", "-q"],
        cwd=AGENT, capture_output=True, text=True, timeout=180,
    )
    match = re.search(r"(\d+)\s+tests? collected", result.stdout)
    return int(match.group(1)) if match else None


def check_test_counts(report: Report) -> None:
    actual = actual_python_tests()
    if actual is None:
        report.warn("拿不到实际单测数（pytest 收集失败），跳过这项检查")
        return

    stated: list[tuple[str, int]] = []
    for md in all_docs():
        # **这里不剥代码块**（链接检查才需要剥）：这些数字最常见的落点
        # 恰恰是命令示例里的注释，例如 `uv run pytest  # 161 个单测`。
        rel = str(md.relative_to(REPO))
        for line in md.read_text(encoding="utf-8", errors="replace").splitlines():
            # 跳过在讲 Java 那套测试的行。否则 `mvn test  # 10 个单测` 会被
            # 当成"文档说 Python 有 10 个单测"——实测踩过这个假阳性。
            if "Java" in line or "mvn" in line:
                continue
            numbers = TEST_COUNT_AFTER.findall(line) + TEST_COUNT_BEFORE.findall(line)
            stated.extend((rel, int(number)) for number in numbers)

    wrong = [(path, n) for path, n in stated if n != actual]
    if wrong:
        detail = "；".join(f"{path} 写的是 {n}" for path, n in wrong)
        report.fail(f"单测数与实际（{actual}）不符", detail)
    elif stated:
        report.ok(f"单测数一致（{len(stated)} 处都写 {actual}）")
    else:
        report.warn("文档里没有找到单测数，无法核对")


def check_adr_count(report: Report) -> None:
    adr_dir = AGENT / "docs" / "decisions"
    files = sorted(p for p in adr_dir.iterdir() if ADR_FILE.match(p.name))
    memory = REPO / "Memory.md"

    match = ADR_COUNT.search(memory.read_text(encoding="utf-8"))
    if not match:
        report.warn("Memory.md 里没找到『ADR，N 篇』，无法核对篇数")
    elif int(match.group(1)) != len(files):
        report.fail(
            f"ADR 篇数不符：Memory.md 写 {match.group(1)}，实际 {len(files)} 个文件"
        )
    else:
        report.ok(f"ADR 篇数一致（{len(files)} 篇）")


def check_index(report: Report, directory: Path, index: Path, label: str) -> None:
    """索引文件必须登记目录里的每一个条目。

    索引是**手工**维护的，新加一篇忘了登记就搜不到——而这一层没有任何提示。
    """
    entries = sorted(p.name for p in directory.glob("*.md") if p.name != index.name)
    text = index.read_text(encoding="utf-8")
    missing = [name for name in entries if name not in text]

    if missing:
        report.fail(f"{label}索引漏了 {len(missing)} 条", "；".join(missing))
    else:
        report.ok(f"{label}索引完整（{len(entries)} 条）")


def check_java_tests(report: Report) -> None:
    """Java 测试数的**近似**核对。

    用源码里 `@Test` 的出现次数，而不是真的跑 `mvn test`（那要十几秒，
    而且这是个文档检查、不该依赖 Maven 环境可用）。
    参数化测试会让这个数偏少，所以只警告、不作为失败。
    """
    src = REPO / "AGI_Server" / "src" / "test"
    if not src.exists():
        return
    count = sum(
        p.read_text(encoding="utf-8", errors="replace").count("@Test")
        for p in src.rglob("*.java")
    )
    for md in all_docs():
        text = strip_code_blocks(md.read_text(encoding="utf-8", errors="replace"))
        for number in re.findall(r"(\d+)\s*个 Java 单测", text):
            if int(number) != count:
                report.warn(
                    f"Java 单测数可疑：{md.relative_to(REPO)} 写 {number}，"
                    f"源码里有 {count} 个 @Test（参数化测试会让这个数不准，请人工确认）"
                )
                return
    report.ok(f"Java 单测数未见矛盾（源码 {count} 个 @Test）")


def main() -> int:
    # 没有参数：输出本来就短，而且**它就是给人读的**（收工前看一眼）。
    # 不留一个没实现的开关——那比没有开关更糟。
    argparse.ArgumentParser(description="文档腐化检查").parse_args()

    print("文档腐化检查")
    print(f"仓库：{REPO}")
    print()

    report = Report()
    check_links(report)
    check_test_counts(report)
    check_adr_count(report)
    check_index(
        report,
        AGENT / "docs" / "decisions",
        AGENT / "docs" / "decisions" / "README.md",
        "ADR ",
    )
    check_index(
        report,
        AGENT / "docs" / "features",
        AGENT / "docs" / "features" / "README.md",
        "功能文档 ",
    )
    check_java_tests(report)

    print()
    if report.failures:
        print(f"发现 {len(report.failures)} 处需要修的问题——**先改文档再收工**。")
        return 1
    if report.warnings:
        print(f"通过，但有 {len(report.warnings)} 条警告（见上）。")
        return 0
    print(f"通过（{report.checks} 项检查）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
