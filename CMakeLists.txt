cmake_minimum_required(VERSION 3.20)

project(geometrize_py LANGUAGES CXX)

find_package(Python COMPONENTS Interpreter Development.Module REQUIRED)
find_package(pybind11 CONFIG REQUIRED)
find_package(Threads REQUIRED)

set(GEOMETRIZE_CORE_SOURCES
    lib/geometrize/geometrize/geometrize/bitmap/bitmap.cpp
    lib/geometrize/geometrize/geometrize/bitmap/rgba.cpp
    lib/geometrize/geometrize/geometrize/rasterizer/rasterizer.cpp
    lib/geometrize/geometrize/geometrize/rasterizer/scanline.cpp
    lib/geometrize/geometrize/geometrize/runner/imagerunner.cpp
    lib/geometrize/geometrize/geometrize/shape/circle.cpp
    lib/geometrize/geometrize/geometrize/shape/ellipse.cpp
    lib/geometrize/geometrize/geometrize/shape/line.cpp
    lib/geometrize/geometrize/geometrize/shape/polyline.cpp
    lib/geometrize/geometrize/geometrize/shape/quadraticbezier.cpp
    lib/geometrize/geometrize/geometrize/shape/rectangle.cpp
    lib/geometrize/geometrize/geometrize/shape/rotatedellipse.cpp
    lib/geometrize/geometrize/geometrize/shape/rotatedrectangle.cpp
    lib/geometrize/geometrize/geometrize/shape/shapefactory.cpp
    lib/geometrize/geometrize/geometrize/shape/shapemutator.cpp
    lib/geometrize/geometrize/geometrize/shape/shapetypes.cpp
    lib/geometrize/geometrize/geometrize/shape/triangle.cpp
    lib/geometrize/geometrize/geometrize/commonutil.cpp
    lib/geometrize/geometrize/geometrize/core.cpp
    lib/geometrize/geometrize/geometrize/model.cpp
    lib/geometrize/geometrize/geometrize/state.cpp
)

pybind11_add_module(_native MODULE
    python/geometrize_py/native_bindings.cpp
    ${GEOMETRIZE_CORE_SOURCES}
)
target_include_directories(_native PRIVATE lib/geometrize/geometrize)
target_compile_features(_native PRIVATE cxx_std_17)
target_link_libraries(_native PRIVATE Threads::Threads)

if(MSVC)
    target_compile_options(_native PRIVATE /bigobj)
endif()

install(TARGETS _native DESTINATION geometrize_py)
