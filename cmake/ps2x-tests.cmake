# ctest registration for the upstream PS2Recomp test binaries, built as targets
# of the one project against the same ps2_runtime library the game links.
# ps2x_tests runs every MiniTest case in one process; on failure ctest
# --output-on-failure (enabled in the tests preset) prints the failing case names.
if(NOT TARGET ps2x_tests)
    return()
endif()

# ps2_runtime is built with TARZAN_USE_METAL=1 exactly as in the game, but the
# native window, GS bridge and gamepad are replaced by inert fakes so tests need
# no GPU or window.
add_library(tarzanTestMetalSeam STATIC "${TARZAN_ROOT}/host/test-metal-seam.cpp")
target_include_directories(tarzanTestMetalSeam PRIVATE "${TARZAN_ROOT}/host")
target_compile_features(tarzanTestMetalSeam PRIVATE cxx_std_20)
foreach(test_target IN ITEMS ps2_test_lib ps2x_tests)
    target_compile_definitions(${test_target} PRIVATE TARZAN_USE_METAL=1)
    target_compile_options(${test_target} PRIVATE -fno-fast-math -ffp-contract=off)
endforeach()
target_link_libraries(ps2x_tests PRIVATE tarzanTestMetalSeam)

set(TARZAN_TEST_RUN_DIR "${CMAKE_BINARY_DIR}/run")
file(MAKE_DIRECTORY "${TARZAN_TEST_RUN_DIR}")
# The code generator tests read headers relative to the working directory.
file(CREATE_LINK "${TARZAN_PS2RECOMP_SOURCE_DIR}/ps2xRecomp"
    "${TARZAN_TEST_RUN_DIR}/ps2xRecomp" SYMBOLIC)

add_test(NAME ps2x_tests COMMAND ps2x_tests WORKING_DIRECTORY "${TARZAN_TEST_RUN_DIR}")
set_tests_properties(ps2x_tests PROPERTIES
    FAIL_REGULAR_EXPRESSION "\\[Failed\\]"
    LABELS ps2x)
