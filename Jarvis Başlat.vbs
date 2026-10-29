Option Explicit
Dim shell, fso, root, py, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
py = root & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(py) Then py = "pythonw.exe"
cmd = """" & py & """ """ & root & "\main.py"""
On Error Resume Next
shell.Run cmd, 1, False
If Err.Number <> 0 Then
    MsgBox "Python bulunamadi. Once Jarvis Kur.bat dosyasini calistirin.", 16, "Jarvis"
End If
