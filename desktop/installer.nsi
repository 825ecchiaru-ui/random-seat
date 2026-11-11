; 安装包脚本（NSIS）。不要直接运行，由 desktop/build.py 调用，并传入以下参数：
;   VERSION  版本号                SOURCE   要安装的程序文件所在的文件夹
;   OUTFILE  生成的安装包文件       ICON     图标文件
;
; 安装方式：只为当前用户安装，不需要管理员权限。
;   程序文件  %LOCALAPPDATA%\Programs\RandomSeat（可在安装时修改）
;   数据      %APPDATA%\RandomSeat（由程序自己建立；卸载和重装都不会动它）
; 本文件必须保存为带BOM的UTF-8编码，否则中文会变成乱码。

Unicode true
!include "MUI2.nsh"

!define APP_NAME "座位随机重排"
!define APP_ID "RandomSeat"
!define APP_EXE "座位随机重排.exe"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}"

Name "${APP_NAME}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\${APP_ID}"
; 以前安装过的话，默认装到上次的位置
InstallDirRegKey HKCU "Software\${APP_ID}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
BrandingText "${APP_NAME} ${VERSION}"

VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=2052 "ProductName" "${APP_NAME}"
VIAddVersionKey /LANG=2052 "FileDescription" "${APP_NAME} 安装程序"
VIAddVersionKey /LANG=2052 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=2052 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=2052 "LegalCopyright" "MIT License"

!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_ABORTWARNING

!define MUI_WELCOMEPAGE_TEXT "这个向导将把 ${APP_NAME} ${VERSION} 安装到您的电脑上。$\r$\n$\r$\n不需要管理员权限，也不需要联网。$\r$\n$\r$\n如果以前安装过旧版本，直接继续安装即可，原有的班级和排座记录都会保留。$\r$\n$\r$\n点“下一步”继续。"
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "立即打开 ${APP_NAME}"
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\使用说明.html"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "查看使用说明"
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"

Section "安装"
  SetOutPath "$INSTDIR"
  ; 升级时先清掉旧版本的程序文件（只是程序，不含数据）
  RMDir /r "$INSTDIR\_internal"
  File /r "${SOURCE}\*.*"

  WriteUninstaller "$INSTDIR\卸载.exe"

  ; 桌面和开始菜单的快捷方式
  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\使用说明.lnk" "$INSTDIR\使用说明.html"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\卸载 ${APP_NAME}.lnk" "$INSTDIR\卸载.exe"

  ; 记下安装位置；在Windows的"设置 - 应用"里登记，这样可以从那里卸载
  WriteRegStr HKCU "Software\${APP_ID}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\卸载.exe"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\卸载.exe" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "EstimatedSize" ${SIZE_KB}
SectionEnd

Section "Uninstall"
  ; 只删除安装时放进去的东西：程序文件、快捷方式、登记信息
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\使用说明.html"
  Delete "$INSTDIR\使用说明.pdf"
  Delete "$INSTDIR\卸载.exe"
  ; 不带 /r：只有文件夹已经空了才会删除，不会误删使用者自己放进去的文件
  RMDir "$INSTDIR"

  Delete "$DESKTOP\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\使用说明.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\卸载 ${APP_NAME}.lnk"
  RMDir "$SMPROGRAMS\${APP_NAME}"

  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  DeleteRegKey HKCU "Software\${APP_ID}"

  ; 程序窗口的缓存和显示偏好（不是数据）一并清掉
  RMDir /r "$LOCALAPPDATA\${APP_ID}\window"
  RMDir "$LOCALAPPDATA\${APP_ID}"

  ; 班级、名单和排座记录保留在数据文件夹里。静默卸载（/S）时不弹出这个提示
  MessageBox MB_OK|MB_ICONINFORMATION "${APP_NAME} 已卸载。$\r$\n$\r$\n班级、名单和排座记录仍然保留在：$\r$\n$APPDATA\${APP_ID}$\r$\n$\r$\n以后重新安装时会继续使用。如果确定不再需要，可以手动删除这个文件夹。" /SD IDOK
SectionEnd
