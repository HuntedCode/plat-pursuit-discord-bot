"""Which roles the webhook API (/assign-role, /remove-role) is allowed to touch.

PlatPursuit asks the bot to toggle milestone and premium roles. Those are configured per
milestone in PlatPursuit's database, so a fixed allowlist here would silently refuse every new
one. Instead the bot refuses any role that carries power: a leaked API key can then only toggle
harmless roles, never hand someone Administrator or a moderator role.
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
    'mention_everyone', 'view_audit_log',
)


def refusal_reason(role: discord.Role) -> str | None:
    """Why the API must not toggle `role`, or None when it may."""
    if role.is_default():
        return 'it is @everyone'
    if role.managed:
        return 'it is managed by an integration or bot'
    granted = [name for name in PRIVILEGED_PERMISSIONS if getattr(role.permissions, name)]
    if granted:
        return f"it grants {', '.join(granted)}"
    return None
