"""Internal lists plus Python providers discovered beside the installed app."""
from ..loader import user_plugin_dir, load_installed_plugins
from .bookmarks import Bookmarks
from .local import Local

# Preserve package-relative imports for supplied and user-authored providers.
# This directory is derived from the executable/module, never the working directory.
__path__.insert(0, str(user_plugin_dir()))
INSTALLED_PLUGINS, PLUGIN_ERRORS = load_installed_plugins(__name__)
_ORDER = ('radiobrowser', 'somafm', 'radioparadise', 'fip', 'nightride', 'jamendo',
          'shoutcast', 'tunein', 'live365', 'iheart', 'litt', 'audioaddict')
INSTALLED_PLUGINS.sort(key=lambda cls: (_ORDER.index(cls.id) if cls.id in _ORDER else len(_ORDER),
                                        str(getattr(cls, 'id', '') or cls.__name__)))
BUILTINS = [Bookmarks, *INSTALLED_PLUGINS, Local]
