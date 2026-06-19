from unittest.mock import MagicMock

import discord

from commands.tickets import TicketsCog


def _cog(category_id=555):
    cog = TicketsCog(MagicMock())
    cog.category_id = category_id
    return cog


def _named(name):
    """A mock channel whose .name is a real string (MagicMock(name=...) would NOT set .name)."""
    ch = MagicMock()
    ch.name = name
    return ch


def test_self_ticket_name_encodes_owner_id():
    user = MagicMock()
    user.name = 'CoolUser'
    user.id = 12345
    assert TicketsCog._self_ticket_name(user) == 'ticket-CoolUser-12345'


def test_is_ticket_channel_accepts_self_and_mod_channels():
    cog = _cog(555)
    for name in ('ticket-bob-1', 'modticket-bob'):
        ch = MagicMock(spec=discord.TextChannel)
        ch.category_id = 555
        ch.name = name
        assert cog._is_ticket_channel(ch) is True


def test_is_ticket_channel_rejects_wrong_category():
    cog = _cog(555)
    ch = MagicMock(spec=discord.TextChannel)
    ch.category_id = 999
    ch.name = 'ticket-bob-1'
    assert cog._is_ticket_channel(ch) is False


def test_is_ticket_channel_rejects_non_ticket_name():
    cog = _cog(555)
    ch = MagicMock(spec=discord.TextChannel)
    ch.category_id = 555
    ch.name = 'general'
    assert cog._is_ticket_channel(ch) is False


def test_find_open_ticket_matches_owner_by_id():
    cog = _cog()
    category = MagicMock()
    category.text_channels = [_named('ticket-alice-111'), _named('ticket-bob-222')]
    assert cog._find_open_ticket(category, 222).name == 'ticket-bob-222'
    assert cog._find_open_ticket(category, 333) is None


def test_find_open_ticket_ignores_mod_tickets():
    cog = _cog()
    category = MagicMock()
    category.text_channels = [_named('modticket-alice')]
    assert cog._find_open_ticket(category, 111) is None


def test_find_open_ticket_no_false_suffix_match():
    cog = _cog()
    category = MagicMock()
    category.text_channels = [_named('ticket-x-111')]
    # user id 11 must not match the channel for user 111
    assert cog._find_open_ticket(category, 11) is None
    assert cog._find_open_ticket(category, 111) is not None
