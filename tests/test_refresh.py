from tests.fake_api import mock_api
from utils.api_errors import UNAVAILABLE_MESSAGE

from commands.refresh import RefreshCog


def _sent_text(interaction):
    args, kwargs = interaction.followup.send.call_args
    return args[0] if args else kwargs.get('content', '')


async def test_refresh_success(api_bot, user_factory, interaction_factory):
    cog = RefreshCog(api_bot)
    interaction = interaction_factory(user=user_factory(7, 'player'))
    with mock_api(cog.bot) as mocked:
        mocked.post(
            'https://api.test/refresh/',
            payload={'linked': True, 'success': True, 'psn_username': 'CoolPSN'},
        )
        await RefreshCog.refresh.callback(cog, interaction)
    assert 'CoolPSN' in _sent_text(interaction)


async def test_refresh_not_linked(api_bot, user_factory, interaction_factory):
    cog = RefreshCog(api_bot)
    interaction = interaction_factory(user=user_factory(7))
    with mock_api(cog.bot) as mocked:
        mocked.post('https://api.test/refresh/', payload={'linked': False, 'message': 'No linked profile'})
        await RefreshCog.refresh.callback(cog, interaction)
    assert 'No linked profile' in _sent_text(interaction)


async def test_refresh_api_error_status(api_bot, user_factory, interaction_factory):
    cog = RefreshCog(api_bot)
    interaction = interaction_factory(user=user_factory(7))
    with mock_api(cog.bot) as mocked:
        mocked.post('https://api.test/refresh/', status=500)
        await RefreshCog.refresh.callback(cog, interaction)
    assert _sent_text(interaction) == UNAVAILABLE_MESSAGE, 'a 500 says PlatPursuit is having trouble, not "API error"'
