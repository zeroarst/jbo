Set sh = CreateObject("WScript.Shell")
Dim ps1Path
ps1Path = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\jbo\jbo-handler.ps1"
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ _
    & ps1Path & """ """ & WScript.Arguments(0) & """", 0, False
