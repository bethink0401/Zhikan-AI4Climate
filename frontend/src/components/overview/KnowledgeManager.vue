<template>
  <div class="knowledge-manager">
    <div class="knowledge-heading">
      <div class="heading-copy">
        <el-icon class="heading-icon" :size="20"><Reading /></el-icon>
        <div><h2>专家知识库</h2><p>管理可追溯的系统资料与本地用户资料</p></div>
      </div>
      <div class="heading-actions">
        <el-button :icon="Refresh" :loading="loading" @click="loadDocuments">刷新</el-button>
        <el-button type="primary" :icon="Plus" :disabled="Boolean(accessError)" @click="openUploadDialog">添加资料</el-button>
      </div>
    </div>

    <el-alert v-if="accessError" type="warning" :closable="false" show-icon>
      <template #title>知识库访问受限</template>
      <div>{{ accessError }}</div>
      <div class="access-note">知识库管理功能仅限 loopback 本地开发访问，未经认证时不得对公网开放。</div>
    </el-alert>

    <template v-else>
      <el-alert v-if="listError" type="error" :closable="true" show-icon @close="listError = null">{{ listError }}</el-alert>
      <el-tabs v-model="activeScope" class="knowledge-tabs">
        <el-tab-pane name="system">
          <template #label><span class="tab-label">系统知识库 <el-tag size="small" type="info">{{ scopeCount.system }}</el-tag></span></template>
        </el-tab-pane>
        <el-tab-pane name="user">
          <template #label><span class="tab-label">用户资料库 <el-tag size="small" type="info">{{ scopeCount.user }}</el-tag></span></template>
        </el-tab-pane>
      </el-tabs>

      <div class="table-shell" v-loading="loading">
        <el-table :data="visibleDocuments" stripe height="100%" size="small" empty-text="当前知识空间暂无资料">
          <el-table-column prop="title" label="标题" min-width="190" show-overflow-tooltip>
            <template #default="{ row }"><div class="document-title"><el-icon :size="15"><Files /></el-icon><span>{{ row.title }}</span></div></template>
          </el-table-column>
          <el-table-column label="来源" min-width="135" show-overflow-tooltip>
            <template #default="{ row }">{{ row.source || row.original_filename || '未注明' }}</template>
          </el-table-column>
          <el-table-column label="分类" width="116"><template #default="{ row }">{{ categoryLabel(row.category) }}</template></el-table-column>
          <el-table-column label="知识空间" width="104">
            <template #default="{ row }"><el-tag :type="row.scope === 'system' ? 'success' : 'info'" size="small" effect="plain">{{ row.scope === 'system' ? '系统' : '用户' }}</el-tag></template>
          </el-table-column>
          <el-table-column label="权威等级" width="108">
            <template #default="{ row }"><el-tag :type="authorityTagType(row.authority_level)" size="small">{{ authorityLabel(row.authority_level) }}</el-tag></template>
          </el-table-column>
          <el-table-column label="年份" width="74"><template #default="{ row }">{{ row.year ?? '—' }}</template></el-table-column>
          <el-table-column label="状态" width="92">
            <template #default="{ row }"><el-tag :type="statusTagType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag></template>
          </el-table-column>
          <el-table-column prop="chunk_count" label="块数" width="66" align="center" />
          <el-table-column label="启用" width="70" align="center">
            <template #default="{ row }"><el-switch v-model="row.enabled" :disabled="row.scope === 'system'" @change="toggleEnabled(row)" /></template>
          </el-table-column>
          <el-table-column label="操作" width="84" fixed="right" align="center">
            <template #default="{ row }">
              <el-button v-if="row.scope === 'user'" size="small" type="danger" text @click="confirmDelete(row)">删除</el-button>
              <span v-else class="system-note">内置</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </template>

    <el-dialog v-model="uploadDialogVisible" title="添加知识资料" width="560px" :close-on-click-modal="!uploading" :close-on-press-escape="!uploading" :show-close="!uploading" @closed="resetUploadForm">
      <el-form :model="uploadForm" label-position="top" size="default">
        <el-form-item label="文件" required>
          <el-upload ref="uploadRef" :auto-upload="false" :limit="1" :on-change="handleFileSelect" :on-exceed="handleExceed" :before-remove="handleRemove" accept=".pdf,.docx,.txt,.md" drag>
            <el-icon class="upload-icon" :size="26"><Files /></el-icon>
            <div class="el-upload__text">拖拽文件到此处，或<em>点击选择</em></div>
            <template #tip><div class="el-upload__tip">支持 PDF、DOCX、TXT、Markdown，最大 {{ maxFileSizeMB }} MB</div></template>
          </el-upload>
        </el-form-item>
        <el-form-item label="标题" required><el-input v-model="uploadForm.title" placeholder="文档标题" maxlength="200" show-word-limit /></el-form-item>
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="作者"><el-input v-model="uploadForm.author" placeholder="作者姓名" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="年份"><el-input-number v-model="uploadForm.year" :min="1900" :max="2100" controls-position="right" style="width: 100%" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="来源"><el-input v-model="uploadForm.source" placeholder="期刊、会议或机构名称" /></el-form-item>
        <el-form-item label="分类">
          <el-select v-model="uploadForm.category" style="width: 100%"><el-option v-for="category in categories" :key="category.value" :label="category.label" :value="category.value" /></el-select>
        </el-form-item>
        <el-form-item label="主题标签">
          <el-select v-model="uploadForm.topics" multiple allow-create filterable placeholder="输入或选择主题标签" style="width: 100%"><el-option v-for="topic in defaultTopics" :key="topic" :label="topic" :value="topic" /></el-select>
        </el-form-item>
        <div class="upload-note"><el-icon :size="15"><InfoFilled /></el-icon><span>用户上传资料将标记为“用户上传”，其元数据由上传者提供，未经外部真实性核验。</span></div>
        <el-alert v-if="uploadError" type="error" :closable="true" show-icon @close="uploadError = null"><template #title>上传失败</template>{{ uploadError }}</el-alert>
      </el-form>
      <template #footer>
        <el-button :disabled="uploading" @click="uploadDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" :disabled="!canUpload" @click="submitUpload">{{ uploading ? '上传并索引中…' : '上传资料' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile, UploadInstance } from 'element-plus'
import { Files, InfoFilled, Plus, Reading, Refresh } from '@element-plus/icons-vue'
import { deleteDocument, KnowledgeHttpError, listDocuments, updateDocument, uploadDocument } from '@/api/knowledge'
import type { AuthorityLevel, DocumentStatus, DocumentUploadRequest, KnowledgeDocument, KnowledgeScope } from '@/types/knowledge'

const maxFileSizeMB = 20
const categories = [
  { label: '空气质量标准', value: 'air_quality_standard' }, { label: '健康指导', value: 'health_guidance' },
  { label: '污染物机理', value: 'pollutant_mechanism' }, { label: '气象扩散', value: 'meteorology_dispersion' },
  { label: '交通污染', value: 'traffic_pollution' }, { label: '治理政策', value: 'governance' },
  { label: '环境政策', value: 'environmental_policy' }, { label: '项目内部', value: 'project_internal' },
  { label: '研究文献', value: 'research' }, { label: '其他', value: 'other' }
]
const defaultTopics = ['气象扩散', '交通污染', '空气质量', '污染物机理']
const activeScope = ref<KnowledgeScope>('system')
const documents = ref<KnowledgeDocument[]>([])
const loading = ref(false)
const listError = ref<string | null>(null)
const accessError = ref<string | null>(null)
const uploadDialogVisible = ref(false)
const uploadRef = ref<UploadInstance>()
const uploading = ref(false)
const uploadError = ref<string | null>(null)

function emptyUploadForm(): DocumentUploadRequest {
  return { file: null as unknown as File, title: '', source: undefined, author: undefined, year: undefined, category: 'other', topics: [] }
}
const uploadForm = ref<DocumentUploadRequest>(emptyUploadForm())
const visibleDocuments = computed(() => documents.value.filter((document) => document.scope === activeScope.value))
const scopeCount = computed(() => ({ system: documents.value.filter((document) => document.scope === 'system').length, user: documents.value.filter((document) => document.scope === 'user').length }))
const canUpload = computed(() => Boolean(uploadForm.value.file && uploadForm.value.title.trim() && !uploading.value))

function openUploadDialog(): void { uploadError.value = null; uploadDialogVisible.value = true }
function resetUploadForm(): void {
  if (uploading.value) return
  uploadForm.value = emptyUploadForm(); uploadError.value = null; uploadRef.value?.clearFiles()
}
function handleFileSelect(file: UploadFile): void {
  if (!file.raw) return
  if (file.raw.size / 1024 / 1024 > maxFileSizeMB) {
    uploadError.value = `文件过大，不能超过 ${maxFileSizeMB} MB`; uploadRef.value?.clearFiles(); uploadForm.value.file = null as unknown as File; return
  }
  uploadError.value = null; uploadForm.value.file = file.raw
  if (!uploadForm.value.title) uploadForm.value.title = file.name.replace(/\.[^/.]+$/, '')
}
function handleExceed(): void { ElMessage.warning('一次只能上传一个文件') }
function handleRemove(): boolean { uploadForm.value.file = null as unknown as File; return true }

async function submitUpload(): Promise<void> {
  if (!canUpload.value) return
  uploading.value = true; uploadError.value = null
  try {
    await uploadDocument(uploadForm.value)
    ElMessage.success('资料上传成功，知识索引正在更新')
    uploadDialogVisible.value = false; activeScope.value = 'user'; await loadDocuments()
  } catch (error) {
    if (error instanceof KnowledgeHttpError) {
      if (error.status === 403) { accessError.value = error.message; uploadDialogVisible.value = false }
      else if (error.status === 409) uploadError.value = error.existingDocumentId ? `资料已存在（文档 ID：${error.existingDocumentId.slice(0, 8)}…）` : '相同资料已经存在，请勿重复上传'
      else if (error.status === 413) uploadError.value = `文件过大，不能超过 ${maxFileSizeMB} MB`
      else if (error.status === 400) uploadError.value = `资料格式或元数据不合法：${error.message}`
      else uploadError.value = error.message
    } else uploadError.value = '上传失败，请稍后重试'
  } finally { uploading.value = false }
}

async function loadDocuments(): Promise<void> {
  loading.value = true; listError.value = null; accessError.value = null
  try { documents.value = (await listDocuments()).documents }
  catch (error) {
    if (error instanceof KnowledgeHttpError && error.status === 403) accessError.value = error.message
    else if (error instanceof KnowledgeHttpError) listError.value = error.message
    else listError.value = '加载文档列表失败'
  } finally { loading.value = false }
}
async function toggleEnabled(document: KnowledgeDocument): Promise<void> {
  try { await updateDocument(document.document_id, { enabled: document.enabled }); ElMessage.success(document.enabled ? '资料已启用' : '资料已停用') }
  catch (error) { document.enabled = !document.enabled; ElMessage.error(error instanceof KnowledgeHttpError ? error.message : '更新失败') }
}
async function confirmDelete(document: KnowledgeDocument): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除“${document.title}”吗？删除后其索引内容也将移除。`, '确认删除', { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' })
    await deleteDocument(document.document_id); ElMessage.success('资料已删除'); await loadDocuments()
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(error instanceof KnowledgeHttpError ? error.message : '删除失败')
  }
}
function categoryLabel(category: string): string { return categories.find((item) => item.value === category)?.label ?? category }
function authorityLabel(level: AuthorityLevel): string { return ({ official: '官方', peer_reviewed: '同行评审', internal: '内部', user_uploaded: '用户上传', unknown: '未知' }[level]) }
function authorityTagType(level: AuthorityLevel): 'success' | 'warning' | 'info' { return level === 'official' || level === 'peer_reviewed' ? 'success' : level === 'internal' ? 'warning' : 'info' }
function statusLabel(status: DocumentStatus): string { return ({ uploaded: '已上传', parsing: '解析中', indexing: '索引中', indexed: '已索引', failed: '失败', requires_ocr: '需 OCR' }[status]) }
function statusTagType(status: DocumentStatus): 'success' | 'warning' | 'danger' | 'info' { return status === 'indexed' ? 'success' : status === 'failed' || status === 'requires_ocr' ? 'danger' : status === 'parsing' || status === 'indexing' ? 'warning' : 'info' }
onMounted(() => { void loadDocuments() })
</script>

<style scoped>
.knowledge-manager { height: 100%; min-height: 520px; display: flex; flex-direction: column; gap: 14px; }
.knowledge-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.heading-copy { display: flex; align-items: center; gap: 10px; }
.heading-icon { color: #7c3aed; }
.heading-copy h2 { margin: 0; color: var(--text-primary); font-size: 18px; }
.heading-copy p { margin: 3px 0 0; color: var(--text-tertiary); font-size: 12px; }
.heading-actions { display: flex; gap: 8px; }
.access-note { margin-top: 6px; font-size: 12px; opacity: .8; }
.knowledge-tabs { flex: 0 0 auto; }
.knowledge-tabs :deep(.el-tabs__header) { margin-bottom: 0; }
.tab-label { display: inline-flex; align-items: center; gap: 6px; }
.table-shell { flex: 1 1 auto; min-height: 360px; overflow: hidden; border: 1px solid var(--border-color); border-radius: 8px; }
.document-title { display: flex; align-items: center; gap: 6px; color: var(--text-primary); font-weight: 500; }
.document-title .el-icon { color: #7c3aed; flex: 0 0 auto; }
.system-note { color: var(--text-tertiary); font-size: 11px; }
.upload-icon { margin-bottom: 6px; color: #7c3aed; }
.upload-note { display: flex; align-items: flex-start; gap: 7px; margin-bottom: 14px; padding: 9px 10px; border-radius: 6px; color: var(--text-secondary); background: rgba(124, 58, 237, .06); font-size: 12px; line-height: 1.5; }
.upload-note .el-icon { margin-top: 2px; color: #7c3aed; flex: 0 0 auto; }
</style>
