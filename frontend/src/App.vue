<template>
  <div class="app-shell">
    <header class="topbar">
      <div>
        <h1>Novel2Script</h1>
        <p>AI 小说转 YAML 结构化剧本工具</p>
      </div>
      <button class="primary" :disabled="!yamlText" @click="downloadYaml">导出 YAML</button>
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
            上传 TXT
            <input type="file" accept=".txt,text/plain" @change="handleFile" />
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
          <ul v-if="chapters.length">
            <li v-for="chapter in chapters" :key="chapter.chapter_id">
              <span>{{ chapter.title }}</span>
              <em>{{ chapter.word_count }} 字</em>
            </li>
          </ul>
          <p v-else class="muted">尚未识别章节</p>
        </div>

        <button class="generate" :disabled="loading || chapterCount < 3" @click="generateScript">
          {{ loading ? "生成中..." : "生成剧本" }}
        </button>
        <p v-if="errorMessage" class="error">{{ errorMessage }}</p>
      </section>

      <section class="panel result-panel">
        <div class="panel-title">
          <h2>工作区</h2>
          <span>{{ currentStep }}</span>
        </div>

        <div class="progress">
          <div v-for="step in steps" :key="step" :class="['step', { active: step === currentStep, done: doneSteps.includes(step) }]">
            {{ step }}
          </div>
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

        <div class="tabs">
          <button :class="{ selected: tab === 'characters' }" @click="tab = 'characters'">人物表</button>
          <button :class="{ selected: tab === 'scenes' }" @click="tab = 'scenes'">场景表</button>
          <button :class="{ selected: tab === 'notes' }" @click="tab = 'notes'">改编总结</button>
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
          <article v-for="item in script.scenes || []" :key="item.id">
            <strong>{{ item.id }}</strong>
            <span>{{ item.heading?.time_of_day }}</span>
            <p>{{ item.purpose }}</p>
            <p class="muted">{{ item.conflict }}</p>
          </article>
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
      </section>

      <section class="panel yaml-panel">
        <div class="panel-title">
          <h2>YAML 输出</h2>
          <span :class="{ pass: validation.valid, danger: validation.valid === false }">
            {{ validationLabel }}
          </span>
        </div>

        <textarea v-model="yamlText" class="yaml-editor" placeholder="生成后展示 YAML"></textarea>

        <div class="actions">
          <button :disabled="!yamlText" @click="copyYaml">复制</button>
          <button :disabled="!script.schema_version" @click="validateCurrent">校验</button>
        </div>

        <div class="validation">
          <h3>校验报告</h3>
          <p v-if="validation.valid" class="pass">YAML 结构通过第一版 Schema 校验</p>
          <ul v-else-if="validation.errors?.length">
            <li v-for="item in validation.errors" :key="item.path">
              <strong>{{ item.path }}</strong>
              <span>{{ item.message }}</span>
            </li>
          </ul>
          <p v-else class="muted">暂无校验结果</p>
        </div>
      </section>
    </main>
  </div>
</template>

<script setup>
import { computed, ref } from "vue";

const sampleText = `第一章 初入会议室
林夏推开会议室的门，所有人的目光都落在她身上。她刚加入项目组，却被要求解释一个失败方案。
周谨合上电脑，声音很低：“你知道这个问题拖了多久吗？”
林夏握紧资料：“我知道，但我找到新的证据。”

第二章 被质疑的方案
办公室里，林夏把数据重新投到屏幕上。周谨没有立刻反驳，其他同事开始交换眼神。
她指出旧方案忽略了用户留存的变化，也说明预算并不是最大阻碍。
周谨问：“如果你错了呢？”林夏回答：“那我承担后果。”

第三章 夜晚的转折
夜里的公司只剩几盏灯。林夏在会议室重新核对日志，发现真正的问题来自一次被遗漏的配置变更。
周谨走到门口，看见她还没离开。
林夏抬头说：“明天之前，我能把完整报告交给你。”`;

const title = ref("示例小说改编");
const novelText = ref("");
const chapters = ref([]);
const chapterCount = ref(0);
const loading = ref(false);
const errorMessage = ref("");
const script = ref({});
const yamlText = ref("");
const validation = ref({});
const tab = ref("characters");
const steps = ["章节解析", "信息抽取", "场景规划", "剧本生成", "Schema 校验"];
const currentStep = ref("待开始");
const doneSteps = ref([]);

const wordCount = computed(() => novelText.value.trim().length);
const validationLabel = computed(() => {
  if (validation.value.valid === true) return "校验通过";
  if (validation.value.valid === false) return "校验失败";
  return "待校验";
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

async function analyzeText() {
  errorMessage.value = "";
  const data = await postJson("/api/analyze", { text: novelText.value });
  chapters.value = data.chapters;
  chapterCount.value = data.chapter_count;
}

function loadSample() {
  novelText.value = sampleText;
  analyzeText();
}

async function handleFile(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  novelText.value = await file.text();
  await analyzeText();
}

async function generateScript() {
  loading.value = true;
  errorMessage.value = "";
  doneSteps.value = [];
  script.value = {};
  yamlText.value = "";
  validation.value = {};
  try {
    for (const step of steps) {
      currentStep.value = step;
      await new Promise((resolve) => setTimeout(resolve, 180));
      doneSteps.value.push(step);
    }
    const data = await postJson("/api/generate", { title: title.value, text: novelText.value });
    script.value = data.script;
    yamlText.value = data.yaml;
    validation.value = data.validation;
    chapters.value = data.script.chapters.map((item) => ({
      chapter_id: item.id,
      title: item.title,
      order: item.order,
      word_count: item.word_count,
    }));
    chapterCount.value = chapters.value.length;
    currentStep.value = "完成";
  } catch (error) {
    errorMessage.value = error.message;
    currentStep.value = "失败";
  } finally {
    loading.value = false;
  }
}

async function validateCurrent() {
  const data = await postJson("/api/validate", { script: script.value });
  validation.value = data;
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
</script>

