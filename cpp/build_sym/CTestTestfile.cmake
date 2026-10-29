# CMake generated Testfile for 
# Source directory: C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp
# Build directory: C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/build_sym
# 
# This file includes the relevant testing commands required for 
# testing this directory and lists subdirectories to be tested as well.
add_test([=[core_test]=] "C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/build_sym/core_test.exe")
set_tests_properties([=[core_test]=] PROPERTIES  _BACKTRACE_TRIPLES "C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/CMakeLists.txt;253;add_test;C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/CMakeLists.txt;0;")
add_test([=[storage_test]=] "C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/build_sym/storage_test.exe")
set_tests_properties([=[storage_test]=] PROPERTIES  _BACKTRACE_TRIPLES "C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/CMakeLists.txt;257;add_test;C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/CMakeLists.txt;0;")
add_test([=[safety_test]=] "C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/build_sym/safety_test.exe")
set_tests_properties([=[safety_test]=] PROPERTIES  _BACKTRACE_TRIPLES "C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/CMakeLists.txt;262;add_test;C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/CMakeLists.txt;0;")
add_test([=[agent_test]=] "C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/build_sym/agent_test.exe")
set_tests_properties([=[agent_test]=] PROPERTIES  _BACKTRACE_TRIPLES "C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/CMakeLists.txt;267;add_test;C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/CMakeLists.txt;0;")
add_test([=[speech_test]=] "C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/build_sym/speech_test.exe")
set_tests_properties([=[speech_test]=] PROPERTIES  _BACKTRACE_TRIPLES "C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/CMakeLists.txt;272;add_test;C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/CMakeLists.txt;0;")
add_test([=[executor_test]=] "C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/build_sym/executor_test.exe")
set_tests_properties([=[executor_test]=] PROPERTIES  _BACKTRACE_TRIPLES "C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/CMakeLists.txt;278;add_test;C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/CMakeLists.txt;0;")
add_test([=[net_test]=] "C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/build_sym/net_test.exe")
set_tests_properties([=[net_test]=] PROPERTIES  _BACKTRACE_TRIPLES "C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/CMakeLists.txt;283;add_test;C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/CMakeLists.txt;0;")
add_test([=[ai_test]=] "C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/build_sym/ai_test.exe")
set_tests_properties([=[ai_test]=] PROPERTIES  _BACKTRACE_TRIPLES "C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/CMakeLists.txt;288;add_test;C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/CMakeLists.txt;0;")
add_test([=[worker_test]=] "C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/build_sym/worker_test.exe")
set_tests_properties([=[worker_test]=] PROPERTIES  _BACKTRACE_TRIPLES "C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/CMakeLists.txt;293;add_test;C:/Users/cem134/Desktop/C.O.R/Jarvis - Kopya/cpp/CMakeLists.txt;0;")
subdirs("_deps/nlohmann_json-build")
subdirs("_deps/cpr-build")
