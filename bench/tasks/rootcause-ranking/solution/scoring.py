def sort_key(player):
    """Order players by score, then by name."""
    return (player["score"], player["name"])
