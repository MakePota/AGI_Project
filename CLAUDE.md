# AGI_Project

Monorepo，三层，都已实现并端到端跑通：

- `AGI_Client` —— 界面（uni-app / Vue 3，当前只跑 H5）
- `AGI_Server` —— 业务服务（Java 21 + Spring Boot 4 + MySQL）
- `AGI_Agent` —— 智能体引擎（Python + LangGraph）

## 动手前按顺序读，不要跳

1. `README.md` —— 仓库入口：三层各是什么、怎么一起跑、文档都在哪。
2. `Memory.md` —— 这是什么项目、做到哪一步了。**纯交接文档，不含设计论证。**
3. 要动哪一层就读哪一层，顺序见 `Memory.md` §7：
   - 引擎 → `AGI_Agent/docs/architecture.md` → `docs/features/README.md` → 功能文档 → 其中的 ADR
   - 服务 → `AGI_Server/README.md` → `AGI_Server/docs/architecture.md`
   - 界面 → `AGI_Client/README.md`

**不要一次读完整个 `docs/`。** 它是档案，不是教材；按需读的约定写在各 README 顶部。

## 收工时（用户说"结束 / 今天就到这 / 收工"之类）

**跑一次文档检查，把结果报出来；有失败项就先改文档再收工。**

```bash
cd AGI_Agent && uv run python scripts/check_docs.py
```

**为什么这条必须做**：`Memory.md` 是**唯一**的状态来源——下一个会话（哪怕是几天后、换一个
AI 工具）全靠它知道"现在做到哪了"。而它是手工维护的：漏更新一次，下一个会话就会
**拿着旧状态开工，而且不会有任何东西报错**。这个脚本就是那个"东西"。

它只查**能被机械验证**的部分（数字对不对、索引全不全、链接通不通）；
"描述是否还算准确"仍然要靠判断。

## 硬约束

完整版见 `Memory.md` §3。摘要：

- 不自动 `git commit` / `git push`；提交前必须先展示变更摘要并等待确认。
- 删除文件/目录/Git 历史、改 `.env` 或密钥、`git push`/`rebase`/`reset --hard`、公开发布：**先问**。
- 注释用中文，写到「用户本人只读注释就能看懂并修改」的程度（含为什么这么做、被否决的方案）。
- 改功能时同步更新它的功能文档。

---

本文件刻意保持极简：**它每次会话都进上下文**，写长了等于每次会话都白付这份 token。
详细内容放 `Memory.md` 与 `docs/`，按需读。
