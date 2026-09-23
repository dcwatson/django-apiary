ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "testapp",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

ROOT_URLCONF = "testapp.urls"
