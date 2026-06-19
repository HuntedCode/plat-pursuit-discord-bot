import discord
from discord import app_commands
from discord.ext import commands
import logging

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from db.models import ModNote

logger = logging.getLogger('psn_api')

MAX_NOTE_LENGTH = 1000
# Keep the rendered note list under Discord's 4096-char embed description limit.
VIEW_CHAR_BUDGET = 3800


class NotesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    note_group = app_commands.Group(
        name='note',
        description='Moderator notes on users.',
        default_permissions=discord.Permissions(manage_messages=True),
        guild_only=True,
    )

    @note_group.command(name='add', description="MODERATOR ONLY: Add a note to a user's record.")
    @app_commands.describe(user='The user to add a note to.', note='The note text.')
    async def add(self, interaction: discord.Interaction, user: discord.Member, note: str):
        await interaction.response.defer(ephemeral=True)

        if not self.bot.db_sessionmaker:
            await interaction.followup.send('Notes are unavailable (database not configured).', ephemeral=True)
            return

        text = note.strip()
        if not text:
            await interaction.followup.send('The note cannot be empty.', ephemeral=True)
            return
        if len(text) > MAX_NOTE_LENGTH:
            await interaction.followup.send(f"Notes are limited to {MAX_NOTE_LENGTH} characters.", ephemeral=True)
            return

        try:
            async with self.bot.db_sessionmaker() as session:
                session.add(ModNote(
                    guild_id=interaction.guild_id,
                    target_user_id=user.id,
                    author_id=interaction.user.id,
                    author_name=str(interaction.user),
                    content=text,
                ))
                await session.commit()
        except SQLAlchemyError as e:
            logger.error(f"Failed to add mod note for {user.id}: {e}")
            await interaction.followup.send('Could not save the note. Please try again later.', ephemeral=True)
            return

        await interaction.followup.send(f"Note added for {user.mention}.", ephemeral=True)
        logger.info(f"Mod note added for {user.id} by {interaction.user.id}")

    @note_group.command(name='view', description="MODERATOR ONLY: View a user's notes.")
    @app_commands.describe(user='The user whose notes to view.')
    async def view(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)

        if not self.bot.db_sessionmaker:
            await interaction.followup.send('Notes are unavailable (database not configured).', ephemeral=True)
            return

        try:
            async with self.bot.db_sessionmaker() as session:
                result = await session.execute(
                    select(ModNote)
                    .where(ModNote.guild_id == interaction.guild_id, ModNote.target_user_id == user.id)
                    .order_by(ModNote.created_at.desc())
                )
                notes = result.scalars().all()

                if not notes:
                    await interaction.followup.send(f"No notes recorded for {user.mention}.", ephemeral=True)
                    return

                # Render inside the session so loaded columns are guaranteed available.
                blocks = []
                budget = VIEW_CHAR_BUDGET
                for n in notes:
                    stamp = discord.utils.format_dt(n.created_at, 'f')
                    block = f"**{n.author_name}** · {stamp}\n{n.content}"
                    if len(block) + 2 > budget:
                        break
                    blocks.append(block)
                    budget -= len(block) + 2
        except SQLAlchemyError as e:
            logger.error(f"Failed to fetch mod notes for {user.id}: {e}")
            await interaction.followup.send('Could not load notes. Please try again later.', ephemeral=True)
            return

        embed = discord.Embed(
            title=f"Notes for {user.display_name}",
            color=0x5865F2,
            description='\n\n'.join(blocks),
        )
        if len(blocks) < len(notes):
            embed.set_footer(text=f"Showing {len(blocks)} of {len(notes)} notes (most recent). Older notes not shown.")
        else:
            embed.set_footer(text=f"{len(notes)} note(s) total")

        await interaction.followup.send(embed=embed, ephemeral=True)
        logger.info(f"Mod notes viewed for {user.id} by {interaction.user.id}")


async def setup(bot):
    await bot.add_cog(NotesCog(bot))
