"""Stand-in for the private module (what Maria remembers).
The real code is in core/_private/memory.py, which is not part of the public repository.
With that folder present, this name simply IS the real module."""
import sys

from core._private import memory as _real

sys.modules[__name__] = _real
