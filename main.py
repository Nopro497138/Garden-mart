import json
import os
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# Put your guild id in the env as GUILD_ID if you need instant command sync for that guild.
GUILD_ID = os.getenv("GUILD_ID")  # e.g. "1378007406365380628"
PRODUCTS_FILE = os.getenv("PRODUCTS_FILE", "products.json")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# --- JSON Helpers ---
def load_products():
    try:
        with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_products(products):
    with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)

def next_id(products):
    if not products:
        return 1
    return max((p.get("id", 0) for p in products)) + 1

# --- Permission check for allowed roles ---
def _member_has_allowed_role(member: discord.Member, allowed_ids: set[int]) -> bool:
    if not member:
        return False
    return any(role.id in allowed_ids for role in member.roles)

def has_allowed_role_env(interaction: discord.Interaction) -> bool:
    """
    Reads ALLOWED_ROLE_IDS from env (comma separated role IDs).
    Returns True if the invoking user has at least one of those roles.
    """
    allowed_raw = os.getenv("ALLOWED_ROLE_IDS", "")
    if not allowed_raw:
        return False
    try:
        allowed_ids = {int(x.strip()) for x in allowed_raw.split(",") if x.strip()}
    except ValueError:
        # env is malformed
        return False

    # interaction.user is normally a Member in guild context
    member = interaction.user if isinstance(interaction.user, discord.Member) else None

    # fallback: try to get member from guild
    if not member and interaction.guild:
        member = interaction.guild.get_member(interaction.user.id)

    return _member_has_allowed_role(member, allowed_ids)

def is_allowed(interaction: discord.Interaction) -> bool:
    if has_allowed_role_env(interaction):
        return True
    raise app_commands.CheckFailure("You don't have permission to use this command.")

# Optional: global error handler for app commands (friendly reply for permission issues)
@bot.event
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    # Only respond if the interaction hasn't been responded to yet
    try:
        if isinstance(error, app_commands.CheckFailure):
            # ephemeral so only the invoker sees it
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return
    except Exception:
        # ignore response errors
        pass

    # For other errors, you may want to log and send a generic message
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message("An error occurred while running the command.", ephemeral=True)
    except Exception:
        pass
    # re-raise to log full stack in console if needed
    raise error

# --- Bot Events ---
@bot.event
async def on_ready():
    print(f"Bot is logged in as {bot.user}")
    try:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            print("Commands synced instantly with Guild ID.")
        else:
            await bot.tree.sync()
            print("Global commands synced (can take up to 1h).")
    except Exception as e:
        print(f"Sync error: {e}")

# --- Commands (restricted by @app_commands.check(is_allowed)) ---
@app_commands.check(is_allowed)
@bot.tree.command(name="product_add", description="Add a new product to the store")
@app_commands.describe(name="Product name", price="Price", category="Category", image="Image URL or path")
async def product_add(interaction: discord.Interaction, name: str, price: str, category: str, image: str):
    products = load_products()
    new_id = next_id(products)
    new_product = {
        "id": new_id,
        "name": name,
        "price": price,
        "category": category,
        "image": image
    }
    products.append(new_product)
    save_products(products)

    embed = discord.Embed(
        title="✅ Product Added",
        description=f"**{name}** has been added to the store.",
        color=discord.Color.green()
    )
    embed.add_field(name="ID", value=str(new_id))
    embed.add_field(name="Price", value=f"${price}")
    embed.add_field(name="Category", value=category)
    embed.set_thumbnail(url=image)

    await interaction.response.send_message(embed=embed)

@app_commands.check(is_allowed)
@bot.tree.command(name="product_remove", description="Remove a product by its ID")
@app_commands.describe(product_id="ID of the product to remove")
async def product_remove(interaction: discord.Interaction, product_id: int):
    products = load_products()
    product_to_remove = next((p for p in products if p.get("id") == product_id), None)

    if not product_to_remove:
        embed = discord.Embed(
            title="❌ Product Not Found",
            description=f"No product with ID `{product_id}` exists.",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)
        return

    new_products = [p for p in products if p.get("id") != product_id]
    save_products(new_products)

    embed = discord.Embed(
        title="🗑 Product Removed",
        description=f"**{product_to_remove['name']}** has been removed.",
        color=discord.Color.orange()
    )
    embed.add_field(name="ID", value=str(product_id))
    embed.set_thumbnail(url=product_to_remove.get("image"))

    await interaction.response.send_message(embed=embed)

@app_commands.check(is_allowed)
@bot.tree.command(name="product_list", description="List all products in the store")
async def product_list(interaction: discord.Interaction):
    products = load_products()

    if not products:
        embed = discord.Embed(
            title="📭 No Products",
            description="The store is currently empty.",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)
        return

    embed = discord.Embed(
        title="🛒 Product List",
        description="Here are the current products:",
        color=discord.Color.blue()
    )

    for p in products:
        embed.add_field(
            name=f"{p['id']}: {p['name']}",
            value=f"💰 ${p['price']} | 🏷 {p['category']}",
            inline=False
        )

    await interaction.response.send_message(embed=embed)

# Run the bot
bot.run(os.getenv("DISCORD_TOKEN"))
