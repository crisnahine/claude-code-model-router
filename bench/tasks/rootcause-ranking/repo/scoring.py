def sort_key(player):
    """Order players by score, then by name."""
    return (str(player["score"]), player["name"])
