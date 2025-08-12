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
GITHUB_OWNER = os.getenv("GITHUB_OWNER")  # e.g. "Nopro497138"
GITHUB_REPO = os.getenv("GITHUB_REPO")    # e.g. "Garden-mart"
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")

# parse allowed role ids into a set of ints
def parse_allowed_roles(raw: str):
    try:
        return {int(x.strip()) for x in raw.split(",") if x.strip()}
    except ValueError:
        return set()

ALLOWED_ROLE_IDS = parse_allowed_roles(ALLOWED_ROLE_IDS_RAW)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# --- JSON helpers ---
def load_products(path=PRODUCTS_FILE):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        # bad JSON -> return empty to avoid crashing, but log
        print(f"Error: products file {path} contains invalid JSON.")
        return []

def save_products_atomic(products, path=PRODUCTS_FILE):
    # write to a temp file and replace (atomic)
    data = json.dumps(products, ensure_ascii=False, indent=2)
    dirpath = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=dirpath, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception as e:
        # cleanup
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise

def next_id(products):
    if not products:
        return 1
    return max((p.get("id", 0) for p in products)) + 1

# --- GitHub file update (optional) ---
def github_update_file(owner, repo, path, content_bytes, message, branch="main", token=None):
    """
    Update a file in the given GitHub repo using the Contents API.
    Returns (True, response_json) on success, (False, error_message) on failure.
    """
    if not token:
        return False, "No GitHub token provided."

    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json"
    }

    # 1) get current file to obtain sha (if exists)
    get_resp = requests.get(api_url, headers=headers, params={"ref": branch})
    sha = None
    if get_resp.status_code == 200:
        try:
            sha = get_resp.json().get("sha")
        except Exception:
            pass
    elif get_resp.status_code not in (404,):
        return False, f"GitHub GET returned {get_resp.status_code}: {get_resp.text}"

    # prepare body
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

# --- Role check helpers ---
def _member_has_allowed_role(member: discord.Member, allowed_ids: set[int]) -> bool:
    if not member:
        return False
    return any(role.id in allowed_ids for role in member.roles)

def has_allowed_role_env(interaction: discord.Interaction) -> bool:
    # If ALLOWED_ROLE_IDS is empty, treat as no-one allowed.
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

# Friendly error handler for app commands
@bot.event
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    try:
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return
    except Exception:
        pass

    # generic fallback
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message("An error occurred while running the command.", ephemeral=True)
    except Exception:
        pass
    # re-raise so it appears in logs
    raise error

# --- Bot events ---
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

# --- Commands (protected) ---
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

    # save locally
    try:
        save_products_atomic(products, PRODUCTS_FILE)
    except Exception as e:
        print(f"Failed to save products locally: {e}")
        await interaction.response.send_message("❌ Failed to save product locally.", ephemeral=True)
        return

    # if configured, try to push to GitHub
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

    # prepare embed reply
    embed = discord.Embed(title="✅ Product Added", description=f"**{name}** has been added to the store.", color=discord.Color.green())
    embed.add_field(name="ID", value=str(new_id))
    embed.add_field(name="Price", value=f"${price}")
    embed.add_field(name="Category", value=category)
    embed.set_thumbnail(url=image)

    # include GitHub info if available
    if github_result:
        ok, info = github_result
        if ok:
            embed.add_field(name="GitHub", value="✅ Pushed to repository", inline=False)
        else:
            embed.add_field(name="GitHub", value=f"⚠️ Failed to push: {info}", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@app_commands.check(is_allowed)
@bot.tree.command(name="product_remove", description="Remove a product by its ID")
@app_commands.describe(product_id="ID of the product to remove")
async def product_remove(interaction: discord.Interaction, product_id: int):
    products = load_products()
    product_to_remove = next((p for p in products if p.get("id") == product_id), None)

    if not product_to_remove:
        await interaction.response.send_message(embed=discord.Embed(title="❌ Product Not Found", description=f"No product with ID `{product_id}` exists.", color=discord.Color.red()), ephemeral=True)
        return

    new_products = [p for p in products if p.get("id") != product_id]

    try:
        save_products_atomic(new_products, PRODUCTS_FILE)
    except Exception as e:
        print(f"Failed to save products locally: {e}")
        await interaction.response.send_message("❌ Failed to remove product locally.", ephemeral=True)
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

    embed = discord.Embed(title="🗑 Product Removed", description=f"**{product_to_remove['name']}** has been removed.", color=discord.Color.orange())
    embed.add_field(name="ID", value=str(product_id))
    embed.set_thumbnail(url=product_to_remove.get("image"))

    if github_result:
        ok, info = github_result
        if ok:
            embed.add_field(name="GitHub", value="✅ Pushed to repository", inline=False)
        else:
            embed.add_field(name="GitHub", value=f"⚠️ Failed to push: {info}", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@app_commands.check(is_allowed)
@bot.tree.command(name="product_list", description="List all products in the store")
async def product_list(interaction: discord.Interaction):
    products = load_products()

    if not products:
        embed = discord.Embed(title="📭 No Products", description="The store is currently empty.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    embed = discord.Embed(title="🛒 Product List", description="Here are the current products:", color=discord.Color.blue())
    for p in products:
        embed.add_field(name=f"{p['id']}: {p['name']}", value=f"💰 ${p['price']} | 🏷 {p['category']}", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

# run the bot
bot.run(os.getenv("DISCORD_TOKEN"))
