; SPDX-License-Identifier: GPL-3.0-or-later
; MiHome-Windows 安装包脚本（Inno Setup 6）
;
; 前置：先跑 .\build.ps1 生成 dist\MiHome-Windows.exe（含依赖目录），
;       再装 Inno Setup 6（https://jrsoftware.org/isdl.php），
;       然后执行 .\make_installer.ps1 或：
;         iscc /DMyAppVersion=0.3.0 installer.iss
;
; 为什么需要它：Nuitka standalone 的产物是"一个 exe + 一堆 dll/目录"，
; 直接拷 exe 是跑不起来的。安装包把这堆东西整体装到用户目录，
; 并提供开始菜单快捷方式与卸载程序。

#ifndef MyAppVersion
  #define MyAppVersion "0.3.8"
#endif

#define MyAppName "MiHome-Windows"
#define MyAppDisplayName "米家桌面控制端"
#define MyAppPublisher "MiHome-Windows contributors"
#define MyAppExeName "MiHome-Windows.exe"
#define MyAppUrl "https://github.com/huanyuejue/MiHome-Windows"

[Setup]
AppId={{8B2E5D31-6C7A-4F42-9E51-2D3A7C4B1F60}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppDisplayName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppUrl}
AppSupportURL={#MyAppUrl}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppDisplayName}
; 装到用户目录，不需要管理员权限，也不会污染 Program Files
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=dist\installer
OutputBaseFilename={#MyAppName}-Setup-{#MyAppVersion}
SetupIconFile=app\ui\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 与 Nuitka 产物版本号对齐，方便"应用和功能"里核对
VersionInfoVersion={#MyAppVersion}

[Languages]
Name: "chinese"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
; dist 下的全部内容（exe + dll + 依赖目录）整体装入
Source: "dist\*"; DestDir: "{app}"; Excludes: "run.build\*,run.dist\*,installer\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppDisplayName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppDisplayName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppDisplayName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppDisplayName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只删安装目录里由安装程序放进去的东西；用户配置在 %LOCALAPPDATA%\MiHome-Windows，
; 不在 {app} 下，卸载不会带走（重装后配置还在）
Type: filesandordirs; Name: "{app}\dist"

[Code]
// 卸载时问一句要不要连配置一起删，避免"卸载后重装发现设置还在"的困惑
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\MiHome-Windows');
    if DirExists(DataDir) then
    begin
      if MsgBox('是否同时删除配置与缓存（' + DataDir + '）？' + #13#10 +
                '选择「否」将保留设置，重装后可直接继续使用。',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
