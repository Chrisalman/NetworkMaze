from __future__ import annotations
import random

# Fixed maze layout. "#" is wall, " " is floor.
# Edit this to change the map; every row must be the same length and the
# outer border should stay solid wall.
LAYOUT = [
    "#####################################################",
    "###               #       #                       ###",
    "###               #       #                       ###",
    "###   #           #                               ###",
    "###   #   #   #   #   #         ###########   #   ###",
    "###   #   #           #               #       #   ###",
    "###       #           #               #       #   ###",
    "###       #           #               #       #   ###",
    "###   #   #         ###         #######   #####   ###",
    "###   #   #           #               #   #       ###",
    "###   #   #   #                       #   #       ###",
    "###   #   #   #                       #   #       ###",
    "###   #   #####   #                       #   #######",
    "###   #           #                   #           ###",
    "###   #           #                   #           ###",
    "###   #           #                   #           ###",
    "###      ##   #############       #####           ###",
    "###           #           #       #               ###",
    "###           #           #       #               ###",
    "###           #           #       #               ###",
    "###       #####       #   #       #############   ###",
    "###                   #                           ###",
    "###      #            #                           ###",
    "###      #            #                           ###",
    "#####################################################",
]


class Maze:
#def __init__(self, width: int | None = None, height: int | None = None,seed: int | None = None, layout: list[str] = LAYOUT):
    def __init__(self):
        layout=LAYOUT
        # width/height are ignored; the size comes from the layout.
        # They're kept so existing calls like Maze(51, 25, seed) still work.
       #  if len({len(row) for row in layout}) != 1:
        #    raise ValueError("All layout rows must be the same length")
        self.height = len(layout)
        self.width = len(layout[0])
        # The RNG is still used for random_floor (spawn points).
        self.rng = random.Random("12")
        self.grid = [list(row) for row in layout]

    def walkable(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height and self.grid[y][x] == " "

    def random_floor(self, occupied=()) -> tuple[int, int]:
        blocked = set(occupied)
        spaces = [(x, y) for y, row in enumerate(self.grid) for x, c in enumerate(row)
                  if c == " " and (x, y) not in blocked]
        return self.rng.choice(spaces)

    def rows(self) -> list[str]:
        return ["".join(row) for row in self.grid]


def visible_cells(maze: Maze, cx: int, cy: int, radius: int = 16) -> set[tuple[int, int]]:
    """Return the set of tiles visible from (cx, cy) within a circular radius.

    Every wall inside the circle is always visible, even behind other walls,
    so the maze outline within the radius is revealed.

    Floor tiles still need line of sight: walk a straight line from the
    player to the tile, and stop at the first wall in the way.
    """
    seen = {(cx, cy)}  # the player's own tile is always visible
    r2 = radius * radius

    # Only scan the square bounding box around the player, clipped to the map.
    for y in range(max(0, cy - radius), min(maze.height, cy + radius + 1)):
        for x in range(max(0, cx - radius), min(maze.width, cx + radius + 1)):
            # Skip the corners of the box that fall outside the circle.
            if (x - cx) ** 2 + (y - cy) ** 2 > r2:
                continue

            # Walls in range are always visible; no ray needed.
            if maze.grid[y][x] == "#":
                seen.add((x, y))
                continue

            # Take one step per tile along the longer axis, so the ray
            # never skips over a tile.
            steps = max(abs(x - cx), abs(y - cy))

            for i in range(1, steps + 1):
                # Point i/steps of the way from the player to the target.
                px = round(cx + (x - cx) * i / steps)
                py = round(cy + (y - cy) * i / steps)
                seen.add((px, py))

                # A wall is visible, but it blocks everything beyond it.
                if maze.grid[py][px] == "#":
                    break

    return seen


def visible_cellsv1(maze: Maze, cx: int, cy: int, radius: int = 8) -> set[tuple[int, int]]:
    """Circular fog with simple ray casting to stop vision behind walls."""
    seen={(cx, cy)}
    r2=radius*radius
    for y in range(max(0,cy-radius), min(maze.height,cy+radius+1)):
        for x in range(max(0,cx-radius), min(maze.width,cx+radius+1)):
            if (x-cx)**2+(y-cy)**2 > r2:
                continue
            steps=max(abs(x-cx),abs(y-cy),1)
            clear=True
            for i in range(1, steps+1):
                px=round(cx+(x-cx)*i/steps); py=round(cy+(y-cy)*i/steps)
                seen.add((px,py))
                if maze.grid[py][px] == "#" and (px,py)!=(x,y):
                    clear=False; break
            if clear: seen.add((x,y))
    return seen