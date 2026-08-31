' Silent warehouse stopper: kills Streamlit on port 8501 without a console window.
Option Explicit

Dim sh, fso, root, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = root

cmd = "cmd.exe /c for /f ""tokens=5"" %a in ('netstat -aon ^| findstr :8501 ^| findstr LISTENING') do taskkill /f /pid %a >nul 2>&1"
sh.Run cmd, 0, True
