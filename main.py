import os
import random
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
MIN_PLAYERS = 3

LOCATIONS = [
    "Airport",
    "Amusement Park",
    "Bank",
    "Beach",
    "Casino",
    "Cathedral",
    "Circus Tent",
    "Corporate Office",
    "Cruise Ship",
    "Embassy",
    "Hospital",
    "Hotel",
    "Library",
    "Movie Studio",
    "Museum",
    "Nightclub",
    "Ocean Liner",
    "Police Station",
    "Restaurant",
    "School",
    "Space Station",
    "Submarine",
    "Supermarket",
    "Theater",
    "University",
    "Zoo",
]


@dataclass
class Player:
    user_id: int
    chat_id: int
    name: str
    lobby_message_id: int | None = None


@dataclass
class Room:
    code: str
    owner_id: int
    players: dict[int, Player] = field(default_factory=dict)
    possible_locations: list[str] = field(default_factory=list)
    actual_location: str | None = None
    spy_id: int | None = None
    in_game: bool = False
    revealed: bool = False


app = FastAPI(title="Spyfall Telegram Bot MVP")
rooms: dict[str, Room] = {}
user_room: dict[int, str] = {}
awaiting_join_code: set[int] = set()


def main_menu_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "🎲 Create room", "callback_data": "create_room"}],
            [{"text": "🔢 Join by code", "callback_data": "join_by_code"}],
            [{"text": "❓ Rules", "callback_data": "rules"}],
        ]
    }


def lobby_keyboard(room: Room, user_id: int) -> dict[str, Any]:
    buttons = []
    if room.in_game:
        if user_id == room.owner_id:
            if room.revealed:
                buttons.append([{"text": "🔁 New game", "callback_data": f"new_game:{room.code}"}])
            else:
                buttons.append([{"text": "👀 Reveal", "callback_data": f"reveal:{room.code}"}])
    else:
        buttons.append([{"text": "▶️ Start game", "callback_data": f"start:{room.code}"}])

    buttons.extend(
        [
            [{"text": "👥 Refresh players", "callback_data": f"refresh:{room.code}"}],
            [{"text": "🚪 Leave room", "callback_data": f"leave:{room.code}"}],
        ]
    )
    return {"inline_keyboard": buttons}


def display_name(user: dict[str, Any]) -> str:
    first_name = user.get("first_name") or "Player"
    username = user.get("username")
    return f"{first_name} (@{username})" if username else first_name


def generate_room_code() -> str:
    while True:
        code = f"{random.randint(0, 999999):06d}"
        if code not in rooms:
            return code


def lobby_text(room: Room) -> str:
    players = "\n".join(
        f"{'👑 ' if player.user_id == room.owner_id else ''}{index}. {player.name}"
        for index, player in enumerate(room.players.values(), start=1)
    )
    text = (
        f"🕵️ Spyfall room\n\n"
        f"Room code: `{room.code}`\n\n"
        f"Players:\n{players}\n\n"
        f"Invite text/link: share this code with friends: `{room.code}`"
    )
    if room.in_game and room.possible_locations:
        locations = "\n".join(f"• {location}" for location in room.possible_locations)
        text += f"\n\nPossible locations:\n{locations}"
    if room.revealed:
        spy = room.players.get(room.spy_id) if room.spy_id else None
        spy_name = spy.name if spy else "Unknown"
        text += f"\n\n🎉 Result:\nLocation: {room.actual_location}\nSpy: {spy_name}"
    return text


async def telegram_request(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(f"{TELEGRAM_API_URL}/{method}", json=payload)
        response.raise_for_status()
        return response.json()


async def send_message(chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await telegram_request("sendMessage", payload)


async def edit_message(
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    await telegram_request("editMessageText", payload)


async def answer_callback_query(callback_query_id: str, text: str | None = None) -> None:
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    await telegram_request("answerCallbackQuery", payload)


async def show_main_menu(chat_id: int) -> None:
    await send_message(
        chat_id,
        "Welcome to Spyfall! Create a room or join friends by code.",
        main_menu_keyboard(),
    )


async def show_lobby(room: Room, player: Player) -> None:
    message = await send_message(player.chat_id, lobby_text(room), lobby_keyboard(room, player.user_id))
    player.lobby_message_id = message["result"]["message_id"]


async def refresh_lobby_for_player(room: Room, player: Player) -> None:
    if not player.lobby_message_id:
        await show_lobby(room, player)
        return
    await edit_message(
        player.chat_id,
        player.lobby_message_id,
        lobby_text(room),
        lobby_keyboard(room, player.user_id),
    )


async def refresh_lobbies(room: Room) -> None:
    for player in list(room.players.values()):
        try:
            await refresh_lobby_for_player(room, player)
        except httpx.HTTPError:
            # A player may have deleted the old lobby message; send a fresh one.
            await show_lobby(room, player)


def add_player_to_room(room: Room, user: dict[str, Any], chat_id: int) -> Player:
    player = Player(user_id=user["id"], chat_id=chat_id, name=display_name(user))
    room.players[player.user_id] = player
    user_room[player.user_id] = room.code
    return player


async def create_room(user: dict[str, Any], chat_id: int) -> None:
    existing_code = user_room.get(user["id"])
    if existing_code in rooms:
        await show_lobby(rooms[existing_code], rooms[existing_code].players[user["id"]])
        return

    code = generate_room_code()
    room = Room(code=code, owner_id=user["id"])
    rooms[code] = room
    player = add_player_to_room(room, user, chat_id)
    await show_lobby(room, player)


async def join_room(code: str, user: dict[str, Any], chat_id: int) -> None:
    room = rooms.get(code)
    if not room:
        await send_message(chat_id, "Room not found. Check the 6-digit code and try again.", main_menu_keyboard())
        return
    if user["id"] in room.players:
        await show_lobby(room, room.players[user["id"]])
        return
    if room.in_game:
        await send_message(chat_id, "This room already has a game in progress. Try again before the next game.")
        return
    if user["id"] in user_room and user_room[user["id"]] in rooms:
        await send_message(chat_id, "Leave your current room before joining another one.")
        return

    player = add_player_to_room(room, user, chat_id)
    await show_lobby(room, player)
    await refresh_lobbies(room)


async def start_game(room: Room, user_id: int, chat_id: int) -> None:
    if user_id != room.owner_id:
        await send_message(chat_id, "Only the room owner can start the game.")
        return
    if room.in_game:
        await send_message(chat_id, "A game is already in progress.")
        return
    if len(room.players) < MIN_PLAYERS:
        await send_message(chat_id, "You need at least 3 players to start.")
        return

    room.possible_locations = random.sample(LOCATIONS, 15)
    room.actual_location = random.choice(room.possible_locations)
    room.spy_id = random.choice(list(room.players.keys()))
    room.in_game = True
    room.revealed = False

    # Roles are sent privately so every player can keep their assignment secret.
    for player in room.players.values():
        if player.user_id == room.spy_id:
            await send_message(player.chat_id, "You are the SPY. Try to guess the location from the list.")
        else:
            await send_message(
                player.chat_id,
                f"You are NOT the spy. Location: {room.actual_location}. Role: ordinary visitor.",
            )
    await refresh_lobbies(room)


async def reveal_game(room: Room, user_id: int, chat_id: int) -> None:
    if user_id != room.owner_id:
        await send_message(chat_id, "Only the room owner can reveal the result.")
        return
    if not room.in_game:
        await send_message(chat_id, "Start a game before revealing the result.")
        return
    room.revealed = True
    await refresh_lobbies(room)


async def new_game(room: Room, user_id: int, chat_id: int) -> None:
    if user_id != room.owner_id:
        await send_message(chat_id, "Only the room owner can start a new game.")
        return
    room.possible_locations = []
    room.actual_location = None
    room.spy_id = None
    room.in_game = False
    room.revealed = False
    await refresh_lobbies(room)


async def leave_room(room: Room, user_id: int, chat_id: int) -> None:
    room.players.pop(user_id, None)
    user_room.pop(user_id, None)
    if not room.players:
        rooms.pop(room.code, None)
        await send_message(chat_id, "You left the room. The room is now closed.", main_menu_keyboard())
        return
    if room.owner_id == user_id:
        room.owner_id = next(iter(room.players))
    await send_message(chat_id, "You left the room.", main_menu_keyboard())
    await refresh_lobbies(room)


async def handle_message(message: dict[str, Any]) -> None:
    chat = message.get("chat", {})
    user = message.get("from", {})
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not user:
        return

    if text == "/start":
        awaiting_join_code.discard(user["id"])
        await show_main_menu(chat_id)
        return

    if user["id"] in awaiting_join_code:
        awaiting_join_code.discard(user["id"])
        if text.isdigit() and len(text) == 6:
            await join_room(text, user, chat_id)
        else:
            await send_message(chat_id, "Please send a valid 6-digit room code.", main_menu_keyboard())
        return

    await send_message(chat_id, "Use the menu buttons to play Spyfall.", main_menu_keyboard())


async def handle_callback(callback_query: dict[str, Any]) -> None:
    callback_query_id = callback_query["id"]
    data = callback_query.get("data", "")
    message = callback_query.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    user = callback_query.get("from", {})
    user_id = user.get("id")
    if not chat_id or not user_id:
        return

    await answer_callback_query(callback_query_id)

    if data == "create_room":
        await create_room(user, chat_id)
        return
    if data == "join_by_code":
        awaiting_join_code.add(user_id)
        await send_message(chat_id, "Send the 6-digit room code.")
        return
    if data == "rules":
        await send_message(
            chat_id,
            "Rules: one player is secretly the spy. Everyone else knows the location. Ask questions, find the spy, and do not reveal too much.",
            main_menu_keyboard(),
        )
        return

    action, _, code = data.partition(":")
    room = rooms.get(code)
    if not room:
        await send_message(chat_id, "This room no longer exists.", main_menu_keyboard())
        return
    if user_id not in room.players:
        await send_message(chat_id, "You are not in this room.", main_menu_keyboard())
        return

    if action == "refresh":
        await refresh_lobby_for_player(room, room.players[user_id])
    elif action == "start":
        await start_game(room, user_id, chat_id)
    elif action == "reveal":
        await reveal_game(room, user_id, chat_id)
    elif action == "new_game":
        await new_game(room, user_id, chat_id)
    elif action == "leave":
        await leave_room(room, user_id, chat_id)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, str]:
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    update = await request.json()
    if "message" in update:
        await handle_message(update["message"])
    elif "callback_query" in update:
        await handle_callback(update["callback_query"])
    return {"status": "ok"}
