"""Which roles the webhook API (/assign-role, /remove-role) is allowed to touch.

PlatPursuit asks the bot to toggle milestone and premium roles. Those are configured per
milestone in PlatPursuit's database, so a fixed allowlist here would silently refuse every new
one. Instead the bot refuses any role that carries power, server-wide or in any one channel: a
leaked API key can then only toggle harmless roles, never hand someone Administrator or a
moderator role. A role may still unlock a private channel (premium channels rely on that).
"""
import discord

#: Permissions a role toggled by the API must not grant. Aliases (manage_emojis,
#: manage_permissions, ...) are covered by the flag they alias.
PRIVILEGED_PERMISSIONS = (
    'administrator',
    'manage_guild', 'manage_roles', 'manage_channels', 'manage_messages', 'manage_webhooks',
    'manage_nicknames', 'manage_expressions', 'manage_events', 'manage_threads',
    'kick_members', 'ban_members', 'moderate_members',
    'mute_members', 'deafen_members', 'move_members',
    'mention_everyone', 'view_audit_log', 'view_guild_insights', 'view_creator_monetization_analytics',
)

#: Moderation permissions Discord has added that this discord.py release cannot name yet, by bit.
#: Caught on the role itself only: discord.py drops unnamed bits when it builds a channel
#: overwrite. Move each into PRIVILEGED_PERMISSIONS once discord.py names it (a test fails then).
UNNAMED_PRIVILEGED_BITS = {
    'pin_messages': 1 << 51,     # split out of manage_messages in 2025
    'bypass_slowmode': 1 << 52,
}


def _privileged(permissions: discord.Permissions | discord.PermissionOverwrite) -> list[str]:
    if isinstance(permissions, discord.PermissionOverwrite):
        # An overwrite's flags are allow, deny or inherit; only the allowed ones grant anything.
        permissions = permissions.pair()[0]
    named = [name for name in PRIVILEGED_PERMISSIONS if getattr(permissions, name)]
    return named + [name for name, bit in UNNAMED_PRIVILEGED_BITS.items() if permissions.value & bit]


def refusal_reason(role: discord.Role) -> str | None:
    """Why the API must not toggle `role`, or None when it may."""
    if role.is_default():
        return 'it is @everyone'
    if role.managed:
        return 'it is managed by an integration or bot'
    if granted := _privileged(role.permissions):
        return f"it grants {', '.join(granted)}"
    for channel in role.guild.channels:
        if granted := _privileged(channel.overwrites_for(role)):
            return f"it grants {', '.join(granted)} in #{channel.name}"
    return None
