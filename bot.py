import os
import sqlite3
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from storage import Store


load_dotenv()
store = Store()
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
deal_group = app_commands.Group(name="deal", description="Create and manage private deals")
admin_group = app_commands.Group(name="admin", description="Configure deal bot settings")
DEAL_TYPES = ["Premade", "Custom", "Session"]


def is_staff(member: discord.Member, guild_id: int) -> bool:
    roles = store.get_roles(guild_id)
    return member.guild_permissions.administrator or bool(
        roles and any(role.id == roles["staff_role_id"] for role in member.roles)
    )


def has_role(member: discord.Member, role_id: int) -> bool:
    return any(role.id == role_id for role in member.roles)


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def deal_embed(embed: discord.Embed, row: sqlite3.Row) -> discord.Embed:
    embed.title = f"Deal #{row['deal_id']} | {row['status']}"
    embed.add_field(name="Type", value=row["type"], inline=True)
    embed.add_field(name="Amount (USD)", value=money(row["amount_cents"]), inline=True)
    embed.add_field(name="Spender", value=f"<@{row['spender_id']}>", inline=True)
    embed.add_field(name="Host", value=f"<@{row['host_id']}>", inline=True)
    embed.add_field(name="Description", value=row["description"][:1024], inline=False)
    embed.timestamp = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
    return embed


async def send_staff_log(guild: discord.Guild, row: sqlite3.Row, note: str | None = None) -> None:
    channel_id = os.getenv("STAFF_LOG_CHANNEL_ID")
    if not channel_id:
        return
    try:
        channel = guild.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
        return
    if not hasattr(channel, "send"):
        return
    embed = deal_embed(discord.Embed(color=discord.Color.dark_teal()), row)
    if note:
        embed.set_footer(text=note[:2048])
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden, discord.HTTPException):
        pass


class DealApprovalView(discord.ui.View):
    def __init__(self, deal_id: int):
        super().__init__(timeout=None)
        self.deal_id = deal_id
        self.accept.custom_id = f"deal:{deal_id}:accept"
        self.decline.custom_id = f"deal:{deal_id}:decline"

    async def resolve(self, interaction: discord.Interaction, new_status: str) -> None:
        row = store.get_deal(self.deal_id)
        if row is None:
            await interaction.response.send_message("This deal no longer exists.", ephemeral=True)
            return
        if interaction.user.id != row["host_id"]:
            await interaction.response.send_message("Only the tagged host can respond to this deal.", ephemeral=True)
            return
        if not store.update_status(self.deal_id, "Pending", new_status):
            await interaction.response.send_message("This deal has already been answered.", ephemeral=True)
            return
        updated = store.get_deal(self.deal_id)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=deal_embed(discord.Embed(color=discord.Color.dark_teal()), updated), view=self)
        guild = bot.get_guild(updated["guild_id"])
        if guild:
            await send_staff_log(guild, updated)
        await interaction.followup.send(f"Deal #{self.deal_id} marked {new_status.lower()}.", ephemeral=True)

    @discord.ui.button(label="Accept Deal", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.resolve(interaction, "Accepted")

    @discord.ui.button(label="Decline Deal", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.resolve(interaction, "Cancelled")


class LeaderboardView(discord.ui.View):
    def __init__(self, guild: discord.Guild, staff_view: bool):
        super().__init__(timeout=300)
        self.guild = guild
        self.staff_view = staff_view
        self.period = "all_time"
        self.role_type = "buyer"
        self.deal_type: str | None = None

    async def refresh(self, interaction: discord.Interaction) -> None:
        embed = await make_leaderboard(
            self.guild, self.period, self.role_type, self.deal_type, self.staff_view
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="My Rank", style=discord.ButtonStyle.secondary, row=2)
    async def my_rank(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        rows = store.leaderboard(
            self.period, month, self.role_type, self.deal_type,
            self.staff_view, limit=None,
        )
        user_rank = next(
            ((rank, row) for rank, row in enumerate(rows, start=1)
             if row["user_id"] == interaction.user.id),
            None,
        )
        if user_rank is None:
            await interaction.response.send_message(
                "You have no completed deals in this leaderboard yet.", ephemeral=True
            )
            return
        rank, row = user_rank
        if self.role_type == "buyer":
            summary = f"{money(row['amount_cents'])} spent across {row['deal_count']} deals"
        elif self.staff_view:
            summary = f"{money(row['amount_cents'])} earned across {row['deal_count']} deals"
        else:
            summary = f"{row['deal_count']} completed deals"
        period_label = "all-time" if self.period == "all_time" else f"this month ({month})"
        type_label = self.deal_type or "all deal types"
        await interaction.response.send_message(
            f"Your rank is **#{rank}** for {period_label}, {type_label}: {summary}.",
            ephemeral=True,
        )

    @discord.ui.button(label="All-Time", style=discord.ButtonStyle.primary, row=0)
    async def all_time(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.period = "all_time"
        self.all_time.style = discord.ButtonStyle.primary
        self.monthly.style = discord.ButtonStyle.secondary
        await self.refresh(interaction)

    @discord.ui.button(label="Monthly", style=discord.ButtonStyle.secondary, row=0)
    async def monthly(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.period = "monthly"
        self.all_time.style = discord.ButtonStyle.secondary
        self.monthly.style = discord.ButtonStyle.primary
        await self.refresh(interaction)

    @discord.ui.button(label="Top Buyers", style=discord.ButtonStyle.primary, row=0)
    async def buyers(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.role_type = "buyer"
        self.buyers.style = discord.ButtonStyle.primary
        self.creators.style = discord.ButtonStyle.secondary
        await self.refresh(interaction)

    @discord.ui.button(label="Top Creators", style=discord.ButtonStyle.secondary, row=0)
    async def creators(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.role_type = "creator"
        self.buyers.style = discord.ButtonStyle.secondary
        self.creators.style = discord.ButtonStyle.primary
        await self.refresh(interaction)

    @discord.ui.select(
        placeholder="All deal types", row=1,
        options=[discord.SelectOption(label="All deal types", value="all")]
        + [discord.SelectOption(label=value, value=value) for value in DEAL_TYPES],
    )
    async def choose_type(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        self.deal_type = None if select.values[0] == "all" else select.values[0]
        await self.refresh(interaction)


async def make_leaderboard(
    guild: discord.Guild, period: str, role_type: str, deal_type: str | None,
    staff_view: bool,
) -> discord.Embed:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    rows = store.leaderboard(period, month, role_type, deal_type, staff_view)
    title_role = "Top Buyers" if role_type == "buyer" else "Top Creators"
    period_label = "All-Time" if period == "all_time" else f"Monthly ({month})"
    embed = discord.Embed(title=f"{title_role} | {period_label}", color=discord.Color.purple())
    embed.description = f"Deal type: {deal_type or 'All'}"
    if not rows:
        embed.add_field(name="No completed deals yet", value="", inline=False)
        return embed
    lines = []
    for rank, row in enumerate(rows, start=1):
        user_id = row["user_id"]
        user_link = f"[{user_id}](https://discord.com/users/{user_id})"
        if role_type == "buyer":
            value = f"{money(row['amount_cents'])} spent | {row['deal_count']} deals"
        elif staff_view:
            value = f"{money(row['amount_cents'])} earned | {row['deal_count']} deals"
        else:
            value = f"{row['deal_count']} completed deals"
        lines.append(f"**{rank}. {user_link}** - {value}")
    embed.description += "\n\n" + "\n".join(lines)
    if role_type == "creator" and not staff_view:
        embed.set_footer(text="Creator earnings are visible only to staff.")
    return embed


@deal_group.command(name="create", description="Send a private deal request to a host")
@app_commands.describe(target_user="The creator/host", deal_type="Premade, Custom, or Session", amount_usd="USD amount", description="Short deal description")
@app_commands.choices(deal_type=[app_commands.Choice(name=value, value=value) for value in DEAL_TYPES])
async def create_deal(
    interaction: discord.Interaction,
    target_user: discord.Member,
    deal_type: app_commands.Choice[str],
    amount_usd: app_commands.Range[float, 0.01, 1000000.0],
    description: str,
) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Deals can only be created in a server.", ephemeral=True)
        return
    roles = store.get_roles(interaction.guild.id)
    if not roles:
        await interaction.response.send_message("An administrator must configure buyer, creator, and staff roles first.", ephemeral=True)
        return
    if not has_role(interaction.user, roles["buyer_role_id"]):
        await interaction.response.send_message("You need the configured buyer role to create a deal.", ephemeral=True)
        return
    if not has_role(target_user, roles["creator_role_id"]):
        await interaction.response.send_message("The selected host does not have the configured creator role.", ephemeral=True)
        return
    if target_user.id == interaction.user.id or target_user.bot:
        await interaction.response.send_message("Choose another server member as the host.", ephemeral=True)
        return
    if not description.strip() or len(description) > 1000:
        await interaction.response.send_message("Description must be between 1 and 1000 characters.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    amount_cents = round(amount_usd * 100)
    deal_id = store.create_deal(
        interaction.guild.id, interaction.user.id, target_user.id,
        deal_type.value, amount_cents, description,
    )
    row = store.get_deal(deal_id)
    try:
        await target_user.send(
            content=f"New private deal request from {interaction.user.mention}.",
            embed=deal_embed(discord.Embed(color=discord.Color.gold()), row),
            view=DealApprovalView(deal_id),
            allowed_mentions=discord.AllowedMentions(users=[interaction.user]),
        )
    except (discord.Forbidden, discord.HTTPException):
        store.update_status(deal_id, "Pending", "Cancelled")
        row = store.get_deal(deal_id)
        await send_staff_log(interaction.guild, row, "Host DM failed; request cancelled.")
        await interaction.followup.send("I could not DM that host, so the deal was cancelled. Ask them to enable server-member DMs and try again.", ephemeral=True)
        return
    await send_staff_log(interaction.guild, row)
    await interaction.followup.send(f"Deal #{deal_id} was sent privately to {target_user.mention}. Only you and the host received the deal details; staff can see them in the staff log.", ephemeral=True)


@deal_group.command(name="complete", description="Mark an accepted deal as completed")
async def complete_deal(interaction: discord.Interaction, deal_id: app_commands.Range[int, 1, 2147483647]) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("This command must be used in the deal's server.", ephemeral=True)
        return
    row = store.get_deal(deal_id)
    if row is None or row["guild_id"] != interaction.guild.id:
        await interaction.response.send_message("Deal not found in this server.", ephemeral=True)
        return
    staff = is_staff(interaction.user, interaction.guild.id)
    if interaction.user.id not in (row["spender_id"], row["host_id"]) and not staff:
        await interaction.response.send_message("Only the spender, host, or staff can complete this deal.", ephemeral=True)
        return
    if row["status"] != "Accepted":
        await interaction.response.send_message("Only accepted deals can be completed.", ephemeral=True)
        return
    if not store.update_status(deal_id, "Accepted", "Completed"):
        await interaction.response.send_message("This deal changed before it could be completed; try again.", ephemeral=True)
        return
    updated = store.get_deal(deal_id)
    await send_staff_log(interaction.guild, updated)
    await interaction.response.send_message(f"Deal #{deal_id} completed. Leaderboards have been updated.", ephemeral=True)


@deal_group.command(name="cancel", description="Force-cancel a pending or accepted deal (staff only)")
async def cancel_deal(interaction: discord.Interaction, deal_id: app_commands.Range[int, 1, 2147483647]) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("This command must be used in the deal's server.", ephemeral=True)
        return
    if not is_staff(interaction.user, interaction.guild.id):
        await interaction.response.send_message("Only staff can force-cancel deals.", ephemeral=True)
        return
    row = store.get_deal(deal_id)
    if row is None or row["guild_id"] != interaction.guild.id:
        await interaction.response.send_message("Deal not found in this server.", ephemeral=True)
        return
    if row["status"] not in ("Pending", "Accepted"):
        await interaction.response.send_message("Only pending or accepted deals can be force-cancelled.", ephemeral=True)
        return
    if not store.update_status(deal_id, row["status"], "Cancelled"):
        await interaction.response.send_message("This deal changed before it could be cancelled; try again.", ephemeral=True)
        return
    updated = store.get_deal(deal_id)
    await send_staff_log(interaction.guild, updated, f"Force-cancelled by staff user {interaction.user.id}.")
    await interaction.response.send_message(f"Deal #{deal_id} was force-cancelled.", ephemeral=True)


@admin_group.command(name="set-roles", description="Configure roles allowed to buy, create, and administer deals")
@app_commands.default_permissions(administrator=True)
async def set_roles(
    interaction: discord.Interaction,
    buyer_role: discord.Role,
    creator_role: discord.Role,
    staff_role: discord.Role,
) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Only a server administrator can configure these roles.", ephemeral=True)
        return
    store.set_roles(interaction.guild.id, buyer_role.id, creator_role.id, staff_role.id)
    await interaction.response.send_message(
        f"Roles saved: buyer {buyer_role.mention}, creator {creator_role.mention}, staff {staff_role.mention}.",
        ephemeral=True,
    )


@bot.tree.command(name="leaderboard", description="View completed-deal leaderboards")
async def leaderboard(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("Use this command in a server.", ephemeral=True)
        return
    staff_view = isinstance(interaction.user, discord.Member) and is_staff(
        interaction.user, interaction.guild.id
    )
    view = LeaderboardView(interaction.guild, staff_view)
    embed = await make_leaderboard(
        interaction.guild, view.period, view.role_type, view.deal_type, staff_view
    )
    await interaction.response.send_message(embed=embed, view=view, ephemeral=staff_view)


@bot.event
async def on_ready() -> None:
    if not getattr(bot, "commands_synced", False):
        store.initialize()
        bot.tree.add_command(deal_group)
        bot.tree.add_command(admin_group)
        await bot.tree.sync()
        for row in store.pending_deals():
            bot.add_view(DealApprovalView(row["deal_id"]))
        bot.commands_synced = True
    print(f"Logged in as {bot.user}")


def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is required")
    if not os.getenv("STAFF_LOG_CHANNEL_ID"):
        raise RuntimeError("STAFF_LOG_CHANNEL_ID is required")
    bot.run(token)


if __name__ == "__main__":
    main()