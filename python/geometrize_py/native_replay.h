#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include <pybind11/pybind11.h>

#include "geometrize/bitmap/rgba.h"
#include "geometrize/rasterizer/rasterizer.h"
#include "geometrize/shape/circle.h"
#include "geometrize/shape/ellipse.h"
#include "geometrize/shape/line.h"
#include "geometrize/shape/polyline.h"
#include "geometrize/shape/quadraticbezier.h"
#include "geometrize/shape/rectangle.h"
#include "geometrize/shape/rotatedellipse.h"
#include "geometrize/shape/rotatedrectangle.h"
#include "geometrize/shape/triangle.h"

namespace geometrize_py
{
namespace py = pybind11;

// Keep these native safety ceilings aligned with contracts.py. They apply to
// replay only; fitting continues to use the original upstream factory.
constexpr int MAX_REPLAY_DIMENSION{8192};
constexpr std::size_t MAX_REPLAY_SHAPES{100000};
constexpr std::size_t MAX_REPLAY_POINTS{10000};
constexpr std::size_t MAX_REPLAY_TOTAL_POINTS{1000000};
constexpr std::uint64_t MAX_REPLAY_RASTER_POINTS{2000000};
constexpr std::uint64_t MAX_REPLAY_WORK{1000000000};

struct ReplayShape
{
    std::shared_ptr<geometrize::Shape> shape;
    geometrize::rgba color;
};

struct ReplayScene
{
    geometrize::rgba background;
    std::vector<ReplayShape> shapes;
    std::uint64_t scratchBytes{64U * 1024U};
    std::uint64_t work{0};
};

inline void consumeReplayWork(std::uint64_t& work, const std::uint64_t amount, const std::uint64_t limit)
{
    if(work > limit || amount > limit - work) {
        throw std::invalid_argument("Prefix exceeds native replay work limit; restore fewer shapes");
    }
    work += amount;
}

inline int replayInteger(const py::handle value, const int lower, const int upper, const char* label)
{
    if(py::isinstance<py::bool_>(value) || !py::isinstance<py::int_>(value)) {
        throw std::invalid_argument(std::string(label) + " must be an integer");
    }
    int overflow{0};
    const auto number{PyLong_AsLongLongAndOverflow(value.ptr(), &overflow)};
    if(PyErr_Occurred()) {
        throw py::error_already_set();
    }
    if(overflow || number < lower || number > upper) {
        throw std::invalid_argument(std::string(label) + " is outside its allowed range");
    }
    return static_cast<int>(number);
}

inline float replayNumber(const py::handle value, const double limit, const char* label)
{
    if(py::isinstance<py::bool_>(value) || !(py::isinstance<py::int_>(value) || py::isinstance<py::float_>(value))) {
        throw std::invalid_argument(std::string(label) + " must be a finite number");
    }
    const double number{py::isinstance<py::int_>(value) ? PyLong_AsDouble(value.ptr()) : PyFloat_AsDouble(value.ptr())};
    if(PyErr_Occurred()) {
        py::error_already_set error;
        if(error.matches(PyExc_OverflowError)) {
            throw std::invalid_argument(std::string(label) + " must be a finite number");
        }
        throw error;
    }
    if(!std::isfinite(number) || std::abs(number) > limit) {
        throw std::invalid_argument(std::string(label) + " exceeds native replay bounds");
    }
    return static_cast<float>(number);
}

inline geometrize::rgba replayColor(const py::handle value)
{
    int channels[4];
    if(py::isinstance<py::dict>(value)) {
        const auto color{py::reinterpret_borrow<py::dict>(value)};
        const char* names[]{"r", "g", "b", "a"};
        for(int i = 0; i < 4; ++i) {
            if(!color.contains(names[i])) {
                throw std::invalid_argument("Replay color requires four RGBA channels");
            }
            channels[i] = replayInteger(color[names[i]], 0, 255, "Replay color channel");
        }
    } else if(py::isinstance<py::list>(value) || py::isinstance<py::tuple>(value)) {
        const auto color{py::reinterpret_borrow<py::sequence>(value)};
        if(color.size() != 4) {
            throw std::invalid_argument("Replay color requires four RGBA channels");
        }
        for(int i = 0; i < 4; ++i) {
            channels[i] = replayInteger(color[i], 0, 255, "Replay color channel");
        }
    } else {
        throw std::invalid_argument("Replay color requires four RGBA channels");
    }
    return geometrize::rgba{static_cast<std::uint8_t>(channels[0]), static_cast<std::uint8_t>(channels[1]),
        static_cast<std::uint8_t>(channels[2]), static_cast<std::uint8_t>(channels[3])};
}

inline std::uint64_t replayPathPoints(const std::vector<std::pair<float, float>>& points, const bool closed)
{
    if(points.empty()) {
        return 0;
    }
    // Bresenham allocates each whole edge before upstream clipping. The last
    // singleton in an open path is a conservative bound for line/polyline use.
    std::uint64_t total{closed ? 0U : 1U};
    const std::size_t edges{closed ? points.size() : points.size() - 1};
    for(std::size_t i = 0; i < edges; ++i) {
        const auto next{(i + 1) % points.size()};
        const auto dx{std::abs(static_cast<std::int64_t>(points[i].first) - static_cast<std::int64_t>(points[next].first))};
        const auto dy{std::abs(static_cast<std::int64_t>(points[i].second) - static_cast<std::int64_t>(points[next].second))};
        total += static_cast<std::uint64_t>((std::max)(dx, dy)) + 1U;
    }
    return total;
}

inline std::uint64_t replayFilledPixels(const std::vector<std::pair<float, float>>& points, const int width, const int height)
{
    if(points.empty()) {
        return 0;
    }
    float xMin{points.front().first};
    float xMax{xMin};
    float yMin{points.front().second};
    float yMax{yMin};
    for(const auto& point : points) {
        xMin = (std::min)(xMin, point.first);
        xMax = (std::max)(xMax, point.first);
        yMin = (std::min)(yMin, point.second);
        yMax = (std::max)(yMax, point.second);
    }
    const auto span = [](const float lower, const float upper, const int axis) {
        const auto clamp = [axis](const double value) { return (std::max)(0.0, (std::min)(axis - 1.0, value)); };
        return static_cast<std::uint64_t>(clamp(std::ceil(upper)) - clamp(std::floor(lower))) + 1U;
    };
    return span(xMin, xMax, width) * span(yMin, yMax, height);
}

inline std::pair<std::uint64_t, std::uint64_t> replayRasterCost(const geometrize::Shape& shape, const int width, const int height)
{
    // Return a scratch-point bound and an additional work bound. Each model
    // replay copies only covered before pixels, then draws and scores them.
    switch(shape.getType()) {
    case geometrize::ShapeTypes::CIRCLE: {
        const auto& s{static_cast<const geometrize::Circle&>(shape)};
        const auto diameter{2U * static_cast<std::uint64_t>(s.m_r) + 1U};
        return {diameter, diameter * diameter + 3U * replayFilledPixels(
            {{s.m_x - s.m_r, s.m_y - s.m_r}, {s.m_x + s.m_r, s.m_y + s.m_r}}, width, height)};
    }
    case geometrize::ShapeTypes::ELLIPSE: {
        const auto& s{static_cast<const geometrize::Ellipse&>(shape)};
        const auto rows{2U * static_cast<std::uint64_t>(std::ceil(s.m_ry))};
        return {rows, rows + 3U * replayFilledPixels(
            {{s.m_x - s.m_rx, s.m_y - s.m_ry}, {s.m_x + s.m_rx, s.m_y + s.m_ry}}, width, height)};
    }
    case geometrize::ShapeTypes::RECTANGLE: {
        const auto& s{static_cast<const geometrize::Rectangle&>(shape)};
        const auto rows{static_cast<std::uint64_t>(std::abs(static_cast<std::int64_t>(s.m_y1) -
            static_cast<std::int64_t>(s.m_y2))) + 1U};
        return {rows, rows + 3U * replayFilledPixels({{s.m_x1, s.m_y1}, {s.m_x2, s.m_y2}}, width, height)};
    }
    case geometrize::ShapeTypes::LINE: {
        const auto& s{static_cast<const geometrize::Line&>(shape)};
        const auto count{replayPathPoints({{s.m_x1, s.m_y1}, {s.m_x2, s.m_y2}}, false)};
        return {count, count * 4U};
    }
    case geometrize::ShapeTypes::POLYLINE: {
        const auto count{replayPathPoints(static_cast<const geometrize::Polyline&>(shape).m_points, false)};
        return {count, count * 4U};
    }
    case geometrize::ShapeTypes::QUADRATIC_BEZIER: {
        const auto& s{static_cast<const geometrize::QuadraticBezier&>(shape)};
        std::vector<std::pair<float, float>> points;
        for(std::uint32_t i = 0; i <= 20; ++i) {
            const float t{static_cast<float>(i) / 20.0F};
            const float tp{1.0F - t};
            points.emplace_back(tp * (tp * s.m_x1 + t * s.m_cx) + t * (tp * s.m_cx + t * s.m_x2),
                tp * (tp * s.m_y1 + t * s.m_cy) + t * (tp * s.m_cy + t * s.m_y2));
        }
        const auto count{replayPathPoints(points, false)};
        return {count, count * 4U};
    }
    default:
        break;
    }
    std::vector<std::pair<float, float>> points;
    if(shape.getType() == geometrize::ShapeTypes::TRIANGLE) {
        const auto& s{static_cast<const geometrize::Triangle&>(shape)};
        points = {{s.m_x1, s.m_y1}, {s.m_x2, s.m_y2}, {s.m_x3, s.m_y3}};
    } else if(shape.getType() == geometrize::ShapeTypes::ROTATED_RECTANGLE) {
        points = geometrize::getCornerPoints(static_cast<const geometrize::RotatedRectangle&>(shape));
    } else {
        points = geometrize::getPointsOnRotatedEllipse(static_cast<const geometrize::RotatedEllipse&>(shape), 20);
    }
    const auto count{replayPathPoints(points, true)};
    // A polygon's scanlines can cover its whole bounding box after clipping.
    return {count, count + 3U * replayFilledPixels(points, width, height)};
}

inline ReplayScene parseReplay(const int width, const int height, const py::handle background,
    const py::handle rawShapes, const std::map<std::string, geometrize::ShapeTypes>& names,
    const std::uint64_t maxWork = MAX_REPLAY_WORK)
{
    if(width < 1 || height < 1 || width > MAX_REPLAY_DIMENSION || height > MAX_REPLAY_DIMENSION) {
        throw std::invalid_argument("Replay dimensions exceed the working image limit");
    }
    if(!py::isinstance<py::list>(rawShapes)) {
        throw std::invalid_argument("Replay shapes must be an array");
    }
    const auto shapes{py::reinterpret_borrow<py::list>(rawShapes)};
    if(shapes.size() > MAX_REPLAY_SHAPES) {
        throw std::invalid_argument("Too many replay shapes");
    }
    ReplayScene replay{replayColor(background), {}};
    replay.shapes.reserve(shapes.size());
    std::size_t totalPoints{0};
    // Five full-score scans, the initial average-color scan, and both
    // background fills are bounded before allocating either runner.
    consumeReplayWork(replay.work, 8U * static_cast<std::uint64_t>(width) * height, maxWork);
    const double geometryLimit{16.0 * (std::max)(width, height)};
    for(const auto raw : shapes) {
        if(!py::isinstance<py::dict>(raw)) {
            throw std::invalid_argument("Replay shape must be an object");
        }
        const auto item{py::reinterpret_borrow<py::dict>(raw)};
        if(!item.contains("type") || !py::isinstance<py::str>(item["type"]) ||
            !item.contains("data") || !py::isinstance<py::dict>(item["data"]) || !item.contains("color")) {
            throw std::invalid_argument("Replay shape requires type, data, and color");
        }
        const auto found{names.find(py::cast<std::string>(item["type"]))};
        if(found == names.end()) {
            throw std::invalid_argument("Unsupported replay shape type");
        }
        const auto data{py::reinterpret_borrow<py::dict>(item["data"])};
        const auto number = [&data, geometryLimit](const char* name, const double limit = 0.0) {
            if(!data.contains(name)) {
                throw std::invalid_argument(std::string("Replay shape requires ") + name);
            }
            return replayNumber(data[name], limit ? limit : geometryLimit, name);
        };
        const auto radius = [&number](const char* name, const int axis) {
            const float value{number(name, (std::max)(32, axis))};
            if(value < 0.0F) {
                throw std::invalid_argument(std::string(name) + " cannot be negative");
            }
            return value;
        };
        std::shared_ptr<geometrize::Shape> shape;
        switch(found->second) {
        case geometrize::ShapeTypes::RECTANGLE:
            shape = std::make_shared<geometrize::Rectangle>(number("x1"), number("y1"), number("x2"), number("y2"));
            break;
        case geometrize::ShapeTypes::ROTATED_RECTANGLE:
            shape = std::make_shared<geometrize::RotatedRectangle>(number("x1"), number("y1"), number("x2"), number("y2"), number("angle", 1e9));
            break;
        case geometrize::ShapeTypes::TRIANGLE:
            shape = std::make_shared<geometrize::Triangle>(number("x1"), number("y1"), number("x2"), number("y2"), number("x3"), number("y3"));
            break;
        case geometrize::ShapeTypes::ELLIPSE: {
            auto ellipse{std::make_shared<geometrize::Ellipse>(number("x"), number("y"), radius("rx", width), radius("ry", height))};
            if(ellipse->m_ry > 0.0F && !std::isfinite(ellipse->m_rx / ellipse->m_ry)) {
                throw std::invalid_argument("Ellipse aspect exceeds native replay bounds");
            }
            shape = std::move(ellipse);
            break;
        }
        case geometrize::ShapeTypes::ROTATED_ELLIPSE:
            shape = std::make_shared<geometrize::RotatedEllipse>(number("x"), number("y"), radius("rx", width), radius("ry", height), number("angle", 1e9));
            break;
        case geometrize::ShapeTypes::CIRCLE:
            shape = std::make_shared<geometrize::Circle>(number("x"), number("y"), radius("r", width));
            break;
        case geometrize::ShapeTypes::LINE:
            shape = std::make_shared<geometrize::Line>(number("x1"), number("y1"), number("x2"), number("y2"));
            break;
        case geometrize::ShapeTypes::QUADRATIC_BEZIER:
            shape = std::make_shared<geometrize::QuadraticBezier>(number("cx"), number("cy"), number("x1"), number("y1"), number("x2"), number("y2"));
            break;
        case geometrize::ShapeTypes::POLYLINE: {
            if(!data.contains("points") || !py::isinstance<py::list>(data["points"])) {
                throw std::invalid_argument("Polyline points must be an array");
            }
            const auto rawPoints{py::reinterpret_borrow<py::list>(data["points"])};
            totalPoints += rawPoints.size();
            if(rawPoints.size() > MAX_REPLAY_POINTS || totalPoints > MAX_REPLAY_TOTAL_POINTS) {
                throw std::invalid_argument("Too many replay polyline points");
            }
            std::vector<std::pair<float, float>> points;
            points.reserve(rawPoints.size());
            for(const auto rawPoint : rawPoints) {
                if(!(py::isinstance<py::list>(rawPoint) || py::isinstance<py::tuple>(rawPoint)) || py::len(rawPoint) != 2) {
                    throw std::invalid_argument("Polyline point requires x and y");
                }
                const auto point{py::reinterpret_borrow<py::sequence>(rawPoint)};
                points.emplace_back(replayNumber(point[0], geometryLimit, "point x"), replayNumber(point[1], geometryLimit, "point y"));
            }
            shape = std::make_shared<geometrize::Polyline>(points);
            break;
        }
        default:
            throw std::invalid_argument("Unsupported replay shape type");
        }
        const auto [scratchPoints, rasterWork]{replayRasterCost(*shape, width, height)};
        if(scratchPoints > MAX_REPLAY_RASTER_POINTS) {
            throw std::invalid_argument("Shape rasterization exceeds native replay limits");
        }
        consumeReplayWork(replay.work, rasterWork, maxWork);
        replay.scratchBytes = (std::max)(replay.scratchBytes, scratchPoints * 128U);
        shape->rasterize = [width, height](const geometrize::Shape& s) {
            // Match ImageRunner's full-image bounds, including its existing
            // last-edge behavior. Export rasterization is intentionally separate.
            return geometrize::rasterize(s, 0, 0, width - 1, height - 1);
        };
        replay.shapes.push_back({std::move(shape), replayColor(item["color"])});
    }
    return replay;
}

}
