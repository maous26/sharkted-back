import os

DATABASE_URL = os.getenv("DATABASE_URL", "")
JWT_SECRET = os.getenv("JWT_SECRET", "change_me_now")
JWT_ALGO = "HS256"
