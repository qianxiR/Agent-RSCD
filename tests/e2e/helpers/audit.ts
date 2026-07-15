import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

export type AuditRecord = {
  name: string;
  status: string;
  steps: string[];
  evidence: unknown;
};

export async function writeAuditRecord(record: AuditRecord) {
  // 入参: record 为单个测试场景的状态、步骤和证据对象，要求 name 可作为文件名的一部分。
  // 方法: 将可审计执行轨迹写入 output/playwright/audit，便于复盘 AI 任务执行链路。
  // 出参: 返回写入后的绝对文件路径；目录不存在时自动创建。
  const dir = path.resolve('output', 'playwright', 'audit');
  await mkdir(dir, { recursive: true });
  const safeName = record.name.replace(/[^\w.-]+/g, '_');
  const filePath = path.join(dir, `${safeName}.json`);
  await writeFile(filePath, JSON.stringify({ ...record, time: new Date().toISOString() }, null, 2), 'utf-8');
  return filePath;
}
