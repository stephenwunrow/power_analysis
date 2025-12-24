import os
import json
import datetime
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
load_dotenv()

# Import YOUR existing logic
from bot_power import (
    authenticate,
    download_and_save_power_streams,
    analyze_power,
    load_power_streams,
    get_max_average_power,
    clean_power_data,
    chunk_message,
    get_activity_max_power,
    get_activities_for_date,
    DATA_DIR
)

### Use systemmd on raspberry PI to guarantee restarts, etc. 

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# ================= BOT EVENTS =================

@bot.event
async def on_ready():
    print(f"🤖 Logged in as {bot.user}")

# ================= STRAVA COMMANDS =================

@bot.command(name="strava")
async def strava_cmd(ctx, action: str):
    """
    !strava update
    """
    if action != "update":
        await ctx.send("Usage: `!strava update`")
        return

    await ctx.send("🔄 Authenticating with Strava...")

    try:
        client = authenticate()
        await ctx.send("⬇️ Downloading new activities (this may take a while)...")

        # Run blocking code in executor
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            download_and_save_power_streams,
            client
        )

        await ctx.send("✅ Strava activities updated.")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

# ================= POWER ANALYSIS =================

@bot.command(name="power")
async def power_cmd(ctx, mode: str, *args):
    """
    !power top <seconds> [N]
    !power date <YYYY-MM-DD> <seconds>
    """

    if mode == "top":
        if len(args) < 1:
            await ctx.send("Usage: `!power top <seconds> [N]`")
            return

        seconds = int(args[0])
        top_n = int(args[1]) if len(args) > 1 else 5

        await ctx.send(f"📊 Analyzing top {top_n} efforts for {seconds}s...")

        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None,
            analyze_power,
            DATA_DIR,
            seconds,
            top_n
        )

        if not results:
            await ctx.send("No results found.")
            return

        msg = f"**Top {top_n} {seconds}s efforts:**\n"
        for r in results:
            msg += f"• **{r['max_power']} W** — {r['date']} — {r['name']}\n"

        for chunk in chunk_message(msg):
            await ctx.send(chunk)

    elif mode == "date":
        if len(args) != 1:
            await ctx.send("Usage: `!power date <YYYY-MM-DD>`")
            return

        date_str = args

        try:
            target_date = datetime.date.fromisoformat(date_str)
        except ValueError:
            await ctx.send("Invalid date.")
            return

        # Step 1: collect activities for the date
        activities = get_activities_for_date(DATA_DIR, target_date)

        if not activities:
            await ctx.send(f"No activities found for {date_str}.")
            return

        # Step 2: auto-handle single activity
        if len(activities) == 1:
            selected = activities[0]
        else:
            # Step 3: ask user to choose
            msg = f"📅 **Activities on {date_str}:**\n"
            for i, act in enumerate(activities, start=1):
                msg += f"{i}) {act['name']} at {act['time']}\n"
            msg += "\nReply with a number or `cancel`."

            await ctx.send(msg)

            # Step 4: wait for response
            def check(m):
                return m.author == ctx.author and m.channel == ctx.channel

            try:
                reply = await bot.wait_for("message", timeout=30.0, check=check)
            except asyncio.TimeoutError:
                await ctx.send("⏱️ Selection timed out.")
                return

            if reply.content.lower() == "cancel":
                await ctx.send("Selection cancelled.")
                return

            try:
                choice = int(reply.content) - 1
                if choice < 0 or choice >= len(activities):
                    raise ValueError
            except ValueError:
                await ctx.send("Invalid selection.")
                return

            selected = activities[choice]

        # --- NEW: repeated analysis loop ---
        await ctx.send(
            f"You selected **{selected['name']}** at {selected['time']}.\n"
            "Enter a duration in seconds for analysis, or `!done` to finish."
        )

        while True:
            def check(m):
                return m.author == ctx.author and m.channel == ctx.channel

            try:
                msg = await bot.wait_for("message", timeout=60.0, check=check)
            except asyncio.TimeoutError:
                await ctx.send("⏱️ Input timed out. Ending selection.")
                break

            content = msg.content.strip().lower()

            if content == "!done":
                await ctx.send("Finished analyzing this activity.")
                break

            try:
                seconds = int(content)
            except ValueError:
                await ctx.send("Please enter a valid number of seconds, or `!done`.")
                continue

            power = clean_power_data(selected["power"])
            val = get_max_average_power(power, seconds)

            if val is None:
                await ctx.send(f"Power data too short for {seconds} seconds interval.")
            else:
                await ctx.send(f"⚡ Max average power for {seconds}s: **{round(val,1)} W**")


@bot.event
async def on_message(message):
    # Ignore the bot's own messages
    if message.author == bot.user:
        return

    # If it doesn't start with the command prefix, show help
    if not message.content.startswith(("!", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9")):
        await message.channel.send(
            "👋 I use commands!\n\n"
            "**Try:**\n"
            "• `!strava update`\n"
            "• `!power top <seconds> [N]`\n"
            "• `!power date <YYYY-MM-DD> <seconds>`\n"
            "• `!help`"
        )
        return

    await bot.process_commands(message)


# ================= START BOT =================

bot.run(DISCORD_TOKEN)
