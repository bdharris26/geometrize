#include <algorithm>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "geometrize/bitmap/bitmap.h"
#include "geometrize/runner/imagerunner.h"
#include "geometrize/runner/imagerunneroptions.h"
#include "geometrize/shape/circle.h"
#include "geometrize/shape/ellipse.h"
#include "geometrize/shape/line.h"
#include "geometrize/shape/polyline.h"
#include "geometrize/shape/quadraticbezier.h"
#include "geometrize/shape/rectangle.h"
#include "geometrize/shape/rotatedellipse.h"
#include "geometrize/shape/rotatedrectangle.h"
#include "geometrize/shape/shape.h"
#include "geometrize/shape/shapetypes.h"
#include "geometrize/shape/triangle.h"
#include "geometrize/shaperesult.h"

namespace py = pybind11;

namespace {

std::uint32_t uint_from_options(const py::dict& options, const char* name, const std::uint32_t defaultValue)
{
    if(!options.contains(name)) {
        return defaultValue;
    }
    const int value = py::cast<int>(options[name]);
    if(value < 0) {
        throw std::invalid_argument(std::string{name} + " must be greater than or equal to zero");
    }
    return static_cast<std::uint32_t>(value);
}

std::uint8_t alpha_from_options(const py::dict& options)
{
    const int value = options.contains("alpha") ? py::cast<int>(options["alpha"]) : 128;
    if(value < 0 || value > 255) {
        throw std::invalid_argument("alpha must be between 0 and 255");
    }
    return static_cast<std::uint8_t>(value);
}

std::string normalize_shape_name(std::string value)
{
    std::replace(value.begin(), value.end(), '-', '_');
    std::transform(value.begin(), value.end(), value.begin(), [](const unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return value;
}

geometrize::ShapeTypes shape_type_from_name(const std::string& name)
{
    const std::string normalized = normalize_shape_name(name);
    if(normalized == "rectangle") {
        return geometrize::ShapeTypes::RECTANGLE;
    }
    if(normalized == "rotated_rectangle") {
        return geometrize::ShapeTypes::ROTATED_RECTANGLE;
    }
    if(normalized == "triangle") {
        return geometrize::ShapeTypes::TRIANGLE;
    }
    if(normalized == "ellipse") {
        return geometrize::ShapeTypes::ELLIPSE;
    }
    if(normalized == "rotated_ellipse") {
        return geometrize::ShapeTypes::ROTATED_ELLIPSE;
    }
    if(normalized == "circle") {
        return geometrize::ShapeTypes::CIRCLE;
    }
    if(normalized == "line") {
        return geometrize::ShapeTypes::LINE;
    }
    if(normalized == "quadratic_bezier") {
        return geometrize::ShapeTypes::QUADRATIC_BEZIER;
    }
    if(normalized == "polyline") {
        return geometrize::ShapeTypes::POLYLINE;
    }
    throw std::invalid_argument("unknown shape type: " + name);
}

const char* shape_name(const geometrize::ShapeTypes type)
{
    switch(type) {
    case geometrize::ShapeTypes::RECTANGLE:
        return "rectangle";
    case geometrize::ShapeTypes::ROTATED_RECTANGLE:
        return "rotated_rectangle";
    case geometrize::ShapeTypes::TRIANGLE:
        return "triangle";
    case geometrize::ShapeTypes::ELLIPSE:
        return "ellipse";
    case geometrize::ShapeTypes::ROTATED_ELLIPSE:
        return "rotated_ellipse";
    case geometrize::ShapeTypes::CIRCLE:
        return "circle";
    case geometrize::ShapeTypes::LINE:
        return "line";
    case geometrize::ShapeTypes::QUADRATIC_BEZIER:
        return "quadratic_bezier";
    case geometrize::ShapeTypes::POLYLINE:
        return "polyline";
    default:
        return "unknown";
    }
}

py::dict color_to_dict(const geometrize::rgba& color)
{
    py::dict data;
    data["r"] = color.r;
    data["g"] = color.g;
    data["b"] = color.b;
    data["a"] = color.a;
    return data;
}

py::dict shape_data_to_dict(const geometrize::Shape& shape)
{
    py::dict data;
    switch(shape.getType()) {
    case geometrize::ShapeTypes::RECTANGLE: {
        const auto& s = static_cast<const geometrize::Rectangle&>(shape);
        data["x1"] = s.m_x1;
        data["y1"] = s.m_y1;
        data["x2"] = s.m_x2;
        data["y2"] = s.m_y2;
        return data;
    }
    case geometrize::ShapeTypes::ROTATED_RECTANGLE: {
        const auto& s = static_cast<const geometrize::RotatedRectangle&>(shape);
        data["x1"] = s.m_x1;
        data["y1"] = s.m_y1;
        data["x2"] = s.m_x2;
        data["y2"] = s.m_y2;
        data["angle"] = s.m_angle;
        return data;
    }
    case geometrize::ShapeTypes::TRIANGLE: {
        const auto& s = static_cast<const geometrize::Triangle&>(shape);
        data["x1"] = s.m_x1;
        data["y1"] = s.m_y1;
        data["x2"] = s.m_x2;
        data["y2"] = s.m_y2;
        data["x3"] = s.m_x3;
        data["y3"] = s.m_y3;
        return data;
    }
    case geometrize::ShapeTypes::ELLIPSE: {
        const auto& s = static_cast<const geometrize::Ellipse&>(shape);
        data["x"] = s.m_x;
        data["y"] = s.m_y;
        data["rx"] = s.m_rx;
        data["ry"] = s.m_ry;
        return data;
    }
    case geometrize::ShapeTypes::ROTATED_ELLIPSE: {
        const auto& s = static_cast<const geometrize::RotatedEllipse&>(shape);
        data["x"] = s.m_x;
        data["y"] = s.m_y;
        data["rx"] = s.m_rx;
        data["ry"] = s.m_ry;
        data["angle"] = s.m_angle;
        return data;
    }
    case geometrize::ShapeTypes::CIRCLE: {
        const auto& s = static_cast<const geometrize::Circle&>(shape);
        data["x"] = s.m_x;
        data["y"] = s.m_y;
        data["r"] = s.m_r;
        return data;
    }
    case geometrize::ShapeTypes::LINE: {
        const auto& s = static_cast<const geometrize::Line&>(shape);
        data["x1"] = s.m_x1;
        data["y1"] = s.m_y1;
        data["x2"] = s.m_x2;
        data["y2"] = s.m_y2;
        return data;
    }
    case geometrize::ShapeTypes::QUADRATIC_BEZIER: {
        const auto& s = static_cast<const geometrize::QuadraticBezier&>(shape);
        data["x1"] = s.m_x1;
        data["y1"] = s.m_y1;
        data["cx"] = s.m_cx;
        data["cy"] = s.m_cy;
        data["x2"] = s.m_x2;
        data["y2"] = s.m_y2;
        return data;
    }
    case geometrize::ShapeTypes::POLYLINE: {
        const auto& s = static_cast<const geometrize::Polyline&>(shape);
        py::list points;
        for(const auto& point : s.m_points) {
            py::list item;
            item.append(point.first);
            item.append(point.second);
            points.append(item);
        }
        data["points"] = points;
        return data;
    }
    default:
        throw std::runtime_error("unsupported shape type");
    }
}

py::dict shape_result_to_dict(const geometrize::ShapeResult& result)
{
    py::dict data;
    const geometrize::ShapeTypes type = result.shape->getType();
    data["score"] = result.score;
    data["color"] = color_to_dict(result.color);
    data["type"] = shape_name(type);
    data["type_id"] = static_cast<std::uint32_t>(type);
    data["data"] = shape_data_to_dict(*result.shape);
    return data;
}

geometrize::ImageRunnerOptions runner_options_from_dict(const py::dict& options)
{
    geometrize::ImageRunnerOptions runnerOptions;
    const std::string shape = options.contains("shape") ? py::cast<std::string>(options["shape"]) : "ellipse";
    runnerOptions.shapeTypes = shape_type_from_name(shape);
    runnerOptions.alpha = alpha_from_options(options);
    runnerOptions.shapeCount = uint_from_options(options, "candidate_shape_count", 50);
    runnerOptions.maxShapeMutations = uint_from_options(options, "max_shape_mutations", 100);
    runnerOptions.seed = uint_from_options(options, "seed", 9001);
    runnerOptions.maxThreads = uint_from_options(options, "max_threads", 0);
    return runnerOptions;
}

py::dict run_rgba(const std::uint32_t width, const std::uint32_t height, const py::bytes rgbaBytes, const py::dict options)
{
    if(width == 0 || height == 0) {
        throw std::invalid_argument("width and height must be greater than zero");
    }

    const std::string rawData = rgbaBytes;
    const std::size_t expectedLength = static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 4U;
    if(rawData.size() != expectedLength) {
        throw std::invalid_argument("rgba data length must equal width * height * 4");
    }

    const std::uint32_t targetShapeCount = uint_from_options(options, "count", 1);
    if(targetShapeCount == 0) {
        throw std::invalid_argument("count must be greater than zero");
    }
    const std::uint32_t maxAttempts = uint_from_options(options, "max_attempts", std::max<std::uint32_t>(targetShapeCount * 20U, targetShapeCount + 20U));
    geometrize::ImageRunnerOptions runnerOptions = runner_options_from_dict(options);

    std::vector<std::uint8_t> pixels(rawData.begin(), rawData.end());
    geometrize::Bitmap target{width, height, pixels};
    geometrize::ImageRunner runner{target};
    std::vector<geometrize::ShapeResult> acceptedShapes;
    acceptedShapes.reserve(targetShapeCount);
    std::uint32_t attempts = 0;

    {
        py::gil_scoped_release release;
        while(acceptedShapes.size() < targetShapeCount && attempts < maxAttempts) {
            std::vector<geometrize::ShapeResult> results = runner.step(runnerOptions);
            for(const geometrize::ShapeResult& result : results) {
                if(acceptedShapes.size() < targetShapeCount) {
                    acceptedShapes.push_back(result);
                }
            }
            ++attempts;
        }
    }

    const std::vector<std::uint8_t> current = runner.getCurrent().copyData();
    py::list shapes;
    for(const geometrize::ShapeResult& shape : acceptedShapes) {
        shapes.append(shape_result_to_dict(shape));
    }

    py::dict result;
    result["width"] = width;
    result["height"] = height;
    result["rgba"] = py::bytes(reinterpret_cast<const char*>(current.data()), current.size());
    result["shapes"] = shapes;
    result["attempts"] = attempts;
    return result;
}

}

PYBIND11_MODULE(_native, module)
{
    module.doc() = "Native bindings for the Geometrize image runner.";
    module.def("is_available", []() {
        return true;
    });
    module.def("run_rgba", &run_rgba, py::arg("width"), py::arg("height"), py::arg("rgba"), py::arg("options"));
}
