import asyncio
import logging
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Request

from locations import LOCATIONS

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")
BOT_USERNAME = os.getenv("BOT_USERNAME")
APP_VERSION = "webhook-asyncio-task-v1"

logger = logging.getLogger(__name__)

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
MIN_PLAYERS = 3
ROOM_CODE_LENGTH = 4
ROUND_LOCATION_COUNT = 15
LOCATION_SELECT_PAGE_SIZE = 10
BASE_DIR = Path(__file__).resolve().parent
LOCATION_IMAGES_DIR = BASE_DIR / "assets" / "locations"
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")
IMAGE_CONTENT_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
FALLBACK_IMAGE_KEY = "fallback"
LOCATION_MODES = {
    "default": "стандартные локации",
    "random": "случайные локации",
    "manual": "ручной выбор",
}
LOCATION_BY_ID = {location["id"]: location for location in LOCATIONS}


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
    possible_locations: list[dict[str, Any]] = field(default_factory=list)
    actual_location: dict[str, Any] | None = None
    spy_id: int | None = None
    in_game: bool = False
    revealed: bool = False
    location_mode: str = "default"
    selected_location_ids: list[str] = field(default_factory=list)
    test_mode: bool = False


app = FastAPI(title="Spyfall Telegram Bot MVP")
rooms: dict[str, Room] = {}
user_room: dict[int, str] = {}
awaiting_join_code: set[int] = set()


def main_menu_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "🎲 Создать комнату", "callback_data": "create_room"}],
            [{"text": "🔢 Войти по коду", "callback_data": "join_by_code"}],
            [{"text": "❓ Правила", "callback_data": "rules"}],
        ]
    }


def lobby_keyboard(room: Room, user_id: int) -> dict[str, Any]:
    buttons = []
    if room.in_game:
        if user_id == room.owner_id:
            if room.revealed:
                buttons.append([{"text": "🔁 Новая игра", "callback_data": f"new_game:{room.code}"}])
            else:
                buttons.append([{"text": "👀 Показать результат", "callback_data": f"reveal:{room.code}"}])
    elif user_id == room.owner_id:
        buttons.extend(
            [
                [{"text": "▶️ Начать игру", "callback_data": f"start:{room.code}"}],
                [{"text": "🧪 Тестовый старт", "callback_data": f"test_start:{room.code}"}],
                [{"text": "⚙️ Настройки", "callback_data": f"settings:{room.code}"}],
            ]
        )

    buttons.extend(
        [
            [{"text": "👥 Обновить игроков", "callback_data": f"refresh:{room.code}"}],
            [{"text": "🚪 Выйти из комнаты", "callback_data": f"leave:{room.code}"}],
        ]
    )
    return {"inline_keyboard": buttons}


def settings_keyboard(room: Room) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "✅ Стандартные локации", "callback_data": f"mode:default:{room.code}"}],
            [{"text": "🎲 Случайные локации", "callback_data": f"mode:random:{room.code}"}],
            [{"text": "☑️ Выбрать вручную", "callback_data": f"select_locations:{room.code}:0"}],
            [{"text": "⬅️ Назад в лобби", "callback_data": f"back_lobby:{room.code}"}],
        ]
    }


def location_selection_keyboard(room: Room, page: int) -> dict[str, Any]:
    start = page * LOCATION_SELECT_PAGE_SIZE
    end = start + LOCATION_SELECT_PAGE_SIZE
    buttons = []
    selected_ids = set(room.selected_location_ids)
    for location in LOCATIONS[start:end]:
        mark = "✅" if location["id"] in selected_ids else "❌"
        buttons.append(
            [
                {
                    "text": f"{mark} {location_name(location)}",
                    "callback_data": f"toggle_location:{room.code}:{page}:{location['id']}",
                }
            ]
        )

    navigation = []
    if page > 0:
        navigation.append({"text": "⬅️ Назад", "callback_data": f"select_locations:{room.code}:{page - 1}"})
    if end < len(LOCATIONS):
        navigation.append({"text": "▶️ Далее", "callback_data": f"select_locations:{room.code}:{page + 1}"})
    if navigation:
        buttons.append(navigation)
    buttons.append([{"text": "✅ Готово", "callback_data": f"manual_done:{room.code}"}])
    return {"inline_keyboard": buttons}


def display_name(user: dict[str, Any]) -> str:
    first_name = user.get("first_name") or "Игрок"
    username = user.get("username")
    return f"{first_name} (@{username})" if username else first_name


def generate_room_code() -> str:
    while True:
        code = f"{random.randint(0, 9999):04d}"
        if code not in rooms:
            return code


def location_name(location: dict[str, Any]) -> str:
    return location["name_ru"]


def find_image_path(image_key: str) -> Optional[Path]:
    for extension in IMAGE_EXTENSIONS:
        image_path = LOCATION_IMAGES_DIR / f"{image_key}{extension}"
        if image_path.exists():
            return image_path
    return None


def find_fallback_image_path() -> Optional[Path]:
    return find_image_path(FALLBACK_IMAGE_KEY)


def find_location_image_path(image_key: str) -> Optional[Path]:
    return find_image_path(image_key) or find_fallback_image_path()


def location_mode_name(room: Room) -> str:
    return LOCATION_MODES.get(room.location_mode, LOCATION_MODES["default"])


def is_room_code(text: str) -> bool:
    return text.isdigit() and len(text) == ROOM_CODE_LENGTH


def invite_text(room: Room) -> str:
    if BOT_USERNAME:
        username = BOT_USERNAME.removeprefix("@")
        return f"Ссылка для приглашения: https://t.me/{username}?start={room.code}"
    return f"Приглашение: отправьте друзьям этот код: `{room.code}`"


def lobby_text(room: Room) -> str:
    players = "\n".join(
        f"{'👑 ' if player.user_id == room.owner_id else ''}{index}. {player.name}"
        for index, player in enumerate(room.players.values(), start=1)
    )
    text = (
        f"🕵️ Комната Spyfall\n\n"
        f"Код комнаты: `{room.code}`\n\n"
        f"Игроки:\n{players}\n\n"
        f"Режим локаций: {location_mode_name(room)}\n\n"
        f"{invite_text(room)}"
    )
    if room.test_mode:
        text += "\n\n🧪 Игра запущена в тестовом режиме."
    if room.in_game and room.possible_locations:
        locations = "\n".join(f"• {location_name(location)}" for location in room.possible_locations)
        text += f"\n\nВозможные локации:\n{locations}"
    if room.revealed:
        spy = room.players.get(room.spy_id) if room.spy_id else None
        spy_name = spy.name if spy else "шпиона нет в этом тестовом раунде"
        actual_location = location_name(room.actual_location) if room.actual_location else "Неизвестно"
        text += f"\n\n🎉 Результат:\nЛокация: {actual_location}\nШпион: {spy_name}"
    return text


def settings_text(room: Room) -> str:
    selected_count = len(room.selected_location_ids)
    return (
        "⚙️ Настройки комнаты\n\n"
        f"Текущий режим локаций: {location_mode_name(room)}\n"
        f"Выбрано вручную: {selected_count}\n\n"
        "Перед стартом игры будет показано 15 возможных локаций."
    )


def location_selection_text(room: Room, page: int) -> str:
    total_pages = (len(LOCATIONS) + LOCATION_SELECT_PAGE_SIZE - 1) // LOCATION_SELECT_PAGE_SIZE
    return (
        "☑️ Ручной выбор локаций\n\n"
        "Нажимайте на локации, чтобы включать или выключать их. "
        "Для старта нужно выбрать минимум 15 локаций.\n\n"
        f"Выбрано: {len(room.selected_location_ids)}\n"
        f"Страница: {page + 1}/{total_pages}"
    )


def available_locations_for_room(room: Room) -> list[dict[str, Any]]:
    if room.location_mode == "random":
        return list(LOCATIONS)
    if room.location_mode == "manual":
        return [LOCATION_BY_ID[location_id] for location_id in room.selected_location_ids if location_id in LOCATION_BY_ID]
    return [location for location in LOCATIONS if location.get("enabled_by_default")]


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


async def send_photo(chat_id: int, image_path: Path, caption: str) -> dict[str, Any]:
    content_type = IMAGE_CONTENT_TYPES.get(image_path.suffix.lower(), "application/octet-stream")
    with image_path.open("rb") as photo:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{TELEGRAM_API_URL}/sendPhoto",
                data={"chat_id": chat_id, "caption": caption},
                files={"photo": (image_path.name, photo, content_type)},
            )
            response.raise_for_status()
            return response.json()


async def send_photo_or_text(
    chat_id: int,
    image_path: Optional[Path],
    caption: str,
    fallback_text: str,
) -> dict[str, Any]:
    if image_path is None:
        return await send_message(chat_id, fallback_text)

    try:
        return await send_photo(chat_id, image_path, caption)
    except Exception:
        logger.exception("Failed to send location image %s to chat %s", image_path, chat_id)
        return await send_message(chat_id, fallback_text)


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
        "Добро пожаловать в Spyfall! Создайте комнату или присоединитесь к друзьям по коду.",
        main_menu_keyboard(),
    )


async def show_lobby(room: Room, player: Player) -> None:
    message = await send_message(player.chat_id, lobby_text(room), lobby_keyboard(room, player.user_id))
    player.lobby_message_id = message["result"]["message_id"]


async def show_settings(room: Room, chat_id: int, message_id: int | None = None) -> None:
    await edit_or_send_message(chat_id, message_id, settings_text(room), settings_keyboard(room))


async def show_location_selection(room: Room, chat_id: int, page: int, message_id: int | None = None) -> None:
    page = max(0, min(page, (len(LOCATIONS) - 1) // LOCATION_SELECT_PAGE_SIZE))
    await edit_or_send_message(
        chat_id,
        message_id,
        location_selection_text(room, page),
        location_selection_keyboard(room, page),
    )


def telegram_error_description(error: httpx.HTTPStatusError) -> str:
    try:
        data = error.response.json()
    except ValueError:
        return error.response.text
    return str(data.get("description", ""))


def is_message_not_modified(error: httpx.HTTPStatusError) -> bool:
    return "message is not modified" in telegram_error_description(error).lower()


def is_lobby_message_unavailable(error: httpx.HTTPStatusError) -> bool:
    description = telegram_error_description(error).lower()
    return any(
        phrase in description
        for phrase in (
            "message to edit not found",
            "message can't be edited",
            "message identifier is not specified",
        )
    )


async def edit_or_send_message(
    chat_id: int,
    message_id: int | None,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not message_id:
        return await send_message(chat_id, text, reply_markup)

    try:
        await edit_message(chat_id, message_id, text, reply_markup)
        return {"result": {"message_id": message_id}}
    except httpx.HTTPStatusError as error:
        if is_message_not_modified(error):
            return {"result": {"message_id": message_id}}
        if is_lobby_message_unavailable(error):
            return await send_message(chat_id, text, reply_markup)
        raise


async def refresh_lobby_for_player(room: Room, player: Player) -> None:
    if not player.lobby_message_id:
        await show_lobby(room, player)
        return
    try:
        await edit_message(
            player.chat_id,
            player.lobby_message_id,
            lobby_text(room),
            lobby_keyboard(room, player.user_id),
        )
    except httpx.HTTPStatusError as error:
        if is_message_not_modified(error):
            return
        if is_lobby_message_unavailable(error):
            await show_lobby(room, player)
            return
        raise


async def refresh_lobbies(room: Room, skip_user_id: int | None = None) -> None:
    for player in list(room.players.values()):
        if player.user_id == skip_user_id:
            continue
        try:
            await refresh_lobby_for_player(room, player)
        except httpx.HTTPError:
            pass


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
        await send_message(chat_id, "Комната не найдена", main_menu_keyboard())
        return
    if user["id"] in room.players:
        await show_lobby(room, room.players[user["id"]])
        return
    if room.in_game:
        await send_message(chat_id, "В этой комнате уже идёт игра. Попробуйте присоединиться перед следующей игрой.")
        return
    if user["id"] in user_room and user_room[user["id"]] in rooms:
        await send_message(chat_id, "Выйдите из текущей комнаты, прежде чем присоединиться к другой.")
        return

    player = add_player_to_room(room, user, chat_id)
    await show_lobby(room, player)
    await refresh_lobbies(room, skip_user_id=player.user_id)


async def start_game(room: Room, user_id: int, chat_id: int, test_mode: bool = False) -> None:
    if user_id != room.owner_id:
        await send_message(chat_id, "Только создатель комнаты может начать игру.")
        return
    if room.in_game:
        await send_message(chat_id, "Игра уже идёт.")
        return
    if not test_mode and len(room.players) < MIN_PLAYERS:
        await send_message(chat_id, "Для начала нужно минимум 3 игрока.")
        return

    available_locations = available_locations_for_room(room)
    if len(available_locations) < ROUND_LOCATION_COUNT:
        await send_message(
            chat_id,
            "Недостаточно локаций для выбранного режима. Нужно минимум 15 локаций.",
        )
        return

    room.possible_locations = random.sample(available_locations, ROUND_LOCATION_COUNT)
    room.actual_location = random.choice(room.possible_locations)
    if test_mode and len(room.players) == 1:
        only_player_id = next(iter(room.players))
        room.spy_id = only_player_id if random.choice([True, False]) else None
    else:
        room.spy_id = random.choice(list(room.players.keys()))
    room.in_game = True
    room.revealed = False
    room.test_mode = test_mode

    actual_location = room.actual_location
    if not actual_location:
        await send_message(chat_id, "Не удалось выбрать локацию для игры.")
        return

    mode_line = "\n\n🧪 Это тестовый старт для проверки игры." if test_mode else ""
    spy_role_text = f"Вы ШПИОН. Локация неизвестна. Попробуйте угадать её из списка.{mode_line}"
    location_role_text = (
        f"Вы НЕ шпион. Локация: {location_name(actual_location)}. "
        f"Роль: обычный посетитель.{mode_line}"
    )
    for player in room.players.values():
        if player.user_id == room.spy_id:
            await send_photo_or_text(player.chat_id, find_fallback_image_path(), spy_role_text, spy_role_text)
        else:
            await send_photo_or_text(
                player.chat_id,
                find_location_image_path(actual_location["image_key"]),
                location_role_text,
                location_role_text,
            )
    await refresh_lobbies(room)


async def reveal_game(room: Room, user_id: int, chat_id: int) -> None:
    if user_id != room.owner_id:
        await send_message(chat_id, "Только создатель комнаты может показать результат.")
        return
    if not room.in_game:
        await send_message(chat_id, "Начните игру, прежде чем показывать результат.")
        return
    room.revealed = True
    await refresh_lobbies(room)


async def new_game(room: Room, user_id: int, chat_id: int) -> None:
    if user_id != room.owner_id:
        await send_message(chat_id, "Только создатель комнаты может начать новую игру.")
        return
    room.possible_locations = []
    room.actual_location = None
    room.spy_id = None
    room.in_game = False
    room.revealed = False
    room.test_mode = False
    await refresh_lobbies(room)


async def leave_room(room: Room, user_id: int, chat_id: int) -> None:
    room.players.pop(user_id, None)
    user_room.pop(user_id, None)
    if not room.players:
        rooms.pop(room.code, None)
        await send_message(chat_id, "Вы вышли из комнаты. Комната закрыта.", main_menu_keyboard())
        return
    if room.owner_id == user_id:
        room.owner_id = next(iter(room.players))
    await send_message(chat_id, "Вы вышли из комнаты.", main_menu_keyboard())
    await refresh_lobbies(room)


async def set_location_mode(
    room: Room,
    user_id: int,
    chat_id: int,
    message_id: int | None,
    mode: str,
) -> None:
    if user_id != room.owner_id:
        await send_message(chat_id, "Только создатель комнаты может менять настройки.")
        return
    if room.in_game:
        await send_message(chat_id, "Настройки доступны только до начала игры.")
        return
    if mode not in LOCATION_MODES:
        await send_message(chat_id, "Неизвестный режим локаций.")
        return
    room.location_mode = mode
    await edit_or_send_message(
        chat_id,
        message_id,
        f"Режим локаций изменён: {location_mode_name(room)}.",
        settings_keyboard(room),
    )
    await refresh_lobbies(room, skip_user_id=user_id)


async def toggle_manual_location(
    room: Room,
    user_id: int,
    chat_id: int,
    message_id: int | None,
    page: int,
    location_id: str,
) -> None:
    if user_id != room.owner_id:
        await send_message(chat_id, "Только создатель комнаты может выбирать локации.")
        return
    if room.in_game:
        await send_message(chat_id, "Локации можно выбирать только до начала игры.")
        return
    if location_id not in LOCATION_BY_ID:
        await send_message(chat_id, "Локация не найдена.")
        return

    if location_id in room.selected_location_ids:
        room.selected_location_ids.remove(location_id)
    else:
        room.selected_location_ids.append(location_id)
    room.location_mode = "manual"
    await show_location_selection(room, chat_id, page, message_id)


async def finish_manual_selection(room: Room, user_id: int, chat_id: int, message_id: int | None) -> None:
    if user_id != room.owner_id:
        await send_message(chat_id, "Только создатель комнаты может выбирать локации.")
        return
    room.location_mode = "manual"
    await edit_or_send_message(
        chat_id,
        message_id,
        f"Готово: выбрано локаций — {len(room.selected_location_ids)}.",
        settings_keyboard(room),
    )
    await refresh_lobbies(room, skip_user_id=user_id)


async def handle_message(message: dict[str, Any]) -> None:
    chat = message.get("chat", {})
    user = message.get("from", {})
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not user:
        return

    if text.startswith("/start"):
        awaiting_join_code.discard(user["id"])
        parts = text.split(maxsplit=1)
        if len(parts) == 1:
            await show_main_menu(chat_id)
            return

        code = parts[1].strip()
        if is_room_code(code) and code in rooms:
            await join_room(code, user, chat_id)
        else:
            await send_message(chat_id, "Комната не найдена", main_menu_keyboard())
        return

    if user["id"] in awaiting_join_code:
        awaiting_join_code.discard(user["id"])
        if is_room_code(text):
            await join_room(text, user, chat_id)
        else:
            await send_message(chat_id, "Пожалуйста, отправьте корректный 4-значный код комнаты.", main_menu_keyboard())
        return

    await send_message(chat_id, "Используйте кнопки меню, чтобы играть в Spyfall.", main_menu_keyboard())


async def handle_callback(callback_query: dict[str, Any]) -> None:
    callback_query_id = callback_query["id"]
    data = callback_query.get("data", "")
    message = callback_query.get("message", {})
    message_id = message.get("message_id")
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
        await send_message(chat_id, "Отправьте 4-значный код комнаты.")
        return
    if data == "rules":
        await send_message(
            chat_id,
            "Правила: один игрок тайно становится шпионом. Все остальные знают локацию. Задавайте вопросы, ищите шпиона и не раскрывайте лишнего.",
            main_menu_keyboard(),
        )
        return

    parts = data.split(":")
    action = parts[0]
    code = parts[1] if len(parts) > 1 else ""
    if action == "mode" and len(parts) >= 3:
        code = parts[2]

    room = rooms.get(code)
    if not room:
        await send_message(chat_id, "Эта комната больше не существует.", main_menu_keyboard())
        return
    if user_id not in room.players:
        await send_message(chat_id, "Вы не в этой комнате.", main_menu_keyboard())
        return

    if action == "refresh":
        try:
            await refresh_lobby_for_player(room, room.players[user_id])
        except httpx.HTTPError:
            pass
    elif action == "start":
        await start_game(room, user_id, chat_id)
    elif action == "test_start":
        await start_game(room, user_id, chat_id, test_mode=True)
    elif action == "settings":
        if user_id != room.owner_id or room.in_game:
            await send_message(chat_id, "Настройки доступны только создателю комнаты до начала игры.")
        else:
            await show_settings(room, chat_id, message_id)
    elif action == "mode" and len(parts) >= 3:
        await set_location_mode(room, user_id, chat_id, message_id, parts[1])
    elif action == "select_locations" and len(parts) >= 3:
        if user_id != room.owner_id or room.in_game:
            await send_message(chat_id, "Выбор локаций доступен только создателю комнаты до начала игры.")
        else:
            await show_location_selection(room, chat_id, int(parts[2]), message_id)
    elif action == "toggle_location" and len(parts) >= 4:
        await toggle_manual_location(room, user_id, chat_id, message_id, int(parts[2]), parts[3])
    elif action == "manual_done":
        await finish_manual_selection(room, user_id, chat_id, message_id)
    elif action == "back_lobby":
        result = await edit_or_send_message(
            chat_id,
            message_id,
            lobby_text(room),
            lobby_keyboard(room, user_id),
        )
        if result:
            room.players[user_id].lobby_message_id = result["result"]["message_id"]
    elif action == "reveal":
        await reveal_game(room, user_id, chat_id)
    elif action == "new_game":
        await new_game(room, user_id, chat_id)
    elif action == "leave":
        await leave_room(room, user_id, chat_id)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/version")
async def version() -> dict[str, str]:
    return {"version": APP_VERSION}


def log_background_task_result(task: asyncio.Task[Any]) -> None:
    try:
        task.result()
    except Exception:
        logger.exception("Telegram update background task failed")


async def process_update(update: dict[str, Any]) -> None:
    logger.info("Telegram update background processing started")
    if "message" in update:
        await handle_message(update["message"])
    elif "callback_query" in update:
        await handle_callback(update["callback_query"])
    logger.info("Telegram update background processing finished")


@app.post("/webhook")
async def webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, str]:
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    update = await request.json()
    logger.info("Received Telegram webhook update")
    task = asyncio.create_task(process_update(update))
    task.add_done_callback(log_background_task_result)
    return {"status": "ok"}
