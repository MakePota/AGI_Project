# AGI_Project

Monorepo：`AGI_Client`（客户端）/ `AGI_Server`（服务端）/ `AGI_Agent`（智能体引擎）。
后两者目前是空目录（`.gitkeep` 占位）。

## 动手前按顺序读，不要跳

1. `Memory.md` —— 这是什么项目、做到哪一步了。**纯交接文档，不含设计论证。**
2. `AGI_Agent/docs/architecture.md` —— 整体结构、技术栈、目录职责。
3. 要动某个功能时：`AGI_Agent/docs/features/README.md` → 对应功能文档 → 它链接的 ADR。

**不要一次读完整个 `docs/`。** 它是档案，不是教材；按需读的约定写在那两个 README 顶部。

## 硬约束

完整版见 `Memory.md` §3。摘要：

- 不自动 `git commit` / `git push`；提交前必须先展示变更摘要并等待确认。
- 删除文件/目录/Git 历史、改 `.env` 或密钥、`git push`/`rebase`/`reset --hard`、公开发布：**先问**。
- 注释用中文，写到「用户本人只读注释就能看懂并修改」的程度（含为什么这么做、被否决的方案）。
- 改功能时同步更新它的功能文档。

---

本文件刻意保持极简：**它每次会话都进上下文**，写长了等于每次会话都白付这份 token。
详细内容放 `Memory.md` 与 `docs/`，按需读。
