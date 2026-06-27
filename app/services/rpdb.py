"""
Backward compatibility re-export module for rpdb.py.
All poster resolution and validation logic has moved to poster_service.py.
"""

from app.services.poster_service import *  # noqa: F401, F403
