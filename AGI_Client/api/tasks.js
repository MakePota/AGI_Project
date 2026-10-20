// 任务接口的客户端。
//
// 接口形状与后端（以及更上游的 AGI_Agent）一致，字段名是 snake_case——
// 三个服务乘同一个形状是刻意的，见 AGI_Server/README.md。
//
// 这里刻意不做任何"业务判断"：不猜状态、不做本地状态机。
// **任务状态只有一个来源**：后端告诉什么就是什么。

import { API_BASE } from './config.js'

/** 已结束的状态。与后端 TaskStatuses 里那份保持一致。 */
export const TERMINAL_STATUSES = ['completed', 'failed', 'cancelled', 'needs_human']

/** 把后端返回的 ProblemDetail 变成一句人话。 */
function errorText(res) {
  const data = res && res.data
  if (data && data.detail) {
    return data.detail
  }
  if (data && data.message) {
    return data.message
  }
  return `请求失败（HTTP ${res && res.statusCode}）`
}

function request(method, path, data) {
  return new Promise((resolve, reject) => {
    uni.request({
      url: API_BASE + path,
      method,
      data,
      header: { 'Content-Type': 'application/json' },
      timeout: 20000,
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data)
        } else {
          // uni.request 不会因为 4xx/5xx 走 fail，所以要在这里自己判。
          // 不判的话前端会把一个错误响应当成正常数据继续用。
          reject(new Error(errorText(res)))
        }
      },
      fail: (err) => {
        // 这条路径只代表"没连上"（后端没起、端口不通），
        // 与"后端返回了错误"要分开说，否则用户会往错的方向查。
        reject(new Error('连不上后端服务（' + API_BASE + '）：' + (err.errMsg || '未知原因')))
      }
    })
  })
}

export function createTask(goal) {
  return request('POST', '/api/tasks', { goal })
}

export function getTask(taskId) {
  return request('GET', `/api/tasks/${taskId}`)
}

/**
 * 生成产物（图片）的地址。
 *
 * **必须是绝对地址**：H5 跑在 HBuilderX 的开发服务器上（通常是 5173），
 * 与后端 8090 不同源。写成 `/api/tasks/...` 会请求到开发服务器自己，
 * 结果是一个 404 的破图，而不是后端的图片。
 *
 * 路径里带 taskId，是为了让服务端校验“这张图确实属于这个任务”，
 * 而不是让任何知道文件名的人都能取到任意产物。
 */
export function artifactUrl(taskId, name) {
  return API_BASE + '/api/tasks/' + encodeURIComponent(taskId)
    + '/artifacts/' + encodeURIComponent(name)
}

export function listTasks() {
  return request('GET', '/api/tasks')
}

export function decideApproval(taskId, approved, reason) {
  return request('POST', `/api/tasks/${taskId}/approval`, { approved, reason: reason || null })
}

/**
 * 上传一张图片，返回它在**引擎那一侧**的路径。
 *
 * 这个路径随后会写进任务目标文本——图片本身不离开这台机器，
 * 也不需要对象存储（见 AGI_Agent 的 `api/uploads.py` 里的说明）。
 *
 * @param {string} filePath uni.chooseImage 给的临时文件路径
 */
export function uploadImage(filePath) {
  return new Promise((resolve, reject) => {
    uni.uploadFile({
      url: API_BASE + '/api/uploads',
      filePath,
      name: 'file',
      timeout: 60000,
      success: (res) => {
        // uni.uploadFile 的 data 是**字符串**（不像 uni.request 会自动解析 JSON），
        // 所以这里必须自己 parse。忘了这一点的表现是"把一个字符串当对象用"，
        // 而且不会报错，只会在取值时得到 undefined。
        let payload = null
        try {
          payload = JSON.parse(res.data)
        } catch (err) {
          payload = null
        }
        if (res.statusCode >= 200 && res.statusCode < 300 && payload) {
          resolve(payload)
        } else {
          reject(new Error((payload && payload.detail) || `上传失败（HTTP ${res.statusCode}）`))
        }
      },
      fail: (err) => reject(new Error('上传失败：' + (err.errMsg || '未知原因')))
    })
  })
}

/**
 * 订阅任务事件流。
 *
 * **仅 H5**。浏览器有原生的 EventSource，直接用它最省事；
 * 但 uni-app 的 App 端不是浏览器环境，**没有 EventSource**，
 * 需要接原生插件或 renderjs 桥接（ADR 0016 里记了这笔代价）。
 * 这里不写一个假的轮询兜底：那是没验证过的代码，
 * 看起来能用却没人测过，比明确不支持更糟。
 *
 * @returns {{close: Function}} 关掉订阅
 */
export function subscribeEvents(taskId, since, handlers) {
  // #ifdef H5
  const url = `${API_BASE}/api/tasks/${taskId}/events?since=${since || 0}`
  const source = new EventSource(url)

  source.onmessage = (e) => {
    try {
      handlers.onEvent(JSON.parse(e.data))
    } catch (err) {
      // 解析不了的帧直接跳过：一条坏帧不该让整个进度列表停摆。
    }
  }
  // 连接断开都会走这里——**包括任务正常结束时服务端关闭连接**。
  // 所以不能一断开就报错，要先问一次状态（由页面决定）。
  source.onerror = () => handlers.onBreak()

  return { close: () => source.close() }
  // #endif

  // #ifndef H5
  handlers.onUnsupported && handlers.onUnsupported()
  return { close: () => {} }
  // #endif
}
