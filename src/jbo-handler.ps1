param([string]$Uri)
Add-Type -AssemblyName System.Web
$q = [System.Web.HttpUtility]::ParseQueryString(([Uri]$Uri).Query)
$file = $q["file"]
if ($file -match '^/mnt/([a-z])/(.*)') {
    # WSL path: /mnt/d/foo → D:\foo
    $file = $matches[1].ToUpper() + ':' + $matches[2] -replace '/', '\'
} elseif ($file -match '^/([a-zA-Z])/(.*)') {
    # MSYS/Git Bash path: /c/foo → C:\foo
    $file = $matches[1].ToUpper() + ':' + $matches[2] -replace '/', '\'
} else {
    $file = $file -replace '/', '\'
}
$line = $q["line"]
$ide  = $q["ide"]

$cfg = Get-Content "$env:LOCALAPPDATA\jbo\config.json" | ConvertFrom-Json
$exeMap = @{
    "webstorm"      = $cfg.webstorm
    "androidstudio" = $cfg.androidstudio
    "intellij"      = $cfg.intellij
}
$exe = $exeMap[$ide]
if (-not $exe) { $exe = $cfg.webstorm }
& $exe --line $line $file
