import bcrypt

def hash_password(plain: str) -> str:
    """Zwraca zahashowane hasło jako string (do zapisu w Firestore)."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain: str, hashed: str) -> bool:
    """Sprawdza czy plain pasuje do zapisanego hasha."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False

def is_hashed(password: str) -> bool:
    """Wykrywa czy hasło jest już zahashowane (zaczyna się od $2b$)."""
    return password.startswith("$2b$") or password.startswith("$2a$")