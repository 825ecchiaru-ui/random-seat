// 启动器：使用者双击的"座位随机重排.exe"就是它。
//
// 它只做一件事：用程序自带的Python（_internal\pythonw.exe）运行 _internal\app\app.py，
// 并等它结束。真正的程序逻辑都在Python代码里。
//
// 为什么需要它：Python的程序本身是一批脚本文件，不能直接双击运行；
// 而用PyInstaller把它们合成一个exe的做法，生成的exe容易被杀毒软件按"长得像"误判为木马。
// 这个启动器很小、不携带任何数据，程序自带的Python又是Python官方发布、带数字签名的文件，
// 这样整个程序包里就没有容易被误判的东西了。
//
// 编译由 desktop/build.py 完成（需要Visual Studio的C编译器）。本文件保存为UTF-8编码，编译时加 /utf-8。
#include <windows.h>

#define APP_NAME L"座位随机重排"
#define PATH_LEN 1024

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR arguments, int show)
{
    wchar_t folder[PATH_LEN];       // 启动器所在的文件夹
    wchar_t python[PATH_LEN];       // 程序自带的Python
    wchar_t script[PATH_LEN];       // 要运行的Python程序
    wchar_t command[PATH_LEN * 3];  // 完整的命令行
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    DWORD length, exit_code = 1;
    wchar_t *slash;

    // 取得启动器自己的完整路径，去掉文件名，得到所在的文件夹
    length = GetModuleFileNameW(NULL, folder, PATH_LEN);
    if (length == 0 || length >= PATH_LEN) {
        MessageBoxW(NULL, L"程序所在位置的路径太长，请把程序文件夹移到路径较短的位置（例如桌面）再试。",
                    APP_NAME, MB_ICONERROR);
        return 1;
    }
    slash = wcsrchr(folder, L'\\');
    if (slash != NULL) *slash = L'\0';

    // wsprintfW一次最多写出1024个字符，下面拼出的命令行里路径会出现两次，所以文件夹的路径不能超过400个字符
    if (lstrlenW(folder) > 400) {
        MessageBoxW(NULL, L"程序所在位置的路径太长，请把程序文件夹移到路径较短的位置（例如桌面）再试。",
                    APP_NAME, MB_ICONERROR);
        return 1;
    }
    wsprintfW(python, L"%s\\_internal\\pythonw.exe", folder);
    wsprintfW(script, L"%s\\_internal\\app\\app.py", folder);

    // 最常见的问题：没有解压就运行，或者只把exe单独拿走了
    if (GetFileAttributesW(python) == INVALID_FILE_ATTRIBUTES || GetFileAttributesW(script) == INVALID_FILE_ATTRIBUTES) {
        MessageBoxW(NULL,
                    L"找不到程序需要的文件。\n\n"
                    L"请确认：\n"
                    L"1. 已经把压缩包完整解压（不要在压缩包里直接双击）；\n"
                    L"2. “_internal”文件夹和本程序在同一个文件夹里，没有被移走或删除。",
                    APP_NAME, MB_ICONERROR);
        return 1;
    }

    // 把程序文件夹的位置告诉Python程序（app.py从环境变量SEAT_APP_DIR读取）
    SetEnvironmentVariableW(L"SEAT_APP_DIR", folder);

    wsprintfW(command, L"\"%s\" \"%s\"", python, script);
    ZeroMemory(&startup, sizeof(startup));
    startup.cb = sizeof(startup);
    ZeroMemory(&process, sizeof(process));
    if (!CreateProcessW(python, command, NULL, NULL, FALSE, 0, NULL, folder, &startup, &process)) {
        MessageBoxW(NULL, L"程序无法启动。请重新解压或重新安装后再试。", APP_NAME, MB_ICONERROR);
        return 1;
    }

    // 等Python程序结束（使用者关闭了窗口），把它的结束状态原样返回
    WaitForSingleObject(process.hProcess, INFINITE);
    GetExitCodeProcess(process.hProcess, &exit_code);
    CloseHandle(process.hProcess);
    CloseHandle(process.hThread);
    return (int)exit_code;
}
