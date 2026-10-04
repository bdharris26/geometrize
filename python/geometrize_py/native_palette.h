#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <exception>
#include <functional>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

#include <pybind11/pybind11.h>

#include "geometrize/bitmap/bitmap.h"
#include "geometrize/core.h"
#include "geometrize/rasterizer/rasterizer.h"
#include "geometrize/runner/imagerunner.h"
#include "geometrize/runner/imagerunneroptions.h"
#include "geometrize/shape/shape.h"

namespace geometrize_py
{

struct Palette
{
    std::vector<std::array<std::uint8_t, 3>> colors;
    double strength{1.0};
};

inline std::optional<Palette> paletteFromDict(const pybind11::dict& options)
{
    namespace py = pybind11;
    if(!options.contains("palette") || options["palette"].is_none()) {
        return std::nullopt;
    }
    if(!py::isinstance<py::dict>(options["palette"])) {
        throw std::invalid_argument("palette must be an object or null");
    }
    const auto value{py::cast<py::dict>(options["palette"])};
    for(const auto item : value) {
        if(!py::isinstance<py::str>(item.first)) {
            throw std::invalid_argument("palette only accepts colors and strength");
        }
        const auto key{py::cast<std::string>(item.first)};
        if(key != "colors" && key != "strength") {
            throw std::invalid_argument("palette only accepts colors and strength");
        }
    }
    Palette palette;
    if(value.contains("strength")) {
        const auto raw{value["strength"]};
        if(py::isinstance<py::bool_>(raw) || !(py::isinstance<py::int_>(raw) || py::isinstance<py::float_>(raw))) {
            throw std::invalid_argument("palette.strength must be a finite number from 0 to 1");
        }
        palette.strength = py::isinstance<py::int_>(raw) ? PyLong_AsDouble(raw.ptr()) : PyFloat_AsDouble(raw.ptr());
        if(PyErr_Occurred()) {
            py::error_already_set error;
            if(error.matches(PyExc_OverflowError)) {
                throw std::invalid_argument("palette.strength must be a finite number from 0 to 1");
            }
            throw error;
        }
        if(!std::isfinite(palette.strength) || palette.strength < 0.0 || palette.strength > 1.0) {
            throw std::invalid_argument("palette.strength must be a finite number from 0 to 1");
        }
    }
    const auto sequence = [](const py::handle item) {
        return py::isinstance<py::list>(item) || py::isinstance<py::tuple>(item);
    };
    if(!value.contains("colors") || !sequence(value["colors"])) {
        throw std::invalid_argument("palette.colors must contain 1 to 32 RGB triples");
    }
    const auto colors{py::cast<py::sequence>(value["colors"])};
    if(colors.size() < 1 || colors.size() > 32) {
        throw std::invalid_argument("palette.colors must contain 1 to 32 RGB triples");
    }
    for(const auto item : colors) {
        if(!sequence(item) || py::len(item) != 3) {
            throw std::invalid_argument("Each palette color must be an RGB triple");
        }
        const auto channels{py::cast<py::sequence>(item)};
        std::array<std::uint8_t, 3> color;
        for(std::size_t i = 0; i < color.size(); ++i) {
            const auto channel{channels[i]};
            if(py::isinstance<py::bool_>(channel) || !py::isinstance<py::int_>(channel)) {
                throw std::invalid_argument("Palette channels must be integers from 0 to 255");
            }
            int overflow{0};
            const auto number{PyLong_AsLongLongAndOverflow(channel.ptr(), &overflow)};
            if(PyErr_Occurred()) {
                throw py::error_already_set();
            }
            if(overflow || number < 0 || number > 255) {
                throw std::invalid_argument("Palette channels must be integers from 0 to 255");
            }
            color[i] = static_cast<std::uint8_t>(number);
        }
        if(std::find(palette.colors.begin(), palette.colors.end(), color) == palette.colors.end()) {
            palette.colors.push_back(color);
        }
    }
    return palette;
}

struct PaletteCoverage
{
    std::vector<geometrize::Scanline> lines;
    bool overlaps{false};
};

inline PaletteCoverage paletteCoverage(const std::vector<geometrize::Scanline>& lines)
{
    PaletteCoverage coverage{lines};
    std::sort(coverage.lines.begin(), coverage.lines.end(), [](const auto& a, const auto& b) {
        return a.y != b.y ? a.y < b.y : (a.x1 != b.x1 ? a.x1 < b.x1 : a.x2 < b.x2);
    });
    std::size_t count{0};
    for(const auto line : coverage.lines) {
        if(count && coverage.lines[count - 1].y == line.y && coverage.lines[count - 1].x2 >= line.x1) {
            coverage.overlaps = true;
            coverage.lines[count - 1].x2 = (std::max)(coverage.lines[count - 1].x2, line.x2);
        } else {
            coverage.lines[count++] = line;
        }
    }
    coverage.lines.resize(count);
    return coverage;
}

inline std::uint64_t paletteFullError(const geometrize::Bitmap& target, const geometrize::Bitmap& current)
{
    const auto& pixels{target.getDataRef()};
    const auto& actual{current.getDataRef()};
    std::uint64_t error{0};
    for(std::size_t i = 0; i < pixels.size(); ++i) {
        const int difference{static_cast<int>(pixels[i]) - actual[i]};
        error += difference * difference;
    }
    return error;
}

inline double paletteScore(const std::uint64_t error, const geometrize::Bitmap& target)
{
    return std::sqrt(static_cast<double>(error) / target.getDataRef().size()) / 255.0;
}

// Clipping can merge otherwise distinct curve pixels. Count their final error
// once while preserving every original draw/blend in its original order.
inline std::int64_t paletteErrorDelta(const geometrize::Bitmap& target, const geometrize::Bitmap& before,
    const geometrize::Bitmap& after, const std::vector<geometrize::Scanline>& lines)
{
    const auto& pixels{target.getDataRef()};
    const auto& previous{before.getDataRef()};
    const auto& next{after.getDataRef()};
    std::int64_t delta{0};
    for(const auto& line : lines) {
        const auto start{(static_cast<std::size_t>(line.y) * target.getWidth() + line.x1) * 4U};
        const auto end{start + static_cast<std::size_t>(line.x2 - line.x1 + 1) * 4U};
        for(auto i = start; i < end; ++i) {
            const int oldDifference{static_cast<int>(pixels[i]) - previous[i]};
            const int newDifference{static_cast<int>(pixels[i]) - next[i]};
            delta += newDifference * newDifference - oldDifference * oldDifference;
        }
    }
    return delta;
}

struct PaletteChoice
{
    geometrize::rgba color;
    std::int64_t delta;
};

inline PaletteChoice choosePaletteColor(const Palette& palette, const std::uint8_t alpha,
    const geometrize::Bitmap& target, const geometrize::Bitmap& current, geometrize::Bitmap& buffer,
    const std::vector<geometrize::Scanline>& lines, const PaletteCoverage& coverage)
{
    const auto optimal{palette.strength < 1.0 ? geometrize::core::computeColor(target, current, lines, alpha)
        : geometrize::rgba{0, 0, 0, alpha}};
    PaletteChoice best{{0, 0, 0, alpha}, 0};
    bool first{true};
    for(const auto& entry : palette.colors) {
        const auto channel = [&palette](const std::uint8_t optimal, const std::uint8_t preferred) {
            return static_cast<std::uint8_t>(std::floor((1.0 - palette.strength) * optimal
                + palette.strength * preferred + 0.5));
        };
        const geometrize::rgba color{channel(optimal.r, entry[0]), channel(optimal.g, entry[1]),
            channel(optimal.b, entry[2]), alpha};
        geometrize::copyLines(buffer, current, lines);
        geometrize::drawLines(buffer, color, lines);
        const auto delta{paletteErrorDelta(target, current, buffer, coverage.lines)};
        if(first || delta < best.delta) {
            best = {color, delta};
            first = false;
        }
    }
    return best;
}

struct PaletteStepResult
{
    std::vector<geometrize::ShapeResult> shapes;
    std::unique_ptr<geometrize::ImageRunner> replacement;
    std::exception_ptr callbackError;
    std::int64_t delta{0};
};

inline PaletteStepResult paletteStep(geometrize::ImageRunner& runner,
    const geometrize::ImageRunnerOptions& options, const Palette& palette,
    const std::function<std::shared_ptr<geometrize::Shape>()>& creator, const std::uint64_t error)
{
    // Worker energy reuses the upstream buffer. The serial acceptance callback
    // uses the temporary current bitmap, which Model.step always rolls back.
    const geometrize::core::EnergyFunction energy = [&palette](const auto& lines, const std::uint32_t alpha,
        const auto& target, const auto& current, auto& buffer, double) {
        const auto coverage{paletteCoverage(lines)};
        return static_cast<double>(choosePaletteColor(palette, static_cast<std::uint8_t>(alpha),
            target, current, buffer, lines, coverage).delta);
    };
    std::shared_ptr<geometrize::Shape> winner;
    geometrize::rgba color{0, 0, 0, options.alpha};
    PaletteStepResult result;
    result.shapes.reserve(1);
    const geometrize::ShapeAcceptancePreconditionFunction accept = [&](const double lastScore, double,
        const auto& shape, const auto& lines, const auto&, const auto& before, const auto&, const auto& target) {
        try {
            auto& scratch{runner.getCurrent()};
            const auto coverage{paletteCoverage(lines)};
            const auto choice{choosePaletteColor(palette, options.alpha, target, before, scratch, lines, coverage)};
            if(choice.delta < 0) {
                geometrize::copyLines(scratch, before, lines);
                geometrize::drawLines(scratch, choice.color, lines);
                const auto score{geometrize::core::differencePartial(target, before, scratch, lastScore, coverage.lines)};
                if(coverage.overlaps) {
                    // Model.drawShape cannot separate draw and score masks.
                    // Construct before rollback, so even allocation failure
                    // leaves the old model intact and its consumed RNG usable.
                    winner = shape.clone();
                    result.replacement = std::make_unique<geometrize::ImageRunner>(target, scratch);
                    color = choice.color;
                    result.delta = choice.delta;
                } else if(std::isfinite(score) && score >= 0.0 && score <= 1.0 && score < lastScore) {
                    winner = shape.clone();
                    color = choice.color;
                    result.delta = choice.delta;
                }
            }
        } catch(...) {
            winner.reset();
            result.replacement.reset();
            result.callbackError = std::current_exception();
        }
        return false;
    };
    runner.step(options, creator, energy, accept);
    if(winner && !result.callbackError) {
        const auto actualScore{paletteScore(error - static_cast<std::uint64_t>(-result.delta), runner.getTarget())};
        if(result.replacement) {
            result.shapes.push_back({actualScore, color, winner});
            return result;
        }
        // The same rasterizer, color and preflight pixels make this score the
        // finite value validated above; drawing does not advance the RNG.
        runner.getModel().drawShape(winner, color);
        result.shapes.push_back({actualScore, color, winner});
    }
    return result;
}

}
