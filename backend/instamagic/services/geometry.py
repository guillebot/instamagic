from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    width: int
    height: int

    def expanded(self, margin: int) -> Box:
        return Box(
            x=self.x - margin,
            y=self.y - margin,
            width=self.width + margin * 2,
            height=self.height + margin * 2,
        )

    def intersects(self, other: Box) -> bool:
        return (
            self.x < other.x + other.width
            and self.x + self.width > other.x
            and self.y < other.y + other.height
            and self.y + self.height > other.y
        )

    def contains(self, other: Box) -> bool:
        return (
            self.x <= other.x
            and self.y <= other.y
            and self.x + self.width >= other.x + other.width
            and self.y + self.height >= other.y + other.height
        )

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    @property
    def cx(self) -> float:
        return self.x + self.width / 2

    @property
    def cy(self) -> float:
        return self.y + self.height / 2
