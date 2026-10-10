; Inno Setup script for Simple-Photos.
; Built with: iscc packaging\windows\installer.iss
; Expects dist\Simple-Photos\ (PyInstaller onedir output) to already
; exist, and the version passed in via /DAppVersion=x.y.z (the release
; workflow does this so the .iss file never has to be hand-edited per
; release).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "Simple-Photos"
#define RepoRoot AddBackslash(SourcePath) + "..\.."

[Setup]
AppId={{157F8B30-A9B2-4E55-AF8A-1D3B6EC0B90B}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Mike Hellyer
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\Simple-Photos.exe
OutputDir={#RepoRoot}\dist
OutputBaseFilename=Simple-Photos-{#AppVersion}-Windows-Setup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile={#RepoRoot}\packaging\icons\icon.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "{#RepoRoot}\dist\Simple-Photos\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\Simple-Photos.exe"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Simple-Photos.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Simple-Photos.exe"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
