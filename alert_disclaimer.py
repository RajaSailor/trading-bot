"""Shared public-channel notice; wording is not a guarantee of legal compliance."""

PUBLIC_DISCLAIMER = (
    "Educational information only - not investment advice or a recommendation; "
    "I am not a SEBI-registered investment adviser or research analyst; "
    "trading involves risk, and decisions and losses are your responsibility."
)


def with_disclaimer(message):
    return message.rstrip() + "\n" + PUBLIC_DISCLAIMER
