from msgspec import Struct, field

from .video import _prefer_url


class PlayAddr(Struct):
    url_list: list[str]


class Cover(Struct):
    url_list: list[str]


class Video(Struct):
    play_addr: PlayAddr
    cover: Cover
    duration: int


class Image(Struct):
    video: Video | None = None
    url_list: list[str] = field(default_factory=list)


class Avatar(Struct):
    url_list: list[str]


class Author(Struct):
    nickname: str
    # avatar_larger: Avatar
    avatar_thumb: Avatar


class SlidesData(Struct):
    author: Author
    desc: str
    create_time: int
    images: list[Image]

    @property
    def name(self) -> str:
        return self.author.nickname

    @property
    def avatar_url(self) -> str:
        return _prefer_url(self.author.avatar_thumb.url_list)

    @property
    def image_urls(self) -> list[str]:
        return [_prefer_url(image.url_list) for image in self.images if image.url_list]

    @property
    def dynamic_urls(self) -> list[str]:
        return [
            _prefer_url(image.video.play_addr.url_list)
            for image in self.images
            if image.video and image.video.play_addr.url_list
        ]


class SlidesInfo(Struct):
    aweme_details: list[SlidesData] = field(default_factory=list)
