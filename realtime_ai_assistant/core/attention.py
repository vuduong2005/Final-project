"""Stand-in for the private module (when Maria speaks).
The real code is in core/_private/attention.py, which is not part of the public repository.
With that folder present, this name simply IS the real module."""
import sys

from core._private import attention as _real

sys.modules[__name__] = _real
