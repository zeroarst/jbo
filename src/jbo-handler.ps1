param([string]$Uri)
Add-Type -AssemblyName System.Web
$q = [System.Web.HttpUtility]::ParseQueryString(([Uri]$Uri).Query)
$file = $q["file"]
if ($file -match '^/mnt/([a-z])/(.*)') {
    $file = $matches[1].ToUpper() + ':' + $matches[2] -replace '/', '\'
} else {
    $file = $file -replace '/', '\'
}
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
