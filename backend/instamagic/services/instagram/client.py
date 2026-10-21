class InstagramClient:
    def configured(self) -> bool:
        return False

    def fetch_media(self) -> list[dict[str, object]]:
        from instamagic.errors import InstagramNotConfigured

        raise InstagramNotConfigured(
            "Instagram credentials are not used in this milestone. Official import is not wired."
        )
