cmake_minimum_required(VERSION 3.16)
project(legacy_cpp_parser CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(parser src/parser.cpp)
target_include_directories(parser PUBLIC include)

add_executable(parse_cli src/main.cpp)
target_link_libraries(parse_cli parser)

enable_testing()
add_executable(test_parser tests/test_parser.cpp)
target_link_libraries(test_parser parser)
add_test(NAME parses_key_value COMMAND test_parser parse)
add_test(NAME known_failure_trims_spaces COMMAND test_parser trim)
