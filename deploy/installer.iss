; ── Inno Setup скрипт для BMS (автономний Windows-інсталятор) ──────────────────
; Збирає PyInstaller-онедір + portable PostgreSQL + WebView2 Runtime у один Setup.exe.
;
; ПЕРЕДУМОВИ (готує BUILD_WINDOWS.md):
;   dist\BMS\                         ← вихід `pyinstaller deploy\bms.spec`
;   deploy\staging\postgres\          ← portable PostgreSQL 16 (тека bin, lib, share)
;   deploy\staging\MicrosoftEdgeWebview2Setup.exe  ← bootstrapper з microsoft.com
;
; ЗБІРКА:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" deploy\installer.iss
;   → Output\BMS_Setup_<version>.exe
;
; Per-user інсталяція (без UAC): {localappdata}\Programs\BMS.
; Дані (pgdata/secrets/seed) — у {localappdata}\BMS (НЕ в теці застосунку → переживає апдейти).

#define AppName "BMS"
#define AppPublisher "BMS"
; Версію зчитуємо з файлу VERSION у корені (тримати в синхроні)
#define AppVersion "0.1.0-alpha"
#define AppExe "BMS.exe"

[Setup]
AppId={{B7A1F3C2-5E4D-4A8B-9C6F-1D2E3F4A5B60}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=BMS_Setup_{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "Запускати BMS при вході в систему"; GroupDescription: "Додатково:"; Flags: unchecked

[Files]
; PyInstaller onedir
Source: "..\dist\BMS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Portable PostgreSQL 16 → поруч із застосунком; embedded_db.resolve_pg_bin_dir() шукає <app>\postgres\bin
Source: "staging\postgres\*"; DestDir: "{app}\postgres"; Flags: ignoreversion recursesubdirs createallsubdirs
; WebView2 bootstrapper (видаляється після інсталяції)
Source: "staging\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Dirs]
; Тека даних — створюємо заздалегідь (pgdata/secrets.env/seed.sql/backups лягають сюди)
Name: "{localappdata}\BMS"
Name: "{localappdata}\BMS\backups"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: autostart

[Run]
; Поставити WebView2 Runtime тихо, ЯКЩО ще не встановлено (перевірка в [Code])
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; \
  StatusMsg: "Встановлення компонента WebView2 Runtime…"; \
  Check: WebView2Missing; Flags: waituntilterminated
; Запустити застосунок після інсталяції
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; \
  Flags: nowait postinstall skipifsilent

[Code]
{ Перевірка наявності Evergreen WebView2 Runtime у реєстрі (per-machine + per-user). }
function WebView2Missing(): Boolean;
var
  pv: String;
  found: Boolean;
begin
  found := False;
  { Per-machine (x64 на 64-біт системі — WOW6432Node) }
  if RegQueryStringValue(HKLM,
      'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
      'pv', pv) then
    if (pv <> '') and (pv <> '0.0.0.0') then found := True;
  { Per-user }
  if (not found) and RegQueryStringValue(HKCU,
      'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
      'pv', pv) then
    if (pv <> '') and (pv <> '0.0.0.0') then found := True;
  Result := not found;
end;
