#include <algorithm>
#include <cctype>
#include <cstdint>
#include <cmath>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "geometrize/bitmap/bitmap.h"
#include "geometrize/core.h"
#include "geometrize/exporter/bitmapdataexporter.h"
#include "geometrize/exporter/shapeserializer.h"
#include "geometrize/runner/imagerunner.h"
#include "geometrize/runner/imagerunneroptions.h"
#include "geometrize/shape/shape.h"
#include "geometrize/shape/shapetypes.h"
#include "geometrize/shaperesult.h"
#include "native_focus.h"
#include "native_palette.h"
#include "native_replay.h"

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

geometrize::ImageRunnerOptions runnerOptionsFromDict(const py::dict& options)
{
    geometrize::ImageRunnerOptions runnerOptions;
    runnerOptions.shapeTypes = static_cast<geometrize::ShapeTypes>(shapeMaskFromOptions(options));
    runnerOptions.alpha = static_cast<std::uint8_t>(readInt(options, "alpha", 128, 1, 255));
    runnerOptions.shapeCount = static_cast<std::uint32_t>(readInt(options, "shape_count", 64, 1, 512));
    runnerOptions.maxShapeMutations = static_cast<std::uint32_t>(readInt(options, "mutations", 128, 1, 2048));
    runnerOptions.seed = static_cast<std::uint32_t>(readInt(options, "seed", 9001, 0, 2147483647));
    runnerOptions.maxThreads = static_cast<std::uint32_t>(readInt(options, "max_threads", 0, 0, 128));
    return runnerOptions;
}

std::optional<geometrize_py::Focus> focusFromDict(const py::dict& options)
{
    if(!options.contains("focus") || options["focus"].is_none()) {
        return std::nullopt;
    }
    const auto value{options["focus"]};
    if(!py::isinstance<py::dict>(value)) {
        throw std::invalid_argument("focus must be an object or null");
    }
    const auto focus{py::cast<py::dict>(value)};
    for(const auto item : focus) {
        const auto name{py::cast<std::string>(item.first)};
        if(name != "x" && name != "y" && name != "radius" && name != "strength") {
            throw std::invalid_argument("focus only accepts x, y, radius, and strength");
        }
    }
    const auto number = [&focus](const char* name, const double fallback, const double lower, const double upper) {
        if(!focus.contains(name)) {
            if(std::string(name) == "x" || std::string(name) == "y") {
                throw std::invalid_argument("focus requires x and y");
            }
            return fallback;
        }
        const auto raw{focus[name]};
        if(py::isinstance<py::bool_>(raw) || !(py::isinstance<py::int_>(raw) || py::isinstance<py::float_>(raw))) {
            throw std::invalid_argument(std::string("focus.") + name + " must be a finite number");
        }
        const double number{py::cast<double>(raw)};
        if(!std::isfinite(number) || number < lower || number > upper) {
            throw std::invalid_argument(std::string("focus.") + name + " is outside its finite range");
        }
        return number;
    };
    return geometrize_py::Focus{number("x", 0.0, 0.0, 1.0), number("y", 0.0, 0.0, 1.0),
        number("radius", 0.2, 0.01, 1.0), number("strength", 0.75, 0.0, 1.0)};
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

class RunnerSession
{
public:
    RunnerSession(const int width, const int height, const py::bytes& rgba, const py::dict& options) :
        m_width{width},
        m_height{height},
        m_pixels{bytesToPixels(width, height, rgba)},
        m_target{static_cast<std::uint32_t>(width), static_cast<std::uint32_t>(height), m_pixels},
        m_runner{std::make_unique<geometrize::ImageRunner>(m_target)},
        m_options{runnerOptionsFromDict(options)},
        m_focus{focusFromDict(options)},
        m_palette{geometrize_py::paletteFromDict(options)},
        m_background{m_runner->getCurrent().getPixel(0, 0)},
        m_initialScore{geometrize::core::differenceFull(m_target, m_runner->getCurrent())},
        m_score{m_initialScore},
        m_attempts{0}
    {}

    py::dict step(const py::dict& options = py::dict())
    {
        if(!options.empty()) {
            const auto nextOptions{runnerOptionsFromDict(options)};
            const auto nextFocus{focusFromDict(options)};
            const auto nextPalette{geometrize_py::paletteFromDict(options)};
            m_options = nextOptions;
            m_focus = nextFocus;
            m_palette = nextPalette;
        }

        std::vector<geometrize::ShapeResult> stepShapes;
        {
            py::gil_scoped_release release;
            auto options{m_options};
            options.seed += m_rngBaseOffset;
            if(m_palette && m_palette->strength > 0.0) {
                if(!m_exactError) {
                    m_exactError = geometrize_py::paletteFullError(m_target, m_runner->getCurrent());
                    const auto actualScore{geometrize_py::paletteScore(*m_exactError, m_target)};
                    if(m_score != actualScore) {
                        // Returning from ordinary fitting may carry rounded or
                        // overlapping-mask partial scores. Correct the baseline
                        // once without changing the next worker's random seed.
                        m_runner = std::make_unique<geometrize::ImageRunner>(m_target, m_runner->getCurrent());
                        m_rngBaseOffset = m_rngOffset;
                        options.seed = m_options.seed + m_rngBaseOffset;
                    }
                    m_score = actualScore;
                }
                std::function<std::shared_ptr<geometrize::Shape>()> creator;
                if(m_focus && m_focus->strength > 0.0) {
                    creator = geometrize_py::focusedShapeCreator(m_options.shapeTypes, m_width, m_height, *m_focus);
                } else if(m_width == 1 || m_height == 1) {
                    creator = geometrize_py::safeShapeCreator(m_options.shapeTypes, m_width, m_height);
                }
                auto result{geometrize_py::paletteStep(*m_runner, options, *m_palette, creator, *m_exactError)};
                m_rngOffset += effectiveWorkers();
                if(result.callbackError) {
                    std::rethrow_exception(result.callbackError);
                }
                if(result.replacement) {
                    m_runner = std::move(result.replacement);
                    m_rngBaseOffset = m_rngOffset;
                }
                if(!result.shapes.empty()) {
                    *m_exactError -= static_cast<std::uint64_t>(-result.delta);
                }
                m_score = geometrize_py::paletteScore(*m_exactError, m_target);
                stepShapes = std::move(result.shapes);
            } else if(m_focus && m_focus->strength > 0.0) {
                stepShapes = m_runner->step(options,
                    geometrize_py::focusedShapeCreator(m_options.shapeTypes, m_width, m_height, *m_focus));
            } else if(m_width == 1 || m_height == 1) {
                stepShapes = m_runner->step(options,
                    geometrize_py::safeShapeCreator(m_options.shapeTypes, m_width, m_height));
            } else {
                // Preserve the original factory and RNG path when disabled.
                stepShapes = m_runner->step(options);
            }
            if(!m_palette || m_palette->strength == 0.0) {
                m_rngOffset += effectiveWorkers();
                m_exactError.reset();
            }
        }

        m_attempts++;

        if(!stepShapes.empty()) {
            m_score = stepShapes.back().score;
        }

        py::list shapes;
        for(const geometrize::ShapeResult& shape : stepShapes) {
            shapes.append(shapeResultToDict(shape));
        }

        py::dict out;
        out["attempt"] = m_attempts;
        out["shapes"] = shapes;
        return out;
    }

    py::bytes currentRgba() const
    {
        return py::bytes(geometrize::exporter::exportBitmapData(m_runner->getCurrent()));
    }

    std::vector<geometrize::ShapeResult> replay(const geometrize_py::ReplayScene& scene, const std::uint64_t maxWork)
    {
        // Called only by restoreRgba on a fresh, unpublished session. No fit
        // attempts or random-generator offsets are advanced during replay.
        py::gil_scoped_release release;
        geometrize::Bitmap current{static_cast<std::uint32_t>(m_width), static_cast<std::uint32_t>(m_height), scene.background};
        geometrize::Bitmap before{current};
        m_background = scene.background;
        m_initialScore = geometrize::core::differenceFull(m_target, current);
        double score{m_initialScore};
        std::uint64_t work{scene.work};
        const auto pixels{static_cast<std::uint64_t>(m_width) * m_height};
        std::vector<geometrize::ShapeResult> results;
        results.reserve(scene.shapes.size());
        for(const auto& item : scene.shapes) {
            const auto lines{item.shape->rasterize(*item.shape)};
            geometrize::copyLines(before, current, lines);
            geometrize::drawLines(current, item.color, lines);
            score = geometrize::core::differencePartial(m_target, before, current, score, lines);
            // Rounded partial SSE can underflow at a perfect imported fit.
            if(!std::isfinite(score) || score < 0.0 || score > 1.0) {
                // Imported tiny shapes can trigger this repeatedly. Debit the
                // whole-image scan before running it on this unpublished model.
                geometrize_py::consumeReplayWork(work, pixels, maxWork);
                score = geometrize::core::differenceFull(m_target, current);
            }
            results.push_back({score, item.color, item.shape});
        }
        m_runner = std::make_unique<geometrize::ImageRunner>(m_target, current);
        // The new model and Python score both start from actual replayed pixels,
        // including any accumulated partial-score rounding in imported scenes.
        m_score = geometrize::core::differenceFull(m_target, m_runner->getCurrent());
        return results;
    }

    int width() const
    {
        return m_width;
    }

    int height() const
    {
        return m_height;
    }

    int attempts() const
    {
        return m_attempts;
    }

    double initialScore() const
    {
        return m_initialScore;
    }

    double score() const
    {
        return m_score;
    }

    py::tuple background() const
    {
        return py::make_tuple(static_cast<int>(m_background.r), static_cast<int>(m_background.g),
            static_cast<int>(m_background.b), static_cast<int>(m_background.a));
    }

private:
    std::uint32_t effectiveWorkers() const
    {
        const auto hardware{std::thread::hardware_concurrency()};
        return m_options.maxThreads ? m_options.maxThreads : (hardware ? hardware : 4U);
    }

    static std::vector<std::uint8_t> bytesToPixels(const int width, const int height, const py::bytes& rgba)
    {
        if(width <= 0 || height <= 0) {
            throw std::invalid_argument("Image dimensions must be positive");
        }

        const std::string raw{rgba};
        const std::size_t expected{static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 4U};
        if(raw.size() != expected) {
            throw std::invalid_argument("RGBA byte count does not match image dimensions");
        }

        return std::vector<std::uint8_t>(raw.begin(), raw.end());
    }

    int m_width;
    int m_height;
    std::vector<std::uint8_t> m_pixels;
    geometrize::Bitmap m_target;
    std::unique_ptr<geometrize::ImageRunner> m_runner;
    geometrize::ImageRunnerOptions m_options;
    std::optional<geometrize_py::Focus> m_focus;
    std::optional<geometrize_py::Palette> m_palette;
    std::optional<std::uint64_t> m_exactError;
    // Mirrors the upstream modulo-uint32 worker offset across rare score
    // repairs. A fresh replay/session starts all three offsets from zero.
    std::uint32_t m_rngOffset{0};
    std::uint32_t m_rngBaseOffset{0};
    geometrize::rgba m_background;
    double m_initialScore;
    double m_score;
    int m_attempts;
};

py::dict runRgba(const int width, const int height, const py::bytes& rgba, const py::dict& options)
{
    const int steps{readInt(options, "steps", 1, 1, 4096)};
    RunnerSession session(width, height, rgba, options);
    py::list shapeList;
    for(int i = 0; i < steps; ++i) {
        const py::dict step{session.step()};
        const py::list stepShapes{step["shapes"]};
        for(const py::handle shape : stepShapes) {
            shapeList.append(shape);
        }
    }

    py::dict out;
    out["width"] = width;
    out["height"] = height;
    out["rgba"] = session.currentRgba();
    out["shapes"] = shapeList;
    out["attempts"] = session.attempts();
    return out;
}

py::dict restoreRgba(const py::handle rawWidth, const py::handle rawHeight, const py::bytes& rgba,
    const py::dict& options, const py::handle background, const py::handle shapes, const py::handle rawMaxWork)
{
    const int width{geometrize_py::replayInteger(rawWidth, 1, geometrize_py::MAX_REPLAY_DIMENSION, "Replay width")};
    const int height{geometrize_py::replayInteger(rawHeight, 1, geometrize_py::MAX_REPLAY_DIMENSION, "Replay height")};
    const int maxWork{geometrize_py::replayInteger(rawMaxWork, 1, geometrize_py::MAX_REPLAY_WORK, "Replay max_work")};
    // Complete geometry, color, scratch, and work validation precedes drawing.
    const auto scene{geometrize_py::parseReplay(width, height, background, shapes, SHAPE_NAMES, maxWork)};
    auto session{std::make_unique<RunnerSession>(width, height, rgba, options)};
    const auto results{session->replay(scene, maxWork)};
    py::list restored;
    for(const auto& result : results) {
        restored.append(shapeResultToDict(result));
    }
    py::dict out;
    out["session"] = py::cast(std::move(session));
    out["shapes"] = restored;
    return out;
}

}

PYBIND11_MODULE(_native, module)
{
    module.doc() = "Native bindings for the Geometrize image runner.";
    module.def("is_available", []() { return true; });
    module.attr("palette_api_version") = 1;
    module.def("run_rgba", &runRgba, py::arg("width"), py::arg("height"), py::arg("rgba"), py::arg("options"));
    module.def("replay_memory", [](const py::handle rawWidth, const py::handle rawHeight,
        const py::handle background, const py::handle shapes, const py::handle rawMaxWork) {
        const int width{geometrize_py::replayInteger(rawWidth, 1, geometrize_py::MAX_REPLAY_DIMENSION, "Replay width")};
        const int height{geometrize_py::replayInteger(rawHeight, 1, geometrize_py::MAX_REPLAY_DIMENSION, "Replay height")};
        const int maxWork{geometrize_py::replayInteger(rawMaxWork, 1, geometrize_py::MAX_REPLAY_WORK, "Replay max_work")};
        return geometrize_py::parseReplay(width, height, background, shapes, SHAPE_NAMES, maxWork).scratchBytes;
    }, py::arg("width"), py::arg("height"), py::arg("background"), py::arg("shapes"),
        py::arg("max_work") = geometrize_py::MAX_REPLAY_WORK);
    module.def("restore_rgba", &restoreRgba, py::arg("width"), py::arg("height"), py::arg("rgba"),
        py::arg("options"), py::arg("background"), py::arg("shapes"), py::arg("max_work") = geometrize_py::MAX_REPLAY_WORK);
    py::class_<RunnerSession>(module, "RunnerSession")
        .def(py::init<int, int, const py::bytes&, const py::dict&>(), py::arg("width"), py::arg("height"), py::arg("rgba"), py::arg("options"))
        .def("step", &RunnerSession::step, py::arg("options") = py::dict())
        .def("current_rgba", &RunnerSession::currentRgba)
        .def_property_readonly("width", &RunnerSession::width)
        .def_property_readonly("height", &RunnerSession::height)
        .def_property_readonly("attempts", &RunnerSession::attempts)
        .def_property_readonly("background", &RunnerSession::background)
        .def_property_readonly("initial_score", &RunnerSession::initialScore)
        .def_property_readonly("score", &RunnerSession::score);
}
