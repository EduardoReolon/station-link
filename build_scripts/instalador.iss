[Setup]
OutputDir=..\dist
AppName=StationLink
AppVersion=1.0.0
AppPublisher=Sigma2B
DefaultDirName={autopf}\StationLink
DisableProgramGroupPage=yes
OutputBaseFilename=Instalador_StationLink
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
SetupIconFile=compiler:SetupClassicIcon.ico

[Files]
Source: "..\dist\Updater.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\StationLink.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\version.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Atalho no Menu Iniciar apontando para o Updater
Name: "{autoprograms}\StationLink"; Filename: "{app}\Updater.exe"
; Atalho OBRIGATÓRIO na pasta de Inicialização do Windows apontando para o Updater
Name: "{userstartup}\StationLink"; Filename: "{app}\Updater.exe"

[Run]
; Inicia o Updater automaticamente assim que a instalação terminar
Filename: "{app}\Updater.exe"; Description: "Iniciar StationLink"; Flags: nowait postinstall skipifsilent