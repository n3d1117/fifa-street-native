# ctest registration for the upstream PS2Recomp test binaries. ps2x_tests runs
# every MiniTest case in one process; on failure ctest --output-on-failure
# (enabled in the tests preset) prints the failing case names.
if(NOT TARGET ps2x_tests)
    return()
endif()

set(TARZAN_TEST_RUN_DIR "${CMAKE_BINARY_DIR}/run")
file(MAKE_DIRECTORY "${TARZAN_TEST_RUN_DIR}")
# The code generator tests read headers relative to the working directory.
file(CREATE_LINK "${TARZAN_PS2RECOMP_SOURCE_DIR}/ps2xRecomp"
    "${TARZAN_TEST_RUN_DIR}/ps2xRecomp" SYMBOLIC)

add_test(NAME ps2x_tests COMMAND ps2x_tests WORKING_DIRECTORY "${TARZAN_TEST_RUN_DIR}")
set_tests_properties(ps2x_tests PROPERTIES
    ENVIRONMENT "PS2X_WALLCLOCK=0"
    FAIL_REGULAR_EXPRESSION "\\[Failed\\]"
    LABELS ps2x)
