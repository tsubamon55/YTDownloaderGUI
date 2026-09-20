; Inno Setup script for YTDownloaderGUI
; Build the app first: pyinstaller YTDownloaderGUI.spec
; Then compile this script: iscc installer.iss

#define MyAppName "YTDownloaderGUI"
#define MyAppVersion "1.0.1"
#define MyAppExeName "YTDownloaderGUI.exe"

[Setup]
AppId={{B3B5B1B0-5B1C-4E6C-9C6B-8F4B6B7B2A11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=installer_output
OutputBaseFilename={#MyAppName}-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "デスクトップにアイコンを作成する"; GroupDescription: "追加のアイコン:"

[InstallDelete]
; 1.0.0は同梱ファイルを{app}\_internalに置いていた。Inno Setupは新しいファイル一覧に無いファイルを
; 消さないため、上書きインストール時に旧構成が丸ごと残ってしまうのを防ぐ
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\{#MyAppName}\*"; DestDir: "{app}"; Excludes: "config.json"; Flags: ignoreversion recursesubdirs createallsubdirs
; config.jsonはユーザーが編集している可能性があるため、更新時に上書きしない(初回インストール時のみ配置する)
Source: "dist\{#MyAppName}\config.json"; DestDir: "{app}"; Flags: onlyifdoesntexist
Source: "licenses\COPYING.GPLv3.txt"; DestDir: "{app}\licenses"; Flags: ignoreversion
Source: "licenses\FFMPEG_NOTICE.md"; DestDir: "{app}\licenses"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
