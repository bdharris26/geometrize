import unittest
import xml.etree.ElementTree as ET

from geometrize_py.svg import render_svg


class SvgTests(unittest.TestCase):
    def test_render_svg_serializes_rectangle_shape(self):
        svg = render_svg(
            10,
            20,
            [
                {
                    "type": "rectangle",
                    "color": {"r": 10, "g": 20, "b": 30, "a": 128},
                    "data": {"x1": 1, "y1": 2, "x2": 5, "y2": 8},
                }
            ],
        )

        root = ET.fromstring(svg)
        rect = root.find("{http://www.w3.org/2000/svg}rect")

        self.assertEqual(root.attrib["width"], "10")
        self.assertEqual(root.attrib["height"], "20")
        self.assertIsNotNone(rect)
        self.assertEqual(rect.attrib["id"], "0")
        self.assertEqual(rect.attrib["fill"], "rgb(10,20,30)")
        self.assertEqual(rect.attrib["fill-opacity"], "0.501961")

    def test_render_svg_serializes_line_as_stroke(self):
        svg = render_svg(
            10,
            20,
            [
                {
                    "type": "line",
                    "color": {"r": 1, "g": 2, "b": 3, "a": 255},
                    "data": {"x1": 1, "y1": 2, "x2": 5, "y2": 8},
                }
            ],
        )

        root = ET.fromstring(svg)
        line = root.find("{http://www.w3.org/2000/svg}line")

        self.assertIsNotNone(line)
        self.assertEqual(line.attrib["stroke"], "rgb(1,2,3)")
        self.assertEqual(line.attrib["fill"], "none")

    def test_render_svg_serializes_rotated_rectangle_as_polygon(self):
        svg = render_svg(
            10,
            20,
            [
                {
                    "type": "rotated_rectangle",
                    "color": {"r": 1, "g": 2, "b": 3, "a": 255},
                    "data": {"x1": 2, "y1": 2, "x2": 6, "y2": 4, "angle": 0},
                }
            ],
        )

        root = ET.fromstring(svg)
        polygon = root.find("{http://www.w3.org/2000/svg}polygon")

        self.assertIsNotNone(polygon)
        self.assertEqual(polygon.attrib["points"], "2,2 6,2 6,4 2,4")


if __name__ == "__main__":
    unittest.main()
