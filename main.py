# main.py
import json
import os
import tempfile
import base64
import requests
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# Config from env
GUILD_ID = os.getenv("GUILD_ID")  # optional, for instant guild sync (string)
PRODUCTS_FILE = os.getenv("PRODUCTS_FILE", "products.json")  # relative path used by the bot
ALLOWED_ROLE_IDS_RAW = os.getenv("ALLOWED_ROLE_IDS", "")  # comma-separated role ids
# GitHub auto-commit settings (optional)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_OWNER = os.getenv("GITHUB_OWNER")
GITHUB_REPO = os.getenv("GITHUB_REPO")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")

def parse_allowed_roles(raw: str):
    try:
        return {int(x.strip()) for x in raw.split(",") if x.strip()}
    except ValueError:
        return set()

ALLOWED_ROLE_IDS = parse_allowed_roles(ALLOWED_ROLE_IDS_RAW)

# Discord intents
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- JSON helpers ---
def load_products(path=PRODUCTS_FILE):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        print(f"Error: products file {path} contains invalid JSON.")
        return []

def save_products_atomic(products, path=PRODUCTS_FILE):
    data = json.dumps(products, ensure_ascii=False, indent=2)
    dirpath = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=dirpath, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception as e:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise

def next_id(products):
    if not products:
        return 1
    return max((p.get("id", 0) for p in products)) + 1

# --- GitHub helpers (optional) ---
def github_update_file(owner, repo, path, content_bytes, message, branch="main", token=None):
    if not token:
        return False, "No GitHub token provided."

    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json"
    }

    get_resp = requests.get(api_url, headers=headers, params={"ref": branch})
    sha = None
    if get_resp.status_code == 200:
        sha = get_resp.json().get("sha")
    elif get_resp.status_code not in (404,):
        return False, f"GitHub GET returned {get_resp.status_code}: {get_resp.text}"

    b64content = base64.b64encode(content_bytes).decode()
    payload = {
        "message": message,
        "content": b64content,
        "branch": branch
    }
    if sha:
        payload["sha"] = sha

    put_resp = requests.put(api_url, headers=headers, json=payload)
    if put_resp.status_code in (200, 201):
        return True, put_resp.json()
    else:
        return False, f"GitHub PUT returned {put_resp.status_code}: {put_resp.text}"

# --- Permission check ---
def _member_has_allowed_role(member: discord.Member, allowed_ids: set[int]) -> bool:
    if not member:
        return False
    return any(role.id in allowed_ids for role in member.roles)

def has_allowed_role_env(interaction: discord.Interaction) -> bool:
    if not ALLOWED_ROLE_IDS:
        return False
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if not member and interaction.guild:
        member = interaction.guild.get_member(interaction.user.id)
    return _member_has_allowed_role(member, ALLOWED_ROLE_IDS)

def is_allowed(interaction: discord.Interaction) -> bool:
    if has_allowed_role_env(interaction):
        return True
    raise app_commands.CheckFailure("You don't have permission to use this command.")

# Friendly error handler for permission issues and others
@bot.event
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    try:
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=discord.Embed(
                    title="❌ Permission denied",
                    description="You don't have permission to use this command.",
                    color=discord.Color.red()
                ), ephemeral=True)
            return
    except Exception:
        pass

    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(embed=discord.Embed(
                title="⚠️ Error",
                description="An error occurred while executing the command.",
                color=discord.Color.dark_red()
            ), ephemeral=True)
    except Exception:
        pass
    raise error

# --- on_ready ---
@bot.event
async def on_ready():
    print(f"Bot is logged in as {bot.user}")
    try:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            print("Commands synced with guild.")
        else:
            await bot.tree.sync()
            print("Global commands synced.")
    except Exception as e:
        print(f"Sync error: {e}")

# --- /product_add with category autocomplete ---
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

    try:
        save_products_atomic(products, PRODUCTS_FILE)
    except Exception as e:
        await interaction.response.send_message(embed=discord.Embed(
            title="❌ Save failed",
            description="Failed to save product locally.",
            color=discord.Color.red()
        ), ephemeral=True)
        print(f"Failed to save products: {e}")
        return

    github_result = None
    if GITHUB_TOKEN and GITHUB_OWNER and GITHUB_REPO:
        try:
            with open(PRODUCTS_FILE, "rb") as f:
                content_bytes = f.read()
            ok, resp = github_update_file(
                GITHUB_OWNER,
                GITHUB_REPO,
                os.path.basename(PRODUCTS_FILE),
                content_bytes,
                message=f"Add product {name} via bot",
                branch=GITHUB_BRANCH,
                token=GITHUB_TOKEN
            )
            github_result = (ok, resp)
        except Exception as e:
            github_result = (False, str(e))

    embed = discord.Embed(title="✅ Product Added", description=f"**{name}** has been added to the store.", color=discord.Color.green())
    embed.add_field(name="ID", value=str(new_id), inline=True)
    embed.add_field(name="Price", value=f"${price}", inline=True)
    embed.add_field(name="Category", value=category, inline=True)
    embed.set_thumbnail(url=image)

    if github_result:
        ok, info = github_result
        if ok:
            embed.add_field(name="GitHub", value="✅ Pushed to repository", inline=False)
        else:
            embed.add_field(name="GitHub", value=f"⚠️ Failed to push: {info}", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

# Autocomplete for category: suggest existing categories from products.json
@product_add.autocomplete("category")
async def category_autocomplete(interaction: discord.Interaction, current: str):
    products = load_products()
    categories = sorted({str(p.get("category", "")).strip() for p in products if p.get("category")})
    # filter by current typed text
    suggestions = [c for c in categories if current.lower() in c.lower()]
    # limit to 25 choices
    choices = [app_commands.Choice(name=c, value=c) for c in suggestions[:25]]
    # if no categories exist, offer some defaults
    if not choices:
        defaults = ["pets", "sheckles", "bundles", "misc"]
        choices = [app_commands.Choice(name=d, value=d) for d in defaults if current.lower() in d.lower()][:25]
    return choices

# --- /product_remove via Select menu ---
class ProductRemoveSelect(discord.ui.Select):
    def __init__(self, products_list):
        # products_list: list of dicts
        options = []
        for p in products_list:
            # label must be <= 100 chars
            label = f"{p.get('name', 'Unnamed')}"
            if len(label) > 100:
                label = label[:97] + "..."
            price = p.get("price", "")
            cat = p.get("category", "")
            description = f"${price} • {cat}"[:100]  # description <=100 chars
            options.append(discord.SelectOption(label=label, value=str(p.get("id")), description=description))
        super().__init__(placeholder="Select a product to remove...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        product_id = int(self.values[0])
        products = load_products()
        product_to_remove = next((p for p in products if p.get("id") == product_id), None)
        if not product_to_remove:
            await interaction.response.edit_message(embed=discord.Embed(
                title="❌ Not found",
                description="The selected product could not be found.",
                color=discord.Color.red()
            ), view=None)
            return

        new_products = [p for p in products if p.get("id") != product_id]
        try:
            save_products_atomic(new_products, PRODUCTS_FILE)
        except Exception as e:
            await interaction.response.edit_message(embed=discord.Embed(
                title="❌ Delete failed",
                description="Failed to remove product locally.",
                color=discord.Color.red()
            ), view=None)
            print(f"Failed to save products after delete: {e}")
            return

        github_result = None
        if GITHUB_TOKEN and GITHUB_OWNER and GITHUB_REPO:
            try:
                with open(PRODUCTS_FILE, "rb") as f:
                    content_bytes = f.read()
                ok, resp = github_update_file(
                    GITHUB_OWNER,
                    GITHUB_REPO,
                    os.path.basename(PRODUCTS_FILE),
                    content_bytes,
                    message=f"Remove product {product_id} via bot",
                    branch=GITHUB_BRANCH,
                    token=GITHUB_TOKEN
                )
                github_result = (ok, resp)
            except Exception as e:
                github_result = (False, str(e))

        embed = discord.Embed(title="🗑 Product Removed", description=f"**{product_to_remove.get('name')}** has been removed.", color=discord.Color.orange())
        embed.add_field(name="ID", value=str(product_id), inline=True)
        embed.add_field(name="Category", value=str(product_to_remove.get("category")), inline=True)
        embed.set_thumbnail(url=product_to_remove.get("image"))

        if github_result:
            ok, info = github_result
            if ok:
                embed.add_field(name="GitHub", value="✅ Pushed to repository", inline=False)
            else:
                embed.add_field(name="GitHub", value=f"⚠️ Failed to push: {info}", inline=False)

        # Edit the original ephemeral message to show result and remove view
        await interaction.response.edit_message(embed=embed, view=None)

class ProductRemoveView(discord.ui.View):
    def __init__(self, products_list, timeout=120):
        super().__init__(timeout=timeout)
        self.add_item(ProductRemoveSelect(products_list))

@app_commands.check(is_allowed)
@bot.tree.command(name="product_remove", description="Remove a product by selecting it from a list")
async def product_remove(interaction: discord.Interaction):
    products = load_products()
    if not products:
        await interaction.response.send_message(embed=discord.Embed(
            title="📭 No Products",
            description="The store is currently empty.",
            color=discord.Color.red()
        ), ephemeral=True)
        return

    # prepare list sorted by id; keep at most 25 items for the select menu
    sorted_products = sorted(products, key=lambda x: x.get("id", 0))
    limited = sorted_products[:25]
    if len(sorted_products) > 25:
        note = "\n\n⚠️ Only the first 25 products are shown. If you need to delete others, use `/product_list` and remove by ID."
    else:
        note = ""

    embed = discord.Embed(title="🗑 Remove Product", description=f"Select a product to remove from the list below.{note}", color=discord.Color.orange())
    # add short list for context
    for p in limited:
        embed.add_field(name=f"{p.get('id')}: {p.get('name')}", value=f"💰 ${p.get('price')} • 🏷 {p.get('category')}", inline=False)

    view = ProductRemoveView(limited)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# --- /product_list remains similar, returns an embed ---
@app_commands.check(is_allowed)
@bot.tree.command(name="product_list", description="List all products in the store")
async def product_list(interaction: discord.Interaction):
    products = load_products()
    if not products:
        await interaction.response.send_message(embed=discord.Embed(
            title="📭 No Products",
            description="The store is currently empty.",
            color=discord.Color.red()
        ), ephemeral=True)
        return

    embed = discord.Embed(title="🛒 Product List", description="Here are the current products:", color=discord.Color.blue())
    for p in products:
        embed.add_field(name=f"{p.get('id')}: {p.get('name')}", value=f"💰 ${p.get('price')} | 🏷 {p.get('category')}", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

# Run the bot
bot.run(os.getenv("DISCORD_TOKEN"))
