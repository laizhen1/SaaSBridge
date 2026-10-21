Option Explicit

Dim shell, files, root, script, powershell, command
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
script = files.BuildPath(root, "scripts\Start-SaaSBridge.ps1")
powershell = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\WindowsPowerShell\v1.0\powershell.exe"

If Not files.FileExists(script) Then
    MsgBox "The launcher script is missing. Keep this file inside the SaaSBridge project folder.", vbCritical, "SaaSBridge"
    WScript.Quit 1
End If

command = Quote(powershell) & " -NoLogo -NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Quote(script)
On Error Resume Next
shell.Run command, 0, False
If Err.Number <> 0 Then
    MsgBox "Windows could not start the SaaSBridge launcher: " & Err.Description, vbCritical, "SaaSBridge"
    WScript.Quit 1
End If

Function Quote(value)
    Quote = Chr(34) & value & Chr(34)
End Function
