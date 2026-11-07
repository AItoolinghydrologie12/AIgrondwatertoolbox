"""Grondwatertoolbox — peilopzet en grondwaterstroming op een rooster (MODFLOW 6).

The page is built by ``toolbox_page.main``; an unexpected error becomes a readable message
instead of a traceback."""

from toolbox_page import main
from ui_common import friendly_errors

with friendly_errors():
    main()
