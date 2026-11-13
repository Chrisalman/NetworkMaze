"""Terminal client for the multiplayer maze game.

How it works, in one paragraph:
    1. We open a TCP connection to the server and send a "join" message.
    2. A background thread listens for messages from the server and drops
       them into a queue (the "inbox").
    3. The main thread runs a simple game loop ~20 times a second:
         - take the newest game state out of the inbox
         - read one key press and send the matching action to the server
         - redraw the whole screen from the latest state
    The server is in charge of the game; this client only shows the state
    it is sent and forwards key presses.

Usage:
    python -m your_package.client_simplified --host 127.0.0.1 --port 8765 --name Chris
    python client_simplified.py --host 127.0.0.1 --port 8765 --name Chris
"""

from __future__ import annotations

import argparse
import curses
import queue
import socket
import threading
import time

# protocol.py handles turning dicts into bytes and back again.
# The try/except lets this file run both as part of a package and on its own.
try:
    from .protocol import receive_messages, send_message
except ImportError:
    from protocol import receive_messages, send_message


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

CONNECT_TIMEOUT = 8      # seconds to wait when first connecting
FRAME_DELAY = 0.05       # seconds between frames (about 20 frames per second)
SIDEBAR_WIDTH = 30       # columns reserved on the right for score/HP/log
COUNTER_TIME = 3         # seconds the server gives you to counter a spell

# Colour pair numbers (set up in setup_curses)
CYAN, RED, YELLOW, GREEN = 1, 2, 3, 4


# ---------------------------------------------------------------------------
# Key bindings
# ---------------------------------------------------------------------------

# Number keys cast spells.
SPELL_KEYS = {
    ord("1"): "rock",
    ord("2"): "paper",
    ord("3"): "scissors",
    ord("4"): "fireball",
    ord("5"): "poison",
    ord("6"): "freeze",
    ord("7"): "lightning",
    ord("8"): "shield",
    ord("9"): "heal",
}

# Arrow keys and WASD both move.
MOVE_KEYS = {
    curses.KEY_UP: "up",
    curses.KEY_DOWN: "down",
    curses.KEY_LEFT: "left",
    curses.KEY_RIGHT: "right",
    ord("w"): "up",
    ord("s"): "down",
    ord("a"): "left",
    ord("d"): "right",
}

# Your own player flips between two characters to look animated.
PLAYER_FRAMES = {
    "right": (">", "}"),
    "left": ("<", "{"),
    "up": ("^", "A"),
    "down": ("v", "V"),
}

ENTER_KEYS = (10, 13)
BACKSPACE_KEYS = (curses.KEY_BACKSPACE, 127, 8)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def put(window, y, x, text, attributes=0):
    """Write text at (y, x), quietly skipping anything that is off-screen.

    curses raises an error if you write outside the window, so every
    piece of drawing goes through this function instead.
    """
    height, width = window.getmaxyx()
    if y < 0 or y >= height or x >= width:
        return

    x = max(0, x)
    room = width - x - 1  # leave the last column empty to avoid curses errors
    if room <= 0:
        return

    try:
        window.addnstr(y, x, str(text), room, attributes)
    except curses.error:
        pass


def bar(filled, total=20, fill="="):
    """Return a fixed-width bar such as '[=====     ]'."""
    filled = max(0, min(total, filled))
    return "[" + fill * filled + " " * (total - filled) + "]"


def setup_curses(screen):
    """Hide the cursor, make getch() non-blocking, and set up colours."""
    try:
        curses.curs_set(0)
    except curses.error:
        pass  # some terminals can't hide the cursor; that's fine

    screen.nodelay(True)  # getch() returns -1 straight away if no key is pressed
    screen.keypad(True)   # turn arrow keys into curses.KEY_* values

    try:
        curses.start_color()
        curses.use_default_colors()  # -1 below means "terminal's own background"
        curses.init_pair(CYAN, curses.COLOR_CYAN, -1)
        curses.init_pair(RED, curses.COLOR_RED, -1)
        curses.init_pair(YELLOW, curses.COLOR_YELLOW, -1)
        curses.init_pair(GREEN, curses.COLOR_GREEN, -1)
    except curses.error:
        pass  # no colour support; the game still works in plain text


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------

class Client:
    """Talks to the maze server and draws the game in the terminal."""

    def __init__(self, host, port, name):
        # Connect, then switch the socket back to blocking mode (no timeout)
        # so the reader thread can wait for messages for as long as it needs.
        self.socket = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
        self.socket.settimeout(None)

        self.send_lock = threading.Lock()  # stops two sends interleaving their bytes
        self.inbox = queue.Queue()          # messages from the reader thread
        self.running = True                 # set to False to shut everything down
        self.state = None                   # latest game state from the server
        self.typed = ""                     # letters typed during a counter challenge

        self.send({"type": "join", "name": name})

        # daemon=True means this thread won't stop Python from exiting.
        threading.Thread(target=self.listen, daemon=True).start()

    # ----- Networking ------------------------------------------------------

    def listen(self):
        """Background thread: read server messages and put them in the inbox."""
        buffer = bytearray()  # holds partial messages between reads
        try:
            while self.running:
                for message in receive_messages(self.socket, buffer):
                    self.inbox.put(message)
        except (OSError, ConnectionError, ValueError):
            self.running = False  # connection lost or bad data: stop the game

    def send(self, message):
        """Send one message to the server. Stops the client if sending fails."""
        if not self.running:
            return
        try:
            send_message(self.socket, message, self.send_lock)
        except OSError:
            self.running = False

    def close(self):
        """Shut the connection down, ignoring errors if it's already closed."""
        self.running = False
        for step in (lambda: self.socket.shutdown(socket.SHUT_RDWR), self.socket.close):
            try:
                step()
            except OSError:
                pass

    # ----- Main loop -------------------------------------------------------

    def run(self, screen):
        """The game loop. curses.wrapper calls this and tidies up afterwards."""
        setup_curses(screen)
        frame = 0
        try:
            while self.running:
                self.read_inbox()
                self.handle_key(screen.getch())
                self.draw(screen, frame)
                frame += 1
                time.sleep(FRAME_DELAY)
        finally:
            self.close()

    def read_inbox(self):
        """Empty the inbox, keeping only the most recent game state."""
        while not self.inbox.empty():
            message = self.inbox.get_nowait()
            if message.get("type") == "state":
                self.state = message

    def active_challenge(self):
        """Return the spell you currently have to counter, or None."""
        if not self.state:
            return None
        for challenge in self.state.get("challenges", []):
            if challenge.get("active"):
                return challenge
        return None

    # ----- Input -----------------------------------------------------------

    def handle_key(self, key):
        """Turn one key press into an action and send it to the server."""
        if key == -1:  # no key pressed this frame
            return

        if key in (ord("q"), ord("Q")):
            self.running = False
            return

        # While someone's spell is being countered, the keyboard is for typing.
        challenge = self.active_challenge()
        if challenge:
            self.type_counter_letter(key, challenge)
            return

        if key in MOVE_KEYS:
            self.send({"action": "move", "direction": MOVE_KEYS[key]})
        elif key == ord(" "):
            self.send({"action": "shoot"})
        elif key in SPELL_KEYS:
            self.send({"action": "cast", "spell": SPELL_KEYS[key]})

    def type_counter_letter(self, key, challenge):
        """Build up the counter word: letters add, Backspace removes, Enter sends."""
        if key in ENTER_KEYS:
            self.send({
                "action": "counter",
                "challenge_id": challenge["id"],
                "word": self.typed,
            })
            self.typed = ""
        elif key in BACKSPACE_KEYS:
            self.typed = self.typed[:-1]
        elif 32 <= key <= 126 and chr(key).isalpha():
            self.typed += chr(key).lower()

    # ----- Drawing ---------------------------------------------------------

    def draw(self, window, frame):
        """Redraw the whole screen from the latest game state."""
        window.erase()

        if not self.state:
            put(window, 0, 0, "Connecting to maze server...")
            window.refresh()
            return

        state = self.state
        me = next((p for p in state["players"] if p["id"] == state["you"]), None)

        # The maze area is whatever space is left after the sidebar (left/right)
        # and the control/challenge lines at the bottom.
        screen_h, screen_w = window.getmaxyx()
        view_w = max(10, screen_w - SIDEBAR_WIDTH - 1)
        view_h = max(8, screen_h - 5)

        # The maze is usually bigger than the screen, so we show a window
        # onto it ("camera") centred on our player.
        left, top = self.camera_position(state["maze"], me, view_w, view_h)

        self.draw_maze(window, state, left, top, view_w, view_h)
        self.draw_things(window, state, left, top, frame)
        self.draw_sidebar(window, state, me, view_w + 1, screen_h)
        self.draw_bottom(window, min(screen_h - 4, view_h))

        window.refresh()

    @staticmethod
    def camera_position(maze, me, view_w, view_h):
        """Work out the maze coordinates of the top-left corner of the view.

        We try to centre on the player, but clamp so the view never goes
        past the edges of the maze.
        """
        x = me["x"] if me else 0
        y = me["y"] if me else 0
        left = max(0, min(len(maze[0]) - view_w, x - view_w // 2))
        top = max(0, min(len(maze) - view_h, y - view_h // 2))
        return left, top

    @staticmethod
    def draw_maze(window, state, left, top, view_w, view_h):
        """Draw the walls/floor you can see; hidden squares are left blank."""
        maze = state["maze"]
        visible = {tuple(pos) for pos in state["visible"]}

        for row in range(view_h):
            y = top + row
            if y >= len(maze):
                break
            line = ""
            for x in range(left, min(left + view_w, len(maze[y]))):
                line += maze[y][x] if (x, y) in visible else " "
            put(window, row, 0, line)

    @staticmethod
    def draw_things(window, state, left, top, frame):
        """Draw pickups, then projectiles, then players (so players are on top).

        Every object has maze coordinates; subtracting the camera position
        turns them into screen coordinates.
        """
        for pickup in state.get("pickups", []):
            put(window, pickup["y"] - top, pickup["x"] - left,
                pickup["glyph"], curses.color_pair(GREEN))

        for shot in state.get("projectiles", []):
            put(window, shot["y"] - top, shot["x"] - left,
                shot["glyph"], curses.color_pair(YELLOW))

        for player in state["players"]:
            if not player["alive"]:
                continue
            if player["id"] == state["you"]:
                # You: an animated arrow in cyan, swapping every 3 frames.
                frames = PLAYER_FRAMES.get(player["direction"], ("?", "?"))
                glyph = frames[(frame // 3) % 2]
                colour = curses.color_pair(CYAN)
            else:
                # Everyone else: the last digit of their ID, in red.
                glyph = str(player["id"] % 10)
                colour = curses.color_pair(RED)
            put(window, player["y"] - top, player["x"] - left, glyph, colour)

    @staticmethod
    def draw_sidebar(window, state, me, x, screen_h):
        """Right-hand panel: scoreboard (rows 0-8), health (10-11), log (13+)."""
        put(window, 0, x, "SCOREBOARD", curses.A_BOLD)
        for row, p in enumerate(state.get("scoreboard", [])[:8], start=1):
            # e.g. "3: Chris        5/2"  (kills/deaths)
            put(window, row, x, f'{p["id"]}: {p["name"][:12]:12} {p["score"]}/{p["deaths"]}')

        if me:
            hp = max(0, me["hp"])
            max_hp = max(1, me["max_hp"])  # avoid dividing by zero
            put(window, 10, x, f'HP {hp:3}/{max_hp}  Shield {me["shield"]:3}')
            put(window, 11, x, bar(int(20 * hp / max_hp)), curses.color_pair(GREEN))

        put(window, 13, x, "COMBAT LOG", curses.A_BOLD)
        lines_that_fit = max(0, min(8, screen_h - 15))
        for i, line in enumerate(state.get("logs", [])[:lines_that_fit]):
            put(window, 14 + i, x, line[:SIDEBAR_WIDTH - 2])

    def draw_bottom(self, window, row):
        """Controls help line, plus the counter-spell prompt when one is active."""
        put(window, row, 0, "Move arrows/WASD | Space bullet | 1-9 spells | q quit")

        challenge = self.active_challenge()
        if not challenge:
            return

        length = challenge["counter_length"]
        remaining = challenge["remaining"]
        queued = max(0, len(self.state.get("challenges", [])) - 1)

        # Line 1: who cast what
        put(window, row + 1, 0,
            f'!!! {challenge["caster_name"]} cast {challenge["spell"]} | '
            f'counter ({length} letters) !!!',
            curses.color_pair(RED) | curses.A_BOLD)

        # Line 2: countdown bar that shrinks as time runs out
        timer = bar(int(20 * remaining / COUNTER_TIME), fill="#")
        put(window, row + 2, 0,
            f"TIME {timer} {remaining:0.1f}s  QUEUED: {queued}",
            curses.color_pair(YELLOW))

        # Line 3: what you've typed, with a bar showing how far through you are
        progress = "=" * min(length, len(self.typed))
        put(window, row + 3, 0, f"TYPE: {self.typed} [{progress:<{length}}] then Enter")


# ---------------------------------------------------------------------------
# Start-up
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Connect to the multiplayer terminal maze server.")
    parser.add_argument("--host", default="127.0.0.1", help="Server address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Server port (default: 8765)")
    parser.add_argument("--name", default="Player", help="Your display name (default: Player)")
    args = parser.parse_args()

    try:
        client = Client(args.host, args.port, args.name)
    except OSError as error:
        raise SystemExit(f"Unable to connect to {args.host}:{args.port}: {error}") from error

    # curses.wrapper sets up the terminal, runs our loop, and always restores
    # the terminal afterwards, even if the game crashes.
    curses.wrapper(client.run)


if __name__ == "__main__":
    main()