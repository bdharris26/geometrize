#pragma once

#include <algorithm>
#include <cmath>
#include <functional>
#include <memory>
#include <utility>

#include "geometrize/commonutil.h"
#include "geometrize/exporter/shapeserializer.h"
#include "geometrize/shape/shape.h"
#include "geometrize/shape/shapefactory.h"
#include "geometrize/shape/shapemutator.h"
#include "geometrize/shape/shapetypes.h"

namespace geometrize_py
{

struct Focus
{
    double x;
    double y;
    double radius;
    double strength;
};

inline double randomUnit()
{
    // Use the same seeded, thread-local generator as upstream shape fitting.
    constexpr int resolution{1 << 24};
    return geometrize::commonutil::randomRange(0, resolution - 1) / static_cast<double>(resolution);
}

inline std::pair<float, float> placementCenter(const geometrize::Shape& shape)
{
    const auto data{geometrize::getRawShapeData(shape)};
    std::size_t count{data.size()};
    switch(shape.getType()) {
    case geometrize::ShapeTypes::CIRCLE:
    case geometrize::ShapeTypes::ELLIPSE:
    case geometrize::ShapeTypes::ROTATED_ELLIPSE:
        count = 2; // Radii and angles are not placement coordinates.
        break;
    case geometrize::ShapeTypes::ROTATED_RECTANGLE:
        count = 4;
        break;
    default:
        break;
    }
    float x{0.0F};
    float y{0.0F};
    for(std::size_t i = 0; i < count; i += 2) {
        x += data[i];
        y += data[i + 1];
    }
    const float points{static_cast<float>(count / 2)};
    return {x / points, y / points};
}

inline std::pair<float, float> focusPoint(const Focus focus, const int width, const int height)
{
    const double cx{focus.x * (width - 1)};
    const double cy{focus.y * (height - 1)};
    const double radius{focus.radius * (std::min)(width, height)};
    const double xMin{(std::max)(0.0, cx - radius)};
    const double yMin{(std::max)(0.0, cy - radius)};
    const double xMax{(std::min)(width - 1.0, cx + radius)};
    const double yMax{(std::min)(height - 1.0, cy + radius)};
    // Rejection from the clipped bounding box is uniform within the visible
    // disk and stays efficient for edge focus and narrow/tiny source images.
    for(int i = 0; i < 64; ++i) {
        const double x{xMin + randomUnit() * (xMax - xMin)};
        const double y{yMin + randomUnit() * (yMax - yMin)};
        if((x - cx) * (x - cx) + (y - cy) * (y - cy) <= radius * radius) {
            return {static_cast<float>(x), static_cast<float>(y)};
        }
    }
    return {static_cast<float>(cx), static_cast<float>(cy)};
}

inline std::function<std::shared_ptr<geometrize::Shape>()> safeShapeCreator(
    const geometrize::ShapeTypes types, const int width, const int height)
{
    const auto fullCreator{geometrize::createDefaultShapeCreator(types, 0, 0, width - 1, height - 1)};
    return [fullCreator, width, height]() {
        auto shape{fullCreator()};
        if(width == 1 || height == 1) {
            // Upstream setup subtracts one from its bounds; make its RNG
            // intervals valid while retaining actual-image raster bounds.
            shape->setup = [width, height](geometrize::Shape& s) {
                geometrize::setup(s, 0, 0, (std::max)(1, width - 1), (std::max)(1, height - 1));
            };
        }
        return shape;
    };
}

inline std::function<std::shared_ptr<geometrize::Shape>()> focusedShapeCreator(
    const geometrize::ShapeTypes types, const int width, const int height, const Focus focus)
{
    const auto fullCreator{safeShapeCreator(types, width, height)};
    return [fullCreator, width, height, focus]() {
        auto shape{fullCreator()};
        const auto setup{shape->setup};
        shape->setup = [setup, width, height, focus](geometrize::Shape& s) {
            setup(s);
            if(randomUnit() < focus.strength) {
                const auto [x, y]{focusPoint(focus, width, height)};
                const auto [cx, cy]{placementCenter(s)};
                geometrize::translate(s, x - cx, y - cy);
            }
        };
        // Mutations, scanlines, and energy remain full-image operations.
        return shape;
    };
}

}
