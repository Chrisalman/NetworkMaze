from __future__ import annotations

import argparse
import socket
import threading
import time

from .game import Game
from .protocol import send_message, receive_messages


"""
Maze Game Server
================

Responsibilities
----------------
- Accept client connections
- Create player sessions
- Process incoming player actions
- Advance game state
- Broadcast snapshots to connected players

Threads
-------
Main Thread
    Accepts incoming connections.

One Client Thread Per Connection
    Receives actions from a single player.

Tick Thread
    Advances the game simulation and sends
    updated game state to all connected players.
"""


class MazeServer:
    """
    Multiplayer maze game server.
    """

    def __init__(
        self,
        host: str,
        port: int,
        width: int,
        height: int,
        seed: int | None = None,
    ):
        """
        Initialise the game server.
        """

        self.host = host
        self.port = port

        # Core game engine.
        self.game = Game(
            width,
            height,
            seed,
        )

        self.running = True

        # Dictionary:
        #
        # {
        #     player_id: (socket, send_lock)
        # }
        #
        self.clients = {}

        # Protects access to self.clients.
        self.clients_lock = threading.Lock()

    # ------------------------------------------------------------------

    def client_loop(
        self,
        connection: socket.socket,
        address,
    ):
        """
        Handle a single connected client.

        This runs in its own thread.
        """

        player_id = None

        receive_buffer = bytearray()

        send_lock = threading.Lock()

        try:
            # ----------------------------------------------------------
            # Wait for join message
            # ----------------------------------------------------------

            hello = next(
                receive_messages(
                    connection,
                    receive_buffer,
                )
            )

            if hello.get("type") != "join":
                raise ValueError(
                    "First message must be join"
                )

            player_name = str(
                hello.get("name", "Player")
            )

            # Add player to game.
            player_id = self.game.add_player(
                player_name
            )

            # Register connected client.
            with self.clients_lock:
                self.clients[player_id] = (
                    connection,
                    send_lock,
                )

            # Send welcome message.
            send_message(
                connection,
                {
                    "type": "welcome",
                    "player_id": player_id,
                },
                send_lock,
            )

            # ----------------------------------------------------------
            # Main receive loop
            # ----------------------------------------------------------

            while self.running:

                messages = receive_messages(
                    connection,
                    receive_buffer,
                )

                for message in messages:
                    self.game.handle(
                        player_id,
                        message,
                    )

        except (
            ConnectionError,
            OSError,
            ValueError,
            StopIteration,
        ):
            pass

        finally:
            # ----------------------------------------------------------
            # Cleanup
            # ----------------------------------------------------------

            if player_id is not None:

                with self.clients_lock:
                    self.clients.pop(
                        player_id,
                        None,
                    )

                self.game.remove_player(
                    player_id
                )

            try:
                connection.close()
            except OSError:
                pass

    # ------------------------------------------------------------------

    def tick_loop(self):
        """
        Main simulation loop.

        Updates game state and sends snapshots
        to every connected player.
        """

        tick_interval = 0.12

        while self.running:

            tick_start_time = time.monotonic()

            # Advance the game simulation.
            self.game.tick()

            # Create stable copy of clients.
            with self.clients_lock:
                connected_clients = list(
                    self.clients.items()
                )

            # Send updated state.
            for player_id, (
                connection,
                send_lock,
            ) in connected_clients:

                try:
                    snapshot = self.game.snapshot(
                        player_id
                    )

                    send_message(
                        connection,
                        snapshot,
                        send_lock,
                    )

                except OSError:
                    pass

            # Maintain fixed tick rate.
            elapsed = (
                time.monotonic()
                - tick_start_time
            )

            sleep_time = max(
                0,
                tick_interval - elapsed,
            )

            time.sleep(sleep_time)

    # ------------------------------------------------------------------

    def run(self):
        """
        Start the server.
        """

        # Start game simulation thread.
        threading.Thread(
            target=self.tick_loop,
            daemon=True,
        ).start()

        with socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM,
        ) as server_socket:

            server_socket.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_REUSEADDR,
                1,
            )

            server_socket.bind(
                (
                    self.host,
                    self.port,
                )
            )

            server_socket.listen()

            print(
                f"Maze server listening on "
                f"{self.host}:{self.port}"
            )

            try:
                while self.running:

                    connection, address = (
                        server_socket.accept()
                    )

                    # Disable Nagle algorithm for
                    # lower latency gameplay.
                    connection.setsockopt(
                        socket.IPPROTO_TCP,
                        socket.TCP_NODELAY,
                        1,
                    )

                    threading.Thread(
                        target=self.client_loop,
                        args=(
                            connection,
                            address,
                        ),
                        daemon=True,
                    ).start()

            except KeyboardInterrupt:

                print(
                    "\nServer shutting down..."
                )

                self.running = False


# ============================================================================
# Command Line Entry Point
# ============================================================================

def main():
    """
    Parse command line arguments and start the server.
    """

    parser = argparse.ArgumentParser(
        description="Maze Game Server"
    )

    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to"
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="TCP port"
    )

    parser.add_argument(
        "--width",
        type=int,
        default=51,
        help="Maze width"
    )

    parser.add_argument(
        "--height",
        type=int,
        default=25,
        help="Maze height"
    )

    parser.add_argument(
        "--seed",
        type=int,
        help="Random seed"
    )

    args = parser.parse_args()

    server = MazeServer(
        host=args.host,
        port=args.port,
        width=args.width,
        height=args.height,
        seed=args.seed,
    )

    server.run()


if __name__ == "__main__":
    main()