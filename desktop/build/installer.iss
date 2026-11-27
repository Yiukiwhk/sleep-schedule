; Inno Setup script for the sleepschedule desktop app (docs/DESKTOP.md section 10).
;
; Build the bundle first, then compile this script:
;   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\desktop\build\build.ps1
; build.ps1 passes /DMyVersion=<version> and compiles only when ISCC.exe exists.
;
; The installer puts program files under %ProgramFiles%\SleepSchedule and only
; creates/authorises %PROGRAMDATA%\YiukiSleep (the shared machine profile); it
; never deletes it, so records survive upgrades and uninstalls.

#ifndef MyVersion
  #define MyVersion "0.1.0"
#endif

#define MyAppName "Sleep schedule"
#define MyAppExeName "SleepSchedule.exe"

[Setup]
AppId={{5B0F3C7E-9A24-4C1D-8E55-6F7A2D9B4C10}
AppName={#MyAppName}
AppVersion={#MyVersion}
AppVerName={#MyAppName} {#MyVersion}
AppPublisher=Yiuki
DefaultDirName={autopf}\SleepSchedule
DefaultGroupName={#MyAppName}
; Do not inherit the group name from an earlier "Yiuki Sleep" install: the old
; shortcuts kept stale names and one of them pointed at the removed exe.
UsePreviousGroup=no
DisableProgramGroupPage=yes
OutputDir=..\..\dist\desktop
OutputBaseFilename=SleepSchedule-setup-{#MyVersion}
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
; Same art as the web favicon (desktop/build/app.ico is rendered from
; frontend/user/favicon.svg), so the wizard matches the exe and the shortcuts.
SetupIconFile=app.ico
CloseApplications=yes
RestartApplications=no
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "..\..\dist\desktop\SleepSchedule\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Upgrades reuse the previous install folder (same AppId); drop the old exe so
; shortcuts and Explorer never point at a stale 0.1.x binary.
[InstallDelete]
Type: files; Name: "{app}\YiukiSleep.exe"
; 0.2.1 and earlier used a group named "Yiuki Sleep" and a shortcut named
; "sleepschedule"; remove those leftovers together with the stale desktop icon.
Type: files; Name: "{commonprograms}\Yiuki Sleep\*.lnk"
Type: files; Name: "{group}\Yiuki Sleep.lnk"
Type: files; Name: "{group}\sleepschedule.lnk"
Type: files; Name: "{group}\Uninstall Yiuki Sleep.lnk"
Type: files; Name: "{group}\Uninstall sleepschedule.lnk"
Type: files; Name: "{autodesktop}\Yiuki Sleep.lnk"

[Dirs]
; %PROGRAMDATA%\SleepSchedule is shared machine-wide, and only this setup runs
; elevated: without the explicit grant a standard user could create the folder
; (the first launch does) but every later launch would fail to open app.log and
; app.db with "Permission denied".
Name: "{commonappdata}\SleepSchedule"; Permissions: users-modify
Name: "{commonappdata}\SleepSchedule\data"; Permissions: users-modify
Name: "{commonappdata}\SleepSchedule\logs"; Permissions: users-modify
Name: "{commonappdata}\SleepSchedule\backup"; Permissions: users-modify
Name: "{commonappdata}\SleepSchedule\models"; Permissions: users-modify

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

; %PROGRAMDATA%\SleepSchedule is intentionally left alone: it holds the local
; database, logs, backups and the cloud binding. Uninstall keeps the records.
; (0.2.2 and earlier used %PROGRAMDATA%\YiukiSleep; the app copies that folder
; once on first launch after the rename and leaves the old one in place.)
