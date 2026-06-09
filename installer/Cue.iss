; Inno Setup script for Cue (by Digitalgrub)
; Compile:  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\Cue.iss
; Produces: installer\output\CueSetup.exe  (single-file installer)

#define MyAppName "Cue"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Digitalgrub"
#define MyAppURL "https://digitalgrub.in"
#define MyAppExeName "Cue.exe"
#define ProjDir "C:\path\to\meeting-copilot"
#define SrcDir ProjDir + "\dist\Cue"

[Setup]
AppId={{8E5C2A14-2B9D-4F6E-A1C7-9D3E0F5B7A22}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppComments=Cue - your cue to speak. A live meeting copilot by Digitalgrub.
DefaultDirName={autopf}\Cue
DefaultGroupName=Cue
DisableProgramGroupPage=yes
; Per-user install -> no admin prompt
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#ProjDir}\installer\output
OutputBaseFilename=CueSetup
SetupIconFile={#ProjDir}\cue.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
InfoBeforeFile={#ProjDir}\installer\PREINSTALL.txt
DisableWelcomePage=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
; Bundle the entire onedir build
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Cue"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall Cue"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Cue"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Cue now"; Flags: nowait postinstall skipifsilent
