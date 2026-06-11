import unittest

import numpy as np

from app.detector import _cv2, _cv2_candidates, _cv2_candidates_detailed


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
