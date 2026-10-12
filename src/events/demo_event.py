"""Event reactor example.

``@event(topic=..., channel=...)`` registers the function as the reactor for
that topic on that channel; importing this module finishes the registration.
Channels are isolated from each other: a same-named topic sent to another
channel does not trigger this reactor.

The callback receives an **EventReactorReq** object rather than the raw
payload; the payload is on ``req.content``.
"""

from zoo_framework.core.aop import event

# Received event payloads, usable as a self-check. Delete together with the
# append below if unneeded.
received = []


@event(topic="demo_topic", channel="demo_channel")
def on_demo(req):
    """Called when a demo_topic event arrives.

    Args:
        req: The event request object; ``req.topic`` / ``req.content`` /
            ``req.reactor_name``.
    """
    received.append(req.content)
