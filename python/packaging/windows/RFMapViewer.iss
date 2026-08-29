#define MyAppName "RF Map Viewer"
#define MyAppPublisher "KaiCao2003"
#define MyAppExecutable "RF Map Viewer.exe"

#ifndef MyAppVersion
  #define MyAppVersion "1.9.6"
#endif

#ifndef MyAppBuild
  #define MyAppBuild "10908"
#endif

#ifndef SourceRoot
  #define SourceRoot "..\..\dist\windows\RF Map Viewer"
#endif

#ifndef OutputDir
  #define OutputDir "..\..\dist\windows"
#endif

#ifndef OutputBaseFilename
  #define OutputBaseFilename "RF_Map_Viewer-python-" + MyAppVersion + "-full-windows-x64-setup"
#endif

[Setup]
AppId={{A944B6A4-358E-48E3-9EA6-9CC91C95D15B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\App\{#MyAppExecutable}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Python stable installer
VersionInfoProductName={#MyAppName}
VersionInfoProductTextVersion={#MyAppVersion}
VersionInfoProductVersion={#MyAppVersion}.0
VersionInfoTextVersion={#MyAppVersion}.{#MyAppBuild}
VersionInfoVersion={#MyAppVersion}.{#MyAppBuild}

#ifdef SetupIconFile
SetupIconFile={#SetupIconFile}
#endif

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceRoot}\App\*"; DestDir: "{app}\App"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceRoot}\Resources\*"; DestDir: "{app}\Resources"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\App\{#MyAppExecutable}"; WorkingDir: "{app}\Resources"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\App\{#MyAppExecutable}"; WorkingDir: "{app}\Resources"; Tasks: desktopicon

[Run]
Filename: "{app}\App\{#MyAppExecutable}"; WorkingDir: "{app}\Resources"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
