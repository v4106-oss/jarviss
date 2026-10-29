"""Sentetik MediaPipe el noktaları; kamera gerektirmeyen test yardımcıları."""

EXTENDED = {
    1: (0.42, 0.76), 2: (0.36, 0.70), 3: (0.32, 0.64), 4: (0.28, 0.58),
    5: (0.43, 0.60), 6: (0.42, 0.52), 7: (0.41, 0.46), 8: (0.40, 0.40),
    9: (0.50, 0.58), 10: (0.50, 0.49), 11: (0.50, 0.43), 12: (0.50, 0.37),
    13: (0.57, 0.60), 14: (0.58, 0.51), 15: (0.59, 0.45), 16: (0.60, 0.39),
    17: (0.63, 0.63), 18: (0.65, 0.55), 19: (0.66, 0.49), 20: (0.67, 0.43),
}
FOLDED = {
    1: (0.45, 0.76), 2: (0.40, 0.74), 3: (0.36, 0.77), 4: (0.33, 0.80),
    5: (0.43, 0.60), 6: (0.42, 0.54), 7: (0.44, 0.66), 8: (0.46, 0.72),
    9: (0.50, 0.58), 10: (0.50, 0.52), 11: (0.51, 0.66), 12: (0.52, 0.73),
    13: (0.57, 0.60), 14: (0.58, 0.54), 15: (0.57, 0.67), 16: (0.56, 0.74),
    17: (0.63, 0.63), 18: (0.65, 0.57), 19: (0.63, 0.70), 20: (0.62, 0.76),
}

_FINGERS = {
    "index": (5, 6, 7, 8),
    "middle": (9, 10, 11, 12),
    "ring": (13, 14, 15, 16),
    "pinky": (17, 18, 19, 20),
}


def make_hand(gesture: str = "fist", dx: float = 0.0, dy: float = 0.0):
    flags = {
        "open": {"thumb": True, "index": True, "middle": True, "ring": True, "pinky": True},
        "fist": {},
        "thumb_up": {"thumb": True},
        "victory": {"index": True, "middle": True},
        "point": {"index": True},
        "pinch": {"index": True, "thumb": True},
    }.get(gesture, {})
    data = dict(EXTENDED if flags.get("thumb") else FOLDED)
    for name, keys in _FINGERS.items():
        source = EXTENDED if flags.get(name) else FOLDED
        for key in keys:
            data[key] = source[key]
    if gesture == "pinch":
        data[4] = (0.42, 0.47)
        data[8] = (0.43, 0.48)
    points = [(0.5, 0.80, 0.0)] * 21
    for index, (x, y) in data.items():
        points[index] = (x + dx, y + dy, 0.0)
    return points


def shift_hand(points, dx: float = 0.0, dy: float = 0.0):
    return [(x + dx, y + dy, z) for x, y, z in points]
