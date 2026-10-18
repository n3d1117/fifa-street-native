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

if(TARZAN_GAME_READY)
    add_executable(tarzanFileIoGuestTest
        "${TARZAN_ROOT}/tests/fileio-guest-test.cpp"
        "${TARZAN_GENERATED_DIR}/_sceFsSemInit_0x2250b8.cpp"
        "${TARZAN_GENERATED_DIR}/_sceFsSigSema_0x225138.cpp"
        "${TARZAN_GENERATED_DIR}/CreateSema_0x2213a0.cpp"
        "${TARZAN_GENERATED_DIR}/SignalSema_0x2213c0.cpp"
        $<TARGET_OBJECTS:ps2_test_function_table>)
    target_include_directories(tarzanFileIoGuestTest PRIVATE "${TARZAN_GENERATED_DIR}"
        $<TARGET_PROPERTY:ps2x_tests,INCLUDE_DIRECTORIES>)
    target_compile_definitions(tarzanFileIoGuestTest PRIVATE
        $<TARGET_PROPERTY:ps2x_tests,COMPILE_DEFINITIONS>)
    target_compile_options(tarzanFileIoGuestTest PRIVATE -fno-fast-math -ffp-contract=off)
    target_link_libraries(tarzanFileIoGuestTest PRIVATE ps2_runtime tarzanTestMetalSeam)
    add_test(NAME fileio_guest COMMAND tarzanFileIoGuestTest)

    add_executable(tarzanRpcPacketGuestTest
        "${TARZAN_ROOT}/tests/rpc-packet-guest-test.cpp"
        "${TARZAN_GENERATED_DIR}/_sceRpcFreePacket_0x223eb8.cpp"
        "${TARZAN_GENERATED_DIR}/_sceRpcGetFPacket_0x223ed8.cpp"
        "${TARZAN_GENERATED_DIR}/_sceRpcGetFPacket2_0x223f08.cpp"
        $<TARGET_OBJECTS:ps2_test_function_table>)
    target_include_directories(tarzanRpcPacketGuestTest PRIVATE "${TARZAN_GENERATED_DIR}"
        $<TARGET_PROPERTY:ps2x_tests,INCLUDE_DIRECTORIES>)
    target_compile_definitions(tarzanRpcPacketGuestTest PRIVATE
        $<TARGET_PROPERTY:ps2x_tests,COMPILE_DEFINITIONS>)
    target_compile_options(tarzanRpcPacketGuestTest PRIVATE -fno-fast-math -ffp-contract=off)
    target_link_libraries(tarzanRpcPacketGuestTest PRIVATE ps2_runtime tarzanTestMetalSeam)
    add_test(NAME rpc_packet_guest COMMAND tarzanRpcPacketGuestTest)
endif()
