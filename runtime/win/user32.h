#pragma once
#include "host.h"
uint64_t win_user32_dropped_messages(void);
#if defined(RECOMP_WINDOWS_COMPILE_ONLY) || defined(NFS_WIN_TEST)
typedef void *HWND,*HINSTANCE,*HCURSOR;
typedef unsigned UINT;
typedef uintptr_t WPARAM;
typedef intptr_t LPARAM,LRESULT;
#define CALLBACK
typedef struct { int32_t left,top,right,bottom; } RECT;
typedef struct { int32_t x,y; } POINT;
typedef struct { UINT style; LRESULT (*lpfnWndProc)(HWND,UINT,WPARAM,LPARAM); int cbClsExtra,cbWndExtra;
    HINSTANCE hInstance; void *hIcon; HCURSOR hCursor; void *hbrBackground;
    const char *lpszMenuName,*lpszClassName; } WNDCLASSA;
typedef struct { HWND hwnd; UINT message; WPARAM wParam; LPARAM lParam; DWORD time; POINT pt; DWORD lPrivate; } MSG;
typedef struct { uint16_t usUsagePage,usUsage; DWORD dwFlags; HWND hwndTarget; } RAWINPUTDEVICE;
typedef struct { DWORD dwType,dwSize; HANDLE hDevice; WPARAM wParam; } RAWINPUTHEADER;
typedef struct { uint16_t usFlags; union { uint32_t ulButtons; struct { uint16_t usButtonFlags,usButtonData; }; };
    uint32_t ulRawButtons; int32_t lLastX,lLastY; uint32_t ulExtraInformation; } RAWMOUSE;
typedef struct { RAWINPUTHEADER header; union { RAWMOUSE mouse; } data; } RAWINPUT;
WIN_API BOOL RegisterRawInputDevices(const RAWINPUTDEVICE *,UINT,UINT);
WIN_API UINT GetRawInputData(HANDLE,UINT,void *,UINT *,UINT);
WIN_API HINSTANCE GetModuleHandleA(const char *);
WIN_API HCURSOR LoadCursorA(HINSTANCE,const char *);
WIN_API int GetKeyNameTextA(int32_t,char *,int);
WIN_API unsigned short RegisterClassA(const WNDCLASSA *);
WIN_API HWND CreateWindowExA(DWORD,const char *,const char *,DWORD,int,int,int,int,HWND,void *,HINSTANCE,void *);
WIN_API LRESULT DefWindowProcA(HWND,UINT,WPARAM,LPARAM);
WIN_API BOOL DestroyWindow(HWND);
WIN_API BOOL ShowWindow(HWND,int);
WIN_API BOOL PeekMessageA(MSG *,HWND,UINT,UINT,UINT);
WIN_API BOOL TranslateMessage(const MSG *);
WIN_API LRESULT DispatchMessageA(const MSG *);
WIN_API BOOL GetClientRect(HWND,RECT *);
WIN_API BOOL ValidateRect(HWND,const RECT *);
WIN_API BOOL GetWindowRect(HWND,RECT *);
WIN_API BOOL SetWindowPos(HWND,HWND,int,int,int,int,UINT);
WIN_API BOOL AdjustWindowRect(RECT *,DWORD,BOOL);
WIN_API HWND SetFocus(HWND);
WIN_API BOOL SetForegroundWindow(HWND);
WIN_API HWND SetCapture(HWND);
WIN_API BOOL ReleaseCapture(void);
WIN_API BOOL GetCursorPos(POINT *);
WIN_API int ShowCursor(BOOL);
WIN_API UINT MapVirtualKeyA(UINT,UINT);
#endif
int win_user32_register(void);
void win_user32_pump(void);
uint32_t win_callback(Cpu *,uint32_t,unsigned,const uint32_t *);
HWND win_user32_native(uint32_t);
