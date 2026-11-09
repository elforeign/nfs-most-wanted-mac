"""Audit direct COFF objects, including the generated /alternatename fallbacks.

No unknown unresolved symbol is accepted. Native SDK/CRT names are an explicit
allowlist, not patterns; the desktop linker must still prove those libraries.
"""
from pathlib import Path
import json
import re
import shutil
import struct
import subprocess

# Native Windows x64 libraries, never replacements supplied by this project.
DESKTOP = {}
for library, names in {
    'kernel32.lib': '''__imp_VirtualAlloc __imp_VirtualFree
        __imp_QueryPerformanceCounter __imp_QueryPerformanceFrequency
        __imp_AcquireSRWLockExclusive __imp_ReleaseSRWLockExclusive
        __imp_SleepConditionVariableSRW __imp_WakeAllConditionVariable
        __imp_Sleep __imp_CreateThread __imp_WaitForSingleObject __imp_CloseHandle
        __imp_WaitForMultipleObjects __imp_SetEvent __imp_ResetEvent __imp_GetLastError
        __imp_GetModuleHandleA __imp_CreateEventA __imp_GetTickCount64''',
    'user32.lib': '''__imp_LoadCursorA __imp_RegisterClassA __imp_AdjustWindowRect
        __imp_CreateWindowExA __imp_ShowWindow __imp_DefWindowProcA
        __imp_DestroyWindow __imp_PostQuitMessage __imp_IsWindow
        __imp_PeekMessageA __imp_TranslateMessage __imp_DispatchMessageA
        __imp_ValidateRect __imp_GetClientRect __imp_GetWindowRect __imp_SetWindowPos __imp_SetFocus
        __imp_SetForegroundWindow __imp_SetCapture __imp_ReleaseCapture
        __imp_GetCursorPos __imp_ShowCursor __imp_MapVirtualKeyA __imp_GetKeyNameTextA
        __imp_RegisterRawInputDevices __imp_GetRawInputData''',
    'dxgi.lib': 'CreateDXGIFactory2 __imp_CreateDXGIFactory2',
    'd3d12.lib': '''D3D12CreateDevice __imp_D3D12CreateDevice
        D3D12SerializeRootSignature __imp_D3D12SerializeRootSignature''',
    'd3dcompiler.lib': 'D3DCompile __imp_D3DCompile',
    'dxguid.lib': '''IID_IDXGIFactory4 IID_ID3D12Device IID_ID3D12CommandQueue
        IID_ID3D12Resource IID_ID3D12DescriptorHeap IID_ID3D12RootSignature
        IID_ID3D12PipelineState IID_ID3D12CommandAllocator
        IID_ID3D12GraphicsCommandList IID_ID3D12Fence''',
    'ucrt/libcmt/vcruntime': '''_fltused memcpy memset memmove strncmp strcmp
        atan2 ceil cos exp2 fabs fegetround fesetround floor fmod frexp ilogb ldexp
        log2 nearbyint remquo sin sqrt tan trunc printf puts _dsign fflush exit malloc calloc free
        fopen fclose fseek ftell fread fwrite rewind _setjmp longjmp _tls_index _tls_used
        __acrt_iob_func __stdio_common_vfprintf __stdio_common_vfscanf
        __chkstk __security_cookie __security_check_cookie''',
}.items():
    for name in names.split():
        DESKTOP[name] = library

for name in ('waveOutOpen', 'waveOutPrepareHeader', 'waveOutWrite',
             'waveOutUnprepareHeader', 'waveOutReset', 'waveOutClose'):
    DESKTOP['__imp_' + name] = 'winmm.lib'
DESKTOP['__imp_TryAcquireSRWLockExclusive'] = 'kernel32.lib'
for name in '''??2@YAPEAX_K@Z ??3@YAXPEAX@Z ??3@YAXPEAX_K@Z
               _Init_thread_epoch _Init_thread_footer _Init_thread_header
               __dyn_tls_on_demand_init __tlregdtor __tls_guard
               atexit atoi fabs fprintf fputc getenv llround memcmp pow snprintf
               strlen strtod strtol vsnprintf'''.split():
    DESKTOP[name] = 'ucrt/libcmt/vcruntime'


def find_nm(compiler):
    found = shutil.which('llvm-nm')
    if found:
        return found
    compiler_path = Path(shutil.which(compiler) or compiler).resolve()
    suffix = '.exe' if compiler_path.suffix.lower() == '.exe' else ''
    found = compiler_path.parent / ('llvm-nm' + suffix)
    if found.is_file():
        return str(found)
    resource = subprocess.check_output([compiler, '-print-resource-dir'], text=True).strip()
    found = Path(resource).parents[2] / 'bin' / ('llvm-nm' + suffix)
    if found.is_file():
        return str(found)
    raise RuntimeError('llvm-nm required for COFF symbol closure')


def directives(data):
    """Read only .drectve, never adjacent binary section bytes as symbol text."""
    if len(data) < 20 or data[:2] != b'\x64\x86':
        raise ValueError('expected ordinary x64 COFF object')
    count = struct.unpack_from('<H', data, 2)[0]
    start = 20 + struct.unpack_from('<H', data, 16)[0]
    if start + count * 40 > len(data):
        raise ValueError('truncated COFF section table')
    for offset in range(start, start + count * 40, 40):
        if data[offset:offset+8].rstrip(b'\0') != b'.drectve':
            continue
        size, pointer = struct.unpack_from('<II', data, offset + 16)
        if pointer + size > len(data):
            raise ValueError('truncated COFF .drectve')
        yield data[pointer:pointer+size].decode('ascii')


def read_symbols(nm, objects):
    if not objects:
        return dict(raw_external=0, fallback_resolved=0, remaining=[], defined=[], aliases={})
    result = subprocess.run([nm, '-P', '--extern-only', *map(str, objects)],
                            capture_output=True, text=True, check=True)
    defined, undefined, aliases = set(), set(), {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        if parts[1] == 'U':
            undefined.add(parts[0])
        elif parts[1] in {'T', 'D', 'B', 'R', 'S', 'C', 'A'}:
            defined.add(parts[0])
    for obj in objects:
        for section in directives(obj.read_bytes()):
            for match in re.finditer(r'/alternatename:(\w+)=(\w+)(?=\s|$)', section, re.ASCII):
                name, target = match.groups()
                if name in aliases and aliases[name] != target:
                    raise ValueError(f'conflicting fallback: {name}')
                aliases[name] = target
    external = undefined - defined
    remaining = {name for name in external if aliases.get(name) not in defined}
    return dict(raw_external=len(external), fallback_resolved=len(external - remaining),
                remaining=sorted(remaining), defined=sorted(defined), aliases=aliases)


def audit(compiler, generated, host, output, sdk_headers=True):
    nm = find_nm(compiler)
    before = read_symbols(nm, generated)
    after = read_symbols(nm, [*generated, *host])
    # The declarations-only build cannot emit UCRT math.h inline calls or
    # hosted printf lowering. Require their owners even on the SDK-free host.
    modeled = [] if sdk_headers else ['_dsign', 'puts']
    remaining = set(after['remaining']) | set(modeled)
    unknown = sorted(remaining - DESKTOP.keys())
    for result in (before, after):
        result.pop('defined')
        result.pop('aliases')
    report = dict(before=before, after=after, unknown=unknown,
                  modeled_sdk=modeled,
                  desktop={s: DESKTOP[s] for s in sorted(remaining) if s in DESKTOP})
    (output / 'symbol-closure.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f"SYMBOLS external {before['raw_external']} -> {after['raw_external']}; "
          f"after fallbacks {len(before['remaining'])} -> {len(after['remaining'])}; "
          f"desktop {len(report['desktop'])}; unknown {len(unknown)}")
    if unknown:
        print('UNKNOWN: ' + ', '.join(unknown))
    return not unknown
