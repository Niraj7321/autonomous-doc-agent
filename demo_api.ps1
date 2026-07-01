<#
.SYNOPSIS
    End-to-end API demo for the Autonomous Document Agent.

.DESCRIPTION
    Starts the FastAPI server (unless one is already running on the port),
    waits until it reports healthy, sends the two required test requests
    (one standard, one complex/ambiguous) to POST /agent, prints the agent's
    self-generated task list, assumptions, self-check and execution trace for
    each, downloads the generated .docx files into .\generated, and finally
    shuts the server back down.

.PARAMETER Port
    Port to run / reach the API on. Default 8000.

.PARAMETER Open
    If set, opens each generated .docx in the default viewer when done.

.PARAMETER KeepAlive
    If set, leaves the API server running after the demo (instead of shutting it
    down) so you can browse it at http://127.0.0.1:<Port>/docs.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\demo_api.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\demo_api.ps1 -Port 8010 -Open

.EXAMPLE
    # Run the demo AND keep the server up so you can open the URL in a browser:
    powershell -ExecutionPolicy Bypass -File .\demo_api.ps1 -Port 8010 -KeepAlive
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$Open,
    [switch]$KeepAlive
)

$ErrorActionPreference = "Stop"

# The API returns UTF-8 (smart quotes in the agent's assumptions, em-dashes, ...).
# Force the console to UTF-8 so those render correctly on Windows terminals that
# default to a legacy code page (cp1252) instead of showing mojibake.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$BaseUrl = "http://127.0.0.1:$Port"
$OutDir  = Join-Path $ProjectRoot "generated"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# The two required scenarios (ASCII-only so the JSON body is encoding-safe).
$Cases = @(
    @{
        Label   = "TEST CASE 1 - STANDARD BUSINESS REQUEST"
        Request = "Write a business proposal for an AI-powered customer support chatbot for a mid-sized e-commerce company."
    },
    @{
        Label   = "TEST CASE 2 - COMPLEX / AMBIGUOUS REQUEST"
        Request = "We have a leadership offsite coming up and need a document about speeding up our product delivery without sacrificing quality or burning out the team. Budget is tight, timeline is unclear, and leadership hasn't agreed on the format - decide the best structure yourself and make reasonable assumptions."
    }
)

function Write-Header([string]$Text) {
    Write-Host ""
    Write-Host ("=" * 78) -ForegroundColor DarkCyan
    Write-Host $Text        -ForegroundColor Cyan
    Write-Host ("=" * 78) -ForegroundColor DarkCyan
}

function Test-Health([string]$Url) {
    try { return (Invoke-RestMethod -Uri "$Url/health" -TimeoutSec 2).status -eq "ok" }
    catch { return $false }
}

function Invoke-Agent([string]$Url, [string]$Request) {
    # Windows PowerShell 5.1's Invoke-RestMethod decodes a charset-less JSON body
    # as Latin-1, corrupting UTF-8 (the smart quotes in the agent's assumptions).
    # Fetch the raw bytes and decode them as UTF-8 ourselves so it is correct on
    # both PowerShell 5.1 and 7+.
    # -UseBasicParsing is required on Windows PowerShell 5.1: without it, a JSON
    # response (no -OutFile) is run through the legacy IE DOM parser, which blocks
    # in a non-interactive session. It also keeps RawContentStream available.
    $body = @{ request = $Request } | ConvertTo-Json
    $wr   = Invoke-WebRequest -Uri "$Url/agent" -Method Post -ContentType "application/json" `
                              -Body $body -UseBasicParsing
    return [System.Text.Encoding]::UTF8.GetString($wr.RawContentStream.ToArray()) | ConvertFrom-Json
}

$server         = $null
$startedByUs    = $false
$serverOutLog   = Join-Path $ProjectRoot "server.demo.out.log"
$serverErrLog   = Join-Path $ProjectRoot "server.demo.err.log"

try {
    # Reuse an already-running server if one answers; otherwise start our own.
    if (Test-Health $BaseUrl) {
        Write-Header "Reusing API server already running on $BaseUrl"
    }
    else {
        Write-Header "Starting API server on $BaseUrl"
        $server = Start-Process -FilePath "python" `
            -ArgumentList @("-m", "uvicorn", "app.main:app", "--port", "$Port", "--log-level", "warning") `
            -PassThru -NoNewWindow `
            -RedirectStandardOutput $serverOutLog -RedirectStandardError $serverErrLog
        $startedByUs = $true

        $healthy = $false
        foreach ($i in 1..40) {          # up to ~20s for first-time startup
            Start-Sleep -Milliseconds 500
            if (Test-Health $BaseUrl) { $healthy = $true; break }
            if ($server.HasExited) { throw "Server process exited early. See $serverErrLog" }
        }
        if (-not $healthy) { throw "Server did not become healthy in time. See $serverErrLog" }
    }

    $health = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 5
    Write-Host ("Server healthy. Active LLM provider(s): {0}" -f ($health.llm_providers -join ", ")) -ForegroundColor Green
    if (-not $health.llm_active) {
        Write-Host "  (running on the offline heuristic engine - set GROQ_API_KEY in .env for LLM-tailored output)" -ForegroundColor DarkGray
    }

    foreach ($case in $Cases) {
        Write-Header $case.Label
        Write-Host "REQUEST:" -ForegroundColor Yellow
        Write-Host ("  " + $case.Request)

        $resp = Invoke-Agent $BaseUrl $case.Request

        Write-Host ""
        Write-Host ("DOCUMENT TYPE : {0}" -f $resp.document_type)
        Write-Host ("TITLE         : {0}" -f $resp.title)
        Write-Host ("LLM PROVIDER  : {0}" -f $resp.llm_provider)
        Write-Host ("ELAPSED       : {0}s" -f $resp.elapsed_seconds)

        Write-Host ""
        Write-Host "AGENT-GENERATED TASK LIST:" -ForegroundColor Yellow
        foreach ($t in $resp.plan.tasks) {
            $mark = switch ($t.status) { "done" { "[x]" } "in_progress" { "[~]" } default { "[ ]" } }
            Write-Host ("  {0} {1,2}. {2}" -f $mark, $t.id, $t.title)
        }

        if ($resp.plan.assumptions) {
            Write-Host ""
            Write-Host "ASSUMPTIONS MADE:" -ForegroundColor Yellow
            foreach ($a in $resp.plan.assumptions) { Write-Host ("  - " + $a) }
        }

        Write-Host ""
        Write-Host "SELF-CHECK (reflection):" -ForegroundColor Yellow
        Write-Host ("  quality_score = {0}/100  passed = {1}  revised = {2}" -f `
            $resp.reflection.quality_score, $resp.reflection.passed, $resp.reflection.revised)
        foreach ($issue in $resp.reflection.issues) { Write-Host ("  ! " + $issue) }

        Write-Host ""
        Write-Host "EXECUTION TRACE:" -ForegroundColor Yellow
        foreach ($s in $resp.trace) {
            $prov = if ($s.provider) { " via $($s.provider)" } else { "" }
            Write-Host ("  - {0,-10} {1}{2}: {3}" -f $s.step, $s.status, $prov, $s.detail)
        }

        # Download the generated .docx via the download_url the API returned.
        $outFile = Join-Path $OutDir ("demo_" + $resp.document.id + "_" + $resp.document.filename)
        Invoke-WebRequest -Uri $resp.document.download_url -OutFile $outFile -UseBasicParsing | Out-Null
        Write-Host ""
        Write-Host ("SAVED DOCUMENT -> {0} ({1:N0} bytes)" -f $outFile, $resp.document.size_bytes) -ForegroundColor Green
        if ($Open) { Invoke-Item $outFile }
    }

    Write-Header "Done. Both documents were generated through the live API."

    if ($KeepAlive) {
        Write-Host "Server is LEFT RUNNING so you can browse it:" -ForegroundColor Green
        Write-Host ("  Swagger UI : {0}/docs" -f $BaseUrl)
        Write-Host ("  Health     : {0}/health" -f $BaseUrl)
        if ($startedByUs -and $server) {
            Write-Host ("  Stop it with: Stop-Process -Id {0}" -f $server.Id) -ForegroundColor DarkGray
        }
    }
    else {
        Write-Host "Tip: re-run with -KeepAlive to leave the server up and browse $BaseUrl/docs" -ForegroundColor DarkGray
    }
}
finally {
    # Only tear down a server WE started, and only when not asked to keep it alive.
    if ($startedByUs -and -not $KeepAlive -and $server -and -not $server.HasExited) {
        Write-Host ""
        Write-Host ("Stopping API server (PID {0})..." -f $server.Id) -ForegroundColor DarkGray
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
}
