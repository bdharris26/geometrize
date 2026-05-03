#include <algorithm>
#include <cstdint>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "geometrize/bitmap/bitmap.h"
#include "geometrize/exporter/bitmapdataexporter.h"
#include "geometrize/exporter/shapeserializer.h"
#include "geometrize/runner/imagerunner.h"
#include "geometrize/runner/imagerunneroptions.h"
#include "geometrize/shape/shapetypes.h"
#include "geometrize/shaperesult.h"

namespace py = pybind11;

namespace
{

const std::map<std::string, geometrize::ShapeTypes> SHAPE_NAMES{
    {"rectangle", geometrize::ShapeTypes::RECTANGLE},
    {"rotated_rectangle", geometrize::ShapeTypes::ROTATED_RECTANGLE},
    {"triangle", geometrize::ShapeTypes::TRIANGLE},
    {"ellipse", geometrize::ShapeTypes::ELLIPSE},
    {"rotated_ellipse", geometrize::ShapeTypes::ROTATED_ELLIPSE},
    {"circle", geometrize::ShapeTypes::CIRCLE},
    {"line", geometrize::ShapeTypes::LINE},
    {"quadratic_bezier", geometrize::ShapeTypes::QUADRATIC_BEZIER},
    {"polyline", geometrize::ShapeTypes::POLYLINE},
};

int readInt(const py::dict& options, const char* key, const int fallback, const int lower, const int upper)
{
    if(!options.contains(key)) {
        return fallback;
    }
    const int value{py::cast<int>(options[key])};
    return (std::max)(lower, (std::min)(upper, value));
}

std::string normalizeShapeName(std::string value)
{
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
        if(c == '-' || c == ' ') {
            return '_';
        }
        return static_cast<char>(std::tolower(c));
    });
    return value;
}

std::uint32_t shapeMaskFromOptions(const py::dict& options)
{
    if(!options.contains("shape_types")) {
        return static_cast<std::uint32_t>(geometrize::ShapeTypes::ELLIPSE);
    }

    const py::object values{options["shape_types"]};
    if(py::isinstance<py::int_>(values)) {
        return py::cast<std::uint32_t>(values);
    }

    std::uint32_t mask{0U};
    for(const py::handle item : values) {
        if(py::isinstance<py::int_>(item)) {
            mask |= py::cast<std::uint32_t>(item);
            continue;
        }

        const std::string name{normalizeShapeName(py::cast<std::string>(item))};
        const auto found{SHAPE_NAMES.find(name)};
        if(found == SHAPE_NAMES.end()) {
            throw std::invalid_argument("Unknown shape type: " + name);
        }
        mask |= static_cast<std::uint32_t>(found->second);
    }

    if(mask == 0U) {
        throw std::invalid_argument("At least one shape type is required");
    }
    return mask;
}

std::string shapeName(const geometrize::ShapeTypes type)
{
    for(const auto& item : SHAPE_NAMES) {
        if(item.second == type) {
            return item.first;
        }
    }
    return "unknown";
}

py::dict rgbaToDict(const geometrize::rgba color)
{
    py::dict out;
    out["r"] = static_cast<int>(color.r);
    out["g"] = static_cast<int>(color.g);
    out["b"] = static_cast<int>(color.b);
    out["a"] = static_cast<int>(color.a);
    return out;
}

py::dict shapeDataToDict(const geometrize::ShapeTypes type, const std::vector<float>& data)
{
    py::dict out;
    switch(type) {
    case geometrize::ShapeTypes::RECTANGLE:
        out["x1"] = data.at(0); out["y1"] = data.at(1); out["x2"] = data.at(2); out["y2"] = data.at(3);
        break;
    case geometrize::ShapeTypes::ROTATED_RECTANGLE:
        out["x1"] = data.at(0); out["y1"] = data.at(1); out["x2"] = data.at(2); out["y2"] = data.at(3); out["angle"] = data.at(4);
        break;
    case geometrize::ShapeTypes::TRIANGLE:
        out["x1"] = data.at(0); out["y1"] = data.at(1); out["x2"] = data.at(2); out["y2"] = data.at(3); out["x3"] = data.at(4); out["y3"] = data.at(5);
        break;
    case geometrize::ShapeTypes::ELLIPSE:
        out["x"] = data.at(0); out["y"] = data.at(1); out["rx"] = data.at(2); out["ry"] = data.at(3);
        break;
    case geometrize::ShapeTypes::ROTATED_ELLIPSE:
        out["x"] = data.at(0); out["y"] = data.at(1); out["rx"] = data.at(2); out["ry"] = data.at(3); out["angle"] = data.at(4);
        break;
    case geometrize::ShapeTypes::CIRCLE:
        out["x"] = data.at(0); out["y"] = data.at(1); out["r"] = data.at(2);
        break;
    case geometrize::ShapeTypes::LINE:
        out["x1"] = data.at(0); out["y1"] = data.at(1); out["x2"] = data.at(2); out["y2"] = data.at(3);
        break;
    case geometrize::ShapeTypes::QUADRATIC_BEZIER:
        out["x1"] = data.at(0); out["y1"] = data.at(1); out["cx"] = data.at(2); out["cy"] = data.at(3); out["x2"] = data.at(4); out["y2"] = data.at(5);
        break;
    case geometrize::ShapeTypes::POLYLINE:
        {
            py::list points;
            for(std::size_t i = 0; i + 1 < data.size(); i += 2) {
                py::tuple point(2);
                point[0] = data[i];
                point[1] = data[i + 1];
                points.append(point);
            }
            out["points"] = points;
        }
        break;
    default:
        throw std::invalid_argument("Unsupported shape type");
    }
    return out;
}

py::dict shapeResultToDict(const geometrize::ShapeResult& result)
{
    const geometrize::ShapeTypes type{result.shape->getType()};
    py::dict out;
    out["score"] = result.score;
    out["color"] = rgbaToDict(result.color);
    out["type"] = shapeName(type);
    out["type_id"] = static_cast<std::uint32_t>(type);
    out["data"] = shapeDataToDict(type, geometrize::getRawShapeData(*result.shape));
    return out;
}

py::dict runRgba(const int width, const int height, const py::bytes& rgba, const py::dict& options)
{
    if(width <= 0 || height <= 0) {
        throw std::invalid_argument("Image dimensions must be positive");
    }

    const std::string raw{rgba};
    const std::size_t expected{static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 4U};
    if(raw.size() != expected) {
        throw std::invalid_argument("RGBA byte count does not match image dimensions");
    }

    std::vector<std::uint8_t> pixels(raw.begin(), raw.end());
    geometrize::Bitmap target(static_cast<std::uint32_t>(width), static_cast<std::uint32_t>(height), pixels);
    geometrize::ImageRunner runner(target);

    geometrize::ImageRunnerOptions runnerOptions;
    runnerOptions.shapeTypes = static_cast<geometrize::ShapeTypes>(shapeMaskFromOptions(options));
    runnerOptions.alpha = static_cast<std::uint8_t>(readInt(options, "alpha", 128, 1, 255));
    runnerOptions.shapeCount = static_cast<std::uint32_t>(readInt(options, "shape_count", 50, 1, 500));
    runnerOptions.maxShapeMutations = static_cast<std::uint32_t>(readInt(options, "mutations", 100, 1, 1000));
    runnerOptions.seed = static_cast<std::uint32_t>(readInt(options, "seed", 9001, 0, 2147483647));
    runnerOptions.maxThreads = static_cast<std::uint32_t>(readInt(options, "max_threads", 0, 0, 128));

    const int steps{readInt(options, "steps", 1, 1, 2000)};
    std::vector<geometrize::ShapeResult> shapes;
    int attempts{0};
    for(int i = 0; i < steps; ++i) {
        std::vector<geometrize::ShapeResult> stepShapes{runner.step(runnerOptions)};
        attempts++;
        shapes.insert(shapes.end(), stepShapes.begin(), stepShapes.end());
    }

    py::list shapeList;
    for(const geometrize::ShapeResult& shape : shapes) {
        shapeList.append(shapeResultToDict(shape));
    }

    const std::string outPixels{geometrize::exporter::exportBitmapData(runner.getCurrent())};
    py::dict out;
    out["width"] = width;
    out["height"] = height;
    out["rgba"] = py::bytes(outPixels);
    out["shapes"] = shapeList;
    out["attempts"] = attempts;
    return out;
}

}

PYBIND11_MODULE(_native, module)
{
    module.doc() = "Native bindings for the Geometrize image runner.";
    module.def("is_available", []() { return true; });
    module.def("run_rgba", &runRgba, py::arg("width"), py::arg("height"), py::arg("rgba"), py::arg("options"));
}
