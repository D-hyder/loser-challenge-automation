# worker_main.py
import asyncio

from config import LOSER_BOT_TOKEN, WORDLE_BOT_TOKEN
from loser_challenge_bot import bot as loser_bot          # Loser Challenge bot (your main.py)
from wordle_bot import bot as wordle_bot   # Wordle bot module you refactored

async def main():

    await asyncio.gather(
        loser_bot.start(LOSER_BOT_TOKEN),
        wordle_bot.start(WORDLE_BOT_TOKEN),
    )

if __name__ == "__main__":
    asyncio.run(main())
