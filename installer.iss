#define AppVersion "0.3.0"
[Setup]
AppId={{2D5350AB-357F-48B5-87C1-E35AF06012BD}
AppName=Offer Helper
AppVersion={#AppVersion}
AppPublisher=Offer Helper
DefaultDirName={localappdata}\Programs\OfferHelper
DefaultGroupName=Offer Helper
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist
OutputBaseFilename=OfferHelper-Setup-{#AppVersion}-x64
SetupIconFile=web\assets\app.ico
UninstallDisplayIcon={app}\OfferHelper.exe
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Files]
Source: "dist\OfferHelper\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Offer Helper"; Filename: "{app}\OfferHelper.exe"
Name: "{userdesktop}\Offer Helper"; Filename: "{app}\OfferHelper.exe"

[Run]
Filename: "{app}\OfferHelper.exe"; Description: "Launch Offer Helper"; Flags: nowait postinstall skipifsilent
