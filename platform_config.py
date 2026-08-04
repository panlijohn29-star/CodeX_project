import os


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_dotenv(path=None):
    env_path = path or os.path.join(BASE_DIR, ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


load_dotenv()


def env_value(name, default=None):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def env_int(name, default):
    value = env_value(name)
    if value is None:
        return default
    return int(value)


def require_env(name):
    value = env_value(name)
    if value is None:
        raise RuntimeError("Missing required environment variable: {0}".format(name))
    return value


def get_db_config(profile):
    prefix = profile.upper()
    host = env_value("{0}_HOST".format(prefix), env_value("DB_HOST"))
    user = env_value("{0}_USER".format(prefix), env_value("DB_USER"))
    password = env_value("{0}_PASSWORD".format(prefix), env_value("DB_PASSWORD"))
    database = env_value("{0}_DATABASE".format(prefix), env_value("{0}_DB".format(prefix)))
    port = env_int("{0}_PORT".format(prefix), env_int("DB_PORT", 3306))

    missing = []
    for key, value in (
        ("{0}_HOST or DB_HOST".format(prefix), host),
        ("{0}_USER or DB_USER".format(prefix), user),
        ("{0}_PASSWORD or DB_PASSWORD".format(prefix), password),
        ("{0}_DATABASE or {0}_DB".format(prefix), database),
    ):
        if not value:
            missing.append(key)
    if missing:
        raise RuntimeError("Missing database configuration: {0}".format(", ".join(missing)))

    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "db": database,
        "charset": "utf8mb4",
        "connect_timeout": env_int("DB_CONNECT_TIMEOUT", 10),
        "read_timeout": env_int("DB_READ_TIMEOUT", 600),
        "write_timeout": env_int("DB_WRITE_TIMEOUT", 600),
    }
