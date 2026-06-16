import tempfile
from pathlib import Path
import unittest

import numpy as np

from app.detector import (
    _as_density,
    _blend_profiles,
    _cv2,
    _cv2_candidates,
    _cv2_candidates_detailed,
    _cv2_detection_profiles,
    _score_results,
    detect_candidate_detail_boxes,
)


def _results(boxes):
    return [
        {"id": f"det_{i}", "x": x, "y": y, "w": w, "h": h}
        for i, (x, y, w, h) in enumerate(boxes)
    ]


class DensityBlendTests(unittest.TestCase):
    def test_as_density_coerces_legacy_bool(self):
        self.assertEqual(_as_density(True), 1.0)
        self.assertEqual(_as_density(False), 0.0)
        self.assertEqual(_as_density(0.4), 0.4)
        self.assertEqual(_as_density(5.0), 1.0)  # clamped
        self.assertEqual(_as_density(-1.0), 0.0)

    def test_blend_endpoints_match_anchors(self):
        loose = {"line_kernel": 45, "min_area_ratio": 0.0008, "text_kernel": (55, 12)}
        packed = {"line_kernel": 11, "min_area_ratio": 0.00018, "text_kernel": (18, 5)}
        self.assertEqual(_blend_profiles(loose, packed, 0.0), loose)
        self.assertEqual(_blend_profiles(loose, packed, 1.0), packed)

    def test_blend_midpoint_is_between_and_monotonic(self):
        profiles = {d: _cv2_detection_profiles(2000, 1500, d) for d in (0.0, 0.5, 1.0)}
        for idx in range(3):
            loose = profiles[0.0][idx]
            mid = profiles[0.5][idx]
            packed = profiles[1.0][idx]
            for key in ("line_kernel", "dilate_kernel", "merge_dx"):
                self.assertGreaterEqual(loose[key], mid[key])
                self.assertGreaterEqual(mid[key], packed[key])


class ScoreResultsTests(unittest.TestCase):
    def test_empty_pass_scores_zero(self):
        self.assertEqual(_score_results([], 2000, 1500), 0.0)

    def test_clean_pass_beats_fragmented_overlapping_pass(self):
        grid = [
            (100, 100, 300, 250), (500, 100, 300, 250), (900, 100, 300, 250),
            (100, 500, 300, 250), (500, 500, 300, 250), (900, 500, 300, 250),
        ]
        clean = _results(grid)
        fragmented = []
        for x, y, w, h in grid:
            fragmented.append((x, y, w, h))
            fragmented.append((x + 20, y + 20, w - 30, h - 30))
        fragmented = _results(fragmented)

        # The fragmented pass emits twice as many boxes; the old len()-based
        # selector would have preferred it. Quality scoring must not.
        self.assertGreater(
            _score_results(clean, 2000, 1500),
            _score_results(fragmented, 2000, 1500),
        )


class Cv2CandidateTests(unittest.TestCase):
    def setUp(self):
        self.cv2 = _cv2()
        if self.cv2 is None:
            self.skipTest("OpenCV is not installed")

    def test_detailed_candidates_return_filter_tuple(self):
        thresh = np.zeros((220, 320), dtype=np.uint8)
        self.cv2.rectangle(thresh, (40, 50), (180, 130), 255, thickness=2)

        boxes, contours, rejected = _cv2_candidates_detailed(
            self.cv2,
            thresh,
            320,
            220,
            line_kernel=15,
            text_kernel=(18, 5),
            dilate_kernel=3,
            iterations=1,
            min_area_ratio=0.0001,
            max_area_ratio=0.8,
        )

        self.assertIsInstance(boxes, list)
        self.assertGreaterEqual(len(contours), 1)
        self.assertIsInstance(rejected, list)
        self.assertGreaterEqual(len(boxes), 1)


    def test_detector_falls_back_for_faint_gray_linework(self):
        image = np.full((700, 1000, 3), 255, dtype=np.uint8)
        for idx in range(2):
            x = 120 + idx * 420
            y = 140
            self.cv2.rectangle(image, (x, y), (x + 280, y + 190), (245, 245, 245), thickness=2)
            for offset in range(30, 160, 35):
                self.cv2.line(image, (x + 30, y + offset), (x + 250, y + offset), (245, 245, 245), 1)

        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "faint_architect_sheet.png"
            self.cv2.imwrite(str(image_path), image)

            boxes = detect_candidate_detail_boxes(image_path)

        self.assertGreaterEqual(len(boxes), 2)


    def test_detector_falls_back_for_sparse_wide_unboxed_linework(self):
        image = np.full((900, 1400, 3), 255, dtype=np.uint8)
        for y in (120, 380):
            for x in (90, 760):
                self.cv2.line(image, (x, y), (x + 520, y), (0, 0, 0), 1)
                self.cv2.line(image, (x, y + 85), (x + 520, y + 85), (0, 0, 0), 1)
                for offset in range(30, 500, 70):
                    self.cv2.line(image, (x + offset, y + 12), (x + offset + 30, y + 72), (0, 0, 0), 1)

        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "sparse_unboxed_sheet.png"
            self.cv2.imwrite(str(image_path), image)

            boxes = detect_candidate_detail_boxes(image_path)

        self.assertGreaterEqual(len(boxes), 4)

    def test_candidate_wrapper_unpacks_detailed_result(self):
        thresh = np.zeros((220, 320), dtype=np.uint8)
        self.cv2.rectangle(thresh, (40, 50), (180, 130), 255, thickness=2)

        boxes = _cv2_candidates(
            self.cv2,
            thresh,
            320,
            220,
            line_kernel=15,
            text_kernel=(18, 5),
            dilate_kernel=3,
            iterations=1,
            min_area_ratio=0.0001,
            max_area_ratio=0.8,
        )

        self.assertIsInstance(boxes, list)
        self.assertGreaterEqual(len(boxes), 1)


if __name__ == "__main__":
    unittest.main()
