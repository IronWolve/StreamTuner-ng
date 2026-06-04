"""Built-in channel registry. (User plugins will also be discovered from a
user-plugins folder later.)"""

from .audioaddict import AudioAddict
from .bookmarks import Bookmarks
from .fip import Fip
from .iheart import IHeart
from .jamendo import Jamendo
from .litt import Litt
from .live365 import Live365
from .local import Local
from .nightride import Nightride
from .radiobrowser import RadioBrowser
from .radioparadise import RadioParadise
from .shoutcast import Shoutcast
from .somafm import SomaFM
from .tunein import TuneIn

# Order here is the sidebar order. AudioAddict's six networks are now one channel
# (the networks are its categories), so it's a single sidebar/Options entry.
BUILTINS = [Bookmarks, RadioBrowser, SomaFM, RadioParadise, Fip, Nightride, Jamendo,
            Shoutcast, TuneIn, Live365, IHeart, Litt, AudioAddict, Local]
