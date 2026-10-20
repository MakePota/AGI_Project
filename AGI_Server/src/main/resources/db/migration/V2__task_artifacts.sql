-- 任务产物清单（JSON 数组）。
--
-- 为什么需要它：终态任务**不再向 Agent 对齐**（TaskService.get 对已结束的任务
-- 直接读本地镜像），所以产物元数据如果只留在 Agent 那边，刷新页面后就拿不到了。
-- 权威仍然在 Agent；这一列是镜像，形状是 AgentContract.AgentArtifact 的数组。
--
-- 为什么用 text 而不是 MySQL 的 json 类型：实体的 ddl-auto 是 validate，
-- Hibernate 对 json 列的映射更容易出现"实体声明与表结构对不上"的启动失败；
-- 而这份清单很小（每项四个短字段），用 text 存 JSON 足够，解析失败还能降级为
-- 空列表而不是让整个任务详情打不开。
--
-- **不要修改 V1__init.sql**：Flyway 校验和会变，所有环境的启动都会失败。
-- 表结构变化一律新增 Vn__xxx.sql。

alter table task
    add column artifacts text null comment 'Agent 产物清单（JSON 数组；权威在 Agent）' after error;
