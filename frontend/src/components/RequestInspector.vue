<template>
  <div class="request-inspector">
    <section class="inspector-section">
      <div class="inspector-section-heading">
        <h3>{{ t('请求追踪') }}</h3>
        <span class="state-label">
          <i :class="{ working: busy, complete: lastResponse && !busy }"></i>
          {{ busy ? t('处理中') : lastResponse ? t('已完成') : t('待开始') }}
        </span>
      </div>
      <p class="inspector-intro">{{ busy ? t('正在等待服务返回处理结果。') : lastResponse ? t('本次请求的处理路径与路由结果。') : t('发送一条消息，查看 Agent 的处理过程。') }}</p>
      <ol class="trace-steps" :class="{ complete: lastResponse && !busy }">
        <li>
          <span class="trace-step-icon">
            <AppIcon name="search" />
          </span>
          <div>
            <strong>{{ t('意图识别') }}</strong>
            <p>{{ lastResponse?.intent || t('理解客户的问题') }}</p>
          </div>
        </li>
        <li>
          <span class="trace-step-icon">
            <AppIcon name="book" />
          </span>
          <div>
            <strong>{{ t('知识检索') }}</strong>
            <p>{{ lastResponse ? lastResponse.knowledgeUsed ? t('已使用知识库') : t('未使用知识库') : t('查找相关业务知识') }}</p>
          </div>
        </li>
        <li>
          <span class="trace-step-icon">
            <AppIcon name="route" />
          </span>
          <div>
            <strong>{{ t('Agent 路由') }}</strong>
            <p>{{ lastResponse ? lastResponse.primaryAgent || lastResponse.agentType || t('未返回 Agent') : t('分配给合适的 Agent') }}</p>
          </div>
        </li>
        <li>
          <span class="trace-step-icon">
            <AppIcon name="chat" />
          </span>
          <div>
            <strong>{{ t('生成回复') }}</strong>
            <p>{{ busy ? t('等待回复') : lastResponse ? lastResponse.escalated ? t('已转人工处理') : t('回复已生成') : t('等待新的请求') }}</p>
          </div>
        </li>
      </ol>
      <div class="request-metrics">
        <div>
          <span>{{ t('响应耗时') }}</span>
          <strong>
            {{ lastResponse ? lastResponse.latencyMs : '—' }}
            <small v-if="lastResponse"> ms</small>
          </strong>
        </div>
        <div>
          <span>{{ t('路由置信度') }}</span>
          <strong>{{ lastResponse ? formatPercent(lastResponse.routingConfidence) : '—' }}</strong>
        </div>
      </div>
      <p v-if="lastResponse?.routingReason" class="routing-note">{{ lastResponse.routingReason }}</p>
      <details v-if="lastTrace?.trace" class="inspector-tools">
        <summary>
          <span>{{ t('工具调用') }}</span>
          <span class="count-tag">{{ lastTrace.trace.toolCalls?.length || 0 }}</span>
          <AppIcon name="chevron" />
        </summary>
        <div v-for="(call, index) in lastTrace.trace.toolCalls" :key="index" class="trace-call-item">
          <div class="trace-call-meta">
            <strong>{{ call.tool_name }}</strong>
            <span>{{ call.latency_ms ?? '—' }} ms</span>
          </div>
          <pre>{{ formatJson(call.input || {}) }}</pre>
        </div>
        <p v-if="!lastTrace.trace.toolCalls?.length" class="muted">{{ lastTrace.trace.toolsUsed?.length ? lastTrace.trace.toolsUsed.join(' · ') : t('本次没有记录到工具输入。') }}</p>
      </details>
    </section>
    <section class="inspector-section">
      <div class="inspector-section-heading">
        <h3>{{ t('运行状态') }}</h3>
        <button class="icon-button" @click="$emit('refresh')" :aria-label="t('刷新运行状态')" :title="t('刷新运行状态')">
          <AppIcon name="refresh" />
        </button>
      </div>
      <div class="runtime-metrics">
        <div>
          <span>{{ t('请求') }}</span>
          <strong>{{ monitorData.loaded ? totalRequests : '—' }}</strong>
        </div>
        <div>
          <span>Agent</span>
          <strong>{{ monitorData.loaded ? agentCount : '—' }}</strong>
        </div>
        <div>
          <span>{{ t('告警') }}</span>
          <strong :class="{ danger: activeAlerts.length }">{{ monitorData.loaded ? activeAlerts.length : '—' }}</strong>
        </div>
      </div>
      <p v-if="activeAlerts.length" class="alert-note">
        <AppIcon name="alert" />
        {{ activeAlerts[0].detail || activeAlerts[0].title }}
      </p>
      <p v-else class="runtime-note">{{ monitorData.loaded ? t('当前没有活跃告警') : t('连接服务后查看运行状态') }}</p>
    </section>
  </div>
</template>

<script setup>
import { t } from '../lib/i18n.js'

import { computed } from 'vue'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  lastResponse: { type: Object, default: null },
  lastTrace: { type: Object, default: null },
  busy: { type: Boolean, default: false },
  monitorData: { type: Object, required: true }
})
defineEmits(['refresh'])
const activeAlerts = computed(() => props.monitorData.active_alerts || [])
const agentCount = computed(() => Object.keys(props.monitorData.agent_stats || {}).length)
const totalRequests = computed(() => Object.values(props.monitorData.agent_stats || {}).reduce((sum, item) => sum + Number(item.total || 0), 0))
function formatPercent(value) {
  const number = Number(value || 0)
  return `${(number <= 1 ? number * 100 : number).toFixed(1)}%`
}
function formatJson(value) { return JSON.stringify(value ?? {}, null, 2) }
</script>
