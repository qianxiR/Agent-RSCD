// ==================== Markdown 渲染 + 富表格增强 ====================
// ★ 对应原生 wms-render.js 的 md（markdownit 实例）+ enhanceMarkdownTables
//   配置与原生一致：html:false / breaks:true / linkify:true / typographer:true
//   富表格：把 markdown table 包进 .rich-table-card（标题 + 行数 + 复制按钮）
//   注意：复制按钮不绑 DOM 事件（v-html 序列化会丢失），由组件层 click 委托处理
import MarkdownIt from 'markdown-it'

const md = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  typographer: true,
})

// 入参: html 字符串（md.render 产物）
// 方法: DOMParser 解析，遍历 table 找前置标题，提取 headers/rows，
//   替换为 .rich-table-card 结构（与原生 buildRichTable 一致）
// 出参: 增强后的 HTML 字符串
function enhanceTables(html) {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  const tables = doc.querySelectorAll('table:not(.rich-table)')
  tables.forEach((table) => {
    let title = ''
    let prev = table.previousElementSibling
    while (prev) {
      if (/^H[1-6]$/.test(prev.tagName)) {
        title = prev.textContent.trim()
        prev.remove()
        break
      }
      if (prev.tagName === 'P' && prev.textContent.trim()) break
      if (prev.classList && prev.classList.contains('rich-table-card')) break
      prev = prev.previousElementSibling
    }

    const headers = []
    table.querySelectorAll('thead th').forEach((th) => headers.push(th.textContent.trim()))
    const rows = []
    table.querySelectorAll('tbody tr').forEach((tr) => {
      const row = []
      tr.querySelectorAll('td').forEach((td) => row.push(td.textContent.trim()))
      rows.push(row)
    })
    if (headers.length === 0 && rows.length > 0) {
      rows.shift().forEach((c) => headers.push(c))
    }

    const card = buildRichTable(title, headers, rows)
    table.replaceWith(card)
  })
  return doc.body.innerHTML
}

// 入参: title 表名, headers 表头数组, rows 数据行二维数组
// 方法: 构建 .rich-table-card DOM（header + table + footer + 复制按钮占位）
// 出参: HTMLElement（复制按钮事件由组件委托绑定）
function buildRichTable(title, headers, rows) {
  const card = document.createElement('div')
  card.className = 'rich-table-card'

  const head = document.createElement('div')
  head.className = 'rich-table-header'
  const titleEl = document.createElement('span')
  titleEl.textContent = title || '数据表'
  const countEl = document.createElement('span')
  countEl.className = 'row-count'
  countEl.textContent = rows.length + ' 行'
  // 复制按钮（图标，移至 header 右上角；事件由组件层 click 委托）
  const copyBtn = document.createElement('button')
  copyBtn.className = 'rich-table-copy-btn'
  copyBtn.type = 'button'
  copyBtn.title = '复制为 TSV'
  copyBtn.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>'
  head.append(titleEl, countEl, copyBtn)
  card.appendChild(head)

  const tbl = document.createElement('table')
  tbl.className = 'rich-table'
  const thead = document.createElement('thead')
  const headTr = document.createElement('tr')
  headers.forEach((h) => {
    const th = document.createElement('th')
    th.textContent = h
    headTr.appendChild(th)
  })
  thead.appendChild(headTr)
  tbl.appendChild(thead)

  const tbody = document.createElement('tbody')
  rows.forEach((row) => {
    const tr = document.createElement('tr')
    row.forEach((cell) => {
      const td = document.createElement('td')
      const num = parseCell(cell)
      if (num === null) {
        const span = document.createElement('span')
        span.className = 'cell-null'
        span.textContent = cell === '' || cell === 'null' ? '—' : cell
        td.appendChild(span)
      } else {
        td.className = 'cell-number'
        td.textContent = String(num)
      }
      tr.appendChild(td)
    })
    tbody.appendChild(tr)
  })
  tbl.appendChild(tbody)
  card.appendChild(tbl)

  const foot = document.createElement('div')
  foot.className = 'rich-table-footer'
  const meta = document.createElement('span')
  meta.textContent = headers.length + ' 列'
  foot.append(meta)
  card.appendChild(foot)

  return card
}

// 入参: cell 单元格文本
// 方法: 判定数值（含千分位/百分比/科学计数）
// 出参: 解析后的数字，或 null（非数值）
function parseCell(cell) {
  if (cell === '' || cell === 'null' || cell === 'None') return null
  const cleaned = cell.replace(/[,%\s]/g, '')
  if (cleaned === '') return null
  const num = Number(cleaned)
  return Number.isFinite(num) ? num : null
}

// 入参: text 原始 markdown 文本
// 方法: md.render + 富表格增强
// 出参: 可直接 v-html 的 HTML 字符串
export function renderMarkdown(text) {
  return enhanceTables(md.render(text || ''))
}