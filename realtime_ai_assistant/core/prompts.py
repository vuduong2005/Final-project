"""Stand-in for the private module (what Maria is told and how her replies are cleaned).
The real code is in core/_private/prompts.py, which is not part of the public repository.
With that folder present, this name simply IS the real module."""
import sys

from core._private import prompts as _real

sys.modules[__name__] = _real
