from commands.x_announcements import XAnnouncementsCog


def _cog(handle):
    """Build a cog instance without running __init__ (which reads env / makes a session)."""
    cog = XAnnouncementsCog.__new__(XAnnouncementsCog)
    cog.own_handle = handle
    return cog


def test_entry_id_prefers_id_then_link():
    assert XAnnouncementsCog._entry_id({'id': 'abc', 'link': 'l'}) == 'abc'
    assert XAnnouncementsCog._entry_id({'link': 'l'}) == 'l'
    assert XAnnouncementsCog._entry_id({}) is None


def test_is_own_tweet_matches_handle_case_insensitively():
    cog = _cog('platpursuit')
    assert cog._is_own_tweet({'link': 'https://x.com/PlatPursuit/status/123'}) is True
    assert cog._is_own_tweet({'link': 'https://x.com/SomeoneElse/status/123'}) is False


def test_is_own_tweet_allows_unrecognized_link_shape():
    cog = _cog('platpursuit')
    # Fail-open: a link that doesn't match the expected pattern is allowed through.
    assert cog._is_own_tweet({'link': 'https://example.com/whatever'}) is True


def test_filter_owned_returns_all_when_no_handle():
    cog = _cog(None)
    entries = [{'link': 'https://x.com/a/status/1'}, {'link': 'https://x.com/b/status/2'}]
    assert cog._filter_owned(entries) == entries


def test_filter_owned_drops_retweets():
    cog = _cog('platpursuit')
    own = {'link': 'https://x.com/platpursuit/status/1'}
    retweet = {'link': 'https://x.com/someoneelse/status/2'}
    assert cog._filter_owned([own, retweet]) == [own]
