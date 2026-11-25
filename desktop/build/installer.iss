; Inno Setup script for the Yiuki Sleep desktop app (docs/DESKTOP.md section 10).
;
; Build the bundle first, then compile this script:
;   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\desktop\build\build.ps1
; build.ps1 passes /DMyVersion=<version> and compiles only when ISCC.exe exists.
;
; The installer puts program files under %ProgramFiles%\YiukiSleep and only
; creates/authorises %PROGRAMDATA%\YiukiSleep (the shared machine profile); it
; never deletes it, so records survive upgrades and uninstalls.

#ifndef MyVersion
  #define MyVersion "0.1.0"
#endif

#define MyAppName "Yiuki Sleep"
#define MyAppExeName "YiukiSleep.exe"

[Setup]
AppId={{5B0F3C7E-9A24-4C1D-8E55-6F7A2D9B4C10}
AppName={#MyAppName}
AppVersion={#MyVersion}
AppVerName={#MyAppName} {#MyVersion}
AppPublisher=Yiuki
DefaultDirName={autopf}\YiukiSleep
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\..\dist\desktop
OutputBaseFilename=YiukiSleep-setup-{#MyVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; "x64compatible" needs Inno Setup 6.3+; older compilers only know "x64".
#if Ver >= 0x06030000
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
#else
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
#endif
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "..\..\dist\desktop\YiukiSleep\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
; %PROGRAMDATA%\YiukiSleep is shared machine-wide, and only this setup runs
; elevated: without the explicit grant a standard user could create the folder
; (the first launch does) but every later launch would fail to open app.log and
; app.db with "Permission denied".
Name: "{commonappdata}\YiukiSleep"; Permissions: users-modify
Name: "{commonappdata}\YiukiSleep\data"; Permissions: users-modify
Name: "{commonappdata}\YiukiSleep\logs"; Permissions: users-modify
Name: "{commonappdata}\YiukiSleep\backup"; Permissions: users-modify
Name: "{commonappdata}\YiukiSleep\models"; Permissions: users-modify

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

; %PROGRAMDATA%\YiukiSleep is intentionally left alone: it holds the local
; database, logs, backups and the cloud binding. Uninstall keeps the records.
