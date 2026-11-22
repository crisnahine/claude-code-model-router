def render(rows):
    # TODO: paginate long reports
    return "\n".join(", ".join(map(str, row)) for row in rows)
