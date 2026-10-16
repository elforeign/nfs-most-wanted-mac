# The plugins this game ships: its bundled core mods.
add_custom_target(core_mods
  COMMAND ${Python3_EXECUTABLE} ${POP_ROOT}/tools/recomp/build_core.py
          --source ${CMAKE_CURRENT_SOURCE_DIR}/core
          --dest ${POP_OUT}/mods/core --cc ${CMAKE_C_COMPILER}
  WORKING_DIRECTORY ${POP_ROOT}
  COMMENT "Installing core mods into build/recomp/mods/core"
  VERBATIM)

add_custom_target(plugins DEPENDS core_mods)

# Private movie colour diagnostics. No game data or device output in CTest.
if(NOT IOS AND NOT ANDROID)
  add_executable(usa_movie_color_tests
    ${RECOMP_GAME_DIR}/native/tests/usa_movie_color_tests.cpp
    ${POP_ROOT}/dx/mf_media.cpp ${POP_ROOT}/dx/video_frame.cpp ${POP_ROOT}/dx/timed_player.cpp)
  target_link_libraries(usa_movie_color_tests PRIVATE recomp_platform)
  pop_link_video(usa_movie_color_tests)
  target_compile_options(usa_movie_color_tests PRIVATE ${POP_WARN_HOST})
  pop_optimize(usa_movie_color_tests 2)
  pop_test_binary(usa_movie_color_tests)
  add_test(NAME usa_movie_color_tests COMMAND usa_movie_color_tests)
  set_tests_properties(usa_movie_color_tests PROPERTIES LABELS nogame)
endif()

# Bounded Xenos research tool: absent from ordinary builds and app packaging.
if(DEFINED ENV{NFSMW_XENIA_SOURCE_DIR} AND NOT "$ENV{NFSMW_XENIA_SOURCE_DIR}" STREQUAL "")
  add_subdirectory(${RECOMP_GAME_DIR}/tools/xenia_research ${CMAKE_BINARY_DIR}/xenia-research)
endif()

# Surface/impact rumble through the real host arbitration and motor router.
if(NOT IOS AND NOT ANDROID)
  add_executable(rumble_feedback_tests ${RECOMP_GAME_DIR}/native/tests/rumble_feedback_tests.cpp
    ${POP_ROOT}/host/controls/vpad.cpp ${POP_ROOT}/host/controls/haptics.cpp
    ${POP_ROOT}/host/tests/mods_core_setting_stub.cpp)   # 3.0.4: vpad.cpp reads the deadzone settings
  target_compile_options(rumble_feedback_tests PRIVATE ${POP_WARN_HOST})
  pop_optimize(rumble_feedback_tests 2)
  pop_test_binary(rumble_feedback_tests)
  add_test(NAME rumble_feedback_tests COMMAND rumble_feedback_tests)
  set_tests_properties(rumble_feedback_tests PROPERTIES LABELS nogame)
  # Test67: Xbox scenery-light data and render-entry tags (pure part, synthetic data).
  add_executable(scenery_lights_tests ${RECOMP_GAME_DIR}/native/tests/scenery_lights_tests.cpp)
  target_compile_options(scenery_lights_tests PRIVATE ${POP_WARN_HOST})
  pop_optimize(scenery_lights_tests 1)
  pop_test_binary(scenery_lights_tests)
  add_test(NAME scenery_lights_tests COMMAND scenery_lights_tests)
  set_tests_properties(scenery_lights_tests PROPERTIES LABELS nogame)
  # Test70: new-career cash ledger and the helicopter takedown fix (pure).
  add_executable(career_fixes_tests ${RECOMP_GAME_DIR}/native/tests/career_fixes_tests.cpp)
  target_compile_options(career_fixes_tests PRIVATE ${POP_WARN_HOST})
  pop_optimize(career_fixes_tests 1)
  pop_test_binary(career_fixes_tests)
  add_test(NAME career_fixes_tests COMMAND career_fixes_tests)
  set_tests_properties(career_fixes_tests PROPERTIES LABELS nogame)
  add_executable(xenon_particle_tests ${RECOMP_GAME_DIR}/native/tests/xenon_particle_tests.cpp)
  target_compile_options(xenon_particle_tests PRIVATE ${POP_WARN_HOST})
  pop_optimize(xenon_particle_tests 1)
  pop_test_binary(xenon_particle_tests)
  add_test(NAME xenon_particle_tests COMMAND xenon_particle_tests)
  set_tests_properties(xenon_particle_tests PROPERTIES LABELS nogame)
endif()
