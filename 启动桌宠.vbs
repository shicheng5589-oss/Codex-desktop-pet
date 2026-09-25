Rem Codex pet launcher (ASCII only on purpose: script hosts read .vbs as ANSI)
Option Explicit

Dim fso, sh, here, python, found, cmd, base, versionDir, rel, rels
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName)
found = ""

python = sh.ExpandEnvironmentStrings("%CODEXPET_PYTHON%")
If python <> "%CODEXPET_PYTHON%" And fso.FileExists(python) Then found = python

If found = "" Then
  base = sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\.cache\codex-runtimes"
  If fso.FolderExists(base) Then
    rels = Array("dependencies\python\pythonw.exe", "dependencies\python\python.exe")
    For Each versionDir In fso.GetFolder(base).SubFolders
      For Each rel In rels
        If found = "" Then
          python = fso.BuildPath(versionDir.Path, rel)
          If fso.FileExists(python) Then found = python
        End If
      Next
    Next
  End If
End If

If found = "" Then found = "pythonw.exe"

cmd = """" & found & """ """ & fso.BuildPath(here, "run.py") & """"
sh.CurrentDirectory = here
sh.Run cmd, 0, False

