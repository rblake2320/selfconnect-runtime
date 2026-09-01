# verify_run.ps1 - one-command owner verification of an SCR team run.
#
#   .\verify_run.ps1 -Home <run-home-path>
#
# Generates a fresh 32-byte key from the OS CSPRNG at runtime (never seen by
# any AI), exports the sealed team bundle to the Desktop, verifies it, and
# prints the run's FACTS from the chain only: package provenance, delegation
# tree, every fs_read/fs_list path, every denial and tool error, and
# AUDITOR_RAN. Exits non-zero if verification fails or the auditor edge is
# missing. Uses only the frozen scr.exe - no Python required.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [Alias('Home')]
    [string]$RunHome
)
$ErrorActionPreference = 'Stop'

# ---- locate pieces ---------------------------------------------------------
$RunHome = (Resolve-Path $RunHome).Path
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$scr = Join-Path (Split-Path -Parent $scriptDir) 'installers\windows\dist\scr.exe'
if (-not (Test-Path $scr)) { Write-Error "scr.exe not found at $scr"; exit 10 }
if (-not (Test-Path (Join-Path $RunHome 'scr.db'))) {
    Write-Error "no scr.db in $RunHome - is this a run home?"; exit 10
}
$desktop = [Environment]::GetFolderPath('Desktop')

# ---- 1. fresh key from the OS CSPRNG (generated HERE, saved only for you) --
$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$key = -join ($bytes | ForEach-Object { $_.ToString('x2') })

# ---- 2. find the team automatically from session list ----------------------
$listing = & $scr --home $RunHome session list 2>&1
$leadLine = $listing | Where-Object { $_ -match 'depth=0' -and $_ -match 'team=([0-9a-f]{32})' } |
    Select-Object -Last 1
if (-not $leadLine) { Write-Error "no team (depth=0) session found in $RunHome"; exit 11 }
$null = $leadLine -match 'team=([0-9a-f]{32})'
$teamId = $Matches[1]
Write-Host "run home : $RunHome"
Write-Host "team id  : $teamId"

# ---- 3. export sealed team bundle with YOUR key to the Desktop -------------
$name = (Split-Path -Leaf $RunHome) + '-' + $teamId.Substring(0, 8)
$bundle = Join-Path $desktop "$name.scevidence"
$keyFile = Join-Path $desktop "$name.key.txt"
& $scr --home $RunHome session export $teamId $bundle --key $key
if ($LASTEXITCODE -ne 0) { Write-Error 'export failed'; exit 12 }
Set-Content -Path $keyFile -Value $key -NoNewline
Write-Host "bundle   : $bundle"
Write-Host "key file : $keyFile  (keep private - anyone with it can re-seal)"

# ---- 4. verify with YOUR key ----------------------------------------------
Write-Host ''
$verify = & $scr --home $RunHome ledger verify $bundle --key $key 2>&1
$verify | ForEach-Object { Write-Host $_ }
$verified = ($verify | Select-String -SimpleMatch 'RESULT: VERIFIED' -Quiet)
if (-not $verified) { Write-Host ''; Write-Host 'FAILED: bundle did not verify'; exit 2 }

# ---- 5. facts from the CHAIN only ------------------------------------------
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($bundle)
try {
    $entry = $zip.GetEntry('bundle.json')
    $reader = New-Object System.IO.StreamReader($entry.Open())
    $data = $reader.ReadToEnd() | ConvertFrom-Json
    $reader.Close()
} finally { $zip.Dispose() }

Write-Host ''
Write-Host '================ FACTS (from the hash chain, not prose) ================'
$pkg = $data.package
if ($pkg -and $pkg.package) {
    Write-Host ("package        : {0} {1}" -f $pkg.package, $pkg.version)
    Write-Host ("  key_id       : {0}" -f $pkg.key_id)
    Write-Host ("  content sha  : {0}" -f $pkg.content_sha256)
} else {
    Write-Host 'package        : (none recorded)'
}

# agent lookup by session for tree edges
$agentOf = @{}
foreach ($m in $data.delegation_tree) { $agentOf[$m.session_id] = $m.agent }
Write-Host 'delegation tree:'
foreach ($m in $data.delegation_tree) {
    if ($m.parent_session) {
        $p = $agentOf[$m.parent_session]
        Write-Host ("  {0} -> {1}   (session {2}, depth {3})" -f $p, $m.agent,
            $m.session_id.Substring(0, 8), $m.depth)
    } else {
        Write-Host ("  {0} [orchestrator]   (session {1}, depth {2})" -f $m.agent,
            $m.session_id.Substring(0, 8), $m.depth)
    }
}

$reads = New-Object System.Collections.Generic.List[string]
$denied = New-Object System.Collections.Generic.List[string]
$errors = New-Object System.Collections.Generic.List[string]
$auditorEdge = $false
foreach ($s in $data.sessions) {
    foreach ($row in $s.events) {
        $e = $row.event | ConvertFrom-Json
        switch ($e.type) {
            'tool_exec' {
                if (($e.tool -eq 'fs_read' -or $e.tool -eq 'fs_list') -and $e.path) {
                    $reads.Add(("  [{0}] {1}  {2}" -f $s.agent, $e.tool, $e.path))
                }
            }
            'cap_denied' { $denied.Add(("  [{0}] {1}: {2}" -f $s.agent, $e.tool, $e.reason)) }
            'tool_error' { $errors.Add(("  [{0}] {1} [{2}]: {3}" -f $s.agent, $e.tool, $e.class, $e.detail)) }
            'delegate'   { if ($e.child -eq 'auditor') { $auditorEdge = $true } }
        }
    }
}
$auditorSession = [bool]($data.delegation_tree | Where-Object { $_.agent -eq 'auditor' })

Write-Host ("fs_read/fs_list events ({0}):" -f $reads.Count)
$reads | ForEach-Object { Write-Host $_ }
Write-Host ("capability denials ({0}):" -f $denied.Count)
if ($denied.Count) { $denied | ForEach-Object { Write-Host $_ } } else { Write-Host '  (none)' }
Write-Host ("tool errors ({0}):" -f $errors.Count)
if ($errors.Count) { $errors | ForEach-Object { Write-Host $_ } } else { Write-Host '  (none)' }

$auditorRan = $auditorEdge -and $auditorSession
Write-Host ''
if ($auditorRan) { Write-Host 'AUDITOR_RAN: YES  (delegate edge + auditor session in the chain)' }
else { Write-Host 'AUDITOR_RAN: NO' }

# ---- 6. review report, if one exists ---------------------------------------
$report = Get-ChildItem -Path $RunHome -Filter 'security-review*.md' -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $report) {
    $outDir = Join-Path $RunHome 'out'
    if (Test-Path $outDir) {
        $report = Get-ChildItem -Path $outDir -Filter '*.md' -ErrorAction SilentlyContinue |
            Select-Object -First 1
    }
}
if ($report) { Write-Host ("review report: {0}" -f $report.FullName) }
else { Write-Host 'review report: (none found in the run home)' }

Write-Host ''
if (-not $auditorRan) { Write-Host 'FAILED: auditor edge missing from the chain'; exit 3 }
Write-Host 'ALL CHECKS PASSED - bundle verified with your key; auditor ran per the chain.'
exit 0
