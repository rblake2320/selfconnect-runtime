# verify_run.ps1 - one-command owner verification of an SCR team run.
#
#   .\verify_run.ps1 -Home <run-home-path>
#
# Generates a fresh 32-byte key from the OS CSPRNG at runtime (never seen by
# any AI), exports the sealed team bundle to the Desktop, verifies it, and
# prints the run's FACTS from the chain only: team (root agent), package
# provenance, delegation tree, EVERY tool_exec (tool + primary argument), every
# denial and tool error, and REQUIRED_CHILDREN_RAN (read from the ledgered
# policy declaration — every declared required child must have run). Exits
# non-zero if the bundle does not verify or a required child did not run.
# Works for ANY layer/team. Uses only the frozen scr.exe - no Python required.
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

# Team name = the depth-0 (root) agent of the delegation tree.
$teamRoot = ($data.delegation_tree | Where-Object { $_.depth -eq 0 } |
    Select-Object -First 1).agent
Write-Host ("team (root agent): {0}" -f $teamRoot)

$toolExec = New-Object System.Collections.Generic.List[string]
$denied = New-Object System.Collections.Generic.List[string]
$errors = New-Object System.Collections.Generic.List[string]
$requiredChildren = New-Object System.Collections.Generic.List[string]
$completedChildren = New-Object System.Collections.Generic.List[string]
foreach ($s in $data.sessions) {
    foreach ($row in $s.events) {
        $e = $row.event | ConvertFrom-Json
        switch ($e.type) {
            'tool_exec' {
                # EVERY tool, not just filesystem - the verifier is the only
                # independent window into a layer's own tools. Show the primary
                # argument (path for fs_*/compliance_map, url for http_get,
                # binary for proc_exec, agent for delegate) so the actual call
                # is visible, not just its name.
                $arg = ''
                if ($e.path) { $arg = $e.path }
                elseif ($e.url) { $arg = $e.url }
                elseif ($e.binary) { $arg = $e.binary }
                elseif ($e.bundle) { $arg = $e.bundle }
                elseif ($e.child) { $arg = "-> $($e.child)" }
                $toolExec.Add(("  [{0}] {1}  {2}" -f $s.agent, $e.tool, $arg))
            }
            'cap_denied' { $denied.Add(("  [{0}] {1}: {2}" -f $s.agent, $e.tool, $e.reason)) }
            'tool_error' { $errors.Add(("  [{0}] {1} [{2}]: {3}" -f $s.agent, $e.tool, $e.class, $e.detail)) }
            'policy_declared' {
                foreach ($c in @($e.required_children)) {
                    if ($c) { [void]$requiredChildren.Add($c) }
                }
            }
            'policy' {
                if ($e.rule -eq 'required_children' -and $e.decision -eq 'completed' -and $e.child) {
                    [void]$completedChildren.Add($e.child)
                }
            }
        }
    }
}
$req = $requiredChildren | Select-Object -Unique
$done = $completedChildren | Select-Object -Unique

Write-Host ("tool executions ({0}) - ALL tools, with primary argument:" -f $toolExec.Count)
if ($toolExec.Count) { $toolExec | ForEach-Object { Write-Host $_ } } else { Write-Host '  (none)' }
Write-Host ("capability denials ({0}):" -f $denied.Count)
if ($denied.Count) { $denied | ForEach-Object { Write-Host $_ } } else { Write-Host '  (none)' }
Write-Host ("tool errors ({0}):" -f $errors.Count)
if ($errors.Count) { $errors | ForEach-Object { Write-Host $_ } } else { Write-Host '  (none)' }

# Generic required-children gate: read the DECLARED requirement from the chain
# and confirm every required child ran. Works for any layer/team.
Write-Host ''
$missing = @($req | Where-Object { $done -notcontains $_ })
if (@($req).Count -eq 0) {
    Write-Host 'REQUIRED_CHILDREN_RAN: N/A  (team declares no required children)'
    $requiredOk = $true
} elseif ($missing.Count -eq 0) {
    Write-Host ("REQUIRED_CHILDREN_RAN: YES  required=[{0}] all completed per the chain" -f ($req -join ', '))
    $requiredOk = $true
} else {
    Write-Host ("REQUIRED_CHILDREN_RAN: NO  required=[{0}] missing=[{1}]" -f ($req -join ', '), ($missing -join ', '))
    $requiredOk = $false
}

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
if (-not $requiredOk) {
    Write-Host 'FAILED: a required child declared in the chain did not run'
    exit 3
}
Write-Host 'ALL CHECKS PASSED - bundle verified with your key.'
exit 0
