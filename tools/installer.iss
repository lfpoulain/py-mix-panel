; Installateur MixPanel (Inno Setup 6). Compilé par tools/build_exe.py, qui passe
; /DAppVersion=x.y.z /DSourceDir=... /DOutputDir=... /DIconFile=...
; Installation par utilisateur : aucun droit administrateur requis.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\MixPanel"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#ifndef IconFile
  #define IconFile "..\build\mixpanel.ico"
#endif

[Setup]
AppId={{6E2B7C1A-4D53-4B8F-9A21-3C5D8E7F1A90}
AppName=MixPanel
AppVersion={#AppVersion}
AppVerName=MixPanel {#AppVersion}
AppPublisher=MixPanel
AppPublisherURL=https://github.com/lfpoulain/py-mix-panel
AppSupportURL=https://github.com/lfpoulain/py-mix-panel/issues
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\MixPanel
DefaultGroupName=MixPanel
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=MixPanel-{#AppVersion}-setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\MixPanel.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\MixPanel"; Filename: "{app}\MixPanel.exe"
Name: "{autodesktop}\MixPanel"; Filename: "{app}\MixPanel.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MixPanel.exe"; Description: "{cm:LaunchProgram,MixPanel}"; Flags: nowait postinstall skipifsilent
; Mise à jour depuis l'app (/SILENT /RELAUNCH=1) : on relance MixPanel à la fin.
Filename: "{app}\MixPanel.exe"; Flags: nowait; Check: WantsRelaunch

; Les profils, scènes et macros vivent dans %APPDATA%\MixPanel : la désinstallation n'y touche pas.

[Code]
function WantsRelaunch: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;
