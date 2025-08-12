import json
import os
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

GUILD_ID = os.getenv("1378007406365380628")  # Optional, for instant command sync
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

# --- Commands ---
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
    embed.set_thumbnail(url=product_to_remove["image"])

    await interaction.response.send_message(embed=embed)

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

bot.run(os.getenv("DISCORD_TOKEN"))
