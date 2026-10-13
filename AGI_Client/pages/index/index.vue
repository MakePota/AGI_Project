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
			<button class="send" type="primary" :loading="busy" :disabled="!goal.trim() || busy"
				@click="submit">发送</button>
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
				const goal = this.goal.trim()
				if (!goal || this.busy) return

				this.busy = true
				this.error = ''
				this.resetRun()
				try {
					const task = await createTask(goal)
					this.taskId = task.task_id
					this.status = task.status
					// 存起来，刷新页面后才接得回去。
					uni.setStorageSync('agi_task_id', this.taskId)
					this.goal = ''
					this.openStream(0)
				} catch (err) {
					this.error = err.message
				} finally {
					this.busy = false
				}
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
			argsText(args) {
				if (!args) return ''
				const parts = Object.keys(args).map((key) => {
					let value = String(args[key]).replace(/\s+/g, ' ')
					if (value.length > 60) value = value.slice(0, 60) + '…'
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

	.send {
		margin: 0;
		font-size: 28rpx;
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
