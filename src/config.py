from __future__ import annotations

from typing import Any

"""Journal configuration and mapping."""

JOURNAL_MAP: dict[str, dict[str, str]] = {
    "cell": {
        "abbr": "cell",
        "full_name": "Cell",
        "path_name": "Cell",
    },
    "immunity": {
        "abbr": "immunity",
        "full_name": "Immunity",
        "path_name": "Cell_Immunity",
    },
    "nature": {
        "abbr": "nature",
        "full_name": "Nature",
        "path_name": "Nature",
    },
    "ni": {
        "abbr": "ni",
        "full_name": "Nature Immunology",
        "path_name": "Nature_Immunology",
    },
    "science": {
        "abbr": "science",
        "full_name": "Science",
        "path_name": "Science",
    },
    "sciimmunol": {
        "abbr": "sciimmunol",
        "full_name": "Science Immunology",
        "path_name": "Science_Immunology",
    },
}


def get_journal_info(journal_name: str) -> dict[str, str] | None:
    """Get journal info by any name/abbreviation.
    
    Args:
        journal_name: The name or abbreviation of the journal.
        
    Returns:
        A dictionary containing journal info or None if not found.
    """
    if not journal_name:
        return None
    
    name_lower = journal_name.lower()
    
    # Direct match with key
    if name_lower in JOURNAL_MAP:
        return JOURNAL_MAP[name_lower]
    
    # Match with full name or path name
    for info in JOURNAL_MAP.values():
        if name_lower == info["full_name"].lower() or name_lower == info["path_name"].lower():
            return info
            
    return None
