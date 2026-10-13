<template>
	<view class="page">
		<view class="header">
			<text class="status" :class="'status-' + statusTone">{{ statusLabel }}</text>
			<button v-if="taskId" class="mini" size="mini" @click="reset">新任务</button>
		</view>

		<view v-if="error" class="error">{{ error }}</view>

		<view class="composer">
			<input class="input" v-model="goal" :disabled="busy"
				placeholder="说一件要做的事，例如：列出当前目录下有哪些文件" confirm-type="send"
				@confirm="submit" />
			<button class="pick" size="mini" :disabled="busy" @click="chooseImage">选图</button>
			<button class="send" type="primary" :loading="busy" :disabled="!canSubmit || busy"
				@click="submit">发送</button>
		</view>

		<!-- 选中的图片。上传发生在点"发送"时，这里只做预览。 -->
		<view v-if="imagePath" class="attach">
			<image class="thumb" :src="imagePath" mode="aspectFit" />
			<text class="attach-name">已选图片（发送时上传）</text>
			<text class="attach-remove" @click="removeImage">移除</text>
		</view>

		<!--
			审批面板：整个界面的重点。
			它必须同时显示**描述**和**参数**——描述是模型写的散文，
			参数才是真正会被执行的东西（要发给哪个工具、动哪个文件、买哪个游戏）。
			只显示描述，人批准的是模型的说法而不是将要发生的事。
		-->
		<view v-if="approval" class="approval">
			<text class="approval-title">需要你确认后才会执行</text>
			<!--
				步骤按结构化方式渲染（描述 / 工具 / 参数分行），而**不是**直接显示
				服务端那段整块摘要：那段摘要里已经包含了同样的步骤文本（它是为终端
				排版准备的），两者一起显示会让人把同一件事读两遍。实测截图里就是这样。
				steps 为空时才退回显示摘要——正常不会发生，留个兜底。
			-->
			<view v-if="approval.steps.length">
				<view v-for="step in approval.steps" :key="step.index" class="step">
					<text class="step-line">[{{ riskLabel(step.risk) }}] {{ step.description }}</text>
					<text v-if="step.tool" class="step-tool">{{ step.tool }}</text>
					<text v-if="argsText(step.arguments)" class="step-args">{{ argsText(step.arguments) }}</text>
				</view>
			</view>
			<text v-else class="approval-summary">{{ approval.summary }}</text>
			<view class="approval-actions">
				<button class="reject" :disabled="busy" @click="decide(false)">拒绝</button>
				<button class="approve" type="primary" :disabled="busy" @click="decide(true)">批准执行</button>
			</view>
		</view>

		<view v-if="result" class="result">
			<text class="result-title">结果</text>
			<text class="result-text">{{ result }}</text>
		</view>

		<scroll-view class="events" scroll-y :scroll-into-view="scrollTarget">
			<view v-if="!events.length" class="empty">还没有事件。发一个任务试试。</view>
			<view v-for="event in events" :key="event.seq" :id="'e' + event.seq" class="event">
				<text class="event-type">{{ typeLabel(event.type) }}</text>
				<text class="event-text">{{ describe(event) }}</text>
			</view>
		</scroll-view>
	</view>
</template>

<script>
	import {
		createTask,
		getTask,
		decideApproval,
		subscribeEvents,
		uploadImage,
		TERMINAL_STATUSES
	} from '@/api/tasks.js'

	// 事件类型 → 进度行上的短标签。故意写得短，进度列表要能一眼扫过。
	const TYPE_LABELS = {
		task_received: '收到',
		routed: '分诊',
		plan_created: '规划',
		plan_revised: '重规划',
		approval_requested: '待批准',
		approval_decided: '已决定',
		tool_started: '调用中',
		tool_finished: '调用完',
		review_passed: '审查通过',
		review_rejected: '审查驳回',
		task_completed: '完成',
		task_failed: '失败',
		task_needs_human: '需人工'
	}

	// 这几个事件一到，任务就不会再有新事件了——这时候必须**主动关掉**事件流：
	// 服务端在任务结束后会关闭连接，而浏览器的 EventSource 会把它当成断线，
	// 每隔几秒重连一次，于是循环地重放历史事件并报错。
	const TERMINAL_EVENTS = ['task_completed', 'task_failed', 'task_needs_human']

	const RISK_LABELS = { read: '只读', write: '需确认', destructive: '不可逆' }

	const STATUS_LABELS = {
		received: '已受理',
		planning: '规划中',
		awaiting_approval: '等你批准',
		executing: '执行中',
		reviewing: '审查中',
		completed: '已完成',
		failed: '已失败',
		cancelled: '已取消',
		needs_human: '需人工处理'
	}

	export default {
		data() {
			return {
				goal: '',
				// 选中的图片的**本地临时路径**（用来预览）。真正的上传发生在点"发送"时——
				// 选了又不发的话不该在磁盘上留下东西。
				imagePath: '',
				taskId: '',
				status: '',
				events: [],
				approval: null,
				result: '',
				error: '',
				busy: false,
				subscription: null,
				scrollTarget: ''
			}
		},
		computed: {
			// 只选图不打字也要能发——"这张图里是什么"是最常见的用法，
			// 逼用户再打一句"描述这张图"没有意义。
			canSubmit() {
				return !!this.goal.trim() || !!this.imagePath
			},
			statusLabel() {
				if (!this.taskId) return '未开始'
				return STATUS_LABELS[this.status] || this.status || '…'
			},
			statusTone() {
				if (this.status === 'completed') return 'ok'
				if (this.status === 'failed' || this.status === 'needs_human') return 'bad'
				if (this.status === 'awaiting_approval') return 'warn'
				return 'run'
			}
		},
		onLoad() {
			// 刷新页面后重新接上正在跑（或正在等人批准）的任务。
			// 这能成立，是因为"等待批准"在服务端是一个**可查询的状态**，
			// 而不是挂在某条连接上的东西（ADR 0015）。
			this.restore()
		},
		onUnload() {
			this.closeStream()
		},
		methods: {
			async submit() {
				const typed = this.goal.trim()
				if (this.busy || !this.canSubmit) return

				this.busy = true
				this.error = ''
				const image = this.imagePath
				this.resetRun()
				try {
					let goal = typed
					if (image) {
						// 先上传拿到**引擎侧的路径**，再把它拼进目标文本。
						// 上传写在这个位置（而不是选图时）是刻意的：
						// 用户选了图又改主意不发，磁盘上就不该多一个文件。
						const uploaded = await uploadImage(image)
						goal = this.buildGoal(typed, uploaded.path)
					}
					const task = await createTask(goal)
					this.taskId = task.task_id
					this.status = task.status
					// 存起来，刷新页面后才接得回去。
					uni.setStorageSync('agi_task_id', this.taskId)
					this.goal = ''
					this.imagePath = ''
					this.openStream(0)
				} catch (err) {
					this.error = err.message
				} finally {
					this.busy = false
				}
			},

			chooseImage() {
				if (this.busy) return
				uni.chooseImage({
					count: 1,
					success: (res) => {
						this.imagePath = res.tempFilePaths && res.tempFilePaths[0]
						this.error = ''
					},
					fail: () => {
						// 用户取消选择也会走到 fail，那不是错误，不该弹提示。
					}
				})
			},

			removeImage() {
				this.imagePath = ''
			},

			/**
			 * 把图片路径拼进任务目标文本。
			 *
			 * **这是一个已知的折中。** `Task` 目前只有 `goal` 一个字段，图片"在哪"
			 * 只能靠这段文字传给 planner，再由它抄进图像工具的参数。
			 * 模型照抄一个路径大概率没问题，但**不是硬保证**；
			 * 更稳妥的做法是给 `Task` 加一个附件字段，那是更大的改动
			 * （要动数据契约、checkpoint 与 plannner 提示词），等这里实测出问题再上。
			 *
			 * 路径用正斜杠（服务端返回时就处理好了）：反斜杠在文本里更容易被转义或吃掉。
			 */
			buildGoal(typed, imagePath) {
				const base = typed || '描述这张图片的内容'
				return base + '\n（本次任务的图片在本机路径 ' + imagePath + '，请用图像工具读取它）'
			},

			async restore() {
				const saved = uni.getStorageSync('agi_task_id')
				if (!saved) return
				this.taskId = saved
				// 先问一次当前状态：可能还在跑、可能在等人、也可能早跑完了。
				await this.refresh()
				if (!TERMINAL_STATUSES.includes(this.status)) {
					// 从 0 开始订阅，服务端会把已有事件全部补发一遍——
					// 于是刷新页面也能看到完整的执行过程。
					this.openStream(0)
				}
			},

			openStream(since) {
				this.closeStream()
				this.subscription = subscribeEvents(this.taskId, since, {
					onEvent: (event) => this.onEvent(event),
					onBreak: () => this.onBreak(),
					onUnsupported: () => {
						this.error = '当前平台暂不支持事件流（App 端需要原生 SSE 插件，见 ADR 0016）。任务仍在后台执行，点"新任务"旁边的状态或重进页面可以看到最新状态。'
					}
				})
			},

			closeStream() {
				if (this.subscription) {
					this.subscription.close()
					this.subscription = null
				}
			},

			onEvent(event) {
				// 按 seq 去重。断线重连后服务端会从头重发一遍，
				// 不去重的话同一条事件会重复出现。
				if (this.events.some((e) => e.seq === event.seq)) return
				this.events.push(event)

				if (event.type === 'approval_requested') {
					this.approval = {
						summary: (event.payload && event.payload.summary) || '',
						steps: (event.payload && event.payload.steps) || []
					}
					this.status = 'awaiting_approval'
				}
				if (event.type === 'approval_decided') {
					this.approval = null
				}
				if (TERMINAL_EVENTS.includes(event.type)) {
					// 任务结束了：关掉流（否则 EventSource 会不停重连），
					// 并拉一次最终状态把结果取回来。
					this.closeStream()
					this.refresh()
				}
				this.scrollToBottom()
			},

			onBreak() {
				// 连着断线都会走到这里，**包括任务正常结束时服务端关连接**，
				// 所以不能一律报错——先问状态再决定。
				this.refresh()
			},

			async refresh() {
				if (!this.taskId) return
				try {
					const task = await getTask(this.taskId)
					this.status = task.status
					if (task.approval) this.approval = task.approval
					if (task.result) this.result = task.result
					if (task.error) this.error = task.error
					if (TERMINAL_STATUSES.includes(task.status)) {
						this.approval = null
						this.closeStream()
						uni.removeStorageSync('agi_task_id')
					}
				} catch (err) {
					this.error = err.message
				}
			},

			async decide(approved) {
				if (this.busy) return
				this.busy = true
				this.error = ''
				try {
					await decideApproval(this.taskId, approved)
					this.approval = null
				} catch (err) {
					// 最常见的失败是 409：这个审批已经被处理过了（比如另一个页面点过）。
					// 不把它当成"操作失败"，而是去拉一次最新状态，让界面回到真实的样子。
					this.error = err.message
				} finally {
					this.busy = false
					await this.refresh()
				}
			},

			reset() {
				this.closeStream()
				uni.removeStorageSync('agi_task_id')
				this.imagePath = ''
				this.resetRun()
			},

			resetRun() {
				this.taskId = ''
				this.status = ''
				this.events = []
				this.approval = null
				this.result = ''
				this.error = ''
			},

			scrollToBottom() {
				if (this.events.length) {
					this.scrollTarget = 'e' + this.events[this.events.length - 1].seq
				}
			},

			typeLabel(type) {
				return TYPE_LABELS[type] || type
			},

			riskLabel(risk) {
				return RISK_LABELS[risk] || risk || '?'
			},

			// 参数渲染成一行紧凑的 "键=值"。它就是"将要发生的事"，
			// 审批时最该看清的就是它。
			//
			// 长度上限给到 200 而不是几十：最常出现的参数是一条**文件路径**，
			// 而路径最有信息量的部分在**末尾**（文件名）。卡在 60 字符会把
			// `...uploads/20261013-224151-450eeb81.png` 截成 `...450eeb81.pn…`，
			// 于是"要动哪个文件"这件事反而看不清了——那正是显示参数的意义所在。
			// 面板是整宽的、样式里也开了 word-break，长一点会换行而不是溢出。
			argsText(args) {
				if (!args) return ''
				const parts = Object.keys(args).map((key) => {
					let value = String(args[key]).replace(/\s+/g, ' ')
					if (value.length > 200) value = value.slice(0, 200) + '…'
					return key + '=' + value
				})
				return parts.join('，')
			},

			// 把事件压成一句人看得懂的话。**只做展示**，不据此判断任务状态——
			// 状态只有一个来源，就是后端返回的 status。
			describe(event) {
				const p = event.payload || {}
				switch (event.type) {
					case 'task_received':
						return p.goal || ''
					case 'routed':
						return p.kind === 'chat' ? '判定为闲聊' : '判定为任务'
					case 'plan_created':
					case 'plan_revised':
						return (p.tools && p.tools.length ? '将调用：' + p.tools.join('、') : '（无需工具）')
							+ (p.rationale ? '　' + p.rationale : '')
					case 'approval_requested':
						return '已停下，等你决定'
					case 'approval_decided':
						return p.approved ? '你已批准' : '你已拒绝' + (p.reason ? '：' + p.reason : '')
					case 'tool_started':
						return p.text || '开始调用工具'
					case 'tool_finished':
						return (p.tool || '') + ' → ' + (p.status || '')
							+ (p.result ? '：' + p.result : '')
							+ (p.error ? '：' + p.error : '')
					case 'review_passed':
						return '审查通过' + (p.reason ? '：' + p.reason : '')
					case 'review_rejected':
						return '审查驳回' + (p.reason ? '：' + p.reason : '')
					case 'task_completed':
						return p.reply || '任务完成'
					case 'task_failed':
						return p.error || '任务失败'
					case 'task_needs_human':
						return p.detail || '需要你处理'
					default:
						return JSON.stringify(p)
				}
			}
		}
	}
</script>

<style>
	.page {
		display: flex;
		flex-direction: column;
		height: 100vh;
		padding: 20rpx;
		box-sizing: border-box;
		background-color: #f7f7f8;
	}

	.header {
		display: flex;
		flex-direction: row;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 16rpx;
	}

	.status {
		font-size: 28rpx;
		padding: 6rpx 18rpx;
		border-radius: 999rpx;
	}

	.status-ok {
		background-color: #e6f7ed;
		color: #0b7a3b;
	}

	.status-bad {
		background-color: #fdecec;
		color: #b3261e;
	}

	.status-warn {
		background-color: #fff5e0;
		color: #9a6400;
	}

	.status-run {
		background-color: #eef1ff;
		color: #3a4bb5;
	}

	.mini {
		margin: 0;
		font-size: 24rpx;
	}

	.error {
		background-color: #fdecec;
		color: #b3261e;
		font-size: 26rpx;
		padding: 16rpx;
		border-radius: 8rpx;
		margin-bottom: 16rpx;
	}

	.composer {
		display: flex;
		flex-direction: row;
		align-items: center;
		background-color: #ffffff;
		border-radius: 12rpx;
		padding: 8rpx;
	}

	.input {
		flex: 1;
		font-size: 28rpx;
		padding: 12rpx;
	}

	.pick {
		margin: 0 8rpx 0 0;
		font-size: 26rpx;
	}

	.send {
		margin: 0;
		font-size: 28rpx;
	}

	.attach {
		display: flex;
		flex-direction: row;
		align-items: center;
		background-color: #ffffff;
		border-radius: 12rpx;
		padding: 10rpx 16rpx;
		margin-top: 12rpx;
	}

	.thumb {
		width: 96rpx;
		height: 96rpx;
		border-radius: 8rpx;
		background-color: #f2f2f2;
	}

	.attach-name {
		flex: 1;
		font-size: 24rpx;
		color: #7a7a7a;
		margin-left: 16rpx;
	}

	.attach-remove {
		font-size: 24rpx;
		color: #b3261e;
		padding: 8rpx 12rpx;
	}

	.approval {
		background-color: #fff8e6;
		border: 2rpx solid #f0c674;
		border-radius: 12rpx;
		padding: 20rpx;
		margin-top: 16rpx;
	}

	.approval-title {
		display: block;
		font-size: 28rpx;
		font-weight: bold;
		color: #8a5a00;
		margin-bottom: 12rpx;
	}

	.approval-summary {
		display: block;
		font-size: 26rpx;
		color: #4a4a4a;
		white-space: pre-wrap;
	}

	.step {
		margin-top: 16rpx;
		padding-top: 12rpx;
		border-top: 2rpx solid #f0e0bb;
	}

	.step-line {
		display: block;
		font-size: 26rpx;
		color: #333333;
	}

	.step-tool {
		display: block;
		font-size: 24rpx;
		color: #7a7a7a;
		margin-top: 4rpx;
	}

	.step-args {
		display: block;
		font-size: 24rpx;
		color: #1a5fb4;
		margin-top: 4rpx;
		word-break: break-all;
	}

	.approval-actions {
		display: flex;
		flex-direction: row;
		margin-top: 20rpx;
	}

	.reject {
		flex: 1;
		margin-right: 16rpx;
		font-size: 28rpx;
	}

	.approve {
		flex: 2;
		font-size: 28rpx;
	}

	.result {
		background-color: #ffffff;
		border-radius: 12rpx;
		padding: 20rpx;
		margin-top: 16rpx;
	}

	.result-title {
		display: block;
		font-size: 26rpx;
		color: #7a7a7a;
		margin-bottom: 8rpx;
	}

	.result-text {
		display: block;
		font-size: 28rpx;
		color: #222222;
		white-space: pre-wrap;
	}

	.events {
		flex: 1;
		margin-top: 16rpx;
		background-color: #ffffff;
		border-radius: 12rpx;
		padding: 12rpx;
	}

	.empty {
		font-size: 26rpx;
		color: #9a9a9a;
		padding: 20rpx;
	}

	.event {
		display: flex;
		flex-direction: row;
		padding: 10rpx 8rpx;
		border-bottom: 2rpx solid #f2f2f2;
	}

	.event-type {
		font-size: 24rpx;
		color: #3a4bb5;
		width: 130rpx;
		flex-shrink: 0;
	}

	.event-text {
		flex: 1;
		font-size: 24rpx;
		color: #444444;
		word-break: break-all;
	}
</style>
