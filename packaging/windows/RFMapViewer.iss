#define MyAppName "RF Map Viewer"
#define MyAppVersion "1.6.1"
#define MyAppPublisher "KaiCao2003"

#ifndef SourceRoot
  #define SourceRoot "..\..\dist\windows\RF Map Viewer"
#endif

[Setup]
AppId={{A944B6A4-358E-48E3-9EA6-9CC91C95D15B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist\windows
OutputBaseFilename=RF_Map_Viewer-python-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
UninstallDisplayIcon={app}\App\RF Map Viewer.exe

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceRoot}\App\*"; DestDir: "{app}\App"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceRoot}\Resources\*"; DestDir: "{app}\Resources"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\RF Map Viewer"; Filename: "{app}\App\RF Map Viewer.exe"; WorkingDir: "{app}\Resources"
Name: "{autodesktop}\RF Map Viewer"; Filename: "{app}\App\RF Map Viewer.exe"; WorkingDir: "{app}\Resources"; Tasks: desktopicon

[Run]
Filename: "{app}\App\RF Map Viewer.exe"; WorkingDir: "{app}\Resources"; Description: "Launch RF Map Viewer"; Flags: nowait postinstall skipifsilent
