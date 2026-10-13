package com.agiproject.server.task;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

/**
 * 任务表的读写入口。
 *
 * <p>只声明真正需要的方法。加一个 {@code findByXxx} 就是加一条查询路径，
 * 而每条查询路径将来都要跟着表结构一起维护——需要时再加，不要预先铺一排。
 */
public interface TaskRepository extends JpaRepository<TaskEntity, String> {

    /** 列表页用：某用户最近的任务排在前面。 */
    List<TaskEntity> findByUserIdOrderByCreatedAtDesc(String userId);
}
