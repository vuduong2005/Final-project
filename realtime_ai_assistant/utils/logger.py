import logging
import sys

# Configure the logger to output to the console
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] [%(threadName)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

def get_logger():
    """Returns the main configured logger instance."""
    # Use a unique name for the logger
    return logging.getLogger("AssistantLogger")

# Example usage (you would replace your print() statements with this)
# logger = get_logger()
# logger.info("Application starting up...")