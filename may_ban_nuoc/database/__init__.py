"""
database/__init__.py
====================
Export DatabaseManager để import gọn hơn từ bên ngoài.

Cách dùng sau khi có file này:
    from database import DatabaseManager       # thay vì
    from database.db_manager import DatabaseManager
"""

from database.db_manager import DatabaseManager

__all__ = ["DatabaseManager"]