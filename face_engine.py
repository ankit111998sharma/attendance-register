from pathlib import Path

import cv2
import numpy as np

CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
FACE_SIZE = (120, 120)
MATCH_THRESHOLD = 0.58
MATCH_MARGIN = 0.025
STRONG_MATCH = 0.72
SIMILAR_SKIP = 0.97
MIN_FACE_PHOTOS = 2
MAX_FACE_PHOTOS = 50
POSE_PROMPTS = [
    "Look straight at the camera",
    "Turn a little to the left",
    "Turn a little to the right",
    "Tilt your chin slightly up",
    "Tilt your chin slightly down",
    "Give a small smile",
    "Keep a calm, neutral face",
    "Move a little closer",
    "Move a little farther back",
    "Look straight again",
    "Turn left a bit more",
    "Turn right a bit more",
]


def _cascade():
    return cv2.CascadeClassifier(CASCADE_PATH)


def decode_image(file_bytes):
    array = np.frombuffer(file_bytes, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    return image


def detect_largest_face(image):
    if image is None:
        return None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    cascade = _cascade()
    faces = []
    for scale, neighbors, minimum in (
        (1.1, 5, 60),
        (1.08, 4, 40),
        (1.05, 3, 28),
    ):
        faces = cascade.detectMultiScale(
            gray, scaleFactor=scale, minNeighbors=neighbors, minSize=(minimum, minimum)
        )
        if len(faces) > 0:
            break
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda box: box[2] * box[3])
    return gray[y : y + h, x : x + w]


def encode_face(face_gray):
    face = cv2.resize(face_gray, FACE_SIZE)
    face = cv2.GaussianBlur(face, (3, 3), 0)
    face = cv2.equalizeHist(face)
    blocks = []
    rows, cols = 4, 4
    bh, bw = FACE_SIZE[1] // rows, FACE_SIZE[0] // cols
    for r in range(rows):
        for c in range(cols):
            block = face[r * bh : (r + 1) * bh, c * bw : (c + 1) * bw]
            hist = cv2.calcHist([block], [0], None, [16], [0, 256]).flatten()
            norm = np.linalg.norm(hist)
            blocks.append(hist / norm if norm else hist)
    return np.concatenate(blocks).astype(np.float32)


def similarity(a, b):
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1.0
    return float(np.dot(a, b) / denom)


def extract_encoding(file_bytes):
    image = decode_image(file_bytes)
    face = detect_largest_face(image)
    if face is None:
        return None
    return encode_face(face)


def save_encoding(path, encoding):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, encoding)


def load_encoding(path):
    path = Path(path)
    if not path.exists():
        return None
    return np.load(path)


def save_preview(path, file_bytes):
    image = decode_image(file_bytes)
    if image is None:
        return False
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), image)
    return True


def pose_prompt(index):
    return POSE_PROMPTS[index % len(POSE_PROMPTS)]


def is_too_similar(encoding, existing, threshold=SIMILAR_SKIP):
    return any(similarity(encoding, item) >= threshold for item in existing)


def match_student(file_bytes, enrolled):
    probe = extract_encoding(file_bytes)
    if probe is None:
        return None, 0.0, "no_face"

    ranked = []
    for student_id, encoding_paths in enrolled:
        best = -1.0
        for encoding_path in encoding_paths:
            gallery = load_encoding(encoding_path)
            if gallery is None:
                continue
            best = max(best, similarity(probe, gallery))
        if best >= 0:
            ranked.append((best, student_id))

    if not ranked:
        return None, 0.0, "no_match"

    ranked.sort(reverse=True)
    best_score, best_id = ranked[0]
    second = ranked[1][0] if len(ranked) > 1 else 0.0
    if best_score < MATCH_THRESHOLD:
        return None, best_score, "no_match"
    if best_score < STRONG_MATCH and (best_score - second) < MATCH_MARGIN:
        return None, best_score, "no_match"
    return best_id, best_score, "ok"
