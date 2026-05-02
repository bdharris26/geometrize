#include <pybind11/pybind11.h>

namespace py = pybind11;

PYBIND11_MODULE(_native, module)
{
    module.doc() = "Native bindings for the Geometrize image runner.";
    module.def("is_available", []() {
        return true;
    });
}
