$src = "E:\0文档\2资料\Agent\agent-flow-study\frontend\render.js"
$dir = "E:\0文档\2资料\Agent\agent-flow-study\frontend\js"
New-Item -ItemType Directory -Force -Path $dir | Out-Null

$lines = Get-Content $src -Encoding UTF8

$lines[0..73]   | Set-Content "$dir\state.js" -Encoding UTF8
$lines[74..302]  | Set-Content "$dir\conv-core.js" -Encoding UTF8
$lines[303..774] | Set-Content "$dir\sidebar.js" -Encoding UTF8
$lines[775..861] | Set-Content "$dir\ws-send.js" -Encoding UTF8
$lines[862..1129] | Set-Content "$dir\samseg.js" -Encoding UTF8
$lines[1130..1214] | Set-Content "$dir\thinking.js" -Encoding UTF8
$lines[1215..2097] | Set-Content "$dir\wms-render.js" -Encoding UTF8
$lines[2098..2490] | Set-Content "$dir\msg-ui.js" -Encoding UTF8

Write-Host "All 8 files created"