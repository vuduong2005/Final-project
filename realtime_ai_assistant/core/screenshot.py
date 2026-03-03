import mss
from PIL import Image
from datetime import datetime
from utils.logger import get_logger

logger = get_logger()

# --- REMOVED Global _sct object ---
# We will create it inside the function to ensure thread-safety

def capture_screenshot(save=False):
    """
    Captures the primary monitor and returns a PIL Image object using mss.
    This function is now thread-safe by creating the mss object locally.
    
    Args:
        save (bool): If True, saves the screenshot to a file.
        
    Returns:
        PIL.Image.Image: The captured screenshot image object, or None if failed.
    """
    try:
        # --- THREAD-SAFETY FIX ---
        # Create the mss object *inside* the function using a context manager.
        # This ensures it's created and destroyed in the correct thread (the VisionLoop).
        with mss.mss() as sct:
            # -------------------------
            
            # Get information of the primary monitor (monitor 1 in mss list)
            monitor = sct.monitors[1] 
            
            # Capture the image data
            sct_img = sct.grab(monitor)
            
            # Convert the captured data to a PIL Image object
            screenshot_pil = Image.frombytes(
                "RGB", 
                (sct_img.width, sct_img.height), 
                sct_img.rgb, 
                "raw", 
                "RGB"
            )
            
            if save:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                screenshot_pil.save(f"screenshot_{timestamp}.png")
                
            return screenshot_pil

    except Exception as e:
        logger.error(f"ERROR: Failed to capture screenshot with mss: {e}")
        return None


