-- 第一版只有一张表：任务。
--
-- 为什么用 Flyway 而不是让 JPA 自动建表：表结构的每次变化都要留下可追溯的记录。
-- 之后加列/加表一律新增 V2__xxx.sql，**不要改这个文件**——Flyway 会校验已执行过的
-- 脚本的校验和，改了它会导致所有环境的启动直接失败（这是有意的保护：
-- 已经上线的迁移脚本不允许被悄悄改写）。

create table if not exists task
(
    -- 与 AGI_Agent 的 task_id **同值**。两边靠它对齐，所以这个值由 Agent 生成后
    -- 原样写入，本地**不重新生成**——否则两个系统各有一套 id，对不上账。
    id         varchar(64)   not null comment '任务 id，与 Agent 侧同值',
    -- 现在就加上这一列，值先固定。以后真做登录时只是"换个值"，
    -- 不需要改表结构，也不需要给存量数据做迁移。
    user_id    varchar(64)   not null comment '所属用户；当前无登录，填固定值',
    goal       varchar(2000) not null comment '用户下达的任务目标',
    -- 用户可见的任务状态。**权威在 Agent**，这里存的是镜像：
    -- 服务端只抄 Agent 告诉它的，绝不自己推断（见 ADR 0014）。
    status     varchar(32)   not null comment '任务状态；取自 Agent，不自行推断',
    result     text          null comment '最终回复',
    error      text          null comment '失败或需要人介入时的说明',
    created_at datetime(6)   not null,
    updated_at datetime(6)   not null,
    primary key (id),
    -- 列表接口按"某用户最近的任务"查，这个复合索引正好覆盖它。
    key idx_task_user_created (user_id, created_at)
) engine = InnoDB
  default charset = utf8mb4 comment '任务（业务数据，执行状态的镜像是 Agent 的 checkpointer）';

-- 这里**没有**事件表，是刻意的：服务端只转发事件、不存事件（ADR 0015 的后果一节）。
-- 触发条件：真要做"任务历史详情页"时再加。不留空壳表，是因为没人敢删的空表
-- 迟早会被误当成"应该往里写东西"。
