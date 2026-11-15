"""Stand-in for the private module (the three-stage pipeline and the reply generator).
The real code is in core/_private/ai_handler.py, which is not part of the public repository.
With that folder present, this name simply IS the real module."""
import sys

from core._private import ai_handler as _real

sys.modules[__name__] = _real
