param([string]$Uri)
Add-Type -AssemblyName System.Web
$q = [System.Web.HttpUtility]::ParseQueryString(([Uri]$Uri).Query)
$file = $q["file"] -replace '/', '\'
$line = $q["line"]
$ide  = $q["ide"]

$exes = @{
    "webstorm"      = "__WEBSTORM_EXE__"
    "androidstudio" = "__AS_EXE__"
    "intellij"      = "__IJ_EXE__"
}

$exe = $exes[$ide]
if (-not $exe) { $exe = $exes["webstorm"] }
& $exe --line $line $file
