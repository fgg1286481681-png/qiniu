<template>
  <div class="app-shell">
    <header class="topbar">
      <div>
        <h1>Novel2Script</h1>
        <p>AI 小说转 YAML 结构化剧本工具</p>
      </div>
      <div class="topbar-actions">
        <button :disabled="aiChecking" @click="checkAiHealth">
          {{ aiChecking ? "检测中..." : "检测 AI" }}
        </button>
        <button @click="openHistory">历史项目</button>
        <button @click="loadOfflineDemo">载入完整演示</button>
        <button class="primary" :disabled="!yamlText" @click="downloadYaml">导出 YAML</button>
      </div>
    </header>

    <main class="workspace">
      <section class="panel input-panel">
        <div class="panel-title">
          <h2>小说输入</h2>
          <span>{{ wordCount }} 字</span>
        </div>

        <input v-model="title" class="title-input" placeholder="项目标题" />

        <div class="actions">
          <label class="file-button">
            上传文件
            <input type="file" :accept="acceptedFileTypes" @change="handleFile" />
          </label>
          <button @click="loadSample">载入样例</button>
          <button @click="analyzeText">识别章节</button>
        </div>

        <textarea v-model="novelText" placeholder="粘贴至少 3 个章节的小说文本"></textarea>

        <div class="chapter-box">
          <div class="panel-title small">
            <h3>章节识别</h3>
            <strong :class="{ danger: chapterCount > 0 && chapterCount < 3 }">{{ chapterCount }} 章</strong>
          </div>
          <p v-if="parseWarning" class="warning">{{ parseWarning }}</p>
          <p v-else-if="parseMode" class="muted">解析模式：{{ parseMode }}</p>
          <ul v-if="chapters.length">
            <li v-for="chapter in chapters" :key="chapter.chapter_id">
              <span>{{ chapter.title }}</span>
              <em>{{ chapter.word_count }} 字</em>
            </li>
          </ul>
          <p v-else class="muted">尚未识别章节</p>
        </div>

        <div class="generate-row">
          <button class="generate" :disabled="loading || chapterCount < 3" @click="generateScript">
            {{ loading ? `生成中 · ${elapsedLabel}` : "生成剧本" }}
          </button>
          <button v-if="loading && currentProjectId" class="cancel-button" @click="cancelGeneration">
            取消
          </button>
        </div>
        <p v-if="chapterCount < 3" class="generation-hint">
          至少识别到 3 个章节后才能生成，当前识别到 {{ chapterCount }} 章。
        </p>
        <p v-else-if="loading" class="generation-hint">
          {{ currentStep }} · 已用时 {{ elapsedLabel }}
        </p>
        <p v-if="errorMessage" class="error">{{ errorMessage }}</p>

        <div v-if="aiHealth.agents?.length" class="ai-health-panel">
          <div class="panel-title small">
            <h3>AI 配置自检</h3>
            <span :class="{ pass: aiHealth.ready, danger: !aiHealth.ready }">
              {{ aiHealth.ready ? "全部就绪" : "需要检查" }}
            </span>
          </div>
          <div v-for="item in aiHealth.agents" :key="item.agent" class="ai-health-row">
            <div>
              <strong>{{ item.agent }}</strong>
              <small>{{ item.model || "未配置模型" }}</small>
            </div>
            <span :class="{ pass: item.reachable, danger: !item.reachable }">
              {{ item.reachable ? `${formatDuration(item.duration_ms)} · 可用${item.shared_check ? "（共享模型检测）" : ""}` : item.configured ? "连接失败" : "未配置" }}
            </span>
            <p v-if="item.error">{{ item.error }}</p>
          </div>
        </div>
      </section>

      <section class="panel result-panel">
        <div class="panel-title">
          <h2>工作区</h2>
          <span>{{ currentStep }}</span>
        </div>

        <div class="progress">
          <div v-for="step in steps" :key="step.key" :class="['step', { active: step.key === currentStepKey, done: progress >= step.doneAt }]">
            {{ step.label }}
          </div>
        </div>
        <div v-if="loading" class="progress-meter">
          <span :style="{ width: `${progress}%` }"></span>
        </div>

        <div class="agent-chain">
          <div class="panel-title small">
            <h3>Agent 工作链</h3>
          </div>
          <article v-for="(item, index) in agentTrace" :key="`${item.stage}-${index}`">
            <strong>{{ item.agent }}</strong>
            <span :class="{ pass: ['success', 'repaired'].includes(item.status), warning: item.status === 'degraded', danger: item.status === 'failed' }">
              {{ item.status }}
            </span>
            <p>{{ item.summary }}</p>
            <small>
              {{ item.source || "unknown" }}
              <template v-if="item.model"> · {{ item.model }}</template>
              · {{ formatDuration(item.duration_ms) }}
              <template v-if="item.attempts"> · {{ item.attempts }} 次请求</template>
            </small>
            <p v-if="item.fallback_reason" class="warning">降级原因：{{ item.fallback_reason }}</p>
          </article>
          <p v-if="!agentTrace.length" class="muted">生成后展示 Reader / Planner / Writer / Validator 执行结果</p>
        </div>

        <div class="summary-grid">
          <div class="metric">
            <span>人物</span>
            <strong>{{ script.characters?.length || 0 }}</strong>
          </div>
          <div class="metric">
            <span>场景</span>
            <strong>{{ script.scenes?.length || 0 }}</strong>
          </div>
          <div class="metric">
            <span>地点</span>
            <strong>{{ script.locations?.length || 0 }}</strong>
          </div>
        </div>

        <div class="quality-panel">
          <div class="panel-title small">
            <h3>初稿质量诊断</h3>
            <span v-if="qualityMetrics.repair_triggered">已修复 {{ qualityMetrics.repair_count }} 轮</span>
          </div>
          <div class="quality-status">
            <span>结构状态</span>
            <strong :class="{ pass: qualityMetrics.schema_valid, danger: qualityMetrics.schema_valid === false }">
              {{ qualityMetrics.schema_valid === true ? "Schema 与引用通过" : qualityMetrics.schema_valid === false ? "结构校验失败" : "待检测" }}
            </strong>
          </div>
          <h4 class="quality-section-title">规则指标</h4>
          <div class="quality-grid">
            <div v-for="metric in qualityCards" :key="metric.label" class="quality-item">
              <span>{{ metric.label }}</span>
              <strong>{{ metric.value }}</strong>
            </div>
          </div>
          <template v-if="aiDraftScore !== null">
            <div class="ai-review-heading">
              <h4 class="quality-section-title">AI 初稿诊断</h4>
              <strong>{{ aiDraftScore }} / 100</strong>
            </div>
            <div class="quality-grid ai-score-grid">
              <div v-for="metric in aiScoreCards" :key="metric.label" class="quality-item">
                <span>{{ metric.label }}</span>
                <strong>{{ metric.value }}</strong>
              </div>
            </div>
            <p class="severity-summary">
              高风险 {{ severityCounts.high || 0 }} 项 ·
              中风险 {{ severityCounts.medium || 0 }} 项 ·
              低风险 {{ severityCounts.low || 0 }} 项
            </p>
            <div v-if="aiIssues.length" class="diagnosis-list">
              <article v-for="(issue, index) in aiIssues" :key="`${issue.scene_id}-${index}`" class="diagnosis-item">
                <div>
                  <span :class="['severity-badge', issue.severity]">{{ severityLabel(issue.severity) }}</span>
                  <strong>{{ issue.scene_id || "全局问题" }}</strong>
                </div>
                <p>{{ issue.message }}</p>
                <small>{{ issue.suggestion || issue.suggested_fix }}</small>
              </article>
            </div>
          </template>
          <p v-else class="muted">当前暂无 AI 初稿诊断，规则指标仍可正常使用。</p>
        </div>

        <div class="tabs">
          <button :class="{ selected: tab === 'characters' }" @click="tab = 'characters'">人物表</button>
          <button :class="{ selected: tab === 'scenes' }" @click="tab = 'scenes'">场景表</button>
          <button :class="{ selected: tab === 'notes' }" @click="tab = 'notes'">改编总结</button>
          <button :class="{ selected: tab === 'repairs' }" @click="tab = 'repairs'">修复记录</button>
        </div>

        <div class="content-list" v-if="tab === 'characters'">
          <article v-for="item in script.characters || []" :key="item.id">
            <strong>{{ item.name }}</strong>
            <span>{{ item.role }}</span>
            <p>{{ item.description }}</p>
          </article>
          <p v-if="!script.characters" class="muted">生成后展示人物表</p>
        </div>

        <div class="content-list" v-if="tab === 'scenes'">
          <details v-for="item in script.scenes || []" :key="item.id" class="scene-card" open>
            <summary>
              <strong>{{ item.id }} · {{ locationName(item.heading?.location_id) }}</strong>
              <span>{{ item.heading?.time_of_day }}</span>
            </summary>
            <p><b>目的：</b>{{ item.purpose }}</p>
            <p><b>冲突：</b>{{ item.conflict }}</p>
            <p class="source-map">
              来源章节：{{ item.source_chapters?.map(chapterName).join("、") }}
            </p>
            <div class="screenplay">
              <div v-for="(element, index) in item.elements || []" :key="index" :class="['script-element', element.type]">
                <strong v-if="element.type === 'dialogue'">{{ characterName(element.character_id) }}</strong>
                <span class="element-type">{{ elementTypeLabel(element.type) }}</span>
                <p>{{ element.text }}</p>
                <small v-if="element.event_id">关联事件：{{ element.event_id }}</small>
              </div>
            </div>
          </details>
          <p v-if="!script.scenes" class="muted">生成后展示场景表</p>
        </div>

        <div class="content-list" v-if="tab === 'notes'">
          <article v-if="script.adaptation_notes">
            <strong>保留</strong>
            <p>{{ script.adaptation_notes.retained?.join("；") }}</p>
            <strong>改写</strong>
            <p>{{ script.adaptation_notes.changed?.join("；") }}</p>
            <strong>下一步</strong>
            <p>{{ script.adaptation_notes.next_steps?.join("；") }}</p>
          </article>
          <p v-else class="muted">生成后展示改编总结</p>
        </div>

        <div class="content-list" v-if="tab === 'repairs'">
          <article v-for="item in repairHistory" :key="item.round">
            <strong>第 {{ item.round }} 轮修复</strong>
            <span :class="{ pass: item.after_validation?.valid, danger: !item.after_validation?.valid }">
              {{ item.after_validation?.valid ? "修复后通过" : "仍需修复" }}
            </span>
            <p v-if="item.reason">修复原因：{{ item.reason }}</p>
            <p v-if="item.rewrite_scope?.length">修改范围：{{ item.rewrite_scope.join("、") }}</p>
            <p>修复前问题：{{ item.before_validation?.errors?.length || 0 }} 项</p>
            <p>修复后问题：{{ item.after_validation?.errors?.length || 0 }} 项</p>
            <p v-if="item.before_metrics">
              AI 初稿评分：{{ metricScore(item.before_metrics) }} → {{ metricScore(item.after_metrics) }}
            </p>
            <p v-if="item.before_metrics">
              事件覆盖：{{ formatPercent(item.before_metrics.event_coverage) }} →
              {{ formatPercent(item.after_metrics?.event_coverage) }}
            </p>
          </article>
          <p v-if="!repairHistory.length" class="muted">本项目未触发自动修复</p>
        </div>
      </section>

      <section class="panel yaml-panel">
        <div class="panel-title">
          <h2>YAML 输出</h2>
          <span :class="{ pass: validation.valid, danger: validation.valid === false }">
            {{ validationLabel }}
          </span>
        </div>

        <textarea v-model="yamlText" class="yaml-editor" placeholder="生成后展示 YAML，可编辑后重新校验"></textarea>

        <div class="actions">
          <button :disabled="!yamlText" @click="copyYaml">复制</button>
          <button :disabled="!yamlText" @click="validateCurrent">校验</button>
        </div>

        <div class="validation">
          <h3>校验报告</h3>
          <p v-if="validation.valid" class="pass">YAML 结构通过 Schema 和引用校验</p>
          <ul v-else-if="validation.errors?.length">
            <li v-for="item in validation.errors" :key="`${item.path}-${item.message}`">
              <strong>{{ item.path }}</strong>
              <span>{{ item.message }}</span>
              <em v-if="item.suggestion">{{ item.suggestion }}</em>
            </li>
          </ul>
          <p v-else class="muted">暂无校验结果</p>
        </div>
      </section>
    </main>

    <div v-if="historyOpen" class="drawer-backdrop" @click.self="historyOpen = false">
      <aside class="history-drawer">
        <div class="drawer-header">
          <div>
            <h2>历史项目</h2>
            <p>{{ projects.length }} 个本地项目</p>
          </div>
          <button title="关闭历史项目" @click="historyOpen = false">关闭</button>
        </div>
        <div class="history-list">
          <article v-for="project in projects" :key="project.id" class="history-item">
            <div class="history-main">
              <strong>{{ project.title }}</strong>
              <span :class="statusClass(project.status)">{{ statusLabel(project.status) }}</span>
              <p>{{ formatProjectDate(project.updated_at) }} · {{ project.source_char_count }} 字</p>
              <small>
                {{ project.chapter_count }} 章 · {{ project.scene_count }} 场景
                <template v-if="project.repair_count"> · 修复 {{ project.repair_count }} 轮</template>
              </small>
            </div>
            <div class="history-actions">
              <button @click="restoreProject(project)">恢复</button>
              <button class="danger-button" @click="deleteProject(project)">删除</button>
            </div>
          </article>
          <p v-if="historyLoading" class="muted">正在读取项目...</p>
          <p v-else-if="!projects.length" class="muted">暂无历史项目</p>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";

const sampleText = `第一章 初入会议室 林夏推开会议室的门，所有人的目光都落在她身上。她刚加入项目组，却被要求解释一个失败方案。周谨合上电脑，声音很低：“你知道这个问题拖了多久吗？”林夏握紧资料：“我知道，但我找到新的证据。”
第二章 被质疑的方案
办公室里，林夏把数据重新投到屏幕上。周谨没有立刻反驳，其他同事开始交换眼神。她指出旧方案忽略了用户留存的变化，也说明预算并不是最大阻碍。周谨问：“如果你错了呢？”林夏回答：“那我承担后果。”
第三章 夜晚的转折 夜里的公司只剩几盏灯。林夏在会议室重新核对日志，发现真正的问题来自一次被遗漏的配置变更。周谨走到门口，看见她还没离开。林夏抬头说：“明天之前，我能把完整报告交给你。”`;

const title = ref("示例小说改编");
const novelText = ref("");
const sourceFilename = ref("");
const chapters = ref([]);
const chapterCount = ref(0);
const loading = ref(false);
const errorMessage = ref("");
const script = ref({});
const yamlText = ref("");
const validation = ref({});
const qualityMetrics = ref({});
const repairHistory = ref([]);
const tab = ref("characters");
const parseMode = ref("");
const parseWarning = ref("");
const agentTrace = ref([]);
const progress = ref(0);
const currentProjectId = ref("");
const elapsedSeconds = ref(0);
let elapsedTimer = null;
const historyOpen = ref(false);
const historyLoading = ref(false);
const projects = ref([]);
const aiChecking = ref(false);
const aiHealth = ref({});
const acceptedFileTypes = [
  ".txt",
  ".md",
  ".markdown",
  ".csv",
  ".tsv",
  ".json",
  ".yaml",
  ".yml",
  ".html",
  ".htm",
  ".xml",
  ".log",
  ".docx",
  ".epub",
  ".pdf",
  "text/plain",
  "text/markdown",
  "text/html",
  "application/json",
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/epub+zip",
].join(",");
const steps = [
  { key: "reader", label: "章节解析", doneAt: 30 },
  { key: "planner", label: "信息抽取", doneAt: 50 },
  { key: "writer", label: "剧本生成", doneAt: 70 },
  { key: "validator", label: "质量校验", doneAt: 78 },
  { key: "repair", label: "自动修复", doneAt: 92 },
  { key: "metrics", label: "指标计算", doneAt: 100 },
];
const currentStep = ref("待开始");
const currentStepKey = ref("");

const wordCount = computed(() => novelText.value.trim().length);
const validationLabel = computed(() => {
  if (validation.value.valid === true) return "校验通过";
  if (validation.value.valid === false) return "校验失败";
  return "待校验";
});
const qualityCards = computed(() => {
  const metrics = qualityMetrics.value || {};
  return [
    { label: "章节覆盖", value: formatPercent(metrics.chapter_coverage) },
    { label: "事件覆盖", value: formatPercent(metrics.event_coverage) },
    { label: "引用一致", value: formatPercent(metrics.reference_consistency) },
    { label: "场景完整", value: formatPercent(metrics.scene_completeness) },
  ];
});
const aiDraftScore = computed(() => {
  const value = qualityMetrics.value?.validator_ai_score
    ?? qualityMetrics.value?.ai_review?.ai_draft_score
    ?? qualityMetrics.value?.ai_review?.score;
  return value === null || value === undefined ? null : value;
});
const aiScores = computed(() => (
  qualityMetrics.value?.ai_scores
  || qualityMetrics.value?.ai_review?.scores
  || {}
));
const aiScoreCards = computed(() => [
  { label: "改编忠实度", value: formatScore(aiScores.value.fidelity) },
  { label: "场景可演性", value: formatScore(aiScores.value.performability) },
  { label: "人物与对白", value: formatScore(aiScores.value.character_dialogue_consistency) },
]);
const severityCounts = computed(() => (
  qualityMetrics.value?.severity_counts
  || qualityMetrics.value?.ai_review?.severity_counts
  || {}
));
const aiIssues = computed(() => qualityMetrics.value?.ai_review?.issues || []);
const elapsedLabel = computed(() => {
  const minutes = Math.floor(elapsedSeconds.value / 60);
  const seconds = elapsedSeconds.value % 60;
  return minutes ? `${minutes} 分 ${String(seconds).padStart(2, "0")} 秒` : `${seconds} 秒`;
});

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "请求失败");
  }
  return data;
}

async function getJson(url) {
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "请求失败");
  }
  return data;
}

async function deleteJson(url) {
  const response = await fetch(url, { method: "DELETE" });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "删除失败");
  }
  return data;
}

function formatPercent(value) {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "--";
}

function formatScore(value) {
  return typeof value === "number" ? `${value} / 100` : "--";
}

function metricScore(metrics) {
  const value = metrics?.validator_ai_score
    ?? metrics?.ai_review?.ai_draft_score
    ?? metrics?.ai_review?.score;
  return typeof value === "number" ? value : "--";
}

function severityLabel(severity) {
  return {
    high: "高",
    critical: "高",
    medium: "中",
    low: "低",
  }[severity] || "提示";
}

function formatDuration(value) {
  if (typeof value !== "number") return "耗时未知";
  if (value < 1000) return `${value} ms`;
  return `${(value / 1000).toFixed(1)} s`;
}

function characterName(id) {
  return script.value.characters?.find((item) => item.id === id)?.name || id || "未知人物";
}

function locationName(id) {
  return script.value.locations?.find((item) => item.id === id)?.name || id || "未知地点";
}

function chapterName(id) {
  return script.value.chapters?.find((item) => item.id === id)?.title || id;
}

function elementTypeLabel(type) {
  return {
    action: "动作",
    dialogue: "对白",
    narration: "旁白",
    transition: "转场",
    sound: "声音",
    shot: "镜头",
  }[type] || type;
}

function statusLabel(status) {
  return {
    processing: "处理中",
    completed: "已完成",
    completed_with_warnings: "有警告",
    failed: "失败",
    interrupted: "已中断",
    cancelled: "已取消",
  }[status] || status;
}

function statusClass(status) {
  if (status === "completed") return "pass";
  if (status === "completed_with_warnings" || status === "interrupted") return "warning";
  if (status === "failed" || status === "cancelled") return "danger";
  return "muted";
}

function formatProjectDate(value) {
  if (!value) return "时间未知";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function startElapsedTimer(initialSeconds = 0) {
  stopElapsedTimer();
  elapsedSeconds.value = Math.max(0, initialSeconds);
  elapsedTimer = window.setInterval(() => {
    elapsedSeconds.value += 1;
  }, 1000);
}

function stopElapsedTimer() {
  if (elapsedTimer !== null) {
    window.clearInterval(elapsedTimer);
    elapsedTimer = null;
  }
}

async function refreshProjects() {
  historyLoading.value = true;
  try {
    const data = await getJson("/api/projects");
    projects.value = data.projects || [];
  } finally {
    historyLoading.value = false;
  }
}

async function openHistory() {
  historyOpen.value = true;
  errorMessage.value = "";
  try {
    await refreshProjects();
  } catch (error) {
    errorMessage.value = error.message;
  }
}

async function checkAiHealth() {
  aiChecking.value = true;
  errorMessage.value = "";
  try {
    aiHealth.value = await postJson("/api/ai/health", {});
  } catch (error) {
    errorMessage.value = error.message;
  } finally {
    aiChecking.value = false;
  }
}

async function restoreProject(project) {
  errorMessage.value = "";
  try {
    const data = await getJson(`/api/projects/${project.id}`);
    currentProjectId.value = project.id;
    applyProject(data);
    historyOpen.value = false;
    if (data.status === "processing") {
      loading.value = true;
      const startedAt = data.started_at ? new Date(data.started_at).getTime() : Date.now();
      startElapsedTimer(Math.floor((Date.now() - startedAt) / 1000));
      localStorage.setItem("novel2script.activeProjectId", project.id);
      await pollProject(project.id);
    }
  } catch (error) {
    errorMessage.value = error.message;
    loading.value = false;
  }
}

async function deleteProject(project) {
  if (!window.confirm(`确认删除项目“${project.title}”及其本地文件吗？`)) return;
  errorMessage.value = "";
  try {
    await deleteJson(`/api/projects/${project.id}`);
    if (currentProjectId.value === project.id) {
      currentProjectId.value = "";
      localStorage.removeItem("novel2script.activeProjectId");
      novelText.value = "";
      sourceFilename.value = "";
      chapters.value = [];
      chapterCount.value = 0;
      script.value = {};
      yamlText.value = "";
      validation.value = {};
      qualityMetrics.value = {};
      repairHistory.value = [];
      agentTrace.value = [];
      progress.value = 0;
      currentStep.value = "待开始";
      currentStepKey.value = "";
      stopElapsedTimer();
    }
    await refreshProjects().catch(() => {});
  } catch (error) {
    errorMessage.value = error.message;
  }
}

function syncParseInfo(data) {
  parseMode.value = data.parse_mode || "";
  parseWarning.value = data.parse_warning || "";
}

async function analyzeText() {
  errorMessage.value = "";
  try {
    const data = await postJson("/api/analyze", { text: novelText.value });
    chapters.value = data.chapters;
    chapterCount.value = data.chapter_count;
    syncParseInfo(data);
  } catch (error) {
    errorMessage.value = error.message;
  }
}

function loadSample() {
  sourceFilename.value = "";
  novelText.value = sampleText;
  analyzeText();
}

function getFileExtension(file) {
  const name = file.name || "";
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(index + 1).toLowerCase() : "";
}

function scoreDecodedText(text) {
  if (!text) return -Infinity;
  const sample = text.slice(0, 4000);
  const replacementCount = (sample.match(/\uFFFD/g) || []).length;
  const cjkCount = (sample.match(/[\u4e00-\u9fff]/g) || []).length;
  const punctuationCount = (sample.match(/[，。！？；：“”‘’、,.!?;:"']/g) || []).length;
  const controlCount = (sample.match(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g) || []).length;
  return cjkCount * 3 + punctuationCount - replacementCount * 20 - controlCount * 30;
}

function decodeTextBuffer(buffer) {
  const bytes = new Uint8Array(buffer);
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) {
    return new TextDecoder("utf-8").decode(bytes.slice(3));
  }
  if (bytes[0] === 0xff && bytes[1] === 0xfe) {
    return new TextDecoder("utf-16le").decode(bytes.slice(2));
  }
  if (bytes[0] === 0xfe && bytes[1] === 0xff) {
    return new TextDecoder("utf-16be").decode(bytes.slice(2));
  }

  const evenNulls = bytes.filter((value, index) => index % 2 === 0 && value === 0).length;
  const oddNulls = bytes.filter((value, index) => index % 2 === 1 && value === 0).length;
  if (oddNulls > bytes.length * 0.2) {
    return new TextDecoder("utf-16le").decode(bytes);
  }
  if (evenNulls > bytes.length * 0.2) {
    return new TextDecoder("utf-16be").decode(bytes);
  }

  const encodings = ["utf-8", "gb18030", "gbk", "big5", "shift_jis", "windows-1252"];
  const results = [];
  for (const encoding of encodings) {
    try {
      const text = new TextDecoder(encoding, { fatal: true }).decode(buffer);
      results.push({ encoding, text, score: scoreDecodedText(text) });
    } catch {
      // Try the next common novel text encoding.
    }
  }
  if (results.length) {
    results.sort((a, b) => b.score - a.score);
    return results[0].text;
  }
  return new TextDecoder("utf-8").decode(buffer);
}

function stripHtml(html) {
  const doc = new DOMParser().parseFromString(html, "text/html");
  doc.querySelectorAll("script, style, noscript").forEach((node) => node.remove());
  return doc.body?.textContent?.replace(/\n{3,}/g, "\n\n").trim() || "";
}

async function readDocx(buffer) {
  const { default: mammoth } = await import("mammoth/mammoth.browser");
  const result = await mammoth.extractRawText({ arrayBuffer: buffer });
  return result.value.trim();
}

async function readEpub(buffer) {
  const { default: JSZip } = await import("jszip");
  const zip = await JSZip.loadAsync(buffer);
  const textParts = [];
  const entries = Object.values(zip.files)
    .filter((file) => !file.dir && /\.(xhtml|html|htm)$/i.test(file.name))
    .sort((a, b) => a.name.localeCompare(b.name));

  for (const entry of entries) {
    const html = await entry.async("string");
    const text = stripHtml(html);
    if (text) textParts.push(text);
  }

  if (!textParts.length) {
    throw new Error("未能从 EPUB 中提取正文");
  }
  return textParts.join("\n\n");
}

async function readPdf(buffer) {
  const pdfjsLib = await import("pdfjs-dist/build/pdf.mjs");
  const pdfWorkerUrl = (await import("pdfjs-dist/build/pdf.worker.mjs?url")).default;
  pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
  const document = await pdfjsLib.getDocument({ data: new Uint8Array(buffer) }).promise;
  const pages = [];
  for (let pageNumber = 1; pageNumber <= document.numPages; pageNumber += 1) {
    const page = await document.getPage(pageNumber);
    const content = await page.getTextContent();
    pages.push(content.items.map((item) => item.str).join(""));
  }
  return pages.join("\n\n").trim();
}

async function readUploadedFile(file) {
  const buffer = await file.arrayBuffer();
  const extension = getFileExtension(file);

  if (extension === "docx") return readDocx(buffer);
  if (extension === "epub") return readEpub(buffer);
  if (extension === "pdf") return readPdf(buffer);

  const text = decodeTextBuffer(buffer);
  if (["html", "htm"].includes(extension)) return stripHtml(text);
  return text;
}

async function handleFile(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  errorMessage.value = "";
  try {
    sourceFilename.value = file.name;
    novelText.value = await readUploadedFile(file);
    await analyzeText();
  } catch (error) {
    errorMessage.value = `读取文件失败：${error.message}`;
  }
}

async function generateScript() {
  loading.value = true;
  errorMessage.value = "";
  progress.value = 0;
  script.value = {};
  yamlText.value = "";
  validation.value = {};
  qualityMetrics.value = {};
  agentTrace.value = [];
  startElapsedTimer();
  try {
    currentStep.value = "任务排队";
    currentStepKey.value = "queued";
    const data = await postJson("/api/generate-async", {
      title: title.value,
      text: novelText.value,
      source_filename: sourceFilename.value || null,
    });
    currentProjectId.value = data.project_id;
    localStorage.setItem("novel2script.activeProjectId", data.project_id);
    await pollProject(data.project_id);
    await refreshProjects().catch(() => {});
  } catch (error) {
    errorMessage.value = error.message;
    currentStep.value = "失败";
    currentStepKey.value = "failed";
    loading.value = false;
    stopElapsedTimer();
    localStorage.removeItem("novel2script.activeProjectId");
  }
}

async function cancelGeneration() {
  if (!currentProjectId.value) return;
  errorMessage.value = "";
  try {
    await postJson(`/api/projects/${currentProjectId.value}/cancel`, {});
    currentStep.value = "正在取消";
    currentStepKey.value = "cancelling";
  } catch (error) {
    errorMessage.value = error.message;
  }
}

function applyProject(data) {
  title.value = data.title || title.value;
  sourceFilename.value = data.source_filename || "";
  if (data.source_text) novelText.value = data.source_text;
  script.value = data.script || {};
  yamlText.value = data.yaml || "";
  validation.value = data.validation || {};
  qualityMetrics.value = data.quality_metrics || {};
  repairHistory.value = data.repair_history || [];
  agentTrace.value = data.agent_trace || [];
  syncParseInfo(data);
  chapters.value = (data.script?.chapters || []).map((item) => ({
    chapter_id: item.id,
    title: item.title,
    order: item.order,
    word_count: item.word_count,
  }));
  chapterCount.value = chapters.value.length;
  progress.value = data.progress || 0;
  currentStepKey.value = data.current_step || "";
  currentStep.value = stepLabel(data.current_step, data.status);
  if (data.status === "processing" && elapsedTimer === null) {
    const startedAt = data.started_at ? new Date(data.started_at).getTime() : Date.now();
    startElapsedTimer(Math.floor((Date.now() - startedAt) / 1000));
  }
}

function stepLabel(step, status) {
  if (["completed", "completed_with_warnings"].includes(status)) {
    return status === "completed" ? "完成" : "完成（有警告）";
  }
  if (status === "failed") return "失败";
  if (status === "interrupted") return "任务已中断";
  if (status === "cancelled") return "已取消";
  if (step === "cancelling") return "正在取消";
  return steps.find((item) => item.key === step)?.label || (step === "queued" ? "任务排队" : "处理中");
}

async function pollProject(projectId) {
  while (true) {
    const data = await getJson(`/api/projects/${projectId}`);
    applyProject(data);
    if (["completed", "completed_with_warnings", "failed", "interrupted", "cancelled"].includes(data.status)) {
      loading.value = false;
      stopElapsedTimer();
      localStorage.removeItem("novel2script.activeProjectId");
      if (data.status === "failed") {
        throw new Error(data.error_message || "生成失败");
      }
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

async function loadOfflineDemo() {
  loading.value = true;
  stopElapsedTimer();
  errorMessage.value = "";
  try {
    const data = await postJson("/api/demo/import", {});
    currentProjectId.value = data.id;
    applyProject(data);
    currentStep.value = "离线演示已载入";
    currentStepKey.value = "completed";
    progress.value = 100;
    await refreshProjects();
  } catch (error) {
    errorMessage.value = error.message;
  } finally {
    loading.value = false;
  }
}

async function validateCurrent() {
  errorMessage.value = "";
  try {
    const data = await postJson("/api/validate", { yaml: yamlText.value });
    validation.value = data;
    qualityMetrics.value = data.quality_metrics || qualityMetrics.value;
    if (data.script) {
      script.value = data.script;
    }
  } catch (error) {
    errorMessage.value = error.message;
  }
}

async function copyYaml() {
  await navigator.clipboard.writeText(yamlText.value);
}

function downloadYaml() {
  const blob = new Blob([yamlText.value], { type: "text/yaml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${title.value || "novel2script"}.yaml`;
  anchor.click();
  URL.revokeObjectURL(url);
}

onMounted(async () => {
  const activeProjectId = localStorage.getItem("novel2script.activeProjectId");
  if (!activeProjectId) return;
  loading.value = true;
  startElapsedTimer();
  currentProjectId.value = activeProjectId;
  try {
    await pollProject(activeProjectId);
  } catch (error) {
    errorMessage.value = error.message;
    loading.value = false;
    stopElapsedTimer();
  }
});
</script>
