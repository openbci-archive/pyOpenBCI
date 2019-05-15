import sys
from .cyton import OpenBCICyton
from .utils import *
from .wifi import OpenBCIWiFi
if sys.platform.startswith("linux"):
    from .ganglion import OpenBCIGanglion
