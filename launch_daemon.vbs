Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
scriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
venvPythonw = scriptDir & "\.venv\Scripts\pythonw.exe"
If FSO.FileExists(venvPythonw) Then
    cmd = """" & venvPythonw & """ """ & scriptDir & "\main.pyw"""
Else
    cmd = "pythonw.exe """ & scriptDir & "\main.pyw"""
End If
WshShell.CurrentDirectory = scriptDir
WshShell.Run cmd, 0, False
