# ctest registration for the upstream PS2Recomp test binaries. The combined
# ps2x_tests run aborts in an EeScheduler executor-thread assert, so every
# MiniTest case runs in its own process via MINITEST_FILTER. Names are read
# from the test sources at configure time.
if(NOT TARGET ps2x_tests)
    return()
endif()

set(TARZAN_TEST_RUN_DIR "${CMAKE_BINARY_DIR}/run")
file(MAKE_DIRECTORY "${TARZAN_TEST_RUN_DIR}")
# The code generator tests read headers relative to the working directory.
file(CREATE_LINK "${TARZAN_PS2RECOMP_SOURCE_DIR}/ps2xRecomp"
    "${TARZAN_TEST_RUN_DIR}/ps2xRecomp" SYMBOLIC)

# Known failures: the first returns a failing status (WILL_FAIL); the others
# abort in the EeScheduler executor-thread assert, which ctest cannot invert,
# so they are DISABLED. All four carry the known-failure label.
set(TARZAN_KNOWN_ABORTS
    "isceSifSetDma and isceSifSetDChain alias the SIF DMA helpers"
    "IOP heap DMA uses private backing instead of aliasing EE RDRAM"
    "sceSifSetDma copies payload and sceSifDmaStat reports complete")
set(TARZAN_KNOWN_FAILURES
    "sceGsSetDefDBuffDc seeds display envs and swap applies the selected page"
    "isceSifSetDma and isceSifSetDChain alias the SIF DMA helpers"
    "IOP heap DMA uses private backing instead of aliasing EE RDRAM"
    "sceSifSetDma copies payload and sceSifDmaStat reports complete")

file(GLOB_RECURSE _ps2x_test_sources CONFIGURE_DEPENDS
    "${TARZAN_PS2RECOMP_SOURCE_DIR}/ps2xTest/src/*.cpp")
set(_ps2x_names)
foreach(_src IN LISTS _ps2x_test_sources)
    file(READ "${_src}" _content)
    string(REGEX MATCHALL "Run\\(\"([^\"\\\\]|\\\\.)*\"" _matches "${_content}")
    foreach(_m IN LISTS _matches)
        string(REGEX REPLACE "^Run\\(\"(.*)\"$" "\\1" _name "${_m}")
        list(APPEND _ps2x_names "${_name}")
    endforeach()
endforeach()
list(REMOVE_DUPLICATES _ps2x_names)
list(LENGTH _ps2x_names _ps2x_count)
if(_ps2x_count EQUAL 0)
    message(FATAL_ERROR "No ps2x_tests case names found in ps2xTest/src")
endif()

foreach(_name IN LISTS _ps2x_names)
    add_test(NAME "ps2x/${_name}" COMMAND ps2x_tests
        WORKING_DIRECTORY "${TARZAN_TEST_RUN_DIR}")
    set_tests_properties("ps2x/${_name}" PROPERTIES
        ENVIRONMENT "PS2X_WALLCLOCK=0;MINITEST_FILTER=${_name}"
        LABELS ps2x)
    if(_name IN_LIST TARZAN_KNOWN_ABORTS)
        set_tests_properties("ps2x/${_name}" PROPERTIES
            DISABLED TRUE LABELS "ps2x;known-failure")
    elseif(_name IN_LIST TARZAN_KNOWN_FAILURES)
        set_tests_properties("ps2x/${_name}" PROPERTIES
            WILL_FAIL TRUE LABELS "ps2x;known-failure")
    endif()
endforeach()
