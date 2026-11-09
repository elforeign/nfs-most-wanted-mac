// SDK-free declarations are only for COFF compilation and synthetic host tests.
// A runnable Windows build must use the SDK via build_windows.py --sdk-headers.
#pragma once
#include <stdint.h>
#include <stddef.h>
#if !defined(RECOMP_WINDOWS_COMPILE_ONLY) && !defined(NFS_WIN_TEST)
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>
#else
#ifdef NFS_WIN_TEST
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>
#define WIN_API
#else
#define WIN_API __declspec(dllimport)
typedef struct WinFile FILE;
FILE *fopen(const char *, const char *);
int fclose(FILE *);
int fseek(FILE *, long, int);
long ftell(FILE *);
size_t fread(void *, size_t, size_t, FILE *);
int printf(const char *, ...);
int fflush(FILE *);
_Noreturn void exit(int);
void *malloc(size_t);
void free(void *);
void *memset(void *, int, size_t);
int strcmp(const char *, const char *);
typedef __declspec(align(16)) uint64_t jmp_buf[32];
int _setjmp(jmp_buf, void *) __attribute__((returns_twice));
_Noreturn void longjmp(jmp_buf, int);
#define setjmp(e) _setjmp(e, __builtin_frame_address(0))
#endif
typedef void *HANDLE;
typedef uint32_t DWORD;
typedef int BOOL;
typedef union { int64_t QuadPart; } LARGE_INTEGER;
typedef struct { void *Ptr; } SRWLOCK, CONDITION_VARIABLE;
#define SRWLOCK_INIT {0}
#define CONDITION_VARIABLE_INIT {0}
#define INFINITE 0xffffffffu
#define MEM_COMMIT 0x1000u
#define MEM_RESERVE 0x2000u
#define MEM_RELEASE 0x8000u
#define PAGE_READWRITE 4u
#define CREATE_SUSPENDED 4u
#define STACK_SIZE_PARAM_IS_A_RESERVATION 0x10000u
#define WAIT_OBJECT_0 0u
#define WAIT_TIMEOUT 258u
#define WAIT_FAILED 0xffffffffu
#define SEEK_END 2
#define SEEK_SET 0
WIN_API void *VirtualAlloc(void *, size_t, DWORD, DWORD);
WIN_API BOOL VirtualFree(void *, size_t, DWORD);
WIN_API BOOL QueryPerformanceCounter(LARGE_INTEGER *);
WIN_API BOOL QueryPerformanceFrequency(LARGE_INTEGER *);
WIN_API void AcquireSRWLockExclusive(SRWLOCK *);
WIN_API void ReleaseSRWLockExclusive(SRWLOCK *);
WIN_API BOOL SleepConditionVariableSRW(CONDITION_VARIABLE *, SRWLOCK *, DWORD, DWORD);
WIN_API void WakeAllConditionVariable(CONDITION_VARIABLE *);
WIN_API void Sleep(DWORD);
WIN_API HANDLE CreateThread(void *, size_t, DWORD (*)(void *), void *, DWORD, DWORD *);
WIN_API DWORD ResumeThread(HANDLE);
WIN_API DWORD WaitForSingleObject(HANDLE, DWORD);
WIN_API DWORD WaitForMultipleObjects(DWORD, const HANDLE *, BOOL, DWORD);
WIN_API HANDLE CreateEventA(void *, BOOL, BOOL, const char *);
WIN_API BOOL SetEvent(HANDLE);
WIN_API BOOL ResetEvent(HANDLE);
WIN_API DWORD GetLastError(void);
WIN_API BOOL CloseHandle(HANDLE);
#endif

// PCM endpoint declarations; native SDK builds use the actual WinMM layouts.
#if !defined(RECOMP_WINDOWS_COMPILE_ONLY) && !defined(NFS_WIN_TEST)
#include <mmsystem.h>
#else
typedef void *HWAVEOUT;
typedef struct {
    char *lpData;
    DWORD dwBufferLength, dwBytesRecorded;
    uintptr_t dwUser;
    volatile DWORD dwFlags;
    DWORD dwLoops;
    void *lpNext;
    uintptr_t reserved;
} WAVEHDR;
typedef struct WinWaveFormat WAVEFORMATEX;
char *getenv(const char *);
WIN_API unsigned waveOutOpen(HWAVEOUT *, unsigned, const WAVEFORMATEX *, uintptr_t, uintptr_t, DWORD);
WIN_API unsigned waveOutPrepareHeader(HWAVEOUT, WAVEHDR *, unsigned);
WIN_API unsigned waveOutWrite(HWAVEOUT, WAVEHDR *, unsigned);
WIN_API unsigned waveOutUnprepareHeader(HWAVEOUT, WAVEHDR *, unsigned);
WIN_API unsigned waveOutReset(HWAVEOUT);
WIN_API unsigned waveOutClose(HWAVEOUT);
#endif
