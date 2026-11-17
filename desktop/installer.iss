; Inno Setup 6 script: wraps dist\TGLibrary into a normal Windows installer
; (dist\TGLibrary-Setup.exe). Built automatically by desktop\build.ps1 when
; Inno Setup is installed (https://jrsoftware.org/isdl.php or
; `winget install JRSoftware.InnoSetup`).
;
; - Installs per user (no admin prompt) into %LOCALAPPDATA%\Programs\TGLibrary.
; - Start-menu shortcuts, optional desktop shortcut, Arabic or English wizard.
; - Uninstall asks whether to also delete settings + Telegram sign-in
;   (%LOCALAPPDATA%\TGLibrary). The books folder is never touched.

#define AppName "TG Library"
#define AppVersion "1.0.0"
#ifndef SourceDir
  #define SourceDir "..\dist\TGLibrary"
#endif

[Setup]
AppId={{6C1F4B0E-2D7A-4E43-9B8C-7A1D3F5E2C90}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=TG Library
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\TGLibrary
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=TGLibrary-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\TGLibrary.exe
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
MinVersion=10.0
; Arabic first: the wizard opens in Arabic (the first [Languages] entry); the
; language box still lets the user switch to English.
LanguageDetectionMethod=none
ShowLanguageDialog=yes
#ifndef NoMutex
; Refuse to (un)install while the app is running (mutex is held by desktop\launcher.py).
AppMutex=Local\TGLibraryDesktopInstance
#endif
CloseApplications=yes

[Languages]
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
arabic.SettingsShortcut=إعدادات {#AppName}
english.SettingsShortcut={#AppName} Settings
arabic.RemoveData=هل تريد أيضاً حذف إعدادات البرنامج وتسجيل دخولك إلى تيليغرام؟%n%nالكتب التي نزّلتها لن تُحذف.
english.RemoveData=Also delete the app's settings and your Telegram sign-in?%n%nThe books you downloaded will NOT be deleted.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Drop files left by an older version before copying the new ones.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\TGLibrary.exe"
Name: "{group}\{cm:SettingsShortcut}"; Filename: "{app}\TGLibrary.exe"; Parameters: "--setup"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\TGLibrary.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\TGLibrary.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\TGLibrary');
    if DirExists(DataDir) and not UninstallSilent then
      if MsgBox(CustomMessage('RemoveData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
