#ifndef StageDir
  #error StageDir must be passed with /DStageDir=...
#endif
#ifndef OutputDir
  #error OutputDir must be passed with /DOutputDir=...
#endif
[Setup]
AppId={{B9B5847A-B096-49A8-838A-8DB6F5E12218}
AppName=Better PianoTrans
AppVersion=0.1.0
DefaultDirName={localappdata}\Programs\Better PianoTrans
DisableDirPage=no
DefaultGroupName=Better PianoTrans
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=BetterPianoTrans-Setup
Compression=lzma2
SolidCompression=yes
UninstallDisplayName=Better PianoTrans

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Better PianoTrans"; Filename: "{app}\BetterPianoTrans.cmd"; Parameters: "--installed"; WorkingDir: "{app}"
Name: "{autodesktop}\Better PianoTrans"; Filename: "{app}\BetterPianoTrans.cmd"; Parameters: "--installed"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "创建桌面快捷方式"

[Run]
Filename: "{app}\BetterPianoTrans.cmd"; Parameters: "--installed"; Description: "启动 Better PianoTrans"; Flags: postinstall nowait skipifsilent
