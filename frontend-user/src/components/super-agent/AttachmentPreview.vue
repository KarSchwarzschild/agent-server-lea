<template>
  <div v-if="attachments.length > 0" class="attachment-preview">
    <div
      v-for="att in attachments"
      :key="att.id"
      class="attachment-item"
      :class="{ 'is-error': att.status === 'error', 'is-uploading': att.status === 'uploading' }"
    >
      <!-- 图片缩略图 -->
      <div v-if="att.fileCategory === 'image' && att.thumbnailUrl" class="att-thumbnail">
        <img :src="att.thumbnailUrl" :alt="att.filename" />
      </div>
      <!-- 文档图标 -->
      <div v-else class="att-icon">
        <span class="file-type-badge">{{ att.fileExt?.toUpperCase() }}</span>
      </div>

      <div class="att-info">
        <span class="att-name" :title="att.filename">{{ truncateName(att.filename) }}</span>
        <div class="att-meta">
          <span class="att-size">{{ formatSize(att.size) }}</span>
          <span v-if="att.indexStatus === 'building'" class="index-badge building">
            深度阅读中...
          </span>
          <span v-else-if="att.indexStatus === 'ready'" class="index-badge ready">
            可精准定位
          </span>
        </div>
        <span v-if="att.errorMessage" class="att-error">{{ att.errorMessage }}</span>
      </div>

      <!-- 上传进度 -->
      <div v-if="att.status === 'uploading'" class="att-progress">
        <div class="progress-bar" :style="{ width: (att.uploadProgress || 0) + '%' }"></div>
      </div>

      <!-- 删除按钮 -->
      <button class="att-remove" @click="$emit('remove', att.id)" title="移除">×</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { AttachmentInfo } from '../../types/super-agent'

defineProps<{
  attachments: AttachmentInfo[]
}>()

defineEmits<{
  (e: 'remove', id: string): void
}>()

function truncateName(name: string): string {
  if (name.length <= 20) return name
  const ext = name.split('.').pop() || ''
  const base = name.slice(0, name.length - ext.length - 1)
  return base.slice(0, 14) + '...' + (ext ? '.' + ext : '')
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + 'B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + 'KB'
  return (bytes / 1024 / 1024).toFixed(1) + 'MB'
}
</script>

<style scoped>
.attachment-preview {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 0;
}

.attachment-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 28px 6px 8px;
  background: var(--n-color-hover, #f5f5f5);
  border-radius: 8px;
  border: 1px solid var(--n-border-color, #e0e0e0);
  max-width: 200px;
  min-width: 120px;
}

.attachment-item.is-error {
  border-color: #ff4d4f;
  background: #fff2f0;
}

.att-thumbnail {
  width: 36px;
  height: 36px;
  border-radius: 4px;
  overflow: hidden;
  flex-shrink: 0;
}

.att-thumbnail img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.att-icon {
  width: 36px;
  height: 36px;
  border-radius: 4px;
  background: var(--n-color, #e8e8e8);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.file-type-badge {
  font-size: 10px;
  font-weight: 600;
  color: var(--n-text-color-3, #999);
}

.att-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.att-name {
  font-size: 12px;
  color: var(--n-text-color, #333);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.att-meta {
  display: flex;
  align-items: center;
  gap: 4px;
}

.att-size {
  font-size: 11px;
  color: var(--n-text-color-3, #999);
}

.index-badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  white-space: nowrap;
  line-height: 1.4;
}

.index-badge.building {
  background: #FFF3E0;
  color: #E65100;
  border: 1px solid #FFB74D;
  animation: pulse-badge 1.5s ease-in-out infinite;
}

.index-badge.ready {
  background: #E8F5E9;
  color: #2E7D32;
  border: 1px solid #81C784;
}

@keyframes pulse-badge {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}

.att-error {
  font-size: 11px;
  color: #ff4d4f;
}

.att-progress {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: #e8e8e8;
  border-radius: 0 0 8px 8px;
  overflow: hidden;
}

.progress-bar {
  height: 100%;
  background: var(--n-color-target, #18a058);
  transition: width 0.2s;
}

.att-remove {
  position: absolute;
  top: 2px;
  right: 4px;
  width: 18px;
  height: 18px;
  border: none;
  background: transparent;
  color: var(--n-text-color-3, #999);
  font-size: 14px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  padding: 0;
  line-height: 1;
}

.att-remove:hover {
  background: rgba(0, 0, 0, 0.08);
  color: #ff4d4f;
}
</style>
