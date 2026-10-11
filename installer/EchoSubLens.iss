; SPDX-License-Identifier: GPL-3.0-only
; Copyright (C) 2026 Mohammad Al-Safadi
;
; Inno Setup 6 script for the EchoSub Lens installer. Build it with scripts\build-installer.ps1, which passes
; /DAppVersion=<version> and expects the PyInstaller output in dist\EchoSubLens.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "EchoSub Lens"
#define AppId "EchoSubLens"
#define AppPublisher "Mohammad Al-Safadi"
#define AppURL "https://github.com/mPhpMaster/echo-sub-lens"
#define AppExe "EchoSubLens.exe"

[Setup]
AppId={{B3D7A1E4-5C2F-4F6B-9E8A-7D1C0B4E2F63}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
AppCopyright=Copyright (C) 2026 {#AppPublisher}
AppComments=Translate the text on your screen, in place.
AppContact=mPhpMaster@gmail.com
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup
VersionInfoCopyright=Copyright (C) 2026 {#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist\installer
OutputBaseFilename=EchoSubLens-Setup-{#AppVersion}
SetupIconFile=..\echosub_lens\assets\echosub.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Windows 10 version 2004 (build 19041) or later: Windows' own text recognition
MinVersion=10.0.19041
; Install for the current user without admin rights by default; the user can choose all users instead.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; Ask to close a running EchoSub Lens before installing or uninstalling (the app owns this mutex).
AppMutex=EchoSubLensMutex
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"

[CustomMessages]
english.StartWithWindows=Start {#AppName} when I sign in to Windows
arabic.StartWithWindows=تشغيل {#AppName} عند تسجيل الدخول إلى ويندوز
english.RemoveData=Also delete EchoSub Lens's settings, downloaded models and logs?%n%n(%1)
arabic.RemoveData=هل تريد أيضاً حذف إعدادات EchoSub Lens والنماذج التي تم تنزيلها والسجلات؟%n%n(%1)
english.FirstRunNote=EchoSub Lens starts with an icon by the clock. Press Ctrl+Alt+T to start or stop translating the screen. To read another language, add its Optical character recognition feature in Windows Settings → Time & language → Language.
arabic.FirstRunNote=يعمل EchoSub Lens بأيقونة بجانب الساعة. اضغط Ctrl+Alt+T لبدء ترجمة الشاشة أو إيقافها. لقراءة لغة أخرى، أضف ميزة التعرّف الضوئي على الحروف لها من إعدادات ويندوز ← الوقت واللغة ← اللغة.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startup"; Description: "{cm:StartWithWindows}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\EchoSubLens\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Translate the text on your screen, in place"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppId}"; \
    ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
    WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10#13#10 + CustomMessage('FirstRunNote');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\{#AppId}');
    if DirExists(DataDir) and not UninstallSilent() then
      if MsgBox(FmtMessage(CustomMessage('RemoveData'), [DataDir]), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
