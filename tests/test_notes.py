from unittest.mock import MagicMock

from sqlalchemy import select

from commands.notes import NotesCog, MAX_NOTE_LENGTH
from db.models import ModNote


def _sent_text(interaction):
    """Return the positional content passed to followup.send."""
    args, kwargs = interaction.followup.send.call_args
    return args[0] if args else kwargs.get('content', '')


async def test_add_inserts_trimmed_note(notes_bot, db_sessionmaker, user_factory, interaction_factory):
    cog = NotesCog(notes_bot)
    target = user_factory(42, 'baduser')
    interaction = interaction_factory(guild_id=1000, user=user_factory(7, 'modA'))

    await NotesCog.add.callback(cog, interaction, user=target, note='  spammy behavior  ')

    interaction.followup.send.assert_awaited()
    async with db_sessionmaker() as session:
        rows = (await session.execute(select(ModNote))).scalars().all()
    assert len(rows) == 1
    assert rows[0].content == 'spammy behavior'
    assert rows[0].target_user_id == 42
    assert rows[0].author_id == 7
    assert rows[0].author_name == 'modA#0001'
    assert rows[0].guild_id == 1000


async def test_add_rejects_empty_note(notes_bot, db_sessionmaker, user_factory, interaction_factory):
    cog = NotesCog(notes_bot)
    interaction = interaction_factory(user=user_factory(7))
    await NotesCog.add.callback(cog, interaction, user=user_factory(42), note='   ')
    assert 'empty' in _sent_text(interaction).lower()
    async with db_sessionmaker() as session:
        assert (await session.execute(select(ModNote))).scalars().all() == []


async def test_add_rejects_too_long_note(notes_bot, user_factory, interaction_factory):
    cog = NotesCog(notes_bot)
    interaction = interaction_factory(user=user_factory(7))
    await NotesCog.add.callback(cog, interaction, user=user_factory(42), note='x' * (MAX_NOTE_LENGTH + 1))
    assert 'limited' in _sent_text(interaction).lower()


async def test_add_without_database_degrades(user_factory, interaction_factory):
    bot = MagicMock()
    bot.db_sessionmaker = None
    cog = NotesCog(bot)
    interaction = interaction_factory(user=user_factory(7))
    await NotesCog.add.callback(cog, interaction, user=user_factory(42), note='x')
    assert 'unavailable' in _sent_text(interaction).lower()


async def test_view_lists_notes_newest_first(notes_bot, user_factory, interaction_factory):
    cog = NotesCog(notes_bot)
    target = user_factory(42, 'baduser')
    for txt in ('first note', 'second note'):
        await NotesCog.add.callback(
            cog, interaction_factory(user=user_factory(7, 'modA')), user=target, note=txt
        )

    interaction = interaction_factory(user=user_factory(8, 'modB'))
    await NotesCog.view.callback(cog, interaction, user=target)

    _, kwargs = interaction.followup.send.call_args
    embed = kwargs.get('embed')
    assert embed is not None
    # newest first: 'second note' should appear before 'first note'
    assert embed.description.index('second note') < embed.description.index('first note')


async def test_view_no_notes_message(notes_bot, user_factory, interaction_factory):
    cog = NotesCog(notes_bot)
    interaction = interaction_factory(user=user_factory(8))
    await NotesCog.view.callback(cog, interaction, user=user_factory(999, 'cleanuser'))
    assert 'no notes' in _sent_text(interaction).lower()


async def test_notes_isolated_by_guild(notes_bot, user_factory, interaction_factory):
    cog = NotesCog(notes_bot)
    target = user_factory(42)
    await NotesCog.add.callback(
        cog, interaction_factory(guild_id=1, user=user_factory(7)), user=target, note='guild 1 note'
    )

    # A different guild must not see guild 1's notes.
    interaction = interaction_factory(guild_id=2, user=user_factory(7))
    await NotesCog.view.callback(cog, interaction, user=target)
    assert 'no notes' in _sent_text(interaction).lower()
