import requests
import json
import time
import threading
import base64
from typing import Dict, Optional, List, Tuple
from PIL import Image
from datetime import datetime
from io import BytesIO

from utils.logger import get_logger
from config import (
    LLAMA_API_URL,
    LLAMA_MODEL_NAME,
    ROBOFLOW_CONFIDENCE_PERCENT,
    ROBOFLOW_CONFIDENCE_THRESHOLD,
    ROBOFLOW_API_KEY,
    ROBOFLOW_GENERAL_URL,
    ROBOFLOW_MOBS_URL,
    ROBOFLOW_BLOCKS_URL,
    USE_GENERAL_MODEL,
    USE_MOBS_MODEL,
    USE_BLOCKS_MODEL,
    MOB_CONFIDENCE_THRESHOLD,
    BLOCK_CONFIDENCE_THRESHOLD,
    VISION_TRIGGER_KEYWORDS,
    PROACTIVE_STREAK_SECONDS,
    PROACTIVE_CHAT_COOLDOWN_SECONDS,
)
from core.frame_buffer import FrameBuffer

logger = get_logger()


# ============================================================================
# CHAT MEMORY - Keeps conversation history
# ============================================================================

class ChatMemory:
    """
    Remembers conversation history between user and Maria.

    """
    
    def __init__(self, max_history=10):
        self.history = []  # List of (role, message) tuples
        self.max_history = max_history  # Keep last N messages
        self.activity_context = ""  # Current activity (mining, building, etc.)
        
        logger.info(f"✅ Chat Memory initialized (max {max_history} messages)")
    
    def add_user_message(self, message: str):
        """Add user message to memory"""
        self.history.append(("user", message))
        self._trim_history()
        logger.debug(f"📝 Memory: User said '{message}' (total: {len(self.history)} messages)")
    
    def add_ai_message(self, message: str):
        """Add Maria's response to memory"""
        self.history.append(("maria", message))
        self._trim_history()
        logger.debug(f"📝 Memory: Maria said '{message}' (total: {len(self.history)} messages)")
    
    def set_activity_context(self, activity: str):
        """Track what the player is currently doing"""
        if activity != self.activity_context:
            self.activity_context = activity
            logger.info(f"🎯 Activity Context: {activity}")
    
    def _trim_history(self):
        """Keep only recent messages"""
        if len(self.history) > self.max_history:
            removed = self.history.pop(0)
            logger.debug(f"🗑️  Trimmed old message from memory: {removed}")
    
    def get_conversation_summary(self) -> str:
        """Get formatted conversation history for LLM context"""
        if not self.history:
            return "No previous conversation"
        
        summary = "Recent conversation:\n"
        for role, msg in self.history[-6:]:  # Last 6 messages
            prefix = "Player:" if role == "user" else "Maria:"
            summary += f"{prefix} {msg}\n"
        
        return summary
    
    def get_full_context(self) -> str:
        """Get full context including activity and history"""
        context = f"Current Activity: {self.activity_context}\n"
        context += self.get_conversation_summary()
        return context
    
    def clear(self):
        """Clear conversation history"""
        self.history = []
        self.activity_context = ""
        logger.info("🧹 Chat memory cleared")


# ============================================================================
# STAGE 1: MULTI-MODEL ROBOFLOW API - Object Detection (3 Models)
# ============================================================================

class MultiModelDetector:
    """Run multiple specialized Roboflow models and combine results"""
    
    def __init__(self):
        self.api_key = ROBOFLOW_API_KEY
        self.general_url = ROBOFLOW_GENERAL_URL
        self.mobs_url = ROBOFLOW_MOBS_URL
        self.blocks_url = ROBOFLOW_BLOCKS_URL
        self.confidence_threshold = ROBOFLOW_CONFIDENCE_PERCENT
        
        logger.info(f"✅ [STAGE 1] Multi-Model Roboflow initialized (3 models)")
        logger.info(f"   General model: {'ENABLED' if USE_GENERAL_MODEL else 'disabled'}")
        logger.info(f"   Mobs model: {'ENABLED' if USE_MOBS_MODEL else 'disabled'}")
        logger.info(f"   Blocks model: {'ENABLED' if USE_BLOCKS_MODEL else 'disabled'}")
    
    def detect(self, image: Image.Image) -> Dict:
        """Run detection on all enabled models and combine results"""
        all_detections = []
        models_run = []
        
        # MODEL 1: General Detection
        if USE_GENERAL_MODEL:
            logger.info("   🔍 Running General Model...")
            general_dets = self._run_single_model(self.general_url, image, "general")
            all_detections.extend(general_dets)
            models_run.append("general")
        
        # MODEL 2: Mobs Detection
        if USE_MOBS_MODEL:
            logger.info("   🔍 Running Mobs Model...")
            mobs_dets = self._run_single_model(self.mobs_url, image, "mobs")
            for det in mobs_dets:
                if det['confidence'] >= MOB_CONFIDENCE_THRESHOLD:
                    all_detections.append(det)
            models_run.append("mobs")
        
        # MODEL 3: Blocks Detection
        if USE_BLOCKS_MODEL:
            logger.info("   🔍 Running Blocks Model...")
            blocks_dets = self._run_single_model(self.blocks_url, image, "blocks")
            for det in blocks_dets:
                if det['confidence'] >= BLOCK_CONFIDENCE_THRESHOLD:
                    all_detections.append(det)
            models_run.append("blocks")
        
        # Remove duplicates
        all_detections = self._remove_duplicates(all_detections)
        
        # Build final result
        detections = {
            "detections": all_detections,
            "metadata": {
                "image_width": image.width,
                "image_height": image.height,
                "num_detections": len(all_detections),
                "models_used": models_run,
                "models_config": {
                    "general": USE_GENERAL_MODEL,
                    "mobs": USE_MOBS_MODEL,
                    "blocks": USE_BLOCKS_MODEL
                },
                "confidence_threshold": self.confidence_threshold
            }
        }
        
        if all_detections:
            classes = [d["class"] for d in all_detections]
            logger.info(f"   [STAGE 1] Detected: {', '.join(classes)}")
        logger.debug(f"   [STAGE 1] Found {len(all_detections)} unique objects")
        return detections
    
    def _run_single_model(self, api_url: str, image: Image.Image, model_name: str) -> List[Dict]:
        """Run a single Roboflow model"""
        try:
            img_byte_arr = BytesIO()
            image.save(img_byte_arr, format='JPEG', quality=80)
            img_byte_arr.seek(0)
            
            response = requests.post(
                api_url,
                params={"api_key": self.api_key},
                files={"file": ("screenshot.jpg", img_byte_arr, "image/jpeg")},
                timeout=15
            )
            
            response.raise_for_status()
            result = response.json()
            
            detections = []
            for pred in result.get("predictions", []):
                confidence = pred.get("confidence", 0)
                
                if confidence < (self.confidence_threshold / 100):
                    continue
                
                x = pred.get("x", 0)
                y = pred.get("y", 0)
                width = pred.get("width", 0)
                height = pred.get("height", 0)
                
                x1 = int(x - width / 2)
                y1 = int(y - height / 2)
                x2 = int(x + width / 2)
                y2 = int(y + height / 2)
                
                detection = {
                    "class": pred.get("class", "unknown"),
                    "confidence": round(confidence, 3),
                    "class_id": 0,
                    "model_source": model_name,
                    "bbox": {
                        "x1": x1,
                        "y1": y1,
                        "x2": x2,
                        "y2": y2,
                        "width": int(width),
                        "height": int(height),
                        "center_x": int(x),
                        "center_y": int(y)
                    }
                }
                detections.append(detection)
            
            logger.debug(f"      ✓ {model_name}: {len(detections)} objects")
            return detections
        
        except Exception as e:
            logger.error(f"      ✗ {model_name} model failed: {e}")
            return []
    
    def _remove_duplicates(self, detections: List[Dict]) -> List[Dict]:
        """Remove duplicate detections from multiple models"""
        if not detections:
            return []
        
        sorted_dets = sorted(detections, key=lambda x: x['confidence'], reverse=True)
        unique = []
        threshold = 50  # pixels
        
        for det in sorted_dets:
            is_duplicate = False
            
            for unique_det in unique:
                if det['class'].lower() == unique_det['class'].lower():
                    x1 = det['bbox']['center_x']
                    y1 = det['bbox']['center_y']
                    x2 = unique_det['bbox']['center_x']
                    y2 = unique_det['bbox']['center_y']
                    
                    distance = ((x1-x2)**2 + (y1-y2)**2) ** 0.5
                    
                    if distance < threshold:
                        is_duplicate = True
                        break
            
            if not is_duplicate:
                unique.append(det)
        
        return unique


# ============================================================================
# STAGE 2: JIMMY - Text Parser (NO RESPONSES - just parsing)
# ============================================================================

class JimmyParser:
    """Converts raw Roboflow JSON into readable text (Stage 2 ONLY)"""
    
    THREAT_CLASSES = {
        "creeper", "zombie", "skeleton", "enderman", "witch",
        "spider", "cave_spider", "ghast", "blaze", "wither",
        "drowned", "husk", "stray"
    }
    
    RESOURCE_CLASSES = {
        "diamond_ore", "gold_ore", "iron_ore", "copper_ore",
        "coal_ore", "lapis_ore", "emerald_ore", "ancient_debris",
        "diamond", "gold", "iron", "copper", "coal", "lapis",
        "emerald", "netherite"
    }
    
    STATUS_CLASSES = {
        "health_bar", "hunger_bar", "exp_bar", "armor",
        "shield", "effect"
    }
    
    HAZARD_CLASSES = {
        "water", "lava", "fire", "cactus", "void",
        "suffocation", "damage"
    }

    PASSIVE_CLASSES = {
        "cow", "pig", "sheep", "horse", "donkey",
        "duck", "fish", "chicken", "villager",
    }
    
    def parse(self, raw_json: Dict) -> str:
        """Convert Roboflow JSON to readable text (NO natural language generation here)"""
        detections = raw_json.get("detections", [])
        
        if not detections:
            logger.debug("[STAGE 2] No detections to parse")
            return "No objects detected."
        
        threats = []
        hazards = []
        resources = []
        status = []
        passive = []
        
        for detection in detections:
            class_name = detection["class"].lower().strip()
            confidence = detection["confidence"]
            bbox = detection["bbox"]
            
            if confidence < ROBOFLOW_CONFIDENCE_THRESHOLD:
                continue
            
            if class_name in self.THREAT_CLASSES:
                threats.append((class_name, confidence, bbox))
            elif class_name in self.HAZARD_CLASSES:
                hazards.append((class_name, confidence, bbox))
            elif any(res in class_name for res in self.RESOURCE_CLASSES):
                resources.append((class_name, confidence, bbox))
            elif class_name in self.STATUS_CLASSES:
                status.append((class_name, confidence, bbox))
            elif class_name in self.PASSIVE_CLASSES:
                passive.append((class_name, confidence, bbox))
            else:
                logger.debug(f"[STAGE 2] Unrecognized class: {class_name}")
        
        # Build raw description (will be enhanced in Stage 3)
        output_parts = []
        
        if threats:
            threat_text = self._format_threats(threats)
            output_parts.append(threat_text)
        
        if hazards:
            hazard_text = self._format_hazards(hazards)
            output_parts.append(hazard_text)
        
        if resources:
            resource_text = self._format_resources(resources)
            output_parts.append(resource_text)
        
        if status:
            status_text = self._format_status(status)
            output_parts.append(status_text)

        if passive:
            passive_text = self._format_passive(passive)
            output_parts.append(passive_text)
        
        if not output_parts:
            logger.debug("[STAGE 2] No categorized detections")
            return "No objects detected."
        
        result = " ".join(output_parts)
        logger.debug(f"[STAGE 2] Parsed: {result}")
        return result
    
    def _format_threats(self, threats: List[Tuple]) -> str:
        threat_sentences = []
        for name, conf, bbox in sorted(threats, key=lambda x: x[1], reverse=True):
            location = self._get_simple_location(bbox)
            threat_sentences.append(f"{name} in {location}")
        return ", ".join(threat_sentences)
    
    def _format_hazards(self, hazards: List[Tuple]) -> str:
        hazard_sentences = []
        for name, conf, bbox in sorted(hazards, key=lambda x: x[1], reverse=True):
            location = self._get_simple_location(bbox)
            hazard_sentences.append(f"{name} in {location}")
        return ", ".join(hazard_sentences)
    
    def _format_resources(self, resources: List[Tuple]) -> str:
        resource_sentences = []
        for name, conf, bbox in sorted(resources, key=lambda x: x[1], reverse=True):
            location = self._get_simple_location(bbox)
            resource_sentences.append(f"{name} in {location}")
        return ", ".join(resource_sentences)

    def _format_passive(self, passive: List[Tuple]) -> str:
        passive_sentences = []
        for name, conf, bbox in sorted(passive, key=lambda x: x[1], reverse=True):
            location = self._get_simple_location(bbox)
            passive_sentences.append(f"{name} in {location}")
        return ", ".join(passive_sentences)
    
    def _format_status(self, status: List[Tuple]) -> str:
        status_list = []
        for name, conf, bbox in status:
            percent = self._estimate_bar_percent(bbox)
            status_list.append(f"{name}({percent}%)")
        return ", ".join(status_list)
    
    def _get_simple_location(self, bbox: Dict) -> str:
        """Get simplified location"""
        center_x = bbox["center_x"]
        center_y = bbox["center_y"]
        
        if center_x < 213:
            x_pos = "left"
        elif center_x > 427:
            x_pos = "right"
        else:
            x_pos = "center"
        
        if center_y < 120:
            y_pos = "top"
        elif center_y > 240:
            y_pos = "bottom"
        else:
            y_pos = "middle"
        
        return f"{x_pos} {y_pos}"
    
    def _estimate_bar_percent(self, bbox: Dict) -> int:
        max_width = 182
        width = bbox["width"]
        percent = min(100, int((width / max_width) * 100))
        return max(0, percent)


# ============================================================================
# STAGE 3: MAIN AI - Dual Mode with MEMORY (Game + Chat)
# ============================================================================

class LlamaResponseGenerator:
    """Dual-mode Llama with chat memory: Game responses + Chat with context"""
    
    GAME_MODE_PROMPT = """You are Maria, a witty and helpful Minecraft AI companion.
You ALWAYS respond to what you see in the game.

CURRENT ACTIVITY: {recent_game_state}

Based on what you see:
- If threats (zombies/creepers): Give urgent warning
- If resources (diamonds/ores): Show excitement  
- If animals (cows/pigs): Make friendly comment
- If nothing: Suggest something interesting

Keep responses SHORT (max 15 words) and NATURAL.
Just respond directly - no "I see", start with action/emotion.

Examples:
"Watch out! Creeper behind you!"
"Diamond ore! Mine it!"
"Aww, cute little pig there"
"Let's find some trees to chop"
"""
    
    CHAT_MODE_PROMPT = """You are Maria, a witty, friendly Minecraft companion AI chatting with the player.

Background context (for flavor only — do NOT just repeat this back):
- Current activity: {activity_context}
- What's on screen right now: {recent_game_state}
- Earlier conversation:
{conversation_history}

============================================
THE PLAYER JUST SAID TO YOU: "{user_message}"
============================================

Your job: reply directly to what the player just said above. Treat it as
the main thing to respond to — the game context is just flavor you can
sprinkle in if it's naturally relevant, not something to lead with or
repeat verbatim.

Rules:
1. Answer/react to "{user_message}" first and foremost.
2. Only mention the game state if it's actually relevant to what they said.
3. Remember earlier conversation naturally, don't just recite it.
4. If they mentioned a goal before (like "make a house"), remember it.
5. Be a real friend — funny, supportive, natural. Don't refuse or lecture.
   If they curse or joke around, play along like a buddy would.

Keep it SHORT (1-3 sentences) but ENGAGING.
"""

    # Used only for unprompted, streak-based comments (no user_message —
    # the player hasn't typed anything). Same friendly Chat Mode voice,
    # just triggered by time instead of input.
    PROACTIVE_CHAT_PROMPT = """You are Maria, a witty, friendly Minecraft companion AI.
The player hasn't said anything, but you've noticed they've been doing the
same thing for a while — {activity}, for about {streak_seconds} seconds now.

Recent conversation (for tone/context only):
{conversation_history}

Make one short, casual, unprompted comment about this streak — like a friend
noticing what you're up to. Reference the specific activity naturally
("You've been mining a while, find anything good?"). Don't greet them like
you're starting a new conversation, and don't ask more than one question.

Keep it SHORT (1 sentence, maybe 2) and casual.
"""
    
    def __init__(self, chat_memory: ChatMemory):
        self.api_url = LLAMA_API_URL
        self.model_name = LLAMA_MODEL_NAME  # ✅ single model used for BOTH Game and Chat Mode
                                             # (must be a vision-capable tag, e.g. llama3.2-vision,
                                             #  since 6GB VRAM can't hold two models at once)
        self.chat_memory = chat_memory
        self.recent_game_state = "Player is exploring"
        self.recent_game_response = ""  # ✅ ADD THIS
        self.observation_time = time.time()  # ✅ ADD THIS
        self.activity_count = {}  # Track activity frames

        # ✅ PROACTIVE COMMENTS: track how long the current activity has
        # been sustained (time-based, not cycle-based — stays consistent
        # even if MIN_ANALYSIS_INTERVAL is tuned later) and whether Maria
        # has already commented on this particular streak.
        self.current_activity = "exploring"
        self.activity_streak_started_at = time.time()
        self.activity_streak_commented = False
        self.last_user_chat_time = 0.0  # updated whenever the player actually types
        
        logger.info(f"✅ [STAGE 3] Llama initialized with Chat Memory")
        logger.info(f"   Supporting dual modes: Game Mode + Chat Mode")
        logger.info(f"   Model (shared, vision-capable): {self.model_name}")
        logger.info(f"   Memory: Remembers conversation history")

    @staticmethod
    def _image_to_base64(image: Image.Image) -> str:
        """Converts a PIL Image to a base64 string for Ollama's images field."""
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=80)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")
    
    def _check_connection(self) -> bool:
        """Check if Llama server is available"""
        try:
            response = requests.get(f"{self.api_url}/api/tags", timeout=2)
            return response.status_code == 200
        except:
            return False
    
    def detect_activity(self, game_state: str) -> str:
        """Detect if player is doing a repetitive activity"""
        # Simple activity detection
        if "wood" in game_state.lower() or "log" in game_state.lower():
            activity = "mining/chopping wood"
        elif "stone" in game_state.lower() or "ore" in game_state.lower():
            activity = "mining"
        elif "pig" in game_state.lower() or "cow" in game_state.lower():
            activity = "animal nearby"
        elif "zombie" in game_state.lower() or "creeper" in game_state.lower():
            activity = "combat"
        else:
            activity = "exploring"
        
        # Track activity frames
        if activity not in self.activity_count:
            self.activity_count[activity] = 0
        self.activity_count[activity] += 1
        
        # Reset other activities
        for key in self.activity_count:
            if key != activity:
                self.activity_count[key] = 0

        # Streak timer for proactive comments: reset when the activity
        # actually changes, otherwise leave the start time alone so it
        # keeps accumulating real elapsed seconds.
        if activity != self.current_activity:
            self.current_activity = activity
            self.activity_streak_started_at = time.time()
            self.activity_streak_commented = False
        
        self.chat_memory.set_activity_context(activity)
        return activity

    def check_proactive_comment(self) -> Optional[str]:
        """
        Called every analysis cycle (not just when the player types).
        Returns a short unprompted comment if the player has been on the
        same activity for PROACTIVE_STREAK_SECONDS, hasn't been commented
        on yet for this streak, and hasn't chatted recently (so we don't
        talk over an active conversation).
        """
        if self.activity_streak_commented:
            return None

        streak_seconds = time.time() - self.activity_streak_started_at
        if streak_seconds < PROACTIVE_STREAK_SECONDS:
            return None

        time_since_chat = time.time() - self.last_user_chat_time
        if time_since_chat < PROACTIVE_CHAT_COOLDOWN_SECONDS:
            logger.debug("[PROACTIVE] Skipping — player chatted recently")
            return None

        prompt = self.PROACTIVE_CHAT_PROMPT.format(
            activity=self.current_activity,
            streak_seconds=int(streak_seconds),
            conversation_history=self.chat_memory.get_conversation_summary(),
        )

        response = self._call_llama(prompt, mode="PROACTIVE")

        # Mark as commented regardless of success, so a failed/refused
        # attempt doesn't retry every single cycle until the streak ends.
        self.activity_streak_commented = True

        if response:
            self.chat_memory.add_ai_message(response)
        return response
    
    def generate_game_response(self, game_state: str) -> Optional[str]:
        """Generate response to game events (STAGE 3)"""
        if not game_state or game_state == "No objects detected.":
            logger.debug("[STAGE 3] No game state to respond to")
            return None
        
        self.recent_game_state = game_state
        activity = self.detect_activity(game_state)
        
        prompt = self.GAME_MODE_PROMPT.format(
            recent_game_state=game_state
        )
        
        return self._call_llama(prompt, mode="GAME")
    
    def generate_chat_response(self, user_message: str, image: Optional[Image.Image] = None) -> Optional[str]:
        """
        Generate friendly chat response with memory.

        Args:
            user_message: what the player typed
            image: optional PIL screenshot (the "hybrid vision" step). Only
                   actually sent to the model if user_message contains one
                   of VISION_TRIGGER_KEYWORDS (e.g. "can you see", "look at")
                   — otherwise this is plain text chat, even if an image
                   was passed in. Keeps normal chat fast/cheap and only
                   pays the vision cost when the player is asking Maria to
                   look at something (e.g. "can you see how nice this house is?").
        """
        if not user_message or len(user_message.strip()) < 2:
            return None

        # Real chat activity — used to suppress proactive comments while
        # an actual conversation is happening.
        self.last_user_chat_time = time.time()

        # Add user message to memory
        self.chat_memory.add_user_message(user_message)
        
        prompt = self.CHAT_MODE_PROMPT.format(
            conversation_history=self.chat_memory.get_conversation_summary(),
            activity_context=self.chat_memory.activity_context,
            recent_game_state=self.recent_game_state,
            user_message=user_message
        )

        # ✅ Keyword-gated hybrid vision: only attach the screenshot (and
        # pay the slower vision-model cost) if the player's message
        # actually asks Maria to look at something. Otherwise plain text
        # chat is faster and cheaper, even if a screenshot is available.
        wants_vision = image is not None and any(
            kw in user_message.lower() for kw in VISION_TRIGGER_KEYWORDS
        )

        if wants_vision:
            logger.debug(f"[STAGE 3] Vision keyword matched in '{user_message}' — using hybrid vision")
            response = self._call_llama_vision(prompt, image, mode="CHAT")
        else:
            response = self._call_llama(prompt, mode="CHAT")
        
        if response:
            # Add Maria's response to memory
            self.chat_memory.add_ai_message(response)
            return response
        
        return None
    
    def _call_llama(self, prompt: str, mode: str = "GAME") -> Optional[str]:
        """Call Llama API"""
        try:
            response = requests.post(
                f"{self.api_url}/api/generate",
                json={
                    "model": self.model_name,
                    "prompt": prompt,
                    "stream": False,
                    "temperature": 0.8,
                    "top_p": 0.95,
                },
                timeout=30
            )
            response.raise_for_status()
            
            result = response.json()
            text = result.get("response", "").strip()
            
            logger.debug(f"[STAGE 3] [{mode}] Raw response: '{text}'")
            
            if not text or "error" in text.lower():
                logger.warning(f"[STAGE 3] [{mode}] Invalid response")
                return None
            
            if any(phrase in text.lower() for phrase in 
                   ["i cannot", "i'm sorry", "i am not"]):
                logger.warning(f"[STAGE 3] [{mode}] Llama refused: {text}")
                return None
            
            logger.info(f"✅ [STAGE 3] [{mode}] Response: '{text}'")
            return text
        
        except requests.exceptions.ConnectionError:
            logger.error("[STAGE 3] ❌ Cannot connect to Llama server")
            logger.error(f"   Make sure Ollama is running at {self.api_url}")
            return None
        except requests.exceptions.Timeout:
            logger.error("[STAGE 3] ❌ Llama request timeout")
            return None
        except Exception as e:
            logger.error(f"[STAGE 3] ❌ Error: {e}")
            return None

    def _call_llama_vision(self, prompt: str, image: Image.Image, mode: str = "CHAT") -> Optional[str]:
        """
        Call the vision-capable Llama model (Ollama) with a screenshot attached.
        Used only by Chat Mode's hybrid vision step — Game Mode stays text-only
        for speed.
        """
        try:
            encoded_image = self._image_to_base64(image)

            response = requests.post(
                f"{self.api_url}/api/generate",
                json={
                    "model": self.model_name,  # same shared model, just with images[] attached
                    "prompt": prompt,
                    "images": [encoded_image],
                    "stream": False,
                    "temperature": 0.8,
                    "top_p": 0.95,
                },
                timeout=45  # vision calls run slower than text-only
            )
            response.raise_for_status()

            result = response.json()
            text = result.get("response", "").strip()

            logger.debug(f"[STAGE 3] [{mode}-VISION] Raw response: '{text}'")

            if not text or "error" in text.lower():
                logger.warning(f"[STAGE 3] [{mode}-VISION] Invalid response")
                return None

            if any(phrase in text.lower() for phrase in
                   ["i cannot", "i'm sorry", "i am not"]):
                logger.warning(f"[STAGE 3] [{mode}-VISION] Llama refused: {text}")
                return None

            logger.info(f"✅ [STAGE 3] [{mode}-VISION] Response: '{text}'")
            return text

        except requests.exceptions.ConnectionError:
            logger.error("[STAGE 3] ❌ Cannot connect to Llama server (vision)")
            logger.error(f"   Make sure Ollama is running at {self.api_url}")
            return None
        except requests.exceptions.Timeout:
            logger.error("[STAGE 3] ❌ Llama vision request timeout")
            return None
        except Exception as e:
            logger.error(f"[STAGE 3] ❌ Vision error: {e}")
            return None


# ============================================================================
# UNIFIED AI HANDLER - Orchestrates all 3 stages with Chat Memory
# ============================================================================

class AIHandler:
    """Complete 3-stage AI pipeline with Multi-Model Roboflow + Chat Memory"""
    
    def __init__(self):
        logger.info("=" * 70)
        logger.info("🚀 INITIALIZING 3-STAGE AI PIPELINE (MULTI-MODEL + CHAT MEMORY)")
        logger.info("=" * 70)
        
        try:
            self.detector = MultiModelDetector()
        except Exception as e:
            logger.error(f"❌ Failed to initialize Stage 1: {e}")
            raise
        
        self.parser = JimmyParser()
        logger.info("✅ [STAGE 2] Jimmy parser initialized")
        
        # NEW: Chat Memory
        self.chat_memory = ChatMemory(max_history=10)
        
        self.generator = LlamaResponseGenerator(self.chat_memory)
        
        if not self.generator._check_connection():
            logger.warning("⚠️  [STAGE 3] Llama not available (yet)")
            logger.warning("   Make sure Ollama is running: ollama serve")
        
        self.frame_buffer = FrameBuffer(max_frames=3)
        logger.info("✅ [FRAME BUFFER] Short-term memory initialized (3 frames)")
        
        logger.info("=" * 70)
        logger.info("✅ ALL STAGES INITIALIZED (WITH CHAT MEMORY)")
        logger.info("=" * 70)
    
    def analyze_screenshot(self, image: Image.Image) -> Optional[Dict]:
        """Full 3-stage pipeline for game events"""
        threading.current_thread().name = "AIHandler"
        
        logger.info("=" * 70)
        logger.info("🔄 STARTING ANALYSIS PIPELINE (3 MODELS + MEMORY)")
        logger.info("=" * 70)
        
        timing = {}
        
        # STAGE 1
        logger.info("📷 STAGE 1: Multi-Model Roboflow detection...")
        stage1_start = time.time()
        
        try:
            detections_json = self.detector.detect(image)
            timing["stage1"] = time.time() - stage1_start
            logger.info(f"   ✅ Complete ({timing['stage1']:.2f}s)")
            logger.info(f"   Found {len(detections_json['detections'])} objects")
        except Exception as e:
            logger.error(f"   ❌ Stage 1 failed: {e}")
            return None
        
        # Add to frame buffer
        logger.info("📸 Adding frame to buffer...")
        self.frame_buffer.add_frame(image, detections_json)
        logger.debug(f"   Buffer size: {self.frame_buffer.size()}/3")
        
        # Analyze trends
        trends = None
        if self.frame_buffer.is_full():
            trends = self.frame_buffer.analyze_trends()
            logger.info(f"📊 Trend Analysis: {trends['threat_level'].upper()}")
        
        # STAGE 2
        logger.info("📝 STAGE 2: Jimmy parsing...")
        stage2_start = time.time()
        
        try:
            game_state = self.parser.parse(detections_json)
            timing["stage2"] = time.time() - stage2_start
            logger.info(f"   ✅ Complete ({timing['stage2']:.2f}s)")
            logger.info(f"   Parsed: '{game_state}'")
        except Exception as e:
            logger.error(f"   ❌ Stage 2 failed: {e}")
            return None
        
        if game_state == "No objects detected.":
            # Still track activity/streak for proactive Chat Mode comments,
            # even when Game Mode itself has nothing worth saying.
            self.generator.detect_activity(game_state)
            logger.info("💤 No urgent game state - staying silent")
            logger.info("=" * 70)
            return None
        
        # STAGE 3 - GAME MODE (With memory)
        logger.info("🤖 STAGE 3: Main AI response (Game Mode + Memory)...")
        stage3_start = time.time()
        
        try:
            response = self.generator.generate_game_response(game_state)
            timing["stage3"] = time.time() - stage3_start
            
            if not response:
                logger.warning("   ⚠️  No response generated")
                return None
            
            # ✅ CHANGE 1: Store game response in ChatMemory!
            self.chat_memory.add_ai_message(response)
            
            # ✅ CHANGE 2: Track recent observations
            self.recent_game_state = game_state
            self.recent_game_response = response
            self.observation_time = time.time()
            
            logger.info(f"   ✅ Complete ({timing['stage3']:.2f}s)")
            logger.info(f"   Response: '{response}'")
        except Exception as e:
            logger.error(f"   ❌ Stage 3 failed: {e}")
            return None
        
        timing["total"] = sum([timing.get("stage1", 0), 
                               timing.get("stage2", 0), 
                               timing.get("stage3", 0)])
        
        logger.info("=" * 70)
        logger.info(f"✅ ANALYSIS COMPLETE ({timing['total']:.2f}s total)")
        logger.info(f"   CV: {timing['stage1']:.2f}s | Text: {timing['stage2']:.2f}s | LLM: {timing['stage3']:.2f}s")
        logger.info("=" * 70)
        
        return {
            "response": response,
            "game_state": game_state,
            "detections": detections_json,
            "timing": timing,
            "timestamp": datetime.now().isoformat()
        }
    
    def generate_chat_response(self, user_message: str, image: Optional[Image.Image] = None) -> Optional[str]:
        """
        Generate friendly chat response with memory.

        Args:
            user_message: what the player typed
            image: optional PIL screenshot — pass app_context['latest_screenshot']
                   to enable the hybrid vision step (Maria can "see" things
                   Roboflow doesn't detect, like a player-built house).
        """
        mode_label = "Chat Mode + Vision + Memory" if image is not None else "Chat Mode + Memory"
        logger.info(f"🤖 STAGE 3: Main AI response ({mode_label})...")
        
        try:
            response = self.generator.generate_chat_response(user_message, image=image)
            
            if response:
                logger.info(f"   ✅ Response: '{response}'")
                return response
            else:
                logger.warning("   ⚠️  No response generated")
                return None
        
        except Exception as e:
            logger.error(f"   ❌ Stage 3 (Chat) failed: {e}")
            return None

    def check_proactive_comment(self) -> Optional[str]:
        """
        Call once per vision-loop cycle (independent of whether Game Mode
        itself spoke). Returns an unprompted Chat-Mode-style comment if the
        player has stayed on the same activity for PROACTIVE_STREAK_SECONDS
        and hasn't chatted recently — otherwise returns None.
        """
        try:
            return self.generator.check_proactive_comment()
        except Exception as e:
            logger.error(f"   ❌ Proactive comment check failed: {e}")
            return None 