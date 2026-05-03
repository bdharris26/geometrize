cmake_minimum_required(VERSION 3.18)

project(geometrize_py LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Python COMPONENTS Interpreter Development.Module REQUIRED)
find_package(pybind11 CONFIG REQUIRED)

file(GLOB_RECURSE GEOMETRIZE_CORE_SOURCES CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/lib/geometrize/geometrize/geometrize/*.cpp"
)

pybind11_add_module(_native
    python/geometrize_py/native_bindings.cpp
    ${GEOMETRIZE_CORE_SOURCES}
)

target_include_directories(_native PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/lib/geometrize/geometrize"
)

if(MSVC)
    target_compile_options(_native PRIVATE /bigobj)
else()
    target_compile_options(_native PRIVATE -pthread)
    target_link_libraries(_native PRIVATE pthread)
endif()

install(TARGETS _native
    LIBRARY DESTINATION geometrize_py
    RUNTIME DESTINATION geometrize_py
    ARCHIVE DESTINATION geometrize_py
)
