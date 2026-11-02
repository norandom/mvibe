#Requires -Version 7.0
<#
.SYNOPSIS
    Live smoke test for the local mvibe proxy (OpenAI, Anthropic and Ollama dialects + embeddings).
.DESCRIPTION
    Run while the proxy is up (default http://127.0.0.1:8765, started via
    ./start_proxy.ps1 or ./start_proxy.sh). No Authorization header is sent:
    upstream auth comes from the proxy's selected shared session (existing-vibe).
    Uses curl.exe and public test prompts only ("Reply with exactly OK.", "hello").
    Prints a PASS/FAIL table and exits non-zero if any check fails.
.EXAMPLE
    pwsh -NoProfile -File smoketests/smoke.ps1
.EXAMPLE
    pwsh -NoProfile -File smoketests/smoke.ps1 -BaseUrl http://127.0.0.1:8799 -ChatModel mistral-large-4
#>
[CmdletBinding()]
param(
    [string]$BaseUrl = 'http://127.0.0.1:8765',
    [string]$ChatModel = 'ministral-8b-latest',
    [string]$EmbedModel = 'mistral-embed',
    [string]$AnthropicVersion = '2023-06-01',
    [int]$MaxTime = 120
)

$ErrorActionPreference = 'Stop'
$BaseUrl = $BaseUrl.TrimEnd('/')
$results = [System.Collections.Generic.List[object]]::new()
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('mvibe-smoke-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null

function Add-Result([string]$name, [bool]$ok, [string]$detail) {
    $script:results.Add([pscustomobject]@{
        check  = $name
        result = if ($ok) { 'PASS' } else { 'FAIL' }
        detail = $detail
    })
}

function Write-Body([string]$name, [object]$obj) {
    $f = Join-Path $tmp $name
    [IO.File]::WriteAllText($f, ($obj | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    return $f
}

function Invoke-Curl([string]$method, [string]$path, [string]$bodyFile, [string[]]$headers, [string]$outFile, [switch]$NoStream) {
    $argList = [System.Collections.Generic.List[string]]::new()
    if ($NoStream) { $argList.Add('-N') }
    $argList.AddRange([string[]]@('-s', '--max-time', "$MaxTime", '-X', $method, "$BaseUrl$path", '-o', (Join-Path $tmp $outFile), '-w', '%{http_code} %{time_total}'))
    foreach ($h in ([string[]]@('Content-Type: application/json') + $headers)) { $argList.AddRange([string[]]@('-H', $h)) }
    if ($bodyFile) { $argList.AddRange([string[]]@('--data-binary', "@$bodyFile")) }
    $meta = (& curl.exe @argList) -join ''
    return @{ code = [int](($meta -split ' ')[0]); out = (Join-Path $tmp $outFile); meta = $meta }
}

function Read-Json([string]$file) {
    try { return (Get-Content $file -Raw | ConvertFrom-Json) } catch { return $null }
}

try {
    # 1. health
    $r = Invoke-Curl GET '/health' $null @() 'health.json'
    $ok = ($r.code -eq 200) -and ((Get-Content $r.out -Raw) -match '"status"\s*:\s*"ok"')
    Add-Result 'GET /health' $ok "HTTP $($r.code)"

    # 2. models, OpenAI shape
    $r = Invoke-Curl GET '/v1/models' $null @() 'models.json'
    $m = Read-Json $r.out
    $n = if ($m -and $m.data) { @($m.data).Count } else { 0 }
    Add-Result 'GET /v1/models (OpenAI shape)' (($r.code -eq 200) -and $n -gt 0) "HTTP $($r.code), $n models"

    # 3. chat, non-streaming
    $f = Write-Body 'chat.json' @{ model = $ChatModel; messages = @(@{ role = 'user'; content = 'Reply with exactly OK.' }); max_tokens = 64; stream = $false }
    $r = Invoke-Curl POST '/v1/chat/completions' $f @() 'chat-resp.json'
    $j = Read-Json $r.out
    $answer = if ($j -and $j.choices) { "$($j.choices[0].message.content)" } else { '' }
    Add-Result 'POST /v1/chat/completions' (($r.code -eq 200) -and [bool]$answer) "HTTP $($r.code), answer '$answer'"

    # 4. chat, streaming
    $f = Write-Body 'chat-stream.json' @{ model = $ChatModel; messages = @(@{ role = 'user'; content = 'Reply with exactly OK.' }); max_tokens = 64; stream = $true }
    $r = Invoke-Curl POST '/v1/chat/completions' $f @() 'chat-stream.txt' -NoStream
    $dataLines = @(Get-Content $r.out | Where-Object { $_ -match '^data:' })
    $assembled = @()
    foreach ($l in $dataLines) {
        if ($l -match '\[DONE\]') { continue }
        $d = $null; try { $d = ($l -replace '^data: ', '').Trim() | ConvertFrom-Json } catch {}
        if ($d -and $d.choices -and $d.choices[0].delta.content) { $assembled += "$($d.choices[0].delta.content)" }
    }
    $ok = ($r.code -eq 200) -and $dataLines.Count -gt 0 -and ($dataLines | Where-Object { $_ -match '\[DONE\]' }) -and $assembled.Count -gt 0
    Add-Result 'POST /v1/chat/completions (stream)' $ok "HTTP $($r.code), $($dataLines.Count) data frames, content '$($assembled -join '')'"

    # 5. embeddings
    $f = Write-Body 'emb.json' @{ model = $EmbedModel; input = @('hello') }
    $r = Invoke-Curl POST '/v1/embeddings' $f @() 'emb-resp.json'
    $j = Read-Json $r.out
    $dims = if ($j -and $j.data -and $j.data[0].embedding) { @($j.data[0].embedding).Count } else { 0 }
    Add-Result 'POST /v1/embeddings' (($r.code -eq 200) -and $dims -gt 0) "HTTP $($r.code), model $EmbedModel, $dims dims"

    # 6. Anthropic messages, non-streaming
    $f = Write-Body 'msg.json' @{ model = $ChatModel; max_tokens = 64; messages = @(@{ role = 'user'; content = 'Reply with exactly OK.' }) }
    $r = Invoke-Curl POST '/v1/messages' $f @("anthropic-version: $AnthropicVersion") 'msg-resp.json'
    $j = Read-Json $r.out
    $text = if ($j -and $j.content) { "$($j.content[0].text)" } else { '' }
    Add-Result 'POST /v1/messages (Anthropic)' (($r.code -eq 200) -and $j -and $j.type -eq 'message') "HTTP $($r.code), stop_reason $($j.stop_reason), content '$text'"

    # 7. Anthropic messages, SSE stream
    $f = Write-Body 'msg-stream.json' @{ model = $ChatModel; max_tokens = 64; stream = $true; messages = @(@{ role = 'user'; content = 'Reply with exactly OK.' }) }
    $r = Invoke-Curl POST '/v1/messages' $f @("anthropic-version: $AnthropicVersion") 'msg-stream.txt' -NoStream
    $events = @(Get-Content $r.out | Where-Object { $_ -match '^event:' } | ForEach-Object { ($_ -replace '^event: ', '').Trim() })
    $ok = ($r.code -eq 200) -and ($events -contains 'message_start') -and ($events -contains 'message_stop')
    Add-Result 'POST /v1/messages (Anthropic stream)' $ok "HTTP $($r.code), events: $($events -join ' -> ')"

    # 8. Anthropic models (paginated shape via header)
    $r = Invoke-Curl GET '/v1/models' $null @("anthropic-version: $AnthropicVersion") 'models-anthropic.json'
    $j = Read-Json $r.out
    $n = if ($j -and $j.data) { @($j.data).Count } else { 0 }
    Add-Result 'GET /v1/models (Anthropic dialect)' (($r.code -eq 200) -and $null -ne $j.has_more -and $n -gt 0) "HTTP $($r.code), $n models, has_more=$($j.has_more)"

    # 9. Ollama version + tags
    $r = Invoke-Curl GET '/api/version' $null @() 'ollama-version.json'
    $j = Read-Json $r.out
    Add-Result 'GET /api/version (Ollama)' (($r.code -eq 200) -and $j -and $j.version) "HTTP $($r.code), version $($j.version)"
    $r = Invoke-Curl GET '/api/tags' $null @() 'ollama-tags.json'
    $j = Read-Json $r.out
    $n = if ($j -and $j.models) { @($j.models).Count } else { 0 }
    Add-Result 'GET /api/tags (Ollama)' (($r.code -eq 200) -and $n -gt 0) "HTTP $($r.code), $n models"

    # 10. Ollama chat
    $f = Write-Body 'ollama-chat.json' @{ model = $ChatModel; messages = @(@{ role = 'user'; content = 'Reply with exactly OK.' }); stream = $false }
    $r = Invoke-Curl POST '/api/chat' $f @() 'ollama-chat-resp.json'
    $j = Read-Json $r.out
    $answer = if ($j -and $j.message) { "$($j.message.content)" } else { '' }
    Add-Result 'POST /api/chat (Ollama)' (($r.code -eq 200) -and $j -and $j.done -eq $true) "HTTP $($r.code), done=$($j.done), message '$answer'"

    # 11. unknown /api/* -> adapter-shaped 404
    $r = Invoke-Curl GET '/api/nope' $null @() 'ollama-404.json'
    $j = Read-Json $r.out
    Add-Result 'GET /api/nope (expect 404)' (($r.code -eq 404) -and $j -and $j.error) "HTTP $($r.code), $($j.error)"
}
finally {
    $results | Format-Table -AutoSize | Out-String -Width 200 | Write-Host
    $failed = @($results | Where-Object { $_.result -eq 'FAIL' }).Count
    Write-Host ("SUMMARY: {0}/{1} checks passed" -f ($results.Count - $failed), $results.Count)
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
if ($failed -gt 0) { exit 1 } else { exit 0 }
