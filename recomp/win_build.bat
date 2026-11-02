@echo off
setlocal EnableExtensions
rem Copy this file to C:\nfsmw\win_build.bat. Any Command Prompt is supported.
if /i "%VSCMD_ARG_TGT_ARCH%"=="x64" goto toolsready
set "VCVARS=%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
if not exist "%VCVARS%" (
    echo ERROR: VS 2022 Build Tools x64 environment not found: "%VCVARS%"
    echo Install Visual Studio 2022 Build Tools with the C++ tools and Windows SDK.
    exit /b 1
)
call "%VCVARS%" || exit /b 1
if not defined VCToolsInstallDir (
    echo ERROR: vcvars64.bat did not initialize VCToolsInstallDir.
    exit /b 1
)
:toolsready
set "ROOT=C:\nfsmw"
set "PORT=%ROOT%\port"
set "PY=%ROOT%\venv\Scripts\python.exe"
cd /d "%PORT%" || exit /b 1
if /i "%~1"=="venv" goto venv
if /i "%~1"=="apply" goto apply
if /i "%~1"=="fetch" goto fetch
if /i "%~1"=="generate" goto generate
if /i "%~1"=="build" goto build
if /i "%~1"=="link" goto link
if /i "%~1"=="smoke" goto smoke
if /i "%~1"=="image" goto image
if /i "%~1"=="boot-prefix" goto boot-prefix
if /i "%~1"=="check-boot" goto check-boot
if /i "%~1"=="image-mask" goto imagemask
echo Usage: C:\nfsmw\win_build.bat venv^|apply patchfile^|fetch [remote]^|generate [game-exe]^|build^|link^|smoke [game-exe]^|boot-prefix [game-exe]^|check-boot^|image^|image-mask
exit /b 2

:venv
if exist "%PY%" goto packages
py -3.12 -m venv "%ROOT%\venv" || exit /b 1
:packages
"%PY%" -I -m pip install capstone==5.0.9 numpy==2.0.2
exit /b %errorlevel%

:fetch
if not "%~2"=="" set "NFS_FETCH_REMOTE=%~2"
if not defined NFS_FETCH_REMOTE set "NFS_FETCH_REMOTE=mac"
git -C "%PORT%" fetch -- "%NFS_FETCH_REMOTE%" main || exit /b 1
git -C "%PORT%" merge --ff-only FETCH_HEAD || exit /b 1
git -C "%PORT%" rev-parse HEAD
exit /b %errorlevel%

:apply
if "%~2"=="" exit /b 2
set "PATCH=%~f2"
if /i not "%PATCH:~0,9%"=="C:\nfsmw\" exit /b 2
git -C "%PORT%" am "%PATCH%" || exit /b 1
git -C "%PORT%" rev-parse HEAD
exit /b %errorlevel%

:generate
if not "%~2"=="" set "NFS_SPEED_EXE=%~2"
if not defined NFS_SPEED_EXE goto needgame
rem Generation needs only Python packages and the matching executable, no SDK.
"%PY%" -I recomp\build_all.py --generate-only --skip-server --game-exe "%NFS_SPEED_EXE%"
exit /b %errorlevel%

:build
set "RECOMP_CFLAGS=-fms-runtime-lib=static"
"%PY%" -I recomp\build_windows.py --host-layer --sdk-headers --compiler clang --jobs 6
exit /b %errorlevel%

:link
if not exist .metal-cache\windows\objects.rsp exit /b 1
link.exe @.metal-cache\windows\objects.rsp /OUT:.metal-cache\windows\nfsmw-headless.exe /SUBSYSTEM:CONSOLE /MACHINE:X64 /STACK:67108864 kernel32.lib libcmt.lib libucrt.lib libvcruntime.lib oldnames.lib
exit /b %errorlevel%

:smoke
if not "%~2"=="" set "NFS_SPEED_EXE=%~2"
if not defined NFS_SPEED_EXE goto needgame
.metal-cache\windows\nfsmw-headless.exe "%NFS_SPEED_EXE%" > .metal-cache\windows\smoke.log 2>&1
set "SMOKE_RC=%errorlevel%"
type .metal-cache\windows\smoke.log
if not "%SMOKE_RC%"=="10" exit /b 1
findstr /c:"win: image loaded, 53636 translated functions, entry 007C4040, 300 imports" .metal-cache\windows\smoke.log >nul || exit /b 1
findstr /c:"win: first import KERNEL32.dll!GetVersionExA slot=008900E0" .metal-cache\windows\smoke.log >nul || exit /b 1
echo SMOKE PASS: first import, guest stop 10
exit /b 0

:check-boot
"%PY%" -I recomp\windows_boot_test.py --desktop
exit /b %errorlevel%

:boot-prefix
if not "%~2"=="" set "NFS_SPEED_EXE=%~2"
if not defined NFS_SPEED_EXE goto needgame
.metal-cache\windows\nfsmw-headless.exe --boot-prefix "%NFS_SPEED_EXE%" > .metal-cache\windows\boot-prefix.log 2>&1
set "BOOT_RC=%errorlevel%"
type .metal-cache\windows\boot-prefix.log
if not "%BOOT_RC%"=="12" exit /b 1
findstr /x /c:"BOOT PREFIX PASS: 007C4144" .metal-cache\windows\boot-prefix.log >nul || exit /b 1
echo BOOT PREFIX PASS: guest checkpoint, stop 12
exit /b 0

:image
set "RECOMP_CFLAGS=-fms-runtime-lib=static"
"%PY%" -I recomp\build_windows.py --image --sdk-headers --compiler clang || exit /b 1
link.exe @.metal-cache\windows-image\objects.rsp /OUT:.metal-cache\windows-image\nfsmw-image.exe /SUBSYSTEM:CONSOLE /MACHINE:X64 kernel32.lib user32.lib d3d12.lib dxgi.lib dxguid.lib d3dcompiler.lib libcmt.lib libucrt.lib libvcruntime.lib oldnames.lib || exit /b 1
if not exist "%ROOT%\shots" mkdir "%ROOT%\shots"
if not exist "%ROOT%\shots" exit /b 1
if exist "%ROOT%\shots\win3-fe.bmp" del "%ROOT%\shots\win3-fe.bmp" || exit /b 1
.metal-cache\windows-image\nfsmw-image.exe .metal-cache\windows-image\fe_vs.hlsl .metal-cache\windows-image\fe_ps.hlsl "%ROOT%\shots\win3-fe.bmp" > .metal-cache\windows-image\image.log 2>&1
set "IMAGE_RC=%errorlevel%"
type .metal-cache\windows-image\image.log
if not "%IMAGE_RC%"=="0" exit /b 1
findstr /c:"IMAGE PASS: D3D12 retail fe VS+PS" .metal-cache\windows-image\image.log >nul || exit /b 1
if not exist "%ROOT%\shots\win3-fe.bmp" exit /b 1
echo IMAGE PASS: window and C:\nfsmw\shots\win3-fe.bmp
exit /b 0

:imagemask
rem Masked front-end pair (second texture t1/s1, TEXCOORD1) through the same D3D12 image path.
set "RECOMP_CFLAGS=-fms-runtime-lib=static"
"%PY%" -I recomp\build_windows.py --image --sdk-headers --compiler clang --image-shader-dir host\shaders\testdata\fe_mask --output .metal-cache\windows-image-mask || exit /b 1
link.exe @.metal-cache\windows-image-mask\objects.rsp /OUT:.metal-cache\windows-image-mask\nfsmw-image.exe /SUBSYSTEM:CONSOLE /MACHINE:X64 kernel32.lib user32.lib d3d12.lib dxgi.lib dxguid.lib d3dcompiler.lib libcmt.lib libucrt.lib libvcruntime.lib oldnames.lib || exit /b 1
if not exist "%ROOT%\shots" mkdir "%ROOT%\shots"
if exist "%ROOT%\shots\win6-fe-mask.bmp" del "%ROOT%\shots\win6-fe-mask.bmp" || exit /b 1
.metal-cache\windows-image-mask\nfsmw-image.exe .metal-cache\windows-image-mask\fe_vs.hlsl .metal-cache\windows-image-mask\fe_ps.hlsl "%ROOT%\shots\win6-fe-mask.bmp" --mask > .metal-cache\windows-image-mask\image.log 2>&1
set "IMAGE_RC=%errorlevel%"
type .metal-cache\windows-image-mask\image.log
if not "%IMAGE_RC%"=="0" exit /b 1
findstr /c:"IMAGE PASS: D3D12 retail fe VS+PS" .metal-cache\windows-image-mask\image.log >nul || exit /b 1
if not exist "%ROOT%\shots\win6-fe-mask.bmp" exit /b 1
echo IMAGE PASS: masked pair and C:\nfsmw\shots\win6-fe-mask.bmp
exit /b 0

:needgame
echo Supply the speed.exe path as argument 2 or set NFS_SPEED_EXE.
exit /b 2
