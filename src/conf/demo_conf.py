"""Startup configuration hook example.

``@configure(topic=...)`` registers the function into the framework's config
registry; ``Master`` calls every registered function **with no arguments** at
construction time. Registration happens at import, so this module MUST be
imported before ``Master()`` - the ``main.py`` entry point already does that.

Only one function per topic: re-registering silently overwrites, so each
config module should use its own topic.
"""

from zoo_framework.core.aop import configure

# Set to True after the hook runs; usable as a self-check. Delete together with
# the assignment below if unneeded.
executed = False


@configure(topic="demo_conf")
def demo_conf():
    """Executed when the framework starts.

    A place for startup preparation: initializing logging, validating
    environment variables, opening external connections, and so on. The function
    takes no arguments and its return value is ignored - read what you need from
    the configuration.
    """
    global executed
    executed = True
