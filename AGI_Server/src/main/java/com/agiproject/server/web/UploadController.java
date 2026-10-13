package com.agiproject.server.web;

import com.agiproject.server.agent.AgentClient;
import com.agiproject.server.agent.AgentContract;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;

/**
 * 图片上传。
 *
 * <p><b>本服务不保存上传的文件</b>，由 `AGI_Agent` 收下并保存（图片归它管：
 * 读图的是它，以后换存储的也是它）。这里只是一个转手点。
 *
 * <p>为什么不让前端直接传给它：前端只应该认识 `AGI_Server`（ADR 0014）。
 * 多这一跳换来的是——将来 Agent 换地址、换存储方式，前端一行都不用改。
 */
@RestController
@RequestMapping("/api/uploads")
public class UploadController {

    private final AgentClient agentClient;

    public UploadController(AgentClient agentClient) {
        this.agentClient = agentClient;
    }

    /**
     * 收下一张图片，转交 Agent 保存，返回它的路径。
     *
     * <p>路径随后会被前端写进任务目标文本，由 planner 交给图像工具去读。
     */
    @PostMapping
    public AgentContract.UploadResult upload(@RequestParam("file") MultipartFile file) {
        if (file == null || file.isEmpty()) {
            throw new IllegalArgumentException("上传的文件是空的");
        }
        byte[] data;
        try {
            data = file.getBytes();
        } catch (IOException ex) {
            // 读不出来通常是请求体在传输中被截断，属于客户端侧的问题，
            // 但也可能是磁盘/临时目录的问题——所以带上原始异常，别把它吞掉。
            throw new IllegalStateException("读取上传内容失败：" + ex.getMessage(), ex);
        }
        return agentClient.uploadImage(data, file.getOriginalFilename());
    }
}
