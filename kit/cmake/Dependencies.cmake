# Third-party code that is fetched rather than vendored. Pinned to a tag, never
# a branch, so a configure today and a configure next year build the same bytes.
include(FetchContent)

set(SDL_SHARED OFF CACHE BOOL "" FORCE)
set(SDL_STATIC ON CACHE BOOL "" FORCE)
set(SDL_TEST_LIBRARY OFF CACHE BOOL "" FORCE)
set(SDL_TESTS OFF CACHE BOOL "" FORCE)
set(SDL_EXAMPLES OFF CACHE BOOL "" FORCE)
# Subsystems the host does not use stay out of the binary.
set(SDL_CAMERA OFF CACHE BOOL "" FORCE)
set(SDL_SENSOR OFF CACHE BOOL "" FORCE)
set(SDL_HAPTIC OFF CACHE BOOL "" FORCE)
# Android keeps the renderer: without it a window has no software surface,
# and the launcher draws through one on a device without Vulkan.
if(ANDROID)
  set(SDL_RENDER ON CACHE BOOL "" FORCE)
else()
  set(SDL_RENDER OFF CACHE BOOL "" FORCE)
endif()
set(SDL_GPU OFF CACHE BOOL "" FORCE)
if(EMSCRIPTEN)
  set(SDL_PTHREADS ON CACHE BOOL "" FORCE) # the game runs on a thread of its own
endif()
FetchContent_Declare(SDL3
  GIT_REPOSITORY https://github.com/libsdl-org/SDL.git
  GIT_TAG release-3.4.16
  GIT_SHALLOW TRUE)
FetchContent_MakeAvailable(SDL3)

# glslang compiles the Direct3D 9 renderer's GLSL to SPIR-V at run time
# (host/gpu/vulkan/d3d9_vulkan.cpp): every platform that renders on Vulkan.
set(RECOMP_D9_VULKAN OFF)
if(NOT IOS AND NOT EMSCRIPTEN)
  set(RECOMP_D9_VULKAN ON)
  set(ENABLE_OPT OFF CACHE BOOL "" FORCE)
  set(ENABLE_HLSL OFF CACHE BOOL "" FORCE)
  set(ENABLE_GLSLANG_BINARIES OFF CACHE BOOL "" FORCE)
  set(ENABLE_SPVREMAPPER OFF CACHE BOOL "" FORCE)
  set(GLSLANG_TESTS OFF CACHE BOOL "" FORCE)
  set(GLSLANG_ENABLE_INSTALL OFF CACHE BOOL "" FORCE)
  set(BUILD_EXTERNAL OFF CACHE BOOL "" FORCE)
  set(ENABLE_CTEST OFF CACHE BOOL "" FORCE)
  FetchContent_Declare(glslang
    GIT_REPOSITORY https://github.com/KhronosGroup/glslang.git
    GIT_TAG 16.6.0
    GIT_SHALLOW TRUE)
  FetchContent_MakeAvailable(glslang)
endif()

# pop_link_sdl(<target>): link SDL3 statically and give the target its headers.
function(pop_link_sdl target)
  target_link_libraries(${target} PRIVATE SDL3::SDL3-static)
endfunction()

# Video is enabled on the hosts with shared-library packaging support.
set(RECOMP_VIDEO_DEFAULT OFF)
if(CMAKE_SYSTEM_NAME STREQUAL "Darwin" OR IOS OR ANDROID OR CMAKE_SYSTEM_NAME STREQUAL "Linux"
    OR (WIN32 AND NOT CMAKE_HOST_WIN32))
  set(RECOMP_VIDEO_DEFAULT ON)
elseif(WIN32)
  # FFmpeg's configure needs an MSYS2 shell and GNU make. The shell is looked
  # for beside make first, so Git's or WSL's bash is not picked by accident.
  find_program(RECOMP_FFMPEG_MAKE NAMES make)
  if(RECOMP_FFMPEG_MAKE)
    get_filename_component(RECOMP_FFMPEG_MSYS_BIN "${RECOMP_FFMPEG_MAKE}" DIRECTORY)
    find_program(RECOMP_FFMPEG_SHELL NAMES bash HINTS "${RECOMP_FFMPEG_MSYS_BIN}" NO_DEFAULT_PATH)
  endif()
  if(NOT RECOMP_FFMPEG_SHELL OR NOT RECOMP_FFMPEG_MAKE)
    message(STATUS "RECOMP_VIDEO stays OFF: Windows requires MSYS2's bash and make")
  else()
    set(RECOMP_VIDEO_DEFAULT ON)
  endif()
endif()
# An MSVC-ABI compiler (clang targeting MSVC, clang-cl, cl) gets an FFmpeg
# built by FFmpeg's own MSVC toolchain, whose DLLs and import libraries it
# links; a MinGW compiler gets a MinGW FFmpeg.
set(RECOMP_FFMPEG_MSVC OFF)
if(WIN32 AND NOT MINGW AND (MSVC OR CMAKE_C_SIMULATE_ID STREQUAL "MSVC"))
  set(RECOMP_FFMPEG_MSVC ON)
endif()
option(RECOMP_VIDEO "Build the shared FFmpeg movie and music dependency" ${RECOMP_VIDEO_DEFAULT})
if(WIN32 AND CMAKE_HOST_WIN32 AND NOT RECOMP_VIDEO_DEFAULT)
  set(RECOMP_VIDEO OFF CACHE BOOL "Build the shared FFmpeg movie and music dependency" FORCE)
endif()

if(RECOMP_VIDEO)
  if(NOT APPLE AND NOT ANDROID AND NOT CMAKE_SYSTEM_NAME STREQUAL "Linux" AND NOT WIN32)
    message(FATAL_ERROR "RECOMP_VIDEO is not supported on ${CMAKE_SYSTEM_NAME}")
  endif()
  include(ExternalProject)
  if(NOT WIN32 OR NOT CMAKE_HOST_WIN32)
    set(RECOMP_FFMPEG_SHELL /bin/sh)
    find_program(RECOMP_FFMPEG_MAKE NAMES make REQUIRED)
  endif()
  set(RECOMP_FFMPEG_PREFIX "${CMAKE_BINARY_DIR}/ffmpeg")
  set(RECOMP_FFMPEG_CONFIGURE
    --prefix=${RECOMP_FFMPEG_PREFIX}
    --enable-shared --disable-static --disable-programs --disable-doc
    --disable-everything --disable-avdevice --disable-avfilter
    --disable-swscale --disable-swresample --disable-postproc --disable-network
    --enable-pic
    --enable-decoder=bink,binkaudio_rdft,binkaudio_dct,smacker,smackaud
    --enable-decoder=wmv1,wmv2,wmv3,vc1,wmav1,wmav2,wmapro,mp3,mp3float
    # MS-MPEG-4 part 2, the fourccs MPG4, MP42 and MP43. A .wmv from the
    # Windows Media Encoder era usually holds one of these rather than a
    # WMV-numbered codec, and the demuxer that reads the container is no
    # use without the decoder that reads the frames.
    --enable-decoder=msmpeg4v1,msmpeg4v2,msmpeg4v3,indeo5,vorbis,adpcm_ima_wav,pcm_s16le,pcm_u8
    # Dolby Digital output (host/audio/dolby_sink.cpp) encodes the 5.1 mix as
    # AC-3; the decoder is for tests that check the round trip.
    --enable-encoder=ac3 --enable-decoder=ac3
    --enable-demuxer=bink,smacker,asf,mp3,avi,ogg --enable-parser=vc1,mpegaudio
    --enable-protocol=file
    --disable-autodetect --disable-xlib --disable-libxcb --disable-sdl2
    --disable-iconv --disable-zlib --disable-bzlib --disable-lzma
    --disable-securetransport --disable-audiotoolbox --disable-videotoolbox)
  if(IOS)
    # CMake accepts either an SDK name or an absolute sysroot; FFmpeg needs
    # the directory. This dependency targets arm64 devices, not the simulator.
    set(RECOMP_FFMPEG_SYSROOT "${CMAKE_OSX_SYSROOT}")
    if(NOT IS_DIRECTORY "${RECOMP_FFMPEG_SYSROOT}")
      execute_process(COMMAND xcrun -sdk iphoneos --show-sdk-path
        OUTPUT_VARIABLE RECOMP_FFMPEG_SYSROOT OUTPUT_STRIP_TRAILING_WHITESPACE
        COMMAND_ERROR_IS_FATAL ANY)
    endif()
    list(APPEND RECOMP_FFMPEG_CONFIGURE
      --enable-cross-compile --target-os=darwin --arch=arm64
      "--cc=xcrun -sdk iphoneos clang" --sysroot=${RECOMP_FFMPEG_SYSROOT}
      "--extra-cflags=-arch arm64 -miphoneos-version-min=17.0"
      "--extra-ldflags=-arch arm64 -miphoneos-version-min=17.0"
      --install-name-dir=@rpath)
  elseif(ANDROID)
    # CMake's compiler is inside the selected NDK prebuilt toolchain. Use its
    # API-29 wrapper so configure and every FFmpeg probe target Android 10.
    get_filename_component(RECOMP_FFMPEG_TOOLCHAIN_BIN "${CMAKE_C_COMPILER}" DIRECTORY)
    get_filename_component(RECOMP_FFMPEG_SYSROOT "${RECOMP_FFMPEG_TOOLCHAIN_BIN}/../sysroot" ABSOLUTE)
    list(APPEND RECOMP_FFMPEG_CONFIGURE
      --enable-cross-compile --target-os=android --arch=aarch64
      --cc=${RECOMP_FFMPEG_TOOLCHAIN_BIN}/aarch64-linux-android29-clang
      --ar=${RECOMP_FFMPEG_TOOLCHAIN_BIN}/llvm-ar
      --nm=${RECOMP_FFMPEG_TOOLCHAIN_BIN}/llvm-nm
      --ranlib=${RECOMP_FFMPEG_TOOLCHAIN_BIN}/llvm-ranlib
      --strip=${RECOMP_FFMPEG_TOOLCHAIN_BIN}/llvm-strip
      --sysroot=${RECOMP_FFMPEG_SYSROOT} --disable-symver
      # 16 KB page devices (Android 15 and later) load only aligned libraries.
      "--extra-ldflags=-Wl,-z,max-page-size=16384")
  elseif(APPLE)
    list(APPEND RECOMP_FFMPEG_CONFIGURE
      --install-name-dir=@rpath --cc=${CMAKE_C_COMPILER})
  elseif(WIN32 AND CMAKE_CROSSCOMPILING)
    # configure must not run Windows probes on the POSIX build host or use
    # its native binutils. Select every tool from the llvm-mingw toolchain.
    get_filename_component(RECOMP_FFMPEG_TOOLCHAIN_BIN "${CMAKE_C_COMPILER}" DIRECTORY)
    list(APPEND RECOMP_FFMPEG_CONFIGURE
      --enable-cross-compile --target-os=mingw32 --arch=${CMAKE_SYSTEM_PROCESSOR}
      --cross-prefix=${RECOMP_FFMPEG_TOOLCHAIN_BIN}/${CMAKE_SYSTEM_PROCESSOR}-w64-mingw32-
      --cc=${CMAKE_C_COMPILER} --ar=${CMAKE_AR} --ranlib=${CMAKE_RANLIB}
      --nm=${RECOMP_FFMPEG_TOOLCHAIN_BIN}/llvm-nm
      --strip=${RECOMP_FFMPEG_TOOLCHAIN_BIN}/llvm-strip
      --windres=${CMAKE_RC_COMPILER})
  else()
    if(RECOMP_FFMPEG_MSVC)
      # cl.exe and link.exe from the Visual Studio developer environment;
      # FFmpeg's compat/windows/mslink finds the right link.exe when MSYS2's
      # coreutils link comes first on PATH.
      list(APPEND RECOMP_FFMPEG_CONFIGURE --toolchain=msvc --target-os=win64 --arch=x86_64)
    else()
      list(APPEND RECOMP_FFMPEG_CONFIGURE --cc=${CMAKE_C_COMPILER})
      if(WIN32)
        list(APPEND RECOMP_FFMPEG_CONFIGURE --target-os=mingw32)
      endif()
    endif()
  endif()
  if(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64)$" OR "x86_64" IN_LIST CMAKE_OSX_ARCHITECTURES)
    list(APPEND RECOMP_FFMPEG_CONFIGURE --disable-x86asm)
  endif()
  set(RECOMP_FFMPEG_LIBRARIES)
  set(RECOMP_FFMPEG_IMPLIBRARIES)
  foreach(component avformat avcodec avutil)
    if(component STREQUAL "avutil")
      set(major 59)
    else()
      set(major 61)
    endif()
    set(libdir lib)
    if(ANDROID)
      # FFmpeg's Android target installs unversioned names and SONAMEs.
      set(filename lib${component}.so)
      set(soname ${filename})
    elseif(APPLE)
      set(filename lib${component}.${major}.dylib)
      set(soname @rpath/${filename})
    elseif(WIN32)
      set(libdir bin)
      set(filename ${component}-${major}.dll)
      set(soname ${filename})
    else()
      set(filename lib${component}.so.${major})
      set(soname ${filename})
    endif()
    list(APPEND RECOMP_FFMPEG_LIBRARIES "${RECOMP_FFMPEG_PREFIX}/${libdir}/${filename}")
    add_library(ffmpeg::${component} SHARED IMPORTED GLOBAL)
    # Keep bundled headers ahead of older FFmpeg headers in /usr/local/include.
    # They must match the bundled libraries, including AVCodecContext's layout.
    set_target_properties(ffmpeg::${component} PROPERTIES
      IMPORTED_NO_SYSTEM TRUE
      IMPORTED_LOCATION "${RECOMP_FFMPEG_PREFIX}/${libdir}/${filename}"
      IMPORTED_SONAME "${soname}"
      INTERFACE_INCLUDE_DIRECTORIES "${RECOMP_FFMPEG_PREFIX}/include")
    if(WIN32)
      if(RECOMP_FFMPEG_MSVC)
        # FFmpeg's win64 target installs the import library beside the DLL.
        set(implib "${RECOMP_FFMPEG_PREFIX}/bin/${component}.lib")
      else()
        set(implib "${RECOMP_FFMPEG_PREFIX}/lib/lib${component}.dll.a")
      endif()
      set_target_properties(ffmpeg::${component} PROPERTIES IMPORTED_IMPLIB "${implib}")
      list(APPEND RECOMP_FFMPEG_IMPLIBRARIES "${implib}")
    endif()
  endforeach()
  # FFmpeg uses a shell configure script and GNU make, not CMake or Ninja.
  # CMAKE_COMMAND is the same (venv) CMake that configured the kit.
  # On a Windows host MSYS2 tools run with their own directory first on PATH, so
  # configure and make find sed, awk and sh there and nowhere else.
  set(RECOMP_FFMPEG_ENV ${CMAKE_COMMAND} -E env)
  if(WIN32 AND CMAKE_HOST_WIN32)
    get_filename_component(RECOMP_FFMPEG_MSYS_BIN "${RECOMP_FFMPEG_MAKE}" DIRECTORY)
    list(APPEND RECOMP_FFMPEG_ENV --modify "PATH=path_list_prepend:${RECOMP_FFMPEG_MSYS_BIN}")
  endif()
  ExternalProject_Add(ffmpeg
    URL https://ffmpeg.org/releases/ffmpeg-7.1.1.tar.xz
    URL_HASH SHA256=733984395e0dbbe5c046abda2dc49a5544e7e0e1e2366bba849222ae9e3a03b1
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    CONFIGURE_COMMAND ${RECOMP_FFMPEG_ENV} ${RECOMP_FFMPEG_SHELL} <SOURCE_DIR>/configure ${RECOMP_FFMPEG_CONFIGURE}
    BUILD_COMMAND ${RECOMP_FFMPEG_ENV} ${RECOMP_FFMPEG_MAKE} -j8
    INSTALL_COMMAND ${RECOMP_FFMPEG_ENV} ${RECOMP_FFMPEG_MAKE} install
    BUILD_BYPRODUCTS ${RECOMP_FFMPEG_LIBRARIES} ${RECOMP_FFMPEG_IMPLIBRARIES})
  if(WIN32)
    # Windows finds a DLL beside the executable: every host and test binary
    # is built into POP_OUT.
    ExternalProject_Add_Step(ffmpeg copy_dlls
      COMMAND ${CMAKE_COMMAND} -E make_directory ${POP_OUT}
      COMMAND ${CMAKE_COMMAND} -E copy_if_different ${RECOMP_FFMPEG_LIBRARIES} ${POP_OUT}
      DEPENDEES install)
  endif()
  # Imported include paths must exist at generation time, before installation.
  file(MAKE_DIRECTORY "${RECOMP_FFMPEG_PREFIX}/include")
  foreach(component avformat avcodec avutil)
    add_dependencies(ffmpeg::${component} ffmpeg)
  endforeach()
endif()

# Object-library consumers must inherit both the headers and the dynamic link.
function(pop_link_video target)
  if(RECOMP_VIDEO)
    target_link_libraries(${target} PUBLIC ffmpeg::avformat ffmpeg::avcodec ffmpeg::avutil)
    target_compile_definitions(${target} PUBLIC RECOMP_HAVE_FFMPEG=1)
  endif()
endfunction()
