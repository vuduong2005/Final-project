import time
from typing import List, Dict, Optional, Tuple
from collections import deque
from PIL import Image
import io

from utils.logger import get_logger
from core.settings import get as get_setting

logger = get_logger()


class FrameBuffer:
    """Manages last 3 frames with sequential analysis and trend detection"""
    
    def __init__(self, max_frames: int = 3):
        self.max_frames = max_frames
        self.frames = deque(maxlen=max_frames)  # Auto-removes oldest when full
        self.detections = deque(maxlen=max_frames)  # Parallel tracking
        self.timestamps = deque(maxlen=max_frames)
        
        logger.info(f"✅ FrameBuffer initialized (max {max_frames} frames)")
    
    # ========================================================================
    # FRAME MANAGEMENT
    # ========================================================================
    
    def add_frame(self, image: Image.Image, detections: Dict, timestamp: float = None):
        """Add new frame to buffer with detections
        
        Args:
            image: PIL Image (will be optimized)
            detections: Roboflow JSON output
            timestamp: Frame timestamp (auto-generated if None)
        """
        if timestamp is None:
            timestamp = time.time()
        
        # Optimize image before storing
        optimized_image = self._optimize_image(image)
        
        self.frames.append(optimized_image)
        self.detections.append(detections)
        self.timestamps.append(timestamp)
        
        logger.debug(f"📸 Frame added (buffer size: {len(self.frames)}/{self.max_frames})")
    
    def _optimize_image(self, image: Image.Image, target_size: Tuple[int, int] = (640, 360), quality: int = 80) -> bytes:
        """Optimize image: resize + compress
        
        Args:
            image: PIL Image
            target_size: Resize to this (width, height)
            quality: JPEG quality 0-100 (80 is good balance)
        
        Returns:
            Optimized image as bytes
        """
        try:
            # Resize
            img_resized = image.resize(target_size, Image.Resampling.LANCZOS)
            
            # Compress to JPEG bytes
            img_byte_arr = io.BytesIO()
            img_resized.save(img_byte_arr, format='JPEG', quality=quality, optimize=True)
            img_byte_arr.seek(0)
            
            original_size = len(image.tobytes()) // 1024  # KB
            optimized_size = len(img_byte_arr.getvalue()) // 1024  # KB
            
            logger.debug(f"🖼️ Image optimized: {original_size}KB → {optimized_size}KB")
            
            return img_byte_arr.getvalue()
        
        except Exception as e:
            logger.error(f"Image optimization failed: {e}")
            return image.tobytes()
    
    def get_frames(self) -> List[bytes]:
        """Get all frames in buffer as optimized bytes"""
        return list(self.frames)
    
    def get_frame_at(self, index: int) -> Optional[bytes]:
        """Get specific frame (0=oldest, -1=newest)"""
        if 0 <= index < len(self.frames):
            return self.frames[index]
        return None
    
    def get_latest_frame(self) -> Optional[bytes]:
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
        
        Returns:
            {
                'confidence_trend': 'increasing'|'decreasing'|'stable',
                'position_trend': 'approaching'|'moving_away'|'stable',
                'movement_pixels': 42,  # pixels moved
                'avg_confidence': 0.91,
                'threat_level': 'critical'|'high'|'medium'|'low'|'none',
                'description': "Zombie getting closer! 95% confident"
            }
        """
        if len(self.detections) < 2:
            return {
                'confidence_trend': 'unknown',
                'position_trend': 'unknown',
                'movement_pixels': 0,
                'avg_confidence': 0.0,
                'threat_level': 'none',
                'description': "Not enough frames to analyze"
            }
        
        detections_list = list(self.detections)
        
        # Extract confidence scores
        confidences = self._extract_confidences(detections_list)
        
        # Extract positions (centers of detections)
        positions = self._extract_positions(detections_list)
        
        # Analyze trends
        conf_trend = self._analyze_confidence_trend(confidences)
        pos_trend, movement_px = self._analyze_position_trend(positions)
        threat_level = self._calculate_threat_level(confidences, movement_px)
        
        # Generate description
        description = self._generate_trend_description(
            conf_trend, pos_trend, movement_px, confidences
        )
        
        return {
            'confidence_trend': conf_trend,
            'position_trend': pos_trend,
            'movement_pixels': movement_px,
            'avg_confidence': sum(confidences) / len(confidences),
            'threat_level': threat_level,
            'description': description,
            'confidences': confidences,
            'positions': positions
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
    
    def _extract_positions(self, detections: List[Dict]) -> List[Tuple[int, int]]:
        """Extract average center position from each frame"""
        positions = []
        for det in detections:
            if det.get('detections'):
                avg_x = sum(d['bbox']['center_x'] for d in det['detections']) / len(det['detections'])
                avg_y = sum(d['bbox']['center_y'] for d in det['detections']) / len(det['detections'])
                positions.append((int(avg_x), int(avg_y)))
            else:
                positions.append((320, 180))  # Center of 640x360
        return positions
    
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
    
    def _analyze_position_trend(self, positions: List[Tuple[int, int]]) -> Tuple[str, int]:
        """Determine if object is approaching, moving away, or stable
        
        Returns:
            (trend, movement_pixels)
        """
        if len(positions) < 2:
            return 'unknown', 0
        
        # Compare distance to center (320, 180) = center of 640x360
        screen_center = (320, 180)
        
        def distance_to_center(pos):
            return ((pos[0] - screen_center[0])**2 + (pos[1] - screen_center[1])**2)**0.5
        
        distances = [distance_to_center(pos) for pos in positions]
        
        # Movement: change in distance
        first_distance = distances[0]
        last_distance = distances[-1]
        movement_px = int(first_distance - last_distance)  # Positive = getting closer
        
        # Threshold (pixels) for "significant movement" - live setting
        MOVEMENT_THRESHOLD = get_setting("FRAME_BUFFER_MOVEMENT_THRESHOLD")
        
        if movement_px > MOVEMENT_THRESHOLD:
            return 'approaching', movement_px
        elif movement_px < -MOVEMENT_THRESHOLD:
            return 'moving_away', abs(movement_px)
        else:
            return 'stable', abs(movement_px)
    
    def _calculate_threat_level(self, confidences: List[float], movement_px: int) -> str:
        """Calculate threat level based on confidence and movement
        
        Levels: critical > high > medium > low > none
        """
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
        
        # Critical: High confidence + approaching
        if avg_conf > 0.85 and movement_px > 100:
            return 'critical'
        
        # High: High confidence OR approaching
        elif avg_conf > 0.80 or movement_px > 50:
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
    
    def _generate_trend_description(self, conf_trend: str, pos_trend: str, movement_px: int, confidences: List[float]) -> str:
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
            parts.append(f"Object APPROACHING (moved {movement_px}px closer)")
        elif pos_trend == 'moving_away':
            parts.append(f"Object MOVING AWAY (moved {movement_px}px away)")
        elif pos_trend == 'stable':
            parts.append(f"Object position STABLE (minimal movement: {movement_px}px)")
        
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
- Position Trend: {trends['position_trend']}
- Movement: {trends['movement_pixels']}px
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
    
    # Frame 1: Zombie at right, 85% confidence
    det1 = {
        'detections': [
            {'class': 'zombie', 'confidence': 0.85, 'bbox': {'center_x': 1600, 'center_y': 540}}
        ]
    }
    buf.add_frame(dummy_img, det1)
    
    # Frame 2: Zombie at right-center, 90% confidence
    det2 = {
        'detections': [
            {'class': 'zombie', 'confidence': 0.90, 'bbox': {'center_x': 1200, 'center_y': 540}}
        ]
    }
    buf.add_frame(dummy_img, det2)
    
    # Frame 3: Zombie at center, 95% confidence
    det3 = {
        'detections': [
            {'class': 'zombie', 'confidence': 0.95, 'bbox': {'center_x': 960, 'center_y': 540}}
        ]
    }
    buf.add_frame(dummy_img, det3)
    
    # Analyze
    print(buf.get_summary())
    trends = buf.analyze_trends()
    print(f"\nTrends: {trends}")
