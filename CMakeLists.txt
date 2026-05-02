cmake_minimum_required(VERSION 3.20)

project(geometrize_py LANGUAGES CXX)

find_package(Python COMPONENTS Interpreter Development.Module REQUIRED)
find_package(pybind11 CONFIG REQUIRED)

pybind11_add_module(_native MODULE python/geometrize_py/native_bindings.cpp)
target_compile_features(_native PRIVATE cxx_std_17)

install(TARGETS _native DESTINATION geometrize_py)
