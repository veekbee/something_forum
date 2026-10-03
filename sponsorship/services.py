import hashlib


def hash_token(token):
    """Invitation tokens are stored only as this hash; the token itself goes out by email."""
    return hashlib.sha256(token.encode()).hexdigest()
