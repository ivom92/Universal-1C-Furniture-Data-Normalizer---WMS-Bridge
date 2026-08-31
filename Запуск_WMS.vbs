' Silent warehouse launcher: Streamlit without a visible cmd.exe window.
Option Explicit

Dim sh, fso, root, cmd, portCheck
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = root

If Not fso.FileExists(root & "\venv\Scripts\python.exe") Then
  MsgBox "venv not found. Run 1_USTANOVKA.bat first.", 16, "WMS Parser"
  WScript.Quit 1
End If

portCheck = sh.Run("cmd.exe /c netstat -aon | findstr :8501 | findstr LISTENING >nul", 0, True)
If portCheck = 0 Then
  sh.Run "http://localhost:8501"
  WScript.Quit 0
End If

cmd = "cmd.exe /c set PYTHONIOENCODING=utf-8&& set PYTHONLEGACYWINDOWSSTDIO=1&& set PYTHONUTF8=1&& " & _
      "venv\Scripts\python.exe -m streamlit run app_ui.py" & _
      " --server.port 8501 --server.headless true --server.fileWatcherType none --browser.gatherUsageStats false"
sh.Run cmd, 0, False
WScript.Sleep 3000
sh.Run "http://localhost:8501"
