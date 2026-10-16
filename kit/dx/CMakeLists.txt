set(POP_DX_SOURCES
  com.cpp dx.cpp host_api.cpp display_stubs.cpp ddraw.cpp d3d.cpp d3d11.cpp dxgi.cpp d3dcompiler.cpp
  d3dx10.cpp dsound.cpp dshow.cpp mp3_source.cpp dinput.cpp dinput_effect.cpp dinput_joystick.cpp xinput.cpp qmixer.cpp
  mss32.cpp redbook.cpp avi.cpp waveout.cpp fmod.cpp soundlib.cpp galaxy_stub.cpp riff.cpp bink.cpp video_frame.cpp mf_media.cpp
  mf.cpp timed_player.cpp weanetr.cpp d3d9.cpp d3d9_raster.cpp d3d9_shader.cpp d3d9_shadergen.cpp host_d9_default.cpp
  d3dx9.cpp)

add_library(recomp_dx OBJECT ${POP_DX_SOURCES})
target_link_libraries(recomp_dx PUBLIC recomp_runtime)
pop_link_video(recomp_dx)
target_compile_options(recomp_dx PRIVATE ${POP_WARN_HOST})
pop_optimize(recomp_dx 2)

# The same shims with every host callback a strong no-op: a binary linking
# these can neither draw nor play sound. The parity fixture and the profile
# tests use it.
add_library(recomp_dx_null OBJECT ${POP_DX_SOURCES})
target_link_libraries(recomp_dx_null PUBLIC recomp_runtime)
pop_link_video(recomp_dx_null)
target_compile_definitions(recomp_dx_null PUBLIC RECOMP_NULL_HOST)
target_compile_options(recomp_dx_null PRIVATE ${POP_WARN_HOST})
pop_optimize(recomp_dx_null 1)

if(NOT IOS AND NOT ANDROID)
# Real-clock media probe, explicitly invoked; no output device in ordinary CTest.
add_executable(timed_player_probe tests/timed_player_probe.cpp mf_media.cpp timed_player.cpp video_frame.cpp)
target_link_libraries(timed_player_probe PRIVATE recomp_platform)
pop_link_video(timed_player_probe)
target_compile_options(timed_player_probe PRIVATE ${POP_WARN_HOST})
pop_optimize(timed_player_probe 2)
pop_test_binary(timed_player_probe)
# Explicit rendered-audio timing probe (render tap + in-process output-rate
# changes, always restored). Manual only: never registered with CTest.
if(APPLE)
  add_executable(timed_player_render_probe tests/timed_player_render_probe.cpp mf_media.cpp
                                           timed_player.cpp video_frame.cpp)
  target_link_libraries(timed_player_render_probe PRIVATE recomp_platform)
  pop_link_video(timed_player_render_probe)
  target_compile_options(timed_player_render_probe PRIVATE ${POP_WARN_HOST})
  pop_optimize(timed_player_render_probe 2)
  pop_test_binary(timed_player_render_probe)
endif()
# host_api.h is a C header too. The C11 consumer is an object the DX tests
# link; the header is also parsed as C++17 on its own.
add_library(host_api_header_test OBJECT tests/host_api_header_test.c)
target_include_directories(host_api_header_test PRIVATE ${POP_ROOT})
target_compile_options(host_api_header_test PRIVATE -Wall -Wextra)
pop_optimize(host_api_header_test 1)
add_test(NAME host_api_header_check
  COMMAND ${CMAKE_CXX_COMPILER} -std=c++17 -O1 -Wall -Wextra -Wno-unused-parameter
          -I${POP_ROOT} -fsyntax-only -x c++ ${CMAKE_CURRENT_SOURCE_DIR}/host_api.h)
set_tests_properties(host_api_header_check PROPERTIES LABELS nogame)

add_executable(dx_tests tests/dx_tests.cpp
  ${POP_ROOT}/runtime/tests/stub_recomp_call.cpp
  $<TARGET_OBJECTS:host_api_header_test>)
target_link_libraries(dx_tests PRIVATE recomp_platform recomp_dx recomp_runtime)
target_compile_options(dx_tests PRIVATE ${POP_WARN_HOST})
pop_optimize(dx_tests 1)
pop_test_binary(dx_tests)
add_test(NAME dx_tests COMMAND dx_tests WORKING_DIRECTORY ${POP_ROOT})
set_tests_properties(dx_tests PROPERTIES LABELS nogame)

add_executable(d3d9_tests tests/d3d9_tests.cpp
  ${POP_ROOT}/runtime/tests/stub_recomp_call.cpp)
target_link_libraries(d3d9_tests PRIVATE recomp_platform recomp_dx recomp_runtime)
target_compile_options(d3d9_tests PRIVATE ${POP_WARN_HOST})
pop_optimize(d3d9_tests 1)
pop_test_binary(d3d9_tests)
add_test(NAME d3d9_tests COMMAND d3d9_tests WORKING_DIRECTORY ${POP_ROOT})
set_tests_properties(d3d9_tests PROPERTIES LABELS nogame)

# The virtual pad through the DirectInput joystick and XInput. Its own binary
# because it defines the host_pad_* callbacks strongly, which dx_tests must not.
add_executable(pad_tests tests/pad_tests.cpp
  ${POP_ROOT}/runtime/tests/stub_recomp_call.cpp)
target_link_libraries(pad_tests PRIVATE recomp_platform recomp_dx recomp_runtime)
target_compile_options(pad_tests PRIVATE ${POP_WARN_HOST})
pop_optimize(pad_tests 1)
pop_test_binary(pad_tests)
add_test(NAME pad_tests COMMAND pad_tests WORKING_DIRECTORY ${POP_ROOT})
set_tests_properties(pad_tests PROPERTIES LABELS nogame)

# No strong host callback anywhere: the weak defaults must carry the link.
# page_overlay.cpp because every host links it beside boot.cpp; the dispatch
# stub because no generated code is present.
add_executable(null_host_link tests/null_host_link.cpp
  ${POP_ROOT}/host/page_overlay.cpp
  ${POP_ROOT}/runtime/tests/stub_recomp_call.cpp)
target_include_directories(null_host_link PRIVATE ${POP_ROOT})
target_link_libraries(null_host_link PRIVATE recomp_platform recomp_runtime recomp_dx host_common)
target_compile_options(null_host_link PRIVATE ${POP_WARN_HOST})
pop_optimize(null_host_link 1)
pop_test_binary(null_host_link)
add_test(NAME null_host_link COMMAND null_host_link WORKING_DIRECTORY ${POP_ROOT})
set_tests_properties(null_host_link PROPERTIES LABELS nogame)
endif()
