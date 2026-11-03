import math
import time
from typing import List, Dict, Optional, Tuple
from collections import deque
from PIL import Image

from utils.logger import get_logger
from core.mob_classes import DANGEROUS_CLASSES, PASSIVE_CLASSES, normalize_class
from core.settings import get as get_setting

logger = get_logger()

def _size_change_ratio() -> float:
    """How much bigger (or smaller) a detection box must get between the first and last frame
    before we call it approaching (or moving away). 0.15 = 15% change in apparent size.
    A live setting, so the status window slider applies from the next frame."""
    return get_setting("FRAME_BUFFER_SIZE_CHANGE_PERCENT") / 100.0

# The FOV we assume the player uses: the value of Minecraft's FOV slider (a vertical angle).
# Players mostly pick between 90 and 110, so 100 is the standard for our calculation. It turns
# "how far off-center is the object" into a viewing angle, so that turning the camera is not
# mistaken for movement. A different real FOV only affects that correction, not the size ratio.
PLAYER_FOV_DEG = 100.0

THREAT_LEVELS = ['none', 'low', 'medium', 'high', 'critical']

# Highest threat level a class can ever cause. Dangerous things (hostile mobs, lava...)
# use the full scale; passive animals top out at 'low'; everything else (ores, status
# bars, blocks) is never a threat.
THREAT_CAP_DANGEROUS = 'critical'
THREAT_CAP_PASSIVE = 'low'
THREAT_CAP_OTHER = 'none'


class FrameBuffer:
    """Manages last 3 frames with sequential analysis and trend detection"""
    
    def __init__(self, max_frames: int = 3):
        self.max_frames = max_frames
        self.frames = deque(maxlen=max_frames)  # Auto-removes oldest when full
        self.detections = deque(maxlen=max_frames)  # Parallel tracking
        self.timestamps = deque(maxlen=max_frames)
        # (width, height) of the frame the detector saw. Detection coordinates are in this
        # space (the stored frames are the same size).
        self.frame_sizes = deque(maxlen=max_frames)
        
        logger.info(f"✅ FrameBuffer initialized (max {max_frames} frames)")
    
    # ========================================================================
    # FRAME MANAGEMENT
    # ========================================================================
    
    def add_frame(self, image: Image.Image, detections: Dict, timestamp: float = None):
        """Add new frame to buffer with detections
        
        Args:
            image: PIL Image, kept as it is (same size as the detections, not copied,
                   so the caller must not modify it afterwards)
            detections: Roboflow JSON output
            timestamp: Frame timestamp (auto-generated if None)
        """
        if timestamp is None:
            timestamp = time.time()
        
        self.frames.append(image)
        self.detections.append(detections)
        self.timestamps.append(timestamp)
        self.frame_sizes.append(self._detection_space(image, detections))
        
        logger.debug(f"📸 Frame added (buffer size: {len(self.frames)}/{self.max_frames})")
    
    @staticmethod
    def _detection_space(image: Image.Image, detections: Dict) -> Tuple[int, int]:
        """Size of the coordinate space the detections are in.

        Prefer what the detector reported; fall back to the image passed in.
        """
        meta = (detections or {}).get('metadata', {})
        w = meta.get('image_width') or image.width
        h = meta.get('image_height') or image.height
        return int(w), int(h)

    def get_frames(self) -> List[Image.Image]:
        """Get all frames in buffer (full size, oldest first)"""
        return list(self.frames)
    
    def get_frame_at(self, index: int) -> Optional[Image.Image]:
        """Get specific frame (0=oldest, -1=newest)"""
        if 0 <= index < len(self.frames):
            return self.frames[index]
        return None
    
    def get_latest_frame(self) -> Optional[Image.Image]:
        """Get newest frame"""
        return self.frames[-1] if self.frames else None
    
    def get_detections(self) -> List[Dict]:
        """Get all detections in buffer"""
        return list(self.detections)
    
    # ========================================================================
    # SEQUENTIAL ANALYSIS & TRENDS
    # ========================================================================
    
    def analyze_trends(self) -> Dict:
        """Analyze frame sequence for patterns and trends
        
        Returns (always the same keys, even with fewer than 2 frames):
            {
                'confidence_trend': 'increasing'|'decreasing'|'stable'|'unknown',
                'position_trend': 'approaching'|'moving_away'|'stable'|'unknown',
                'size_change_percent': 62,   # how much bigger/smaller the object looks
                'avg_confidence': 0.91,
                'threat_level': 'critical'|'high'|'medium'|'low'|'none',
                'threat_class': 'zombie',    # the class that caused the threat, or None
                'description': "...",
                'confidences': [..],         # average confidence of each buffered frame
            }
        """
        if len(self.detections) < 2:
            return {
                'confidence_trend': 'unknown',
                'position_trend': 'unknown',
                'size_change_percent': 0,
                'avg_confidence': 0.0,
                'threat_level': 'none',
                'threat_class': None,
                'description': "Not enough frames to analyze",
                'confidences': [],
            }
        
        detections_list = list(self.detections)
        
        # Extract confidence scores
        confidences = self._extract_confidences(detections_list)
        conf_trend = self._analyze_confidence_trend(confidences)
        
        # Depth (3D) trend from apparent size: closer objects look bigger.
        # This is what decides approaching / moving away. Screen position only tells
        # left/right/up/down, so turning the camera or a head-on charge fooled it.
        ranges = self._extract_ranges(detections_list, list(self.frame_sizes))
        pos_trend, size_change_pct = self._analyze_depth_trend(ranges)
        
        # Threat is judged per class, so a sheep or a health bar cannot raise it.
        threat_level, threat_class = self._calculate_class_threat(
            detections_list, self._class_growth(ranges)
        )
        
        # Generate description
        description = self._generate_trend_description(
            conf_trend, pos_trend, size_change_pct, confidences
        )
        
        return {
            'confidence_trend': conf_trend,
            'position_trend': pos_trend,
            'size_change_percent': size_change_pct,
            'avg_confidence': sum(confidences) / len(confidences),
            'threat_level': threat_level,
            'threat_class': threat_class,
            'description': description,
            'confidences': confidences,
        }
    
    def _extract_confidences(self, detections: List[Dict]) -> List[float]:
        """Extract average confidence from each frame"""
        confidences = []
        for det in detections:
            if det.get('detections'):
                avg_conf = sum(d['confidence'] for d in det['detections']) / len(det['detections'])
                confidences.append(avg_conf)
            else:
                confidences.append(0.0)
        return confidences
    
    def _extract_ranges(self, detections: List[Dict], sizes: List[Tuple[int, int]]) -> List[Dict[str, float]]:
        """Relative distance from the player of each class, per frame (smaller = closer).

        Two facts of perspective are combined:
          1. Apparent size is inversely proportional to depth along the view axis:
             depth ~ 1 / size, with size = sqrt(box width * box height).
          2. An object away from the screen center is further away than its depth, by
             sqrt(1 + u^2 + v^2), where u = (x - center_x) / focal_length and
             v = (y - center_y) / focal_length are its sideways and up/down offsets.
        Dividing out both gives the straight-line distance from the camera, which does
        not change when the camera only turns or tilts (yaw or pitch).
        The numbers are relative (no real-world unit), so only ratios between frames matter.
        """
        ranges = []
        for det, (frame_w, frame_h) in zip(detections, sizes):
            # focal length in pixels, from the vertical FOV (works for any screen shape)
            focal = (frame_h / 2) / math.tan(math.radians(PLAYER_FOV_DEG / 2))
            by_class: Dict[str, List[float]] = {}
            for d in det.get('detections') or []:
                w = d['bbox'].get('width', 0)
                h = d['bbox'].get('height', 0)
                if w > 0 and h > 0:
                    size = (w * h) ** 0.5
                    u = (d['bbox']['center_x'] - frame_w / 2) / focal
                    v = (d['bbox']['center_y'] - frame_h / 2) / focal
                    by_class.setdefault(d['class'], []).append(math.sqrt(1.0 + u * u + v * v) / size)
            ranges.append({cls: sum(v) / len(v) for cls, v in by_class.items()})
        return ranges
    
    @staticmethod
    def _class_growth(ranges: List[Dict[str, float]]) -> Dict[str, float]:
        """growth = first_range / last_range for every class seen in at least two frames.

        growth 2.0 means the object is now half as far away (it looks about twice as big).
        """
        growth = {}
        for cls in {c for frame in ranges for c in frame}:
            seen = [i for i, frame in enumerate(ranges) if cls in frame]
            if len(seen) >= 2:
                growth[cls] = ranges[seen[0]][cls] / ranges[seen[-1]][cls]
        return growth
    
    def _analyze_depth_trend(self, ranges: List[Dict[str, float]]) -> Tuple[str, int]:
        """Approaching / moving away / stable from how an object's distance changes.

        Each class is followed separately between the first and last frame it
        appears in (see _class_growth), and the class that changed the most is
        reported.

        Returns:
            (trend, size_change_percent)  percent is always >= 0
        """
        growth_by_class = self._class_growth(ranges)
        if not growth_by_class:
            return 'unknown', 0
        
        def change(g: float) -> float:
            # symmetric: 2x bigger and 2x smaller count as the same amount of change
            return g - 1.0 if g >= 1.0 else 1.0 / g - 1.0
        
        best_growth = max(growth_by_class.values(), key=change)
        
        ratio = _size_change_ratio()
        if best_growth > 1.0 + ratio:
            return 'approaching', int(round((best_growth - 1.0) * 100))
        elif best_growth < 1.0 / (1.0 + ratio):
            return 'moving_away', int(round((1.0 - best_growth) * 100))
        else:
            return 'stable', int(round(abs(best_growth - 1.0) * 100))
    
    def _calculate_class_threat(self, detections: List[Dict],
                                growth_by_class: Dict[str, float]) -> Tuple[str, Optional[str]]:
        """Threat level of the scene = the highest threat of any single class.

        For every class in the newest frame:
          avg_conf     = its mean confidence over the buffered frames WHERE IT WAS SEEN
                         (a mob that has only just appeared must not be diluted by the
                         frames before it arrived)
          approach_pct = how much bigger it looks, 0 unless it is approaching
          level        = _calculate_threat_level(avg_conf, approach_pct), then capped
                         by what kind of thing it is (see THREAT_CAP_*)

        Returns:
            (threat_level, the class that caused it or None)
        """
        if not detections or not detections[-1].get('detections'):
            return 'none', None
        
        ratio = _size_change_ratio()
        best_level, best_class = 'none', None
        for cls in {d['class'] for d in detections[-1]['detections']}:
            per_frame = []
            for det in detections:
                confs = [d['confidence'] for d in det.get('detections') or [] if d['class'] == cls]
                if confs:
                    per_frame.append(sum(confs) / len(confs))
            avg_conf = sum(per_frame) / len(per_frame)   # never empty: cls is in the newest frame
            
            growth = growth_by_class.get(cls, 1.0)
            approach_pct = int(round((growth - 1.0) * 100)) if growth > 1.0 + ratio else 0
            level = self._calculate_threat_level([avg_conf], approach_pct)
            
            name = normalize_class(cls)
            if name in DANGEROUS_CLASSES:
                cap = THREAT_CAP_DANGEROUS
            elif name in PASSIVE_CLASSES:
                cap = THREAT_CAP_PASSIVE
            else:
                cap = THREAT_CAP_OTHER
            level = THREAT_LEVELS[min(THREAT_LEVELS.index(level), THREAT_LEVELS.index(cap))]
            
            if THREAT_LEVELS.index(level) > THREAT_LEVELS.index(best_level):
                best_level, best_class = level, cls
        return best_level, best_class
    
    def _analyze_confidence_trend(self, confidences: List[float]) -> str:
        """Determine if confidence is increasing, decreasing, or stable"""
        if len(confidences) < 2:
            return 'unknown'
        
        # Compare average of first half vs second half
        mid = len(confidences) // 2
        first_half = sum(confidences[:mid]) / max(1, mid)
        second_half = sum(confidences[mid:]) / max(1, len(confidences) - mid)
        
        diff = second_half - first_half
        threshold = get_setting("FRAME_BUFFER_CONFIDENCE_THRESHOLD")  # live setting
        
        if diff > threshold:
            return 'increasing'
        elif diff < -threshold:
            return 'decreasing'
        else:
            return 'stable'
    
    def _calculate_threat_level(self, confidences: List[float], approach_pct: int) -> str:
        """Calculate threat level based on confidence and how fast it is getting closer

        approach_pct: how much bigger the object looks (0 if it is not approaching).
        Levels: critical > high > medium > low > none
        """
        # Round first: three frames of 0.80 average to 0.8000000000000002, which must not
        # count as 'above 0.80'. Roboflow confidences have 3 decimals, so 3 is exact enough.
        avg_conf = round(sum(confidences) / len(confidences), 3) if confidences else 0.0
        
        # Critical: High confidence + clearly approaching (looks 50% bigger or more)
        if avg_conf > 0.85 and approach_pct >= 50:
            return 'critical'
        
        # High: High confidence OR approaching (looks 25% bigger or more)
        elif avg_conf > 0.80 or approach_pct >= 25:
            return 'high'
        
        # Medium: Moderate confidence
        elif avg_conf > 0.65:
            return 'medium'
        
        # Low: Low confidence
        elif avg_conf > 0.50:
            return 'low'
        
        # None: Nothing detected
        else:
            return 'none'
    
    def _generate_trend_description(self, conf_trend: str, pos_trend: str, size_change_pct: int, confidences: List[float]) -> str:
        """Generate human-readable description of trends"""
        parts = []
        
        # Confidence trend
        if conf_trend == 'increasing':
            parts.append("Detection confidence is INCREASING")
        elif conf_trend == 'decreasing':
            parts.append("Detection confidence is DECREASING")
        elif conf_trend == 'stable':
            parts.append("Detection confidence is STABLE")
        
        # Position trend
        if pos_trend == 'approaching':
            parts.append(f"Object APPROACHING (looks {size_change_pct}% bigger)")
        elif pos_trend == 'moving_away':
            parts.append(f"Object MOVING AWAY (looks {size_change_pct}% smaller)")
        elif pos_trend == 'stable':
            parts.append(f"Object distance STABLE (size changed {size_change_pct}%)")
        
        # Overall assessment
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
        parts.append(f"Avg confidence: {avg_conf:.1%}")
        
        return " | ".join(parts)
    
    # ========================================================================
    # BUFFER STATE
    # ========================================================================
    
    def is_full(self) -> bool:
        """Check if buffer has max frames"""
        return len(self.frames) == self.max_frames
    
    def is_empty(self) -> bool:
        """Check if buffer is empty"""
        return len(self.frames) == 0
    
    def size(self) -> int:
        """Get current number of frames"""
        return len(self.frames)
    
    def clear(self):
        """Clear all frames"""
        self.frames.clear()
        self.detections.clear()
        self.timestamps.clear()
        self.frame_sizes.clear()
        logger.info("🗑️ FrameBuffer cleared")
    
    def get_summary(self) -> str:
        """Get human-readable summary of buffer state"""
        if self.is_empty():
            return "FrameBuffer: Empty"
        
        trends = self.analyze_trends()
        return f"""
FrameBuffer Summary:
- Frames: {self.size()}/{self.max_frames}
- Threat Level: {trends['threat_level'].upper()}
- Confidence Trend: {trends['confidence_trend']}
- Distance Trend: {trends['position_trend']} (size changed {trends['size_change_percent']}%)
- Description: {trends['description']}
        """.strip()


# ============================================================================
# TEST
# ============================================================================

if __name__ == "__main__":
    from PIL import Image
    
    # Create test buffer
    buf = FrameBuffer(max_frames=3)
    
    # Simulate frames
    dummy_img = Image.new('RGB', (1920, 1080), color='red')
    
    # The zombie walks toward the camera: its box grows from 60x120 to 130x260 px
    # Frame 1: Zombie at right, 85% confidence
    det1 = {
        'detections': [
            {'class': 'zombie', 'confidence': 0.85, 'bbox': {'center_x': 1600, 'center_y': 540, 'width': 60, 'height': 120}}
        ]
    }
    buf.add_frame(dummy_img, det1)
    
    # Frame 2: Zombie at right-center, 90% confidence
    det2 = {
        'detections': [
            {'class': 'zombie', 'confidence': 0.90, 'bbox': {'center_x': 1200, 'center_y': 540, 'width': 90, 'height': 180}}
        ]
    }
    buf.add_frame(dummy_img, det2)
    
    # Frame 3: Zombie at center, 95% confidence
    det3 = {
        'detections': [
            {'class': 'zombie', 'confidence': 0.95, 'bbox': {'center_x': 960, 'center_y': 540, 'width': 130, 'height': 260}}
        ]
    }
    buf.add_frame(dummy_img, det3)
    
    # Analyze
    print(buf.get_summary())
    trends = buf.analyze_trends()
    print(f"\nTrends: {trends}")
