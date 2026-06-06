import type { LogArtifactResponse } from '@/api/schemas/workspace'

export function logArtifactPreviewContent(artifact: LogArtifactResponse) {
  if (artifact.parsed_json !== undefined && artifact.parsed_json !== null) {
    return JSON.stringify(artifact.parsed_json, null, 2)
  }
  if (artifact.text) {
    return artifact.text
  }
  if (artifact.base64) {
    return `${artifact.file_name}\n${formatArtifactBytes(artifact.size_bytes)}\n${artifact.sha256}`
  }
  return ''
}

export function downloadLogArtifact(artifact: LogArtifactResponse) {
  const blob = new Blob([artifactBlobPart(artifact)], {
    type: artifact.content_type,
  })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = artifact.file_name || 'artifact'
  link.click()
  URL.revokeObjectURL(url)
}

export function formatArtifactBytes(value: number) {
  if (value < 1024) {
    return `${value} B`
  }
  const units = ['KB', 'MB', 'GB']
  let size = value / 1024
  let unitIndex = 0
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex += 1
  }
  return `${size.toFixed(size >= 10 ? 1 : 2)} ${units[unitIndex]}`
}

export function isSupportedLogArtifactKey(objectKey: string) {
  return (
    objectKey.startsWith('system/logs/') &&
    (objectKey.includes('/diagnostic_bundles/') ||
      objectKey.includes('/log_archives/')) &&
    ['.json', '.jsonl', '.log', '.txt', '.zip'].some((suffix) =>
      objectKey.endsWith(suffix),
    )
  )
}

function artifactBlobPart(artifact: LogArtifactResponse): BlobPart {
  if (artifact.base64) {
    return base64ToBytes(artifact.base64)
  }
  if (artifact.text !== undefined && artifact.text !== null) {
    return artifact.text
  }
  if (artifact.parsed_json !== undefined) {
    return JSON.stringify(artifact.parsed_json, null, 2)
  }
  return ''
}

function base64ToBytes(value: string) {
  const binary = window.atob(value)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index)
  }
  return bytes
}
